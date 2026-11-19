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
import logging
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from liberty.ai.appconfig import AiFile, merged_starters
from liberty.ai.attachments import AttachmentError, accepted, check_content, to_block
from liberty.web.deps import request_language
from liberty.ai.assistant import AiAssistant
from liberty.ai.catalog import label_for
from liberty.ai.tools import hides_sensitive
from liberty.ai.usage import TurnRecorder, app_of
from liberty.auth.dependencies import require_permission
from liberty.auth.principal import Principal

router = APIRouter(prefix="/ai", tags=["ai"])
_log = logging.getLogger(__name__)

_AI_CHAT = "ai:chat"


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str | list[dict[str, Any]]


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    # What the user is looking at (app / screen / filters / the clicked row), rendered by the
    # browser; capped server-side to liberty.ai.assistant.CONTEXT_MAX_CHARS. Optional.
    context: str | None = None


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
    principal: Annotated[Principal, Depends(require_permission(_AI_CHAT))],
) -> dict[str, Any]:
    """Diagnostic surface for the AI assistant. Returns:

    - ``available`` — whether the assistant has a working Anthropic API key.
    - ``model`` — the model id currently in use (e.g. ``claude-sonnet-4-6``).
    - ``tools`` — full tool catalogue: connector-tools (``list_connectors`` /
      ``sql_query`` / optionally ``api_call``) and scaffold-tools
      (``introspect_table`` / ``scaffold_*``) when ``[ai] scaffold_tools = true``.
    - ``models`` — the models the caller may pick (``[ai] model`` first, then
      ``allowed_models``), with labels; one entry means no picker.
    - ``starters`` — the starter questions per ``*`` / app / app/screen: what the apps
      ship in ``ai.toml``, with the operator's ``[ai] starters`` on top.
    - ``starter_groups`` — the same keys, the questions under headings (``ai.toml`` ``groups``);
      the chat shows the headings as pills when a key has them.

    Useful when wiring the assistant for the first time or after toggling a flag —
    operators can hit this to confirm the runtime saw the change."""
    assistant = _assistant(request)
    return {
        "available": assistant.available,
        "model": assistant.settings.model,
        "tools": assistant.tool_catalog(principal),
        "models": [{"id": m, "label": label_for(m)} for m in _allowed_models(assistant)],
        # assistant.settings is the AISettings ([ai] in app.toml): the operator's starters win over ai.toml's
        "starters": merged_starters(getattr(request.app.state, "ai_config", None), assistant.settings.starters, request_language(request)),
        "starter_groups": (getattr(request.app.state, "ai_config", None) or AiFile()).starter_groups(request_language(request)),
        # files attached to a question: the size limit (0 = off) and the extensions the picker offers
        "attachments": {"max_mb": assistant.settings.attachment_max_mb, "accept": accepted() if assistant.settings.attachment_max_mb > 0 else []},
        # the installation keeps the data it flagged sensitive from the assistant: the chat leaves those
        # columns out of the page context it sends (the tools do the same on their side)
        "hide_sensitive": hides_sensitive(request.app.state),
    }


@router.post(
    "/attachments",
    summary="Prepare a file for a question",
    responses={
        400: {"description": "The file is too big, empty, of a kind that cannot be attached, or unreadable."},
        403: {"description": "Caller lacks ``ai:chat`` permission."},
        404: {"description": "AI assistant is disabled, or attachments are (``[ai] attachment_max_mb = 0``)."},
    },
)
async def attach(
    request: Request,
    _: Annotated[Principal, Depends(require_permission(_AI_CHAT))],
    file: Annotated[UploadFile, File(description="An image, a PDF, a spreadsheet or a text file.")],
) -> dict[str, Any]:
    """Turn an uploaded file into the part of a message the assistant reads: ``{name, kind,
    size, block[, note]}``. The browser adds ``block`` to the user message it sends to
    ``POST /ai/chat``. Nothing is stored and nothing is loaded anywhere: an image or a PDF comes
    back as base64, a spreadsheet or a text file as text (cut beyond a size, ``note`` says so)."""
    assistant = _assistant(request)
    max_mb = assistant.settings.attachment_max_mb
    if max_mb <= 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Attachments are disabled ([ai] attachment_max_mb = 0)")
    limit = max_mb * 1_048_576
    data = await file.read(limit + 1)
    try:
        return to_block(file.filename or "file", data, max_bytes=limit)
    except AttachmentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


NOTE_KIND = "ai_note"   # the user's notes in the saved-views store (liberty/web/views.py), keyed by application or "*"


