"""``/admin`` — clone an app namespace (cross-file duplication) (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, _snapshot_all_config, router
from liberty.web.admin.builders import _dictionary_path
from pydantic import BaseModel

from liberty.web.clone import CloneError, clone_app, delete_app

# ── clone an app namespace (cross-file duplication) ────────────────────────────────────────


class CloneAppBody(BaseModel):
    """Payload for ``POST /admin/config/clone-app``.

    Duplicates every per-app entry (connector / dictionary overlay / menu / screens) under
    ``new_app`` with ``connector`` field values inside the clone rewritten to point at
    ``new_app``. The cloned connector's ``pool`` is set to ``new_pool`` — which must
    already exist in :file:`connectors.toml` (create the database + the pool entry first;
    we refuse to clone pointing at a non-existent pool).

    The regression-testing path: create ``nomasx1b`` (CREATE DATABASE + add the pool +
    set a password via the UI), then clone ``nomasx1`` → ``nomasx1b`` to get a parallel
    app namespace pointing at the new DB. Run agent jobs against it, diff vs the existing
    ``nomasx1``.
    """

    source_app: str
    new_app: str
    new_pool: str


class DeleteAppBody(BaseModel):
    """Payload for ``POST /admin/config/delete-app``.

    Removes every per-app entry (connector / dictionary overlay / menu / screens) for
    *app*. The cross-file inverse of clone-app; same files touched, opposite direction.
    Pool deletion is NOT included — pools are managed separately via Liberty Next → Administer → Pools
    and may be shared between apps.
    """

    app: str


@router.post("/config/delete-app", summary="Delete app")
async def delete_app_endpoint(body: DeleteAppBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Remove a whole app namespace across the 4 config files. Surgical text-edit on
    each file — fast even on big dictionary.toml (preserves comments + formatting in
    every section that isn't being deleted). Atomic: validates the rewritten text via
    Pydantic before any file is touched; if any fails, nothing changes on disk.

    Does **not** reload — the caller calls ``POST /admin/reload`` after to apply changes
    everywhere (in-flight requests still see the old registry until they finish)."""
    _snapshot_all_config(request)  # cross-file mutation — snapshot all config first
    settings = request.app.state.settings
    try:
        result = delete_app(
            body.app,
            connectors_path=Path(settings.connectors.config_path),
            dictionary_path=_dictionary_path(settings),
            menus_path=Path(settings.menus.config_path),
            screens_path=Path(settings.screens.config_path),
            charts_path=Path(settings.charts.config_path),
            dashboards_path=Path(settings.dashboards.config_path),
        )
    except CloneError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return result.to_dict()


@router.post("/config/clone-app", summary="Clone app")
async def clone_app_endpoint(body: CloneAppBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Clone a whole app namespace across :file:`connectors.toml`, :file:`dictionary.toml`,
    :file:`menus.toml`, and :file:`screens.toml`. Atomic: validates every rewritten doc
    against its Pydantic schema before writing; if any fails, nothing changes on disk.

    Does **not** reload — the caller calls ``POST /admin/reload`` after to apply changes
    everywhere (in-flight requests still see the old registry until they finish)."""
    _snapshot_all_config(request)  # cross-file mutation — snapshot all config first
    settings = request.app.state.settings
    try:
        result = clone_app(
            body.source_app, body.new_app, new_pool=body.new_pool,
            connectors_path=Path(settings.connectors.config_path),
            dictionary_path=_dictionary_path(settings),
            menus_path=Path(settings.menus.config_path),
            screens_path=Path(settings.screens.config_path),
            charts_path=Path(settings.charts.config_path),
            dashboards_path=Path(settings.dashboards.config_path),
        )
    except CloneError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return result.to_dict()
