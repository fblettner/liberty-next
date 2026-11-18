"""``/admin/ai-usage`` — the assistant's audit and usage record (Monitoring → AI usage): the
period in figures (totals, per user, per day, per model, per tool) and the latest turns with
their tool calls. Superuser only. See :mod:`liberty.ai.usage` for what is kept and what is not."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal

router = APIRouter(prefix="/admin/ai-usage", tags=["admin"])
Superuser = Annotated[Principal, Depends(require_superuser)]


def _store(request: Request) -> Any:
    store = getattr(request.app.state, "ai_usage", None)
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The AI usage record is not available (the assistant is disabled, or its pool is unusable)")
    return store


@router.get("", summary="AI usage over a period")
async def get_usage(
    request: Request, _: Superuser,
    days: Annotated[int, Query(ge=1, le=366)] = 30,
    username: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """``summary`` — totals and the breakdowns per user, day, model and tool over the last
    *days*; ``recent`` — the latest turns (*username* narrows them), each with its tool calls.
    ``retention_days`` is ``[ai] usage_retention_days``."""
    store = _store(request)
    return {
        "summary": await store.summary(days=days),
        "recent": await store.recent(days=days, limit=limit, username=username),
        "retention_days": int(getattr(request.app.state.settings.ai, "usage_retention_days", 0) or 0),
    }
