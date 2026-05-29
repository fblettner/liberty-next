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
* ``page`` — always kept (the target frontend route enforces its own auth); only the
  item's ``roles`` filter applies

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


def _keeper(principal: Principal, dashboards: DashboardsFile, app: str):
    """A ``keep(item, connector)`` predicate, with the first-class ``menu:<app>:<id>`` /
    ``dashboard:<id>`` RBAC overlay on top of the query-derived visibility:

    * An explicit **deny** (``!menu:<app>:<id>`` or ``!dashboard:<id>``) hides the node outright —
      this is how "all access but disable the Security menu" works (role has ``*`` + the deny).
    * Otherwise a node is visible when its target is **runnable** (the existing
      ``sql:``/``api:`` check, or the dashboard exists) **OR** an explicit ``menu:``/``dashboard:``
      **allow** surfaces it (``*`` satisfies that). So existing query-only roles are unchanged,
      and a "menu only" role (allow ``menu:<app>:<id>`` + the target query) shows just that menu.

    A ``roles`` filter on the item is still honoured first."""

    def keep(item: MenuItem, connector: str) -> bool:
        if item.roles and not any(principal.has_role(r) for r in item.roles):
            return False
        menu_perm = f"menu:{app}:{item.id}"
        if principal.is_denied(menu_perm):
            return False
        if item.type == "dashboard":
            did = item.target or ""
            if principal.is_denied(f"dashboard:{did}"):
                return False
            # Existence is the visibility gate for dashboards (per-widget perms gate at render);
            # a menu-allow doesn't fabricate a link to a dashboard that isn't in the catalog.
            return did in dashboards.dashboards
        if item.type == "page":
            # A page leaf points at a frontend route; the route enforces its own auth. Only the
            # roles filter + the menu deny (both above) gate it here.
            return True
        perm = f"{'sql' if item.type == 'query' else 'api'}:{connector}:{item.target}"
        return principal.has_permission(perm) or principal.has_permission(menu_perm)

    return keep


def _home_path(app: str, app_menu: AppMenu, *, keep) -> str | None:
    """Resolve ``AppMenu.home`` (a menu item id) to a frontend route — ``/dashboard/<id>`` for
    ``type = 'dashboard'``, ``/sql/<connector>/<target>`` for queries, ``/http/<c>/<t>`` for
    endpoints. ``None`` when no home is set, the target isn't a leaf (e.g. operator pointed it
    at a folder), or the caller can't see the target (in which case the workspace picker just
    falls through to the default landing — never leaks the home pointer to non-permitted users)."""
    if not app_menu.home:
        return None
    target = next((it for it in app_menu.items if it.id == app_menu.home), None)
    if target is None or not target.type or not target.target:
        return None
    # Resolve the effective connector exactly like ``build_menu_tree`` does (item.connector
    # else the app name) so the permission predicate ``keep(target, connector)`` runs against
    # the right scope. Dashboards have no connector — ``keep`` checks their existence instead.
    effective_connector = target.connector or app
    if not keep(target, effective_connector):
        return None
    if target.type == "dashboard":
        return f"/dashboard/{target.target}"
    prefix = "sql" if target.type == "query" else "http"
    return f"/{prefix}/{effective_connector}/{target.target}"


def _app_tree(
    app: str, app_menu: AppMenu, *, language: str | None, principal: Principal, dashboards: DashboardsFile,
) -> dict[str, Any] | None:
    keep = _keeper(principal, dashboards, app)
    items = build_menu_tree(app_menu, app=app, language=language, keep=keep)
    if not items:
        return None  # nothing the caller can see → no menu for this app
    out: dict[str, Any] = {"app": app, "label": app_menu.label or app, "items": items}
    # Only emit ``home_path`` when set AND the caller can reach it — non-permitted users see
    # the menu without the home pointer (and the workspace picker falls back to the default
    # landing). Keeps the wire payload terse for the common no-home case.
    home_path = _home_path(app, app_menu, keep=keep)
    if home_path:
        out["home_path"] = home_path
    return out


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
