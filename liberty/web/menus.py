"""``/api/menus`` — the app navigation menus, resolved and permission-pruned.

| Route | Permission | Notes |
|---|---|---|
| ``GET /api/menus`` | authenticated | every app's menu tree, labels in the request's language, with every leaf the caller can't run (and the folders left empty) removed |
| ``GET /api/menus/{app}`` | authenticated | one app's tree; 404 if there's no menu for it or nothing survives the pruning |

A leaf is kept iff:
* ``query`` — the caller holds ``sql:{connector}:{target}``
* ``endpoint`` — the caller holds ``api:{connector}:{target}``
* ``dashboard`` — the dashboard with id ``target`` exists in the catalog (per-widget
  permission gating happens at render time; an "empty" dashboard still surfaces)

…plus any ``roles`` filter on the item. The frontend uses these to render the sidebar;
with no ``menus.toml`` (or nothing accessible) it falls back to the flat connector list.
Hot-reloaded together with ``connectors.toml`` via ``POST /admin/reload``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from liberty.auth.dependencies import CurrentPrincipal
from liberty.auth.principal import Principal
from liberty.dashboards import DashboardsFile
from liberty.menus import AppMenu, MenuItem, MenusFile, build_menu_tree
from liberty.web.deps import get_menus, request_language

router = APIRouter(prefix="/api", tags=["menus"])

Menus = Annotated[MenusFile, Depends(get_menus)]


def _get_dashboards(request: Request) -> DashboardsFile:
    return request.app.state.dashboards


Dashboards = Annotated[DashboardsFile, Depends(_get_dashboards)]


def _keeper(principal: Principal, dashboards: DashboardsFile):
    """A ``keep(item, connector)`` predicate. For ``query`` / ``endpoint`` leaves: the caller
    must run the target. For ``dashboard`` leaves: the dashboard must exist in the catalog
    (per-widget permission gating runs at render time). A ``roles`` filter on the item is
    always honoured."""

    def keep(item: MenuItem, connector: str) -> bool:
        if item.roles and not any(principal.has_role(r) for r in item.roles):
            return False
        if item.type == "dashboard":
            return (item.target or "") in dashboards.dashboards
        perm = f"{'sql' if item.type == 'query' else 'api'}:{connector}:{item.target}"
        return principal.has_permission(perm)

    return keep


def _app_tree(
    app: str, app_menu: AppMenu, *, language: str | None, principal: Principal, dashboards: DashboardsFile,
) -> dict[str, Any] | None:
    items = build_menu_tree(app_menu, app=app, language=language, keep=_keeper(principal, dashboards))
    if not items:
        return None  # nothing the caller can see → no menu for this app
    return {"app": app, "label": app_menu.label or app, "items": items}


@router.get("/menus")
async def list_menus(
    request: Request, principal: CurrentPrincipal, menus: Menus, dashboards: Dashboards,
) -> dict[str, Any]:
    lang = request_language(request)
    out = {
        app: tree
        for app, app_menu in menus.menus.items()
        if (tree := _app_tree(app, app_menu, language=lang, principal=principal, dashboards=dashboards)) is not None
    }
    return {"menus": out}


@router.get("/menus/{app}")
async def get_app_menu(
    app: str, request: Request, principal: CurrentPrincipal, menus: Menus, dashboards: Dashboards,
) -> dict[str, Any]:
    app_menu = menus.menus.get(app)
    tree = _app_tree(
        app, app_menu, language=request_language(request), principal=principal, dashboards=dashboards,
    ) if app_menu else None
    if tree is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No accessible menu for app {app!r}")
    return tree
