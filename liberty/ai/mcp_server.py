"""The MCP endpoint (``/mcp``) — the assistant's tools for an external client (Claude Desktop,
claude.ai, Claude Code) acting as a Liberty user.

Nothing of the tools is written again: the list is :meth:`ToolRegistry.offered` for the caller,
a call is :meth:`ToolRegistry.execute` — the same permission checks, the same data scope, the
same result cap as the chat — and every call lands in the usage record under the ``mcp``
channel. The protocol is the official ``mcp`` package's (the ``mcp`` extra): its low-level
server with our two handlers, served by its Streamable HTTP session manager in stateless mode
(one process, no session to keep).

The caller is a **personal access token** (permission ``mcp:use``) in the ``Authorization:
Bearer`` header: pasted by the user (``POST /auth/tokens``) or obtained by the client through
the OAuth sign-in (:mod:`liberty.auth.oauth` — the user signs in to Liberty and allows it).
The account is re-read at every request (:func:`liberty.auth.routes.principal_from_personal_token`),
so the client holds the user's rights of the moment, never more.

Off unless ``[ai] mcp_enabled = true`` (read at each request: no restart to switch it). The
tools whose result is a card of the Liberty chat (the scaffolding proposals) are not offered.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from liberty.ai.assistant import _truncate, cut_result
from liberty.ai.usage import CHANNEL_MCP, TurnRecorder
from liberty.auth.principal import Principal, current_principal
from liberty.auth.scope import current_scope, current_username
from liberty.auth.tokens import TokenError

_log = logging.getLogger(__name__)

SCOPE_KEY = "liberty.principal"     # where the endpoint leaves the caller for the handlers

INSTRUCTIONS = (
    "Liberty Next — the user's business applications (configured queries, screens, dashboards, "
    "Data Explorer models, scheduled jobs). You act as the signed-in Liberty user: you see what "
    "they may see, and every tool is read-only unless its description says it saves something.\n"
    "- describe_instance tells what this installation holds; search_catalog finds a screen, a "
    "dashboard, a model, a job or a playbook by keyword.\n"
    "- For a total, a count, a ranking or a trend, use the Data Explorer tools (explorer_models, "
    "explorer_fields, explorer_query). Never add up rows yourself.\n"
    "- To read rows, call sql_query with the exact connector and query names the other tools "
    "show (a screen's query is on the screen's connector); narrow with `filters` instead of "
    "reading a table. A result marked truncated is a sample, never a basis for a count.\n"
    "- When the user asks HOW to do something in an application, list_playbooks then "
    "load_playbook, and follow it.\n"
    "- A tool named <application>_<something> is shipped by that application for its own subject.\n"
    "- If a tool answers 'permission denied', tell the user; do not look for another way."
)


def mcp_available() -> bool:
    try:
        import mcp.server.lowlevel  # noqa: F401
    except ImportError:
        return False
    return True


def _caller(ctx: Any) -> Principal:
    scope = getattr(getattr(ctx, "request", None), "scope", None) or {}
    who = scope.get(SCOPE_KEY)
    if not isinstance(who, Principal):
        raise PermissionError("no authenticated caller on this MCP request")
    return who


def build_server(state: Callable[[], Any]) -> Any:
    """The low-level MCP server over the live assistant's tool registry (read at each request:
    a reload of the configuration is seen)."""
    import mcp_types as types
    from mcp.server.lowlevel import Server

    from liberty import __version__

    def registry() -> Any:
        assistant = getattr(state(), "ai", None)
        if assistant is None:
            raise RuntimeError("the assistant is disabled on this server ([ai] enabled = false)")
        return assistant.tools

    async def on_list_tools(ctx: Any, params: Any) -> Any:
        who = _caller(ctx)
        return types.ListToolsResult(tools=[
            types.Tool(name=t.name, description=t.description, input_schema=t.input_schema,
                       annotations=types.ToolAnnotations(read_only_hint=t.read_only))
            for t in registry().offered(who) if not t.chat_only
        ])

    async def on_call_tool(ctx: Any, params: Any) -> Any:
        who = _caller(ctx)
        # the caller and its data scope for everything the tool runs — as the chat sets them
        current_principal.set(who)
        current_scope.set(who.data_scope)
        current_username.set(who.username)
        reg = registry()
        name, arguments = params.name, dict(params.arguments or {})
        tool = reg.get(name)
        recorder = TurnRecorder(username=who.username, model="mcp", channel=CHANNEL_MCP)
        recorder.observe({"type": "tool_call", "name": name, "summary": tool.summarize(arguments) if tool is not None else name})
        if tool is None or tool.chat_only or tool not in reg.offered(who):
            content, is_error = json.dumps({"error": f"unknown tool: {name}"}), True      # the same answer for "not yours"
        else:
            content, is_error = await reg.execute(name, arguments)
        full_size = len(content)
        content = cut_result(content)
        recorder.observe({"type": "tool_result", "name": name, "ok": not is_error, "summary": _truncate(content), "size": full_size})
        recorder.observe({"type": "done", "stop_reason": "end_turn", "usage": {}})
        store = getattr(state(), "ai_usage", None)
        if store is not None:
            try:
                await store.record(recorder)
            except Exception as exc:  # noqa: BLE001 — the record never fails a call
                _log.warning("AI usage: the MCP call of %s could not be recorded (%s)", who.username, exc)
        return types.CallToolResult(content=[types.TextContent(text=content)], is_error=is_error)

    return Server("liberty-next", version=__version__, title="Liberty Next", instructions=INSTRUCTIONS,
                  on_list_tools=on_list_tools, on_call_tool=on_call_tool)


@asynccontextmanager
async def running(app: Any) -> AsyncIterator[None]:
    """Run the MCP session manager for the application's lifetime (``app.state.mcp``); a no-op
    when the ``mcp`` extra is not installed — the endpoint then answers 503 with the hint."""
    if not mcp_available():
        app.state.mcp = None
        yield
        return
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    manager = StreamableHTTPSessionManager(app=build_server(lambda: app.state), json_response=True, stateless=True)
    async with manager.run():
        app.state.mcp = manager
        try:
            yield
        finally:
            app.state.mcp = None


class _Asgi:
    """An ASGI callable as an object: Starlette's ``Route`` treats a function as a
    request → response endpoint and anything else as an ASGI app."""

    def __init__(self, serve: Any) -> None:
        self._serve = serve

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self._serve(scope, receive, send)


def install(app: Any) -> None:
    """Serve the endpoint at ``/mcp`` — the exact path a client is given — and under ``/mcp/``.
    A mount alone answers ``/mcp/`` only: without the application's page mounted at ``/`` the
    router redirects ``/mcp`` to it, with it the bare path fell to the page (a 405 on the POST),
    so the endpoint was unreachable on a real server (7.0.77). Must be called before the page
    is mounted. The OAuth discovery addresses and the sign-in are :mod:`liberty.auth.oauth`'s."""
    from starlette.routing import Mount, Route

    serve = _Asgi(endpoint(app))
    app.router.routes.extend([
        Route("/mcp", endpoint=serve, methods=["GET", "POST", "DELETE"], name="mcp"),
        Mount("/mcp", app=serve, name="mcp-sub"),
    ])


