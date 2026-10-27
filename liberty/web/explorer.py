"""``/api/explorer`` — the Data Explorer (C3).

| Route | Permission | Notes |
|---|---|---|
| ``GET /api/explorer`` | authenticated | the models the caller may use (``explorer:{app}:{model}``) |
| ``GET /api/explorer/{app}/{model}/model`` | ``explorer:{app}:{model}`` | the model with its discovered fields; ``?refresh=1`` bypasses the cache |
| ``POST /api/explorer/{app}/{model}/query`` | ``explorer:{app}:{model}`` | an aggregate run (:mod:`liberty.explorer.engine`); ``debug`` adds the SQL for a superuser |
| ``POST /api/explorer/{app}/{model}/detail`` | ``explorer:{app}:{model}`` | detail rows, paged; ``drill`` turns an aggregate cell into filters |
| ``POST /api/explorer/{app}/{model}/calc/test`` | ``explorer:{app}:{model}`` | compile a calculated field and sample eight values |
| ``GET/PUT /api/explorer/{app}/{model}/saved/{kind}``, ``DELETE …/{id}`` | ``explorer:{app}:{model}`` | saved analyses (``analyses``) and dashboards of analyses (``dashboards``): shared + mine |
| ``POST /api/explorer/{app}/{model}/export`` | ``explorer:{app}:{model}`` | an analysis (saved or inline) or a dashboard as ``xlsx`` / ``pdf`` (:mod:`liberty.explorer.report`) |

An app holds several models (data marts); ``explorer:<app>:*`` grants them all. The permission
is checked before the model lookup (no name enumeration). The fields come
from :mod:`liberty.explorer.model`, under the caller's row-level scope and language.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
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
from liberty.explorer.report import build_sections, common_period, sections_to_pdf, sections_to_xlsx
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


def permission_for(app: str, model: str) -> str:
    return f"explorer:{app}:{model}"


def _model_or_404(explorer: ExplorerFile, app: str, model: str) -> Any:
    m = explorer.get(app, model)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no explorer model {app}/{model}")
    return m


def _saved_key(app: str, model: str) -> str:
    return f"{app}/{model}"


@router.get("/explorer", summary="List explorer models")
async def list_models(principal: CurrentPrincipal, explorer: Explorer) -> dict[str, Any]:
    """``{models: [{app, model, label, description, hub, entities: n}]}`` — only the models the
    caller holds ``explorer:{app}:{model}`` for."""
    out = []
    for app, mid, m in explorer.items():
        if principal.has_permission(permission_for(app, mid)):
            out.append({"app": app, "model": mid, "label": m.label or mid, "description": m.description, "hub": m.hub, "entities": len(m.entities)})
    return {"models": out}


@router.get("/explorer/{app}/{model}/model", summary="Get an explorer model with its fields")
async def get_model(
    app: str, model: str, request: Request, principal: CurrentPrincipal, explorer: Explorer, cache: Cache, connectors: Connectors, screens: Screens,
    language: Language, refresh: bool = False,
) -> dict[str, Any]:
    require_permission(principal, permission_for(app, model))
    view = await cache.get(connectors, screens, app, _model_or_404(explorer, app, model), language=language, refresh=refresh)
    return view.to_dict()


async def _run(
    request: Request, app: str, model: str, runner: Any, body: Any, principal: Any, explorer: ExplorerFile, cache: ModelCache, connectors: ConnectorRegistry,
    screens: ScreensFile, language: str | None,
) -> dict[str, Any]:
    """Permission, model, cached view, then the engine; a request the model cannot serve is a
    422 naming the reason, a database error a 502 (the full text for a superuser)."""
    require_permission(principal, permission_for(app, model))
    view = await cache.get(connectors, screens, app, _model_or_404(explorer, app, model), language=language)
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


@router.post("/explorer/{app}/{model}/query", summary="Run an explorer aggregate")
async def query_model(
    app: str, model: str, request: Request, body: AggregateRequest, principal: CurrentPrincipal, explorer: Explorer, cache: Cache, connectors: Connectors,
    screens: Screens, language: Language,
) -> dict[str, Any]:
    """``{columns, rows, groups, scanned, truncated}`` — see :func:`liberty.explorer.engine.run_aggregate`."""
    return await _run(request, app, model, run_aggregate, body, principal, explorer, cache, connectors, screens, language)


@router.post("/explorer/{app}/{model}/detail", summary="Run an explorer detail query")
async def detail_model(
    app: str, model: str, request: Request, body: DetailRequest, principal: CurrentPrincipal, explorer: Explorer, cache: Cache, connectors: Connectors,
    screens: Screens, language: Language,
) -> dict[str, Any]:
    """``{columns, rows, total, page, page_size}`` — the rows behind an aggregate cell (``drill``)
    or any filtered list of the primary entity's rows."""
    return await _run(request, app, model, run_detail, body, principal, explorer, cache, connectors, screens, language)


@router.post("/explorer/{app}/{model}/calc/test", summary="Test a calculated field")
async def calc_test(
    app: str, model: str, request: Request, body: CalcTestRequest, principal: CurrentPrincipal, explorer: Explorer, cache: Cache, connectors: Connectors,
    screens: Screens, language: Language,
) -> dict[str, Any]:
    """Compile the named calculated field (declared in ``calculated``) and sample eight values:
    ``{name, type, kind, values}`` — the editor's "Test" before "Save"."""
    return await _run(request, app, model, run_calc_test, body, principal, explorer, cache, connectors, screens, language)


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


