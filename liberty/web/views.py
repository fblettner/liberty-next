"""``/api/views`` — per-user saved views (grid formats, chart specs).

Durable replacement for the browser localStorage that used to hold a table's
column layout: a view is stored per ``(username, kind, view_key)`` on the auth
pool (see :mod:`liberty.userviews`) so it follows the user across devices.

Always scoped to the authenticated caller's own username — no extra permission
needed (you can only read / write your own views). ``view_key`` is the
app-scoped identity the frontend builds (e.g. ``"<app>::<screen>::<grid>"``),
passed as a query arg on GET/DELETE and in the body on PUT so it may contain
``/`` or ``::`` without path-encoding games.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from liberty.auth.dependencies import CurrentPrincipal

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/views", tags=["views"])

# Known view kinds. Extensible — add a kind here + a frontend producer.
_KINDS = {"grid", "chart"}


class ViewBody(BaseModel):
    """``PUT /api/views/{kind}`` body."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=512, description="App-scoped view key.")
    payload: dict[str, Any] = Field(default_factory=dict, description="The saved view (frontend-owned JSON).")


def _store(request: Request):
    st = getattr(request.app.state, "user_views", None)
    if st is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="user-view store unavailable (auth pool not reachable at boot)",
        )
    return st


def _check_kind(kind: str) -> None:
    if kind not in _KINDS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"unknown view kind {kind!r} (known: {sorted(_KINDS)})",
        )


@router.get("/{kind}", summary="Get a saved view")
async def get_view(kind: str, key: str, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    """The caller's saved view for *kind*/*key*. ``payload`` is null when none
    is saved (the grid then falls back to the screen default / column config)."""
    _check_kind(kind)
    payload = await _store(request).get(principal.username, kind, key)
    return {"kind": kind, "key": key, "payload": payload}


@router.get("", summary="List my saved views")
async def list_views(request: Request, principal: CurrentPrincipal, kind: Optional[str] = None) -> dict[str, Any]:
    """Every view the caller has saved (optionally filtered by kind)."""
    if kind is not None:
        _check_kind(kind)
    return {"views": await _store(request).list(principal.username, kind)}


@router.put("/{kind}", summary="Save a view")
async def put_view(kind: str, body: ViewBody, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    """Save (upsert) the caller's view for *kind*/``body.key``."""
    _check_kind(kind)
    await _store(request).put(principal.username, kind, body.key, body.payload)
    return {"ok": True, "kind": kind, "key": body.key}


@router.delete("/{kind}", summary="Reset a saved view")
async def delete_view(kind: str, key: str, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    """Delete the caller's saved view (reset to the screen default / columns)."""
    _check_kind(kind)
    deleted = await _store(request).delete(principal.username, kind, key)
    return {"deleted": deleted}