def endpoint(app: Any) -> Any:
    """The ASGI app behind ``/mcp``: the switch, the caller's token — a personal access token,
    pasted by the user or obtained through the OAuth sign-in — then the package's session
    manager. A 401 names the OAuth metadata, which is how a client finds the sign-in."""
    from starlette.requests import Request

    from liberty.auth.oauth import challenge
    from liberty.auth.routes import principal_from_personal_token

    async def reply(send: Any, status: int, detail: str, headers: list[tuple[bytes, bytes]] | None = None) -> None:
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *(headers or [])]})
        await send({"type": "http.response.body", "body": body})

    async def serve(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        state = app.state
        settings = getattr(getattr(state, "settings", None), "ai", None)
        if settings is None or not settings.enabled or not settings.mcp_enabled:
            await reply(send, 404, "The MCP endpoint is disabled ([ai] mcp_enabled = false)")
            return
        manager = getattr(state, "mcp", None)
        if manager is None:
            await reply(send, 503, "The MCP endpoint needs the `mcp` extra: pip install 'liberty-next[mcp]'")
            return
        auth = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"authorization"), "")
        if not auth.lower().startswith("bearer "):
            await reply(send, 401, "Sign in (OAuth) or send a personal access token (Authorization: Bearer …)",
                        [(b"www-authenticate", challenge(Request(scope)).encode())])
            return
        try:
            who = await principal_from_personal_token(auth[7:].strip(), state.token_service, state.auth_backend)
        except TokenError as exc:
            await reply(send, 401, f"Invalid access token: {exc}",
                        [(b"www-authenticate", challenge(Request(scope), error="invalid_token").encode())])
            return
        scope[SCOPE_KEY] = who
        await manager.handle_request(scope, receive, send)

    return serve
