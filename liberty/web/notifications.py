"""``/api/notifications`` — the current user's inbox (the bell and the Notifications page), and
``POST /admin/notify/test`` — a superuser sends a message through the dispatcher to check the
channels (SMTP settings, a webhook) without waiting for an event.

| Route | Who | What |
|---|---|---|
| ``GET /api/notifications?unread=1&limit=50`` | any user | own rows, newest first |
| ``GET /api/notifications/unread_count`` | any user | the bell's badge |
| ``POST /api/notifications/read`` ``{ids: [...]}`` or ``{}`` | any user | mark own rows read (all when no ids) |
| ``POST /admin/notify/test`` | superuser | dispatch one message, returns the per-channel report |
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from liberty.auth.dependencies import CurrentPrincipal, require_superuser
from liberty.auth.principal import Principal
from liberty.notify import ActionTarget, Message

router = APIRouter(tags=["notifications"])
Superuser = Annotated[Principal, Depends(require_superuser)]


def _store(request: Request):
    st = getattr(request.app.state, "notifications", None)
    if st is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="notifications are unavailable (the framework pool could not be used)")
    return st


def _dispatcher(request: Request):
    d = getattr(request.app.state, "notify", None)
    if d is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="notifications are disabled")
    return d


@router.get("/api/notifications", summary="My notifications")
async def list_notifications(request: Request, principal: CurrentPrincipal, unread: bool = False, limit: int = 50) -> dict[str, Any]:
    rows = await _store(request).list_for(principal.username, unread_only=unread, limit=limit)
    return {"notifications": rows}


@router.get("/api/notifications/unread_count", summary="Unread count")
async def unread_count(request: Request, principal: CurrentPrincipal) -> dict[str, int]:
    return {"unread": await _store(request).unread_count(principal.username)}


class ReadBody(BaseModel):
    ids: list[str] | None = Field(default=None, description="The notifications to mark read; omitted = every unread one.")


@router.post("/api/notifications/read", summary="Mark read")
async def mark_read(body: ReadBody, request: Request, principal: CurrentPrincipal) -> dict[str, int]:
    return {"marked": await _store(request).mark_read(principal.username, body.ids)}


class TestBody(BaseModel):
    channels: list[Literal["portal", "email", "action"]] = Field(default_factory=lambda: ["portal"])
    recipients: list[str] = Field(default_factory=list, description="user:<name> / role:<name> / email:<address>")
    subject: str = Field(default="Test notification", max_length=512)
    body: str | None = None
    link: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] | None = Field(default=None, description="{connector, query | endpoint, params} for the action channel")


@router.post("/admin/notify/test", summary="Send a test notification")
async def send_test(body: TestBody, request: Request, principal: Superuser) -> dict[str, Any]:
    recipients = body.recipients or [f"user:{principal.username}"]
    action = None
    if body.action:
        a = body.action
        if not isinstance(a.get("connector"), str):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="action.connector is required")
        action = ActionTarget(connector=a["connector"], query=a.get("query"), endpoint=a.get("endpoint"), params=dict(a.get("params") or {}))
    report = await _dispatcher(request).dispatch(
        Message(subject=body.subject, body=body.body, link=body.link, rule="test", payload=body.payload),
        channels=list(body.channels), recipients=recipients, action=action,
    )
    return report.to_dict()
