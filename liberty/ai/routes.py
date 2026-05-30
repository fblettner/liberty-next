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


@router.get(
    "/tools",
    summary="List AI tools",
    responses={
        401: {"description": "Missing / invalid access token."},
        403: {"description": "Caller lacks ``ai:chat`` permission."},
        404: {"description": "AI assistant is disabled (``[ai] enabled = false`` in app.toml)."},
    },
)
async def tools(
    request: Request,
    _: Annotated[Principal, Depends(require_permission(_AI_CHAT))],
) -> dict[str, Any]:
    """Diagnostic surface for the AI assistant. Returns:

    - ``available`` — whether the assistant has a working Anthropic API key.
    - ``model`` — the model id currently in use (e.g. ``claude-sonnet-4-6``).
    - ``tools`` — full tool catalogue: connector-tools (``list_connectors`` /
      ``sql_query`` / optionally ``api_call``) and scaffold-tools
      (``introspect_table`` / ``scaffold_*``) when ``[ai] scaffold_tools = true``.

    Useful when wiring the assistant for the first time or after toggling a flag —
    operators can hit this to confirm the runtime saw the change."""
    assistant = _assistant(request)
    return {
        "available": assistant.available,
        "model": assistant.settings.model,
        "tools": assistant.tool_catalog(),
    }


@router.post(
    "/chat",
    summary="Chat (streaming)",
    responses={
        200: {
            "description": (
                "Server-Sent-Events stream. Each ``data:`` line is one JSON ChatEvent. "
                "Types: ``token`` (text delta) · ``thinking`` (adaptive-thinking delta) · "
                "``tool_call`` (assistant invoked ``name(input)``) · ``tool_result`` "
                "(tool returned) · ``proposal`` (scaffold-tool returned a Proposal envelope "
                "ready for Apply) · ``error`` (terminal) · ``done`` (clean stop with usage)."
            ),
            "content": {"text/event-stream": {}},
        },
        400: {"description": "First message must have ``role = \"user\"``."},
        401: {"description": "Missing / invalid access token."},
        403: {"description": "Caller lacks ``ai:chat`` permission."},
        404: {"description": "AI assistant is disabled."},
    },
)
async def chat(
    body: ChatRequest,
    request: Request,
    _: Annotated[Principal, Depends(require_permission(_AI_CHAT))],
) -> StreamingResponse:
    """Stream a chat completion as Server-Sent Events.

    **Request body:**

    ```json
    {
      "messages": [
        {"role": "user", "content": "list connectors then show me 5 rows from security_users"}
      ],
      "model": "claude-sonnet-4-6"  // optional override; defaults to [ai] model
    }
    ```

    **Wire format:** standard ``text/event-stream`` — newline-delimited ``data:`` lines.
    Each frame is a JSON object (one ``ChatEvent``); the stream ends with a literal
    ``data: [DONE]`` sentinel after the final ``done`` event so clients have an explicit
    stop marker. The browser SSE consumer in the SPA is
    ``frontend/src/ai/useChat.ts`` — pair against that for the canonical client shape.

    **Tool loop:** the assistant runs up to ``[ai] max_tool_rounds`` rounds of
    tool-use → tool-result before forcing a final assistant message. Each tool call
    fires one ``tool_call`` event and (after the tool returns) one ``tool_result``
    event so the UI can render a pending → completed status pill per call."""
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
