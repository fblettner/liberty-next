"""``/admin`` routes — operational endpoints (superuser only).

``POST /admin/reload`` rebuilds the :class:`ConnectorRegistry` from
``connectors.toml`` on disk and swaps it into ``app.state`` (also re-pointing the
auth database at the new pool registry), then disposes the old registry. New
connector definitions and edited queries take effect immediately for subsequent
requests. The AI assistant's connector tools still reference the previous
registry until the app restarts; in-flight requests keep using whichever
registry they started with.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from liberty.auth.db import AuthDatabase
from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.connectors import load_connectors

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reload")
async def reload_connectors(
    request: Request, _: Annotated[Principal, Depends(require_superuser)]
) -> dict[str, object]:
    settings = request.app.state.settings
    old = request.app.state.connectors
    new = load_connectors(settings.connectors.config_path)
    request.app.state.connectors = new
    request.app.state.auth_db = AuthDatabase(new.pools, settings.auth.pool)
    await old.aclose()
    return {"reloaded": True, "connectors": new.names(), "pools": new.pools.names()}
