"""``/api/explorer`` — the Data Explorer (C3).

| Route | Permission | Notes |
|---|---|---|
| ``GET /api/explorer`` | authenticated | the models the caller may use (``explorer:{app}``) |
| ``GET /api/explorer/{app}/model`` | ``explorer:{app}`` | the model with its discovered fields; ``?refresh=1`` bypasses the cache |
| ``POST /api/explorer/{app}/query`` | ``explorer:{app}`` | an aggregate run (:mod:`liberty.explorer.engine`); ``debug`` adds the SQL for a superuser |
| ``POST /api/explorer/{app}/detail`` | ``explorer:{app}`` | detail rows, paged; ``drill`` turns an aggregate cell into filters |
| ``POST /api/explorer/{app}/calc/test`` | ``explorer:{app}`` | compile a calculated field and sample eight values |
| ``GET/PUT /api/explorer/{app}/saved/{kind}``, ``DELETE …/{id}`` | ``explorer:{app}`` | saved analyses (``analyses``) and dashboards of analyses (``dashboards``): shared + mine |

The permission is checked before the model lookup (no name enumeration). The fields come
from :mod:`liberty.explorer.model`, under the caller's row-level scope and language.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

import logging

from sqlalchemy.exc import SQLAlchemyError

from liberty.auth.dependencies import CurrentPrincipal
from liberty.auth.scope import current_scope
from liberty.connectors.base import ConnectorError
from liberty.connectors.registry import ConnectorRegistry
from liberty.explorer import ExplorerFile
from liberty.explorer.engine import AggregateRequest, CalcTestRequest, DetailRequest, ExplorerError, run_aggregate, run_calc_test, run_detail
from liberty.explorer.model import ModelCache
from liberty.explorer.store import KIND_ANALYSIS, KIND_DASHBOARD, AnalysisStore
from liberty.web.errors import db_error_detail, http_for_connector_error

from liberty.screens import ScreensFile
from liberty.web.deps import get_connectors, get_screens, request_language, require_permission

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["explorer"])


def get_explorer(request: Request) -> ExplorerFile:
    return request.app.state.explorer


def get_model_cache(request: Request) -> ModelCache:
    return request.app.state.explorer_cache


Explorer = Annotated[ExplorerFile, Depends(get_explorer)]
Cache = Annotated[ModelCache, Depends(get_model_cache)]
Screens = Annotated[ScreensFile, Depends(get_screens)]
Connectors = Annotated[ConnectorRegistry, Depends(get_connectors)]
Language = Annotated[str | None, Depends(request_language)]


def permission_for(app: str) -> str:
    return f"explorer:{app}"


@router.get("/explorer", summary="List explorer models")
async def list_models(principal: CurrentPrincipal, explorer: Explorer) -> dict[str, Any]:
    """``{models: [{app, label, description, hub, entities: n}]}`` — only the models the caller
    holds ``explorer:{app}`` for."""
    out = []
    for app, m in explorer.explorer.items():
        if principal.has_permission(permission_for(app)):
            out.append({"app": app, "label": m.label or app, "description": m.description, "hub": m.hub, "entities": len(m.entities)})
    return {"models": out}


@router.get("/explorer/{app}/model", summary="Get an explorer model with its fields")
async def get_model(
    app: str, principal: CurrentPrincipal, explorer: Explorer, cache: Cache, connectors: Connectors, screens: Screens,
    language: Language, refresh: bool = False,
) -> dict[str, Any]:
    require_permission(principal, permission_for(app))
    model = explorer.explorer.get(app)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no explorer model for {app!r}")
    view = await cache.get(connectors, screens, app, model, language=language, refresh=refresh)
    return view.to_dict()


async def _run(
    app: str, runner: Any, body: Any, principal: Any, explorer: ExplorerFile, cache: ModelCache, connectors: ConnectorRegistry,
    screens: ScreensFile, language: str | None,
) -> dict[str, Any]:
    """Permission, model, cached view, then the engine; a request the model cannot serve is a
    422 naming the reason, a database error a 502 (the full text for a superuser)."""
    require_permission(principal, permission_for(app))
    model = explorer.explorer.get(app)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no explorer model for {app!r}")
    view = await cache.get(connectors, screens, app, model, language=language)
    try:
        return await runner(
            connectors, screens, view, body, scope=current_scope.get(), debug=getattr(body, "debug", True) and principal.is_superuser,
        )
    except ExplorerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=db_error_detail(exc, context=f"explorer {app} query failed", verbose=principal.is_superuser, log=_log),
        ) from exc


@router.post("/explorer/{app}/query", summary="Run an explorer aggregate")
async def query_model(
    app: str, body: AggregateRequest, principal: CurrentPrincipal, explorer: Explorer, cache: Cache, connectors: Connectors,
    screens: Screens, language: Language,
) -> dict[str, Any]:
    """``{columns, rows, groups, scanned, truncated}`` — see :func:`liberty.explorer.engine.run_aggregate`."""
    return await _run(app, run_aggregate, body, principal, explorer, cache, connectors, screens, language)


@router.post("/explorer/{app}/detail", summary="Run an explorer detail query")
async def detail_model(
    app: str, body: DetailRequest, principal: CurrentPrincipal, explorer: Explorer, cache: Cache, connectors: Connectors,
    screens: Screens, language: Language,
) -> dict[str, Any]:
    """``{columns, rows, total, page, page_size}`` — the rows behind an aggregate cell (``drill``)
    or any filtered list of the primary entity's rows."""
    return await _run(app, run_detail, body, principal, explorer, cache, connectors, screens, language)