@router.get("/explorer/{app}/{model}/saved/{kind}", summary="List saved analyses or dashboards")
async def list_saved(app: str, model: str, kind: str, request: Request, principal: CurrentPrincipal, explorer: Explorer) -> dict[str, Any]:
    """The shared objects plus the caller's own: ``{items: [{id, name, description, audience,
    owner, shared, bundled, version, updated_at, definition}]}``."""
    require_permission(principal, permission_for(app, model))
    _model_or_404(explorer, app, model)
    return {"items": await _saved_store(request).list(_saved_key(app, model), _kind(kind), principal.username)}


@router.put("/explorer/{app}/{model}/saved/{kind}", summary="Save an analysis or a dashboard")
async def put_saved(app: str, model: str, kind: str, body: SaveBody, request: Request, principal: CurrentPrincipal, explorer: Explorer) -> dict[str, Any]:
    """Create (no ``id`` or ``as_new``) or replace. Replacing a shared object changes it for
    everyone; the ``bundled`` marker is dropped so a later upgrade leaves the edited copy alone."""
    require_permission(principal, permission_for(app, model))
    _model_or_404(explorer, app, model)
    record = await _saved_store(request).save(
        _saved_key(app, model), _kind(kind), principal.username, obj_id=body.id, name=body.name, definition=body.definition,
        description=body.description, audience=body.audience, shared=body.shared, as_new=body.as_new,
    )
    return {"item": record}


@router.delete("/explorer/{app}/{model}/saved/{kind}/{obj_id}", summary="Delete a saved analysis or dashboard")
async def delete_saved(app: str, model: str, kind: str, obj_id: str, request: Request, principal: CurrentPrincipal, explorer: Explorer) -> dict[str, Any]:
    """The owner, or a superuser, deletes; a shared object by someone else is refused (403)."""
    require_permission(principal, permission_for(app, model))
    _model_or_404(explorer, app, model)
    try:
        deleted = await _saved_store(request).delete(_saved_key(app, model), _kind(kind), principal.username, obj_id, superuser=principal.is_superuser)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return {"deleted": deleted}


# ── export ────────────────────────────────────────────────────────────────────


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    format: Literal["xlsx", "pdf"] = "xlsx"
    analysis: str | None = Field(default=None, description="A saved analysis id…")
    dashboard: str | None = Field(default=None, description="…or a saved dashboard id (one section per tile)…")
    definition: dict[str, Any] | None = Field(default=None, description="…or an inline AnalysisDefinition.")
    title: str | None = None
    from_: date | None = Field(default=None, alias="from", description="A forced window (overrides the definitions' periods).")
    to: date | None = None


@router.post("/explorer/{app}/{model}/export", summary="Export an analysis or a dashboard")
async def export_model(
    app: str, model: str, body: ExportBody, request: Request, principal: CurrentPrincipal, explorer: Explorer, cache: Cache, connectors: Connectors,
    screens: Screens, language: Language,
) -> StreamingResponse:
    """The file (``Content-Disposition: attachment``). A PDF needs the ``reports`` extra (503
    otherwise). The caller's row-level scope applies, as on the page."""
    require_permission(principal, permission_for(app, model))
    view = await cache.get(connectors, screens, app, _model_or_404(explorer, app, model), language=language)
    key = _saved_key(app, model)
    items: list[tuple[str, dict[str, Any]]] = []
    title = body.title or ""
    if body.definition is not None:
        items.append((title or "Analysis", body.definition))
    elif body.analysis:
        a = await _saved_store(request).get(key, KIND_ANALYSIS, principal.username, body.analysis)
        if a is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no analysis {body.analysis!r}")
        title = title or a["name"]
        items.append((a["name"], a["definition"]))
    elif body.dashboard:
        store = _saved_store(request)
        d = await store.get(key, KIND_DASHBOARD, principal.username, body.dashboard)
        if d is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no dashboard {body.dashboard!r}")
        title = title or d["name"]
        analyses = {a["id"]: a for a in await store.list(key, KIND_ANALYSIS, principal.username)}
        period = (d["definition"] or {}).get("period")
        for tile in (d["definition"] or {}).get("tiles") or []:
            a = analyses.get(tile.get("analysis"))
            if a is None:
                continue
            definition = dict(a["definition"])
            if period and definition.get("period", {}) and (definition.get("period") or {}).get("field"):
                definition["period"] = {**definition["period"], "preset": period.get("preset"), "range": period.get("range") or {}}
            items.append((tile.get("title") or a["name"], definition))
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="give an analysis, a dashboard or a definition")
    window = (body.from_, body.to) if (body.from_ and body.to) else None
    try:
        sections = await build_sections(connectors, screens, view, items, window=window, scope=current_scope.get())
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=db_error_detail(exc, context=f"explorer {app} export failed", verbose=principal.is_superuser, log=_log)) from exc
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (title or "explorer")).strip("_") or "explorer"
    if body.format == "pdf":
        from liberty.reports.render import ReportsUnavailableError
        settings = request.app.state.settings
        branding = getattr(getattr(settings, "reports", None), "branding", None)
        try:
            data = sections_to_pdf(
                sections, title or "Explorer", app_name=getattr(settings.app, "name", ""), branding=branding.model_dump() if branding is not None else None,
                lang=language, author=principal.username, period=common_period(items, window),
            )
        except ReportsUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return StreamingResponse(iter([data]), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{safe}.pdf"'})
    data = sections_to_xlsx(sections)
    return StreamingResponse(iter([data]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{safe}.xlsx"'})
