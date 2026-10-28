"""``/admin/dev`` — the Developer page (C12): what the running code is made of. Superuser only.
``GET /config`` — every app.toml key with where the UI edits it (or why it stays in the file) and the
code that reads it. ``GET /api`` — every route: method, path, group, the access it requires. See :mod:`liberty.devref`."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.devref import api_reference, config_reference

router = APIRouter(prefix="/admin/dev", tags=["admin"])
Superuser = Annotated[Principal, Depends(require_superuser)]


@router.get("/config", summary="Every setting: where it is edited, who reads it")
async def get_config_reference(_: Superuser) -> dict[str, Any]:
    keys = await asyncio.to_thread(config_reference)   # a scan of the package's sources (once per process)
    gaps = [k["key"] for k in keys if not k["editor"] and not k["file_only"]]
    return {"keys": keys, "gaps": gaps, "editable": sum(1 for k in keys if k["editor"]), "total": len(keys)}


@router.get("/api", summary="Every route: method, path, group, access")
async def get_api_reference(request: Request, _: Superuser) -> dict[str, Any]:
    routes = api_reference(request.app)
    return {"routes": routes, "total": len(routes)}