async def _notes(request: Request, username: str, app: str | None) -> list[str]:
    """What *username* asked the assistant to remember: the notes for every application, then
    those of the page's application. Best-effort — without the store, no notes."""
    store = getattr(request.app.state, "user_views", None)
    if store is None:
        return []
    out: list[str] = []
    try:
        for key in ("*", *([app] if app else [])):
            for view in await store.list_views(username, NOTE_KIND, key):
                text = str((view.get("payload") or {}).get("text") or "").strip()
                if text:
                    out.append(text)
    except Exception as exc:  # noqa: BLE001
        _log.warning("AI notes of %s could not be read (%s)", username, exc)
    return out


async def _record(request: Request, recorder: TurnRecorder) -> None:
    """Keep the turn in the usage record — best-effort: a store that is down is logged, never
    an error for the user."""
    store = getattr(request.app.state, "ai_usage", None)
    if store is None:
        return
    try:
        await store.record(recorder)
    except Exception as exc:  # noqa: BLE001
        _log.warning("AI usage: the turn of %s could not be recorded (%s)", recorder.username, exc)


class ScreenWriteRequest(BaseModel):
    """One record written through its screen — what the chat's card applies."""

    app: str = Field(min_length=1)
    screen: str = Field(min_length=1)
    operation: Literal["insert", "update", "delete"]
    values: dict[str, Any] = Field(default_factory=dict)
    key: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class ScreenActionRequest(BaseModel):
    """One action of a screen run on a record — what the chat's card applies."""

    app: str = Field(min_length=1)
    screen: str = Field(min_length=1)
    action: str = Field(min_length=1)
    key: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


def _write_context(request: Request, principal: Principal) -> Any:
    from liberty.ai.write_tools import write_context
    from liberty.connectors.api import current_pool

    settings = request.app.state.settings.ai
    if not settings.enabled or not settings.write_tools:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The assistant's write tools are off ([ai] write_tools)")
    return write_context(request.app.state, principal, language=request_language(request), pool=current_pool.get())


async def _record_applied(
    request: Request, principal: Principal, tool_name: str, app: str | None, what: str, skipped: bool, *, error: str | None = None,
) -> None:
    """Keep an applied card in the usage record (channel ``apply``): who changed what through
    the assistant, on which record — the turn that proposed it recorded a proposal only. A check
    that wrote nothing (a dry run, a record that already held the values: *skipped*) is not a
    change and is not kept; a refused apply is, with the reason."""
    from liberty.ai.usage import CHANNEL_APPLY

    if skipped:
        return
    recorder = TurnRecorder(username=principal.username, model="apply", channel=CHANNEL_APPLY, app=app)
    recorder.observe({"type": "tool_call", "name": tool_name, "summary": what})
    recorder.observe({"type": "tool_result", "name": tool_name, "ok": error is None, "summary": error or "applied"})
    recorder.observe({"type": "done", "stop_reason": "end_turn", "usage": {}})
    await _record(request, recorder)


def _refusal(exc: Exception) -> HTTPException:
    text = str(exc)
    return HTTPException(status.HTTP_403_FORBIDDEN if text.startswith("permission denied") else status.HTTP_409_CONFLICT, detail=text)


class JobRunRequest(BaseModel):
    """A job run now for the user — what the chat's card applies."""

    job: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    preset: str | None = None
    # {step: the path POST /api/jobs/{job}/upload returned} for the steps that load a file
    files: dict[str, str] = Field(default_factory=dict)
    dry_run: bool = False


@router.post(
    "/job-run",
    summary="Run a job for the user",
    responses={
        403: {"description": "Caller lacks ``ai:write`` or ``job:<id>``."},
        404: {"description": "The write tools are off (``[ai] write_tools``)."},
        409: {"description": "The job cannot be run from here (it needs an uploaded file, is already running, a parameter is wrong)."},
    },
)
async def job_run(
    body: JobRunRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("ai:write"))],
) -> dict[str, Any]:
    """Apply what the assistant's `job_run` tool proposed in the chat: fire the job as the job's
    own page does for this user (its declared parameters, its presets, `job:<id>`). The run is
    queued; the answer carries its id."""
    from liberty.ai.write_tools import perform_job_run
    from liberty.screens.save import ScreenWriteError

    _write_context(request, principal)          # the switch ([ai] write_tools)
    what = (f"job={body.job} params={body.params}" + (f" preset={body.preset}" if body.preset else "")
            + (f" files={sorted(Path(v).name for v in body.files.values())}" if body.files else ""))
    try:
        out = await perform_job_run(request.app.state, principal, job=body.job, params=body.params, preset=body.preset,
                                    files=body.files, dry_run=body.dry_run)
    except ScreenWriteError as exc:
        await _record_applied(request, principal, "job_run", None, what, body.dry_run, error=str(exc))
        raise _refusal(exc) from exc
    await _record_applied(request, principal, "job_run", None, f"{what} run={out.get('run_id')}", body.dry_run)
    return out


