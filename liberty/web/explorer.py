"""``/api/explorer`` — the Data Explorer (C3).

| Route | Permission | Notes |
|---|---|---|
| ``GET /api/explorer`` | authenticated | the models the caller may use (``explorer:{app}``) |
| ``GET /api/explorer/{app}/model`` | ``explorer:{app}`` | the model with its discovered fields; ``?refresh=1`` bypasses the cache |
| ``POST /api/explorer/{app}/query`` | ``explorer:{app}`` | an aggregate run (:mod:`liberty.explorer.engine`); ``debug`` adds the SQL for a superuser |

The permission is checked before the model lookup (no name enumeration). The fields come
from :mod:`liberty.explorer.model`, under the caller's row-level scope and language.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

import logging

from sqlalchemy.exc import SQLAlchemyError

from liberty.auth.dependencies import CurrentPrincipal
from liberty.auth.scope import current_scope
from liberty.connectors.base import ConnectorError
from liberty.connectors.registry import ConnectorRegistry
from liberty.explorer import ExplorerFile
from liberty.explorer.engine import AggregateRequest, ExplorerError, run_aggregate
from liberty.explorer.model import ModelCache
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


@router.post("/explorer/{app}/query", summary="Run an explorer aggregate")
async def query_model(
    app: str, body: AggregateRequest, principal: CurrentPrincipal, explorer: Explorer, cache: Cache, connectors: Connectors,
    screens: Screens, language: Language,
) -> dict[str, Any]:
    """``{columns, rows, groups, scanned, truncated}`` — see :func:`liberty.explorer.engine.run_aggregate`.
    A request the model cannot serve is a 422 naming the reason; a database error a 502 (the
    full text for a superuser)."""
    require_permission(principal, permission_for(app))
    model = explorer.explorer.get(app)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no explorer model for {app!r}")
    view = await cache.get(connectors, screens, app, model, language=language)
    try:
        return await run_aggregate(
            connectors, screens, view, body, scope=current_scope.get(), debug=body.debug and principal.is_superuser,
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