@router.post("/explorer/{app}/calc/test", summary="Test a calculated field")
async def calc_test(
    app: str, body: CalcTestRequest, principal: CurrentPrincipal, explorer: Explorer, cache: Cache, connectors: Connectors,
    screens: Screens, language: Language,
) -> dict[str, Any]:
    """Compile the named calculated field (declared in ``calculated``) and sample eight values:
    ``{name, type, kind, values}`` — the editor's "Test" before "Save"."""
    return await _run(app, run_calc_test, body, principal, explorer, cache, connectors, screens, language)


# ── saved analyses + dashboards ───────────────────────────────────────────────

_SAVED_KINDS = {"analyses": KIND_ANALYSIS, "dashboards": KIND_DASHBOARD}


def _saved_store(request: Request) -> AnalysisStore:
    views = getattr(request.app.state, "user_views", None)
    if views is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="the saved-views store is unavailable (auth pool not reachable at boot)")
    return AnalysisStore(views)


def _kind(kind: str) -> str:
    try:
        return _SAVED_KINDS[kind]
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown kind {kind!r} (analyses | dashboards)") from None


class SaveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, description="The object to replace; omitted = create.")
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    audience: str | None = Field(default=None, max_length=64)
    shared: bool = False
    as_new: bool = Field(default=False, description="Save as: a new object owned by the caller, whatever `id` says.")
    definition: dict[str, Any] = Field(default_factory=dict)


@router.get("/explorer/{app}/saved/{kind}", summary="List saved analyses or dashboards")
async def list_saved(app: str, kind: str, request: Request, principal: CurrentPrincipal, explorer: Explorer) -> dict[str, Any]:
    """The shared objects plus the caller's own: ``{items: [{id, name, description, audience,
    owner, shared, bundled, version, updated_at, definition}]}``."""
    require_permission(principal, permission_for(app))
    if app not in explorer.explorer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no explorer model for {app!r}")
    return {"items": await _saved_store(request).list(app, _kind(kind), principal.username)}


@router.put("/explorer/{app}/saved/{kind}", summary="Save an analysis or a dashboard")
async def put_saved(app: str, kind: str, body: SaveBody, request: Request, principal: CurrentPrincipal, explorer: Explorer) -> dict[str, Any]:
    """Create (no ``id`` or ``as_new``) or replace. Replacing a shared object changes it for
    everyone; the ``bundled`` marker is dropped so a later upgrade leaves the edited copy alone."""
    require_permission(principal, permission_for(app))
    if app not in explorer.explorer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no explorer model for {app!r}")
    record = await _saved_store(request).save(
        app, _kind(kind), principal.username, obj_id=body.id, name=body.name, definition=body.definition,
        description=body.description, audience=body.audience, shared=body.shared, as_new=body.as_new,
    )
    return {"item": record}


@router.delete("/explorer/{app}/saved/{kind}/{obj_id}", summary="Delete a saved analysis or dashboard")
async def delete_saved(app: str, kind: str, obj_id: str, request: Request, principal: CurrentPrincipal, explorer: Explorer) -> dict[str, Any]:
    """The owner, or a superuser, deletes; a shared object by someone else is refused (403)."""
    require_permission(principal, permission_for(app))
    if app not in explorer.explorer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no explorer model for {app!r}")
    try:
        deleted = await _saved_store(request).delete(app, _kind(kind), principal.username, obj_id, superuser=principal.is_superuser)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return {"deleted": deleted}
