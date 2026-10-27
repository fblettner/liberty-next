"""AiAssistant — the agentic chat loop over Anthropic's Messages API.

Ported from nomaubl's ``AiAssistant``: stream text deltas to the caller as they
arrive, surface tool invocations, execute local tools, feed ``tool_result`` back,
and loop until the model returns a non-``tool_use`` stop reason or the safety cap
is reached. Differences: async (``AsyncAnthropic.messages.stream``), tools are
decorated Python functions (schema from type hints — see :mod:`liberty.ai.tools`),
the connectors are the built-in tools, and the optional server-side ``web_fetch``
is restricted to allowlisted domains.

:meth:`AiAssistant.chat` is an async generator of :class:`ChatEvent`; the web
layer serialises those as SSE.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

import json

from liberty.ai.connector_tools import build_connector_tools
from liberty.ai.instance_tools import build_instance_tools
from liberty.ai.ops_tools import build_ops_tools
from liberty.ai.scaffold_tools import build_scaffold_tools
from liberty.ai.tools import ToolRegistry
from liberty.config import AISettings
from liberty.connectors import ConnectorRegistry

DEFAULT_SYSTEM_PROMPT = (
    "You are the Liberty assistant — a connector-driven low-code framework. Users "
    "configure SQL queries and HTTP endpoints (\"connectors\") in TOML; you help them "
    "inspect and query their data and answer questions about it.\n\n"
    "Rules:\n"
    "1. To answer anything about the user's data, ALWAYS call list_connectors first to "
    "discover the available connectors and the exact query/endpoint names and parameters, "
    "then call sql_query (or api_call) with names taken from that result. Do not invent "
    "connector or query names.\n"
    "2. SQL access is read-only — there is no tool to mutate data. If the user asks you to "
    "change something, explain that you can only read.\n"
    "3. If a tool fails, tell the user the tool name, the input you tried, and the error "
    "message that came back so they can debug.\n"
    "4. Be concise and reply in the user's language.\n"
    "5. describe_instance tells you what THIS installation holds (applications, menus, screens, "
    "dashboards, explorer models, jobs, playbooks) — call it when the user asks what exists, where "
    "to find something, or which application handles a subject.\n"
    "6. Playbooks are the procedures the applications ship. When the user asks HOW to do something "
    "in an application, call list_playbooks; if one fits, load_playbook it and follow its steps in "
    "order, telling the user which playbook you follow. If none fits, say so and answer from what "
    "describe_instance shows.\n"
    "7. When offered, the operations tools read the running server: jobs_status (the scheduled "
    "jobs, their last and next runs, a job's recent runs with the failing step), server_log (the "
    "recent log lines) and integrity_checks (broken references in the configuration). Prefer "
    "them to guessing when the user reports that something failed, is late or is missing.\n"
    "8. End every reply with one last line of the form\n"
    "   >>> [\"a short follow-up question\", \"another one\"]\n"
    "   — two or three questions the user is likely to ask next, in their language, as a JSON "
    "list of strings; nothing after that line. Leave the line out when no follow-up makes "
    "sense (a yes/no answer, an error report).\n\n"
    "Scaffolding tools (introspect_table / scaffold_*): when enabled they let you propose "
    "new connector queries, dictionary entries, screens, and menu items. These tools never "
    "write to disk — they return a Proposal envelope that the user reviews and applies via "
    "an Apply button. Work step-by-step: emit ONE proposal at a time and STOP, telling the "
    "user what to review. After they apply, they will continue the conversation and you "
    "can move to the next step. Do not chain multiple proposals in one turn.\n"
    "Canonical chain to build a screen from a table:\n"
    "  a. introspect_table to confirm columns + primary key.\n"
    "  b. scaffold_crud_queries — STOP after this. DEFAULT to kinds=['select'] (read-only). "
    "Only request insert/update/delete when the user EXPLICITLY asks for an editable screen; "
    "for reports, drill-downs, aging balances, dashboards, lookups, or anything the user "
    "describes as a view / report / list, stick to select-only. Generating write paths the "
    "user didn't ask for is dangerous and pollutes the connector with queries they'll have to clean up.\n"
    "  c. scaffold_dict_entries for any column ids that need labels — STOP.\n"
    "  d. scaffold_screen pointing at the (now-applied) read_query — STOP.\n"
    "  e. scaffold_menu_item so the screen is reachable from the sidebar — DONE."
)

# The page context the browser sends with a turn (what the user is looking at: app, screen,
# filters, the clicked row) is capped here as well as in the SPA — it is a volatile, UNCACHED
# second system block, so its size is paid on every request.
CONTEXT_MAX_CHARS = 4000

# A tool result above this many characters is cut, with a note asking the model to narrow the
# call (filters, limits, fewer columns): one runaway SELECT must not flood the context.
TOOL_RESULT_MAX_CHARS = 60_000

# Server-side web_fetch (Anthropic-hosted). GA tool version — no beta header.
# Restricted to the operator-configured domains; never offered when the list is empty.
_WEB_FETCH_TYPE = "web_fetch_20260209"


FOLLOWUP_MARKER = ">>>"


class _FollowupSplitter:
    """Streams the reply's text while holding back the trailing ``>>> [...]`` line (rule 8 of
    the prompt): the user never sees the marker, the chat gets the questions as chips.

    Text is released up to the last point that can still be the start of a marker line, so a
    marker split across deltas is caught; at the end of the turn the held part is parsed — a
    JSON list of strings becomes the follow-ups, anything else is released as text."""

    def __init__(self) -> None:
        self._buf = ""          # text received but not yet released
        self._held = False      # the buffer starts at a marker line

    def feed(self, delta: str) -> str:
        """Return the text to show now."""
        if self._held:
            self._buf += delta
            return ""
        self._buf += delta
        i = self._buf.find("\n" + FOLLOWUP_MARKER)
        if i < 0 and self._buf.startswith(FOLLOWUP_MARKER):
            i = 0
        if i >= 0:
            out, self._buf, self._held = self._buf[:i], self._buf[i:], True
            return out
        # hold back a suffix that may be the beginning of "\n>>>"
        keep = 0
        probe = "\n" + FOLLOWUP_MARKER
        for n in range(min(len(self._buf), len(probe) - 1), 0, -1):
            if probe.startswith(self._buf[-n:]):
                keep = n
                break
        out, self._buf = self._buf[: len(self._buf) - keep], self._buf[len(self._buf) - keep:]
        return out

    def finish(self) -> tuple[str, list[str]]:
        """``(remaining text, follow-up questions)`` at the end of the turn."""
        buf, self._buf, held = self._buf, "", self._held
        self._held = False
        if not held:
            return buf, []
        line = buf.strip()
        payload = line[len(FOLLOWUP_MARKER):].strip() if line.startswith(FOLLOWUP_MARKER) else ""
        try:
            parsed = json.loads(payload)
        except ValueError:
            return buf, []
        if isinstance(parsed, list) and parsed and all(isinstance(q, str) and q.strip() for q in parsed):
            return "", [q.strip() for q in parsed][:4]
        return buf, []


@dataclass(slots=True)
class ChatEvent:
    """One event emitted by :meth:`AiAssistant.chat`."""

    type: str  # token | thinking | tool_call | tool_result | proposal | followups | error | done
    text: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    ok: bool | None = None
    summary: str | None = None
    message: str | None = None
    stop_reason: str | None = None
    usage: dict[str, int] | None = None
    # Structured payload for ``proposal`` events — the Proposal envelope from
    # liberty.ai.proposal so the chat UI can render an Apply card. None for every
    # other event type.
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {
            "type": self.type,
            "text": self.text,
            "name": self.name,
            "input": self.input,
            "ok": self.ok,
            "summary": self.summary,
            "message": self.message,
            "stop_reason": self.stop_reason,
            "usage": self.usage,
            "data": self.data,
        }.items() if v is not None}


class AiAssistant:
    """Holds the configured Anthropic client, system prompt, and tool set."""

    def __init__(
        self,
        settings: AISettings,
        *,
        client: Any | None,  # anthropic.AsyncAnthropic | None
        tools: ToolRegistry | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self.system_prompt = (settings.system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
        self.tools = tools or ToolRegistry()

    @property
    def available(self) -> bool:
        """Whether a call can actually be made (client + API key wired up)."""
        return self._client is not None

    async def aclose(self) -> None:
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if close is not None:
                await close()

    # -- request assembly -------------------------------------------------- #

    def _tool_definitions(self) -> list[dict[str, Any]]:
        defs = self.tools.definitions()
        if self.settings.web_fetch_domains:
            defs.append({
                "type": _WEB_FETCH_TYPE,
                "name": "web_fetch",
                "allowed_domains": list(self.settings.web_fetch_domains),
                "max_uses": max(1, self.settings.web_fetch_max_uses),
            })
        return defs

    def _request_kwargs(self, *, model: str | None, context: str | None = None) -> dict[str, Any]:
        # The stable prompt is the cached block; the page context (changes with every screen the
        # user opens) comes AFTER it, without cache_control, so it never breaks the cached prefix.
        system: list[dict[str, Any]] = [{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}]
        if context and context.strip():
            system.append({"type": "text", "text": _context_block(context)})
        kwargs: dict[str, Any] = {
            "model": model or self.settings.model,
            "max_tokens": self.settings.max_tokens,
            # Caching the system block also caches the (stable) tool list, which
            # renders just before it. Message-history caching: not done yet.
            "system": system,
        }
        defs = self._tool_definitions()
        if defs:
            kwargs["tools"] = defs
        if self.settings.thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        if self.settings.effort and "haiku" not in (model or self.settings.model):
            kwargs["output_config"] = {"effort": self.settings.effort}
        return kwargs

    def tool_catalog(self) -> list[dict[str, Any]]:
        """Metadata for the configured tools (for the /ai/tools debug endpoint)."""
        cat: list[dict[str, Any]] = [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
            for t in self.tools.definitions()
        ]
        if self.settings.web_fetch_domains:
            cat.append({"name": "web_fetch", "type": _WEB_FETCH_TYPE, "allowed_domains": list(self.settings.web_fetch_domains)})
        return cat

    # -- the loop ---------------------------------------------------------- #

    async def chat(
        self, messages: list[dict[str, Any]], *, model: str | None = None, context: str | None = None
    ) -> AsyncIterator[ChatEvent]:
        """Run the agentic loop for *messages*; yield :class:`ChatEvent`s.

        *messages* is a standard Messages-API list (``[{"role": "user", "content": ...}]``).
        It is copied — the caller's list isn't mutated. *context* is the page context the
        browser rendered (see :data:`CONTEXT_MAX_CHARS`); it rides as a second, uncached
        system block for this call only.
        """
        if self._client is None:
            yield ChatEvent("error", message="Anthropic API key is not configured (set [ai] api_key / ANTHROPIC_API_KEY).")
            return

        convo: list[dict[str, Any]] = list(messages)
        base_kwargs = self._request_kwargs(model=model, context=context)
        usage_total = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

        for _ in range(max(1, self.settings.max_iterations)):
            splitter = _FollowupSplitter()
            try:
                async with self._client.messages.stream(messages=convo, **base_kwargs) as stream:
                    async for event in stream:
                        etype = getattr(event, "type", None)
                        if etype != "content_block_delta":
                            continue
                        delta = getattr(event, "delta", None)
                        dtype = getattr(delta, "type", None)
                        if dtype == "text_delta" and getattr(delta, "text", None):
                            shown = splitter.feed(delta.text)
                            if shown:
                                yield ChatEvent("token", text=shown)
                        elif dtype == "thinking_delta" and getattr(delta, "thinking", None):
                            yield ChatEvent("thinking", text=delta.thinking)
                    final = await stream.get_final_message()
            except Exception as exc:  # network / API error — surface and stop
                rest, _ = splitter.finish()
                if rest:
                    yield ChatEvent("token", text=rest)
                yield ChatEvent("error", message=f"Anthropic request failed: {type(exc).__name__}: {exc}")
                return
            rest, followups = splitter.finish()
            if rest:
                yield ChatEvent("token", text=rest)
            if followups:
                yield ChatEvent("followups", data={"questions": followups})

            self._accumulate(usage_total, getattr(final, "usage", None))
            convo.append({"role": "assistant", "content": final.content})
            stop_reason = getattr(final, "stop_reason", None)

            if stop_reason == "pause_turn":
                # Server-side tool hit its iteration cap — re-send to resume.
                continue
            if stop_reason != "tool_use":
                yield ChatEvent("done", stop_reason=stop_reason, usage=dict(usage_total))
                return

            tool_uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:  # tool_use stop with no client tool block — treat as done
                yield ChatEvent("done", stop_reason="end_turn", usage=dict(usage_total))
                return

            results: list[dict[str, Any]] = []
            for block in tool_uses:
                arguments = dict(getattr(block, "input", {}) or {})
                tdef = self.tools.get(block.name)
                summary = tdef.summarize(arguments) if tdef else block.name
                yield ChatEvent("tool_call", name=block.name, input=arguments, summary=summary)
                content, is_error = await self.tools.execute(block.name, arguments)
                if len(content) > TOOL_RESULT_MAX_CHARS:
                    content = (
                        content[:TOOL_RESULT_MAX_CHARS]
                        + f"\n…[cut: the result was {len(content)} characters, above the {TOOL_RESULT_MAX_CHARS} limit. "
                        "Narrow the call — filters, a limit, fewer columns — and call again.]"
                    )
                # Sniff for a Proposal in the tool result — write-capable tools (the
                # scaffold_* set) return ``{"proposal": {...}, "message": ...}``. When
                # we find one, emit an extra ``proposal`` event so the chat UI can
                # render an Apply card. The model still sees the whole dict in its
                # tool_result so it can reference what it proposed.
                if not is_error:
                    try:
                        parsed = json.loads(content)
                    except (ValueError, TypeError):
                        parsed = None
                    if isinstance(parsed, dict) and isinstance(parsed.get("proposal"), dict):
                        yield ChatEvent("proposal", name=block.name, data=parsed["proposal"])
                yield ChatEvent("tool_result", name=block.name, ok=not is_error, summary=_truncate(content))
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    **({"is_error": True} if is_error else {}),
                })
            convo.append({"role": "user", "content": results})

        yield ChatEvent("error", message=f"Reached the {self.settings.max_iterations}-iteration tool cap without a final answer.", usage=dict(usage_total))

    @staticmethod
    def _accumulate(total: dict[str, int], usage: Any) -> None:
        if usage is None:
            return
        for key in total:
            total[key] += int(getattr(usage, key, 0) or 0)


def _truncate(text: str, limit: int = 280) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _context_block(context: str) -> str:
    """The uncached system block carrying the page context: the browser's text (capped) with a
    short frame telling the model what it is and how far to trust it."""
    body = context.strip()
    if len(body) > CONTEXT_MAX_CHARS:
        body = body[: CONTEXT_MAX_CHARS - 1] + "…"
    return (
        "<page_context>\n" + body + "\n</page_context>\n"
        "The block above describes what the user is looking at in the Liberty UI right now (sent by "
        "their browser with this message; it may already be stale). Use it to resolve references such "
        "as \"this screen\", \"these rows\" or \"the selected row\". The connector and query names "
        "in it are real: you can query them with sql_query. Do not repeat the block back to the user."
    )


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def build_assistant(
    settings: AISettings,
    connectors: ConnectorRegistry,
    *,
    master_key: str = "",
    state: Callable[[], Any] | None = None,
) -> AiAssistant | None:
    """Build an :class:`AiAssistant` from settings + the connector registry.

    Returns ``None`` when AI is disabled. When enabled but no API key is set, the
    assistant is still returned (so the chat endpoint exists) but its calls fail
    fast with a clear error.

    *master_key*: when settings.api_key is ENC:-encrypted (Settings UI save encrypts
    with the install's master key), decrypt before instantiating AsyncAnthropic.
    *state*: ``() -> app.state`` — adds the instance tools (describe_instance, the
    playbooks), which read the live state at call time.
    """
    if not settings.enabled:
        return None

    client = None
    api_key = settings.api_key
    if api_key.startswith("ENC:"):
        import logging
        _log = logging.getLogger("liberty.ai.assistant")
        if not master_key:
            _log.warning("ai.assistant: api_key is ENC:-encrypted but no master_key configured — disabling client")
            api_key = ""
        else:
            from liberty.crypto import decrypt
            try:
                api_key = decrypt(api_key, master_key)
            except Exception as exc:  # noqa: BLE001
                _log.warning("ai.assistant: api_key decrypt failed (%s) — disabling client", exc)
                api_key = ""

    if api_key:
        from anthropic import AsyncAnthropic  # imported lazily so tests can run without it
        client = AsyncAnthropic(api_key=api_key, timeout=settings.request_timeout)

    registry = ToolRegistry()
    if settings.connector_tools:
        registry.add(*build_connector_tools(
            connectors,
            allowed=settings.allowed_connectors or None,
            include_api=settings.api_tool,
        ))
    if settings.scaffold_tools:
        registry.add(*build_scaffold_tools(
            connectors,
            allowed=settings.allowed_connectors or None,
        ))
    if state is not None:
        registry.add(*build_instance_tools(state, allowed=settings.allowed_connectors or None))
        if settings.ops_tools:
            registry.add(*build_ops_tools(state))
    return AiAssistant(settings, client=client, tools=registry)
