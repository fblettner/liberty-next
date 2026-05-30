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
from liberty.connectors import ConnectorRegistry, UnknownConnectorError
from liberty.dashboards import DashboardsFile
from liberty.menus import AppMenu, MenuItem, MenusFile, build_menu_tree
from liberty.web.deps import get_connectors, get_menus, request_language

router = APIRouter(prefix="/api", tags=["menus"])

Menus = Annotated[MenusFile, Depends(get_menus)]


def _get_dashboards(request: Request) -> DashboardsFile:
    return request.app.state.dashboards


Dashboards = Annotated[DashboardsFile, Depends(_get_dashboards)]
Connectors = Annotated[ConnectorRegistry, Depends(get_connectors)]


def _app_settings(connectors: ConnectorRegistry, app: str) -> tuple[bool, str | None]:
    """Read the connector's app-level settings — ``(show_in_switcher, home)``. Returns the model
    defaults when the connector is missing (orphan menu) so the menu still surfaces."""
    try:
        cfg = connectors.get(app).config
    except UnknownConnectorError:
        return True, None
    return bool(getattr(cfg, "show_in_switcher", True)), getattr(cfg, "home", None)


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
            # ``target`` is the qualified ``<scope>.<id>`` id.
            return dashboards.find(did) is not None
        if item.type == "page":
            # A page leaf points at a frontend route; the route enforces its own auth. Only the
            # roles filter + the menu deny (both above) gate it here.
            return True
        perm = f"{'sql' if item.type == 'query' else 'api'}:{connector}:{item.target}"
        return principal.has_permission(perm) or principal.has_permission(menu_perm)

    return keep


def _home_path(app: str, app_menu: AppMenu, home: str | None, *, keep) -> str | None:
    """Resolve the connector's ``home`` (a menu item id) to a frontend route —
    ``/dashboard/<id>`` for ``type = 'dashboard'``, ``/sql/<connector>/<target>`` for queries,
    ``/http/<c>/<t>`` for endpoints. ``None`` when no home is set, the target isn't a leaf, or
    the caller can't see the target."""
    if not home:
        return None
    target = next((it for it in app_menu.items if it.id == home), None)
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
    app: str, app_menu: AppMenu, *, language: str | None, principal: Principal,
    dashboards: DashboardsFile, connectors: ConnectorRegistry,
) -> dict[str, Any] | None:
    keep = _keeper(principal, dashboards, app)
    items = build_menu_tree(app_menu, app=app, language=language, keep=keep)
    if not items:
        return None  # nothing the caller can see → no menu for this app
    show_in_switcher, home = _app_settings(connectors, app)
    out: dict[str, Any] = {"app": app, "label": app_menu.label or app, "items": items, "show_in_switcher": show_in_switcher}
    home_path = _home_path(app, app_menu, home, keep=keep)
    if home_path:
        out["home_path"] = home_path
    return out


@router.get(
    "/menus",
    summary="List menus",
    responses={
        401: {"description": "Missing / invalid access token."},
    },
)
async def list_menus(
    request: Request, principal: CurrentPrincipal, menus: Menus, dashboards: Dashboards, connectors: Connectors,
) -> dict[str, Any]:
    """Returns ``{ "menus": { "<app>": <menu-tree>, ... } }``. Each tree carries:
    ``label``, ``items`` (the visible tree), ``show_in_switcher`` (whether the app
    appears in the workspace switcher), and ``home_path`` (the default landing route
    for the app — first visible leaf item). Apps with no visible items are dropped."""
    lang = request_language(request)
    out = {
        app: tree
        for app, app_menu in menus.menus.items()
        if (tree := _app_tree(app, app_menu, language=lang, principal=principal, dashboards=dashboards, connectors=connectors)) is not None
    }
    return {"menus": out}


@router.get(
    "/menus/{app}",
    summary="Get app menu",
    responses={
        401: {"description": "Missing / invalid access token."},
        404: {"description": "App not found, or every item in its menu is permission-filtered out."},
    },
)
async def get_app_menu(
    app: str, request: Request, principal: CurrentPrincipal, menus: Menus, dashboards: Dashboards, connectors: Connectors,
) -> dict[str, Any]:
    """Same shape as one element of the ``GET /api/menus`` map, narrowed to a single
    app. Returns 404 when the caller can see nothing in the menu — keeps parity with
    ``/api/screens/{app}``."""
    app_menu = menus.menus.get(app)
    tree = _app_tree(
        app, app_menu, language=request_language(request), principal=principal,
        dashboards=dashboards, connectors=connectors,
    ) if app_menu else None
    if tree is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No accessible menu for app {app!r}")
    return tree
