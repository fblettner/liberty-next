"""``/api/menus`` — the app navigation menus, resolved and permission-pruned.

| Route | Permission | Notes |
|---|---|---|
| ``GET /api/menus`` | authenticated | every app's menu tree, labels in the request's language, with every leaf the caller can't run (and the folders left empty) removed |
| ``GET /api/menus/{app}`` | authenticated | one app's tree; 404 if there's no menu for it or nothing survives the pruning |

A leaf is kept iff:
* ``query`` — the caller holds ``sql:{connector}:{target}``
* ``endpoint`` — the caller holds ``api:{connector}:{target}``
* ``dashboard`` — the dashboard with id ``target`` exists in the catalog AND the caller can read
  at least one of its data widgets (or holds ``dashboard:<id>`` / the menu allow); a dashboard of
  texts and links only is kept
* ``page`` — kept (the target frontend route enforces its own auth), except a page of a
  feature area with its own permission: the nomaflow pages (``/nomaflow``…) need ``nomaflow:view``
  (or a menu allow), the Data Explorer (``/explorer``) a model of the app the caller may use
  (``explorer:<app>:<model>``); the item's ``roles`` filter applies to all

An application is listed when one of its kept leaves carries a permission of the caller: links
that need none (an open page, an external address, a dashboard of texts) do not surface an
application by themselves — unless the caller holds the leaf's menu allow.

…plus any ``roles`` filter on the item. The frontend uses these to render the sidebar;
with no ``menus.toml`` (or nothing accessible) it falls back to the flat connector list.
Hot-reloaded together with ``connectors.toml`` via ``POST /admin/reload``.
"""

from __future__ import annotations

import re

from urllib.parse import quote

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from liberty.auth.dependencies import CurrentPrincipal
from liberty.auth.principal import Principal
from liberty.connectors import ConnectorRegistry, UnknownConnectorError
from liberty.dashboards import DashboardsFile, LinksWidget, TextWidget
from liberty.menus import AppMenu, MenuItem, MenusFile, build_menu_tree
from liberty.screens import ScreensFile
from liberty.web.deps import get_connectors, get_menus, get_screens, request_language
from liberty.web.schemas import AppMenu as AppMenuOut, ExternalUrls, MenusOut

router = APIRouter(prefix="/api", tags=["menus"])

Menus = Annotated[MenusFile, Depends(get_menus)]

# Page routes whose feature area has its own permission: a menu leaf to them is shown only to who
# may open them (the frontend's Access editor mirrors this — pages/Settings/Access/permissions.ts).
_PAGE_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("/nomaflow", "nomaflow:view"),
    # the tests (liberty/web/admin/apptests.py): their results and their cases, from an application's menu
    ("/liberty/test-results", "tests:view"), ("/liberty/tests", "tests:view"),
)
# A permission that includes another (an editor of jobs also sees them — liberty/web/jobs.py; who runs or edits
# the tests also sees them).
_INCLUDED_IN: dict[str, tuple[str, ...]] = {"nomaflow:view": ("nomaflow:edit",), "tests:view": ("tests:run", "tests:edit")}


_EXPLORER_ON = re.compile(r"^/explorer/([^/]+)/([^/]+)(?:/[ad]/[^/]+)?$")


def page_permission(target: str | None) -> str | None:
    """The permission a ``page`` leaf's route needs, or None when the route is open."""
    t = target or ""
    # a page an application ships (/app/<app>/<page>, liberty/web/app_pages.py): its own permission
    from liberty.web.app_pages import route_permission
    shipped = route_permission(t)
    if shipped:
        return shipped
    # the Explorer opened on ONE model — or on an analysis / a dashboard published in a menu
    # (/explorer/<app>/<model>[/a|d/<id>], liberty/web/admin/menu_publish.py): who may use that model
    on_model = _EXPLORER_ON.match(t)
    if on_model:
        return f"explorer:{on_model.group(1)}:{on_model.group(2)}"
    return next((perm for prefix, perm in _PAGE_PERMISSIONS if t == prefix or t.startswith(prefix + "/")), None)


def _get_dashboards(request: Request) -> DashboardsFile:
    return request.app.state.dashboards


Dashboards = Annotated[DashboardsFile, Depends(_get_dashboards)]
Connectors = Annotated[ConnectorRegistry, Depends(get_connectors)]
Screens = Annotated[ScreensFile, Depends(get_screens)]