@router.post(
    "/screen-action",
    summary="Run a screen's action",
    responses={
        403: {"description": "Caller lacks ``ai:write``, or may not open the screen."},
        404: {"description": "The write tools are off (``[ai] write_tools``)."},
        409: {"description": "The action cannot run (unknown, a value it asks is missing, the record is not found)."},
    },
)
async def screen_action(
    body: ScreenActionRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("ai:write"))],
) -> dict[str, Any]:
    """Apply what the assistant's `screen_action` tool proposed in the chat: run one action of a
    screen (a row menu's "Close", a button) on a record, with the screen's own steps — each one
    under its own permission. A step that fails is in the answer (`ok`, `error`), not a status."""
    from liberty.ai.write_tools import perform_action
    from liberty.screens.save import ScreenWriteError

    ctx = _write_context(request, principal)
    what = f"action={body.action} app={body.app} screen={body.screen} key={body.key}"
    try:
        out = await perform_action(ctx, app=body.app, screen=body.screen, action=body.action, key=body.key,
                                   inputs=body.inputs, params=body.params, dry_run=body.dry_run)
    except ScreenWriteError as exc:
        await _record_applied(request, principal, "screen_action", body.app, what, body.dry_run, error=str(exc))
        raise _refusal(exc) from exc
    failed = None if out.get("ok", True) else str(out.get("error") or "; ".join(out.get("errors") or []))
    await _record_applied(request, principal, "screen_action", body.app, what, body.dry_run, error=failed)
    return out


@router.post(
    "/screen-write",
    summary="Write a record through its screen",
    responses={
        403: {"description": "Caller lacks ``ai:write``, or the permission of a query the write runs."},
        404: {"description": "The write tools are off (``[ai] write_tools``)."},
        409: {"description": "The screen refuses the write (a required field, a value outside its list, a read-only record…)."},
    },
)
async def screen_write(
    body: ScreenWriteRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("ai:write"))],
) -> dict[str, Any]:
    """Apply what the assistant's `screen_write` tool proposed in the chat: add, change or delete
    one record through its screen — the screen's own queries, the form's rules, the audit table,
    the data scope. Everything is checked again here; `dry_run` checks without writing."""
    from liberty.ai.write_tools import perform
    from liberty.screens.save import ScreenWriteError

    ctx = _write_context(request, principal)
    what = f"operation={body.operation} app={body.app} screen={body.screen} key={body.key}"
    try:
        out = await perform(ctx, app=body.app, screen=body.screen, operation=body.operation, values=body.values,
                            key=body.key, params=body.params, inputs=body.inputs, dry_run=body.dry_run)
    except ScreenWriteError as exc:
        await _record_applied(request, principal, "screen_write", body.app, what, body.dry_run, error=str(exc))
        raise _refusal(exc) from exc
    if out.get("assigned"):
        what += f" assigned={out['assigned']}"          # the key the server gave a new record
    await _record_applied(request, principal, "screen_write", body.app, what, body.dry_run or not out.get("written"))
    return out


def _allowed_models(assistant: AiAssistant) -> list[str]:
    out = [assistant.settings.model]
    for m in assistant.settings.allowed_models:
        if m and m not in out:
            out.append(m)
    return out


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
        400: {"description": "First message must have ``role = \"user\"``, or ``model`` is not an allowed one."},
        401: {"description": "Missing / invalid access token."},
        403: {"description": "Caller lacks ``ai:chat`` permission."},
        404: {"description": "AI assistant is disabled."},
    },
)
async def chat(
    body: ChatRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission(_AI_CHAT))],
) -> StreamingResponse:
    """Stream a chat completion as Server-Sent Events.

    **Request body:**

    ```json
    {
      "messages": [
        {"role": "user", "content": "list connectors then show me 5 rows from security_users"}
      ],
      "model": "claude-sonnet-4-6",  // optional override; defaults to [ai] model
      "context": "app: nomajde\nscreen: f0092 — Users …"  // optional: the page the user is on
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
    if body.model and body.model not in _allowed_models(assistant):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"model {body.model!r} is not allowed — see [ai] allowed_models")
    limit = assistant.settings.attachment_max_mb * 1_048_576
    try:
        for m in body.messages:
            if m.role == "user":
                check_content(m.content, max_bytes=limit)
    except AttachmentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    api_messages = [{"role": m.role, "content": m.content} for m in body.messages]

    app = app_of(body.context)
    recorder = TurnRecorder(username=principal.username, model=body.model or assistant.settings.model, app=app)
    notes = await _notes(request, principal.username, app)

    async def event_stream():
        try:
            async for event in assistant.chat(api_messages, model=body.model, context=body.context, principal=principal, notes=notes):
                payload = event.to_dict()
                recorder.observe(payload)
                yield f"data: {json.dumps(payload)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            # also when the client went away mid-turn: what happened was still paid for
            await _record(request, recorder)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
