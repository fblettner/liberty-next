"""The pages an application ships.

A configured screen is a grid and a form; some things an application has to show are neither (a
three-level structure to browse, a guided flow). For those an application ships a **page**: one
JavaScript module per page under its plugin folder, ``plugins/<app>/pages/<page>.js``, built by the
application from its own source. The browser loads it in a tab — the route ``/app/<app>/<page>``,
which a menu entry of type ``page`` points at — and hands it what the framework's own pages are
made of (React, the shared components, the API client, the theme): see
``frontend/src/pages/AppPage/sdk.ts`` for that contract.

Two routes, both behind the sign-in:

* ``GET /api/pages`` — the pages the caller may open;
* ``GET /api/pages/{app}/{page}`` — the module's source.

The permission is ``page:<app>:<page>`` (a superuser holds it); it is checked before the file is
looked up, and :func:`liberty.web.menus.page_permission` asks the same one of the menu entry. A page
is code of the application, like its Python: it runs in the caller's session and reads only what
the caller's permissions let the API answer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status

from liberty.auth.dependencies import CurrentPrincipal
from liberty.licensing import offered
from liberty.plugins.invoke import plugins_root
from liberty.web.deps import require_permission

router = APIRouter(prefix="/api", tags=["pages"])

#: the route a page opens at: ``/app/<app>/<page>``
ROUTE = re.compile(r"^/app/([a-z0-9][a-z0-9_]*)/([a-z0-9][a-z0-9_]*)$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_]*$")
#: a module larger than this is not a page (a bundle that pulled a framework in)
MAX_BYTES = 2_000_000


def permission_for(app: str, page: str) -> str:
    return f"page:{app}:{page}"


def route_permission(target: str | None) -> str | None:
    """The permission of the route *target* when it is an application's page, else None."""
    m = ROUTE.match(target or "")
    return permission_for(m.group(1), m.group(2)) if m else None


def page_file(app: str, page: str, root: Path | None = None) -> Path | None:
    """The module of *page*, or None: the names are plain identifiers (nothing of a path in them)
    and the file sits exactly at ``<plugins>/<app>/pages/<page>.js``."""
    if not _NAME.match(app) or not _NAME.match(page):
        return None
    path = (root or plugins_root()) / app / "pages" / f"{page}.js"
    return path if path.is_file() else None


def discover_pages(root: Path | None = None) -> list[dict[str, str]]:
    """Every ``plugins/<app>/pages/<page>.js`` whose names are valid, sorted."""
    base = root or plugins_root()
    out: list[dict[str, str]] = []
    if not base.is_dir():
        return out
    for path in sorted(base.glob("*/pages/*.js")):
        app, page = path.parent.parent.name, path.stem
        if _NAME.match(app) and _NAME.match(page):
            out.append({"app": app, "page": page, "route": f"/app/{app}/{page}"})
    return out


@router.get("/pages", summary="List the applications' pages")
async def list_pages(principal: CurrentPrincipal, request: Request) -> dict[str, Any]:
    """The pages the applications ship that the caller may open — none of an application the licence does not cover."""
    connectors = getattr(request.app.state, "connectors", None)
    return {"pages": [p for p in discover_pages() if offered(connectors, p["app"])
                      and (principal.is_superuser or principal.has_permission(permission_for(p["app"], p["page"])))]}


@router.get("/pages/{app}/{page}", summary="Read an application's page")
async def read_page(app: str, page: str, principal: CurrentPrincipal, request: Request) -> Response:
    """The page's module (JavaScript). 403 without ``page:<app>:<page>``, or when the licence does not cover the
    application; 404 when the application ships no such page — or has not built it."""
    require_permission(principal, permission_for(app, page))
    if not offered(getattr(request.app.state, "connectors", None), app):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"The licence of this installation does not cover the application {app}.")
    path = page_file(app, page)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No page {app}/{page}: the application ships none, or it is not built.")
    if path.stat().st_size > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"The page {app}/{page} is larger than {MAX_BYTES // 1_000_000} MB.")
    # the page changes with the application's version: always asked again, never kept by a proxy
    return Response(path.read_bytes(), media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "no-store"})
