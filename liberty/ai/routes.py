"""``/ai`` routes — the streaming chat endpoint and a tool-catalog inspector.

``POST /ai/chat`` runs the agentic loop and streams Server-Sent Events: one
``token`` event per text delta, ``tool_call`` / ``tool_result`` around each tool
invocation, and a terminal ``done`` (or ``error``). Gated behind the
``ai:chat`` permission. When AI is disabled in config the route is absent (404);
when enabled but unconfigured (no API key), the stream's first event is an
``error``.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from liberty.ai.assistant import AiAssistant
from liberty.auth.dependencies import require_permission
from liberty.auth.principal import Principal

router = APIRouter(prefix="/ai", tags=["ai"])

_AI_CHAT = "ai:chat"


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str | list[dict[str, Any]]


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None


def _assistant(request: Request) -> AiAssistant:
    assistant: AiAssistant | None = getattr(request.app.state, "ai", None)
    if assistant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The AI assistant is disabled")
    return assistant


@router.get("/tools")
async def tools(
    request: Request,
    _: Annotated[Principal, Depends(require_permission(_AI_CHAT))],
) -> dict[str, Any]:
    assistant = _assistant(request)
    return {
        "available": assistant.available,
        "model": assistant.settings.model,
        "tools": assistant.tool_catalog(),
    }


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    _: Annotated[Principal, Depends(require_permission(_AI_CHAT))],
) -> StreamingResponse:
    assistant = _assistant(request)
    if body.messages[0].role != "user":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="The first message must have role 'user'")
    api_messages = [{"role": m.role, "content": m.content} for m in body.messages]

    async def event_stream():
        async for event in assistant.chat(api_messages, model=body.model):
            yield f"data: {json.dumps(event.to_dict())}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
