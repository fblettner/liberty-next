"""``/api/screens`` — the per-app screen catalog, permission-pruned.

| Route | Permission | Notes |
|---|---|---|
| ``GET /api/screens`` | authenticated | every accessible screen, grouped by app — labels in the request's language, screens the caller can't read filtered out |
| ``GET /api/screens/{app}`` | authenticated | one app's screens (404 if no screens for the app, 404 if everything is filtered) |
| ``GET /api/screens/{app}/{screen_id}`` | per-screen ``sql:{connector}:{read_query}`` | one screen's full definition (dialog + actions + row_menu) |

A screen is kept iff the caller holds ``sql:{connector}:{read_query}`` — the same gate the
``TableView`` already checks before issuing the SELECT. The screen's effective ``connector``
(when not spelled out it's the app name) carries through to that permission check, so a
screen on a cross-pool query (e.g. an NOMAJDE menu pointing at a ``jdedwards`` query) gates
on *that* connector's permission.

Each row in the list response is the screen's ``public_dict()`` — `id`/`label`/`connector`/
the four CRUD query names + the flags. The single-screen route adds the dialog/actions/row_menu
so the frontend can render the form. The same ``X-Liberty-Lang`` header used by the rest of the
API drives label resolution.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from liberty.auth.dependencies import CurrentPrincipal
from liberty.auth.principal import Principal
from liberty.screens import Screen, ScreensFile
from liberty.web.deps import get_screens, request_language

router = APIRouter(prefix="/api", tags=["screens"])

Screens = Annotated[ScreensFile, Depends(get_screens)]


def _screen_connector(screen: Screen, *, app: str) -> str:
    """Effective connector — explicit ``connector`` field if set, else the app name (matches
    the convention :mod:`liberty.migrations.v1.migrate_screens` uses to keep cross-pool
    references explicit and same-pool references implicit)."""
    return screen.connector or app


def _can_read(principal: Principal, screen: Screen, *, app: str) -> bool:
    perm = f"sql:{_screen_connector(screen, app=app)}:{screen.read_query}"
    return principal.has_permission(perm)


def _label_for(screen: Screen, *, language: str | None) -> str:
    """Display label for the list view — currently just ``label`` then ``description`` then
    the screen ``id``. (Per-language overrides on the screen itself land later — for now the
    *dictionary* drives translation when fields are rendered.)"""
    return screen.label or screen.description or screen.id


def _list_view(screen: Screen, *, app: str, language: str | None) -> dict[str, Any]:
    """The compact descriptor returned by ``GET /api/screens`` — no dialog / actions / row_menu
    body, just enough for the frontend to (a) render the screen list and (b) decide whether to
    fetch the full screen body for a given (connector, query). The three ``has_*`` flags are the
    gate: when *any* is true the TableView fires ``GET /api/screens/{app}/{id}`` to pull the
    full body in (so right-click row menus appear even on screens without a dialog, and toolbar
    actions appear even without row menus, etc.)."""
    return {
        "id": screen.id,
        "app": app,
        "label": _label_for(screen, language=language),
        "description": screen.description,
        "connector": _screen_connector(screen, app=app),
        "read_query": screen.read_query,
        "update_query": screen.update_query,
        "insert_query": screen.insert_query,
        "delete_query": screen.delete_query,
        "auto_load": screen.auto_load,
        "audit": screen.audit,
        "editable": screen.editable,
        "uploadable": screen.uploadable,
        "has_dialog": screen.dialog is not None,
        "has_row_menu": bool(screen.row_menu),
        "has_actions": bool(screen.actions),
    }


def _full_view(screen: Screen, *, app: str, language: str | None) -> dict[str, Any]:
    """The full screen descriptor — ``GET /api/screens/{app}/{id}``. Includes the dialog/actions/row_menu
    body so the frontend can render the form. ``model_dump(mode='json')`` drops Pydantic's defaults that
    weren't set and gives us plain dicts ready to ship over JSON."""
    body = screen.model_dump(mode="json", exclude_none=True)
    return {**_list_view(screen, app=app, language=language), **body}


@router.get("/screens")
async def list_screens(request: Request, principal: CurrentPrincipal, screens: Screens) -> dict[str, Any]:
    lang = request_language(request)
    out: dict[str, list[dict[str, Any]]] = {}
    for app, app_screens in screens.screens.items():
        kept = [
            _list_view(s, app=app, language=lang)
            for s in app_screens.values()
            if _can_read(principal, s, app=app)
        ]
        if kept:
            out[app] = kept
    return {"screens": out}


@router.get("/screens/{app}")
async def list_app_screens(
    app: str, request: Request, principal: CurrentPrincipal, screens: Screens
) -> dict[str, Any]:
    app_screens = screens.screens.get(app)
    if not app_screens:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No screens for app {app!r}")
    lang = request_language(request)
    kept = [
        _list_view(s, app=app, language=lang)
        for s in app_screens.values()
        if _can_read(principal, s, app=app)
    ]
    if not kept:
        # The app has screens, but the caller can't read any — keep parity with /api/menus' 404.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No accessible screens for app {app!r}")
    return {"app": app, "screens": kept}


@router.get("/screens/{app}/{screen_id}")
async def get_screen(
    app: str, screen_id: str, request: Request, principal: CurrentPrincipal, screens: Screens
) -> dict[str, Any]:
    app_screens = screens.screens.get(app) or {}
    screen = app_screens.get(screen_id)
    if screen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown screen {app!r}/{screen_id!r}")
    if not _can_read(principal, screen, app=app):
        # 404 (not 403) so we don't leak the existence of screens the caller can't open —
        # same convention the connector routes use.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown screen {app!r}/{screen_id!r}")
    return _full_view(screen, app=app, language=request_language(request))
