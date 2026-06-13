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
    """``PUT /api/views/{kind}`` body — save one named view."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=512, description="App-scoped view key (e.g. the screen/table id).")
    name: str = Field(min_length=1, max_length=128, description="The view's name (unique per user + key).")
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


@router.get("/{kind}", summary="List my saved views for a key")
async def list_views(kind: str, key: str, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    """The caller's named views for *kind*/*key* — ``{views: [{name, payload,
    updated_at}]}``. Empty when the user has saved none (the grid then shows a
    shared view / the column-config default)."""
    _check_kind(kind)
    return {"kind": kind, "key": key, "views": await _store(request).list_views(principal.username, kind, key)}


@router.put("/{kind}", summary="Save a named view")
async def put_view(kind: str, body: ViewBody, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    """Save (upsert) one of the caller's named views for *kind*/``body.key``."""
    _check_kind(kind)
    await _store(request).save_view(principal.username, kind, body.key, body.name, body.payload)
    return {"ok": True, "kind": kind, "key": body.key, "name": body.name}


@router.delete("/{kind}", summary="Delete one named view")
async def delete_view(kind: str, key: str, name: str, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    """Delete one of the caller's named views."""
    _check_kind(kind)
    deleted = await _store(request).delete_view(principal.username, kind, key, name)
    return {"deleted": deleted}
