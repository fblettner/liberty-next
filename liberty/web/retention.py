"""``/admin/retention`` — the Retention page (C8): every store with its policy, where it is set
and its size; a purge per store, previewed with ``dry_run``. Superuser only. See
:mod:`liberty.retention_center`."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.retention_center import STORES, run, status as stores_status

router = APIRouter(prefix="/admin/retention", tags=["admin"])
Superuser = Annotated[Principal, Depends(require_superuser)]
_log = logging.getLogger(__name__)


@router.get("", summary="Every store's retention")
async def get_retention(request: Request, _: Superuser) -> dict[str, Any]:
    return {"stores": await stores_status(request.app.state)}


@router.post("/{store}/run", summary="Purge one store (or preview it)")
async def run_retention(store: str, request: Request, principal: Superuser, dry_run: bool = True) -> dict[str, Any]:
    """``dry_run`` (the default) counts what would go without deleting anything."""
    if store not in STORES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown store {store!r}")
    try:
        res = await run(request.app.state, store, dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not dry_run:
        _log.info("retention: %s purged by %s — %s", store, principal.username, res)
    return {"store": store, "dry_run": dry_run, **res}