def _app_settings(connectors: ConnectorRegistry, app: str, roles: tuple[str, ...] = ()) -> tuple[bool, str | None]:
    """Read the connector's app-level settings — ``(show_in_switcher, home)``; ``home`` is the item
    of the caller's first role listed in ``home_by_role``, else ``home``. Returns the model
    defaults when the connector is missing (orphan menu) so the menu still surfaces."""
    try:
        cfg = connectors.get(app).config
    except UnknownConnectorError:
        return True, None
    by_role = getattr(cfg, "home_by_role", None) or {}
    home = next((by_role[r] for r in roles if r in by_role), None) or getattr(cfg, "home", None)
    return bool(getattr(cfg, "show_in_switcher", True)), home


def _screen_perm(screens: "ScreensFile | None", app: str, screen_id: str | None, connectors: Any = None) -> str | None:
    """The ``sql:<connector>:<read_query>`` permission gating a ``screen`` menu leaf — resolved
    from the designed screen ``(app, screen_id)``. Returns ``None`` when the screen can't be
    resolved (no catalog / unknown id), so the caller falls back to the menu-allow check."""
    if screens is None or not screen_id:
        return None
    scr = (screens.screens.get(app) or {}).get(screen_id)
    if scr is None:
        return None
    from liberty.connectors.api_rows import read_permission

    return read_permission(connectors, scr.connector or app, scr.read_query)      # sql:… or, for an endpoint a screen reads, api:…


EXPLORER_PAGE = "/explorer"


def _explorer_models(explorer: Any, app: str) -> list[str]:
    return [mid for a, mid, _m in (explorer.items() if explorer is not None and hasattr(explorer, "items") else []) if a == app]


def _open_to_all(item: MenuItem, dashboards: DashboardsFile, explorer: Any, app: str) -> bool:
    """A leaf that needs no permission at all — an external address, an open page, a dashboard
    of texts and links: shown inside an application, never the reason an application is listed."""
    if item.type == "url":
        return True
    if item.type == "page":
        if item.target == EXPLORER_PAGE:
            return explorer is None or not _explorer_models(explorer, app)
        return page_permission(item.target) is None
    if item.type == "dashboard":
        d = dashboards.find(item.target or "")
        return d is not None and not any(not isinstance(w, (TextWidget, LinksWidget)) for w in d.widgets)
    return False


def _keeper(
    principal: Principal, dashboards: DashboardsFile, app: str, screens: "ScreensFile | None" = None,
    *, charts: Any = None, explorer: Any = None, connectors: Any = None,
):
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
            # A menu-allow doesn't fabricate a link to a dashboard that isn't in the catalog
            # (``target`` is the qualified ``<scope>.<id>`` id). An existing one is shown to who
            # can read one of its data widgets, or holds ``dashboard:<id>`` / the menu allow: a
            # dashboard that would render empty is not a link. Texts and links only: shown.
            d = dashboards.find(did)
            if d is None:
                return False
            if principal.has_permission(f"dashboard:{did}") or principal.has_permission(menu_perm):
                return True
            from liberty.web.dashboards import readable   # web.dashboards imports nothing from here

            return readable(principal, d, charts) is not False
        if item.type == "page":
            # A page leaf points at a frontend route; the route enforces its own auth. The roles
            # filter + the menu deny (both above) gate it, and a feature area's own permission.
            perm = page_permission(item.target)
            if item.target == EXPLORER_PAGE and explorer is not None:
                # the Data Explorer of this application: a model of it the caller may use
                models = _explorer_models(explorer, app)
                if models and not principal.has_permission(menu_perm):
                    return any(principal.has_permission(f"explorer:{app}:{m}") for m in models)
            return (perm is None or principal.has_permission(menu_perm)
                    or any(principal.has_permission(p) for p in (perm, *_INCLUDED_IN.get(perm, ()))))
        if item.type == "url":
            # An external address: nothing of ours to run — the roles filter + the menu deny gate it.
            return True
        if item.type == "job":
            # A job leaf opens the job for the user to run: shown to who may run it — ``job:<id>``
            # held by name, or given by "read and change" on the entry or its folder
            # (liberty.auth.implied). The user routes check the same permission, so an entry is never
            # shown to someone the run would refuse.
            return principal.has_permission(f"job:{item.target or ''}")
        if item.type == "screen":
            # A screen leaf's target is a screen id — resolve it to the screen's read query and
            # gate on that (same ``sql:<connector>:<read_query>`` the screen catalog uses). When
            # the screen can't be resolved, a menu-allow can still surface it.
            perm = _screen_perm(screens, app, item.target, connectors)
            if perm is None:
                return principal.has_permission(menu_perm)
            return principal.has_permission(perm) or principal.has_permission(menu_perm)
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
    if target.type == "job":
        # A pinned preset rides in the tab target as ``<job>::<preset>`` (``::`` is not legal in a
        # job id) — the frontend's menuPath.jobTarget builds the same.
        preset = target.params.get("preset")
        return f"/job/{quote(f'{target.target}::{preset}' if preset else target.target, safe='')}"
    if target.type == "screen":
        # A screen home opens the dedicated screen route — resolved to its specific screen id.
        return f"/screen/{app}/{target.target}"
    prefix = "sql" if target.type == "query" else "http"
    return f"/{prefix}/{effective_connector}/{target.target}"


