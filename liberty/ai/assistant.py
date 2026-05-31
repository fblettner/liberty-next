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

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import json

from liberty.ai.connector_tools import build_connector_tools
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
    "4. Be concise and reply in the user's language.\n\n"
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

# Server-side web_fetch (Anthropic-hosted). GA tool version — no beta header.
# Restricted to the operator-configured domains; never offered when the list is empty.
_WEB_FETCH_TYPE = "web_fetch_20260209"


@dataclass(slots=True)
class ChatEvent:
    """One event emitted by :meth:`AiAssistant.chat`."""

    type: str  # token | thinking | tool_call | tool_result | proposal | error | done
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

    def _request_kwargs(self, *, model: str | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": model or self.settings.model,
            "max_tokens": self.settings.max_tokens,
            # Caching the system block also caches the (stable) tool list, which
            # renders just before it. Message-history caching: not done yet.
            "system": [{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}],
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
        self, messages: list[dict[str, Any]], *, model: str | None = None
    ) -> AsyncIterator[ChatEvent]:
        """Run the agentic loop for *messages*; yield :class:`ChatEvent`s.

        *messages* is a standard Messages-API list (``[{"role": "user", "content": ...}]``).
        It is copied — the caller's list isn't mutated.
        """
        if self._client is None:
            yield ChatEvent("error", message="Anthropic API key is not configured (set [ai] api_key / ANTHROPIC_API_KEY).")
            return

        convo: list[dict[str, Any]] = list(messages)
        base_kwargs = self._request_kwargs(model=model)
        usage_total = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

        for _ in range(max(1, self.settings.max_iterations)):
            try:
                async with self._client.messages.stream(messages=convo, **base_kwargs) as stream:
                    async for event in stream:
                        etype = getattr(event, "type", None)
                        if etype != "content_block_delta":
                            continue
                        delta = getattr(event, "delta", None)
                        dtype = getattr(delta, "type", None)
                        if dtype == "text_delta" and getattr(delta, "text", None):
                            yield ChatEvent("token", text=delta.text)
                        elif dtype == "thinking_delta" and getattr(delta, "thinking", None):
                            yield ChatEvent("thinking", text=delta.thinking)
                    final = await stream.get_final_message()
            except Exception as exc:  # network / API error — surface and stop
                yield ChatEvent("error", message=f"Anthropic request failed: {type(exc).__name__}: {exc}")
                return

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


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def build_assistant(
    settings: AISettings,
    connectors: ConnectorRegistry,
    *,
    master_key: str = "",
) -> AiAssistant | None:
    """Build an :class:`AiAssistant` from settings + the connector registry.

    Returns ``None`` when AI is disabled. When enabled but no API key is set, the
    assistant is still returned (so the chat endpoint exists) but its calls fail
    fast with a clear error.

    *master_key*: when settings.api_key is ENC:-encrypted (Settings UI save encrypts
    with the install's master key), decrypt before instantiating AsyncAnthropic.
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
    return AiAssistant(settings, client=client, tools=registry)