def app_is_theirs(principal: Principal, app: str, app_menu: AppMenu, keep: Any, dashboards: DashboardsFile, explorer: Any) -> bool:
    """Whether a kept leaf of the menu carries a permission of the caller — see the module doc.
    An explicit menu allow on a leaf (``menu:<app>:<id>``, which ``*`` and a superuser hold)
    counts, whatever the leaf."""
    return any(
        it.type is not None and keep(it, it.connector or app)
        and (not _open_to_all(it, dashboards, explorer, app) or principal.has_permission(f"menu:{app}:{it.id}"))
        for it in app_menu.items
    )


def _app_tree(
    app: str, app_menu: AppMenu, *, language: str | None, principal: Principal,
    dashboards: DashboardsFile, connectors: ConnectorRegistry, screens: "ScreensFile | None" = None,
    charts: Any = None, explorer: Any = None,
) -> dict[str, Any] | None:
    keep = _keeper(principal, dashboards, app, screens, charts=charts, explorer=explorer, connectors=connectors)
    items = build_menu_tree(app_menu, app=app, language=language, keep=keep)
    if not items or not app_is_theirs(principal, app, app_menu, keep, dashboards, explorer):
        return None  # nothing of the caller's → no menu for this app
    show_in_switcher, home = _app_settings(connectors, app, tuple(principal.roles))
    out: dict[str, Any] = {"app": app, "label": app_menu.label or app, "items": items, "show_in_switcher": show_in_switcher}
    home_path = _home_path(app, app_menu, home, keep=keep)
    if home_path:
        out["home_path"] = home_path
    return out


@router.get("/external-urls", summary="The install's external addresses",
            responses={200: {"model": ExternalUrls, "description": "The named addresses."}})
async def external_urls(request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    """``{urls: {NAME: address}}`` — ``[app] external_urls``, what a ``#URL.NAME#`` placeholder of an
    ``open_url`` action or a ``url`` menu item becomes in the browser. Signed-in only: an internal
    address is not for the sign-in page."""
    del principal
    return {"urls": dict(request.app.state.settings.app.external_urls)}


@router.get(
    "/menus",
    summary="List menus",
    responses={
        200: {"model": MenusOut, "description": "Per app, the menu tree the caller may see."},
        401: {"description": "Missing / invalid access token."},
    },
)
async def list_menus(
    request: Request, principal: CurrentPrincipal, menus: Menus, dashboards: Dashboards, connectors: Connectors, screens: Screens,
) -> dict[str, Any]:
    """Returns ``{ "menus": { "<app>": <menu-tree>, ... } }``. Each tree carries:
    ``label``, ``items`` (the visible tree), ``show_in_switcher`` (whether the app
    appears in the workspace switcher), and ``home_path`` (the default landing route
    for the app — first visible leaf item). Apps with no visible items are dropped."""
    lang = request_language(request)
    out = {
        app: tree
        for app, app_menu in menus.menus.items()
        if (tree := _app_tree(app, app_menu, language=lang, principal=principal, dashboards=dashboards, connectors=connectors, screens=screens,
                              charts=getattr(request.app.state, "charts", None), explorer=getattr(request.app.state, "explorer", None))) is not None
    }
    return {"menus": out}


@router.get(
    "/menus/{app}",
    summary="Get app menu",
    responses={
        200: {"model": AppMenuOut, "description": "The app's menu tree."},
        401: {"description": "Missing / invalid access token."},
        404: {"description": "App not found, or every item in its menu is permission-filtered out."},
    },
)
async def get_app_menu(
    app: str, request: Request, principal: CurrentPrincipal, menus: Menus, dashboards: Dashboards, connectors: Connectors, screens: Screens,
) -> dict[str, Any]:
    """Same shape as one element of the ``GET /api/menus`` map, narrowed to a single
    app. Returns 404 when the caller can see nothing in the menu — keeps parity with
    ``/api/screens/{app}``."""
    app_menu = menus.menus.get(app)
    tree = _app_tree(
        app, app_menu, language=request_language(request), principal=principal,
        dashboards=dashboards, connectors=connectors, screens=screens,
        charts=getattr(request.app.state, "charts", None), explorer=getattr(request.app.state, "explorer", None),
    ) if app_menu else None
    if tree is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No accessible menu for app {app!r}")
    return tree
