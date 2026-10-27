from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from liberty.ai.assistant import AiAssistant
from liberty.ai.tools import ToolRegistry, tool
from liberty.config import AISettings


# --------------------------------------------------------------------------- #
# minimal fakes for the Anthropic streaming API
# --------------------------------------------------------------------------- #


def _text_delta(t: str):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=t))


def _thinking_delta(t: str):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking=t))


def _text_block(t: str):
    return SimpleNamespace(type="text", text=t)


def _tool_block(block_id: str, name: str, payload: dict[str, Any]):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=payload)


def _usage(i: int = 10, o: int = 5):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _msg(content: list, stop_reason: str):
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=_usage())


class _FakeStream:
    def __init__(self, events: list, final):
        self._events, self._final = events, final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e

        return gen()

    async def get_final_message(self):
        if self._final is None:
            raise RuntimeError("stream broke")
        return self._final


class _FakeMessages:
    def __init__(self, turns: list, raise_on: int | None = None):
        self._turns = list(turns)
        self._i = 0
        self._raise_on = raise_on
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        if self._raise_on is not None and self._i == self._raise_on:
            self._i += 1
            raise RuntimeError("connection reset")
        self.calls.append(kwargs)
        turn = self._turns[self._i] if self._i < len(self._turns) else self._turns[-1]
        self._i += 1
        return _FakeStream(*turn)


class FakeAnthropic:
    def __init__(self, turns: list, raise_on: int | None = None):
        self.messages = _FakeMessages(turns, raise_on)

    async def close(self):
        pass


def _assistant(turns, *, tools: ToolRegistry | None = None, raise_on=None, **settings_kw) -> AiAssistant:
    return AiAssistant(
        AISettings(api_key="x", model="claude-opus-4-7", **settings_kw),
        client=FakeAnthropic(turns, raise_on=raise_on),
        tools=tools or ToolRegistry(),
    )


async def _collect(assistant: AiAssistant, messages=None):
    return [e async for e in assistant.chat(messages or [{"role": "user", "content": "hi"}])]


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_plain_text_response() -> None:
    a = _assistant([([_text_delta("Hello "), _text_delta("world")], _msg([_text_block("Hello world")], "end_turn"))])
    events = await _collect(a)
    assert [e.type for e in events] == ["token", "token", "done"]
    assert [e.text for e in events[:2]] == ["Hello ", "world"]
    assert events[-1].stop_reason == "end_turn"
    assert events[-1].usage == {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


@pytest.mark.asyncio
async def test_thinking_deltas_surface() -> None:
    a = _assistant([([_thinking_delta("hmm "), _text_delta("answer")], _msg([_text_block("answer")], "end_turn"))])
    events = await _collect(a)
    assert [e.type for e in events] == ["thinking", "token", "done"]


@pytest.mark.asyncio
async def test_tool_use_round_trip() -> None:
    @tool
    def echo(text: str) -> dict:
        return {"echoed": text}

    a = _assistant(
        [
            ([], _msg([_tool_block("tu1", "echo", {"text": "hi"})], "tool_use")),
            ([_text_delta("ok")], _msg([_text_block("ok")], "end_turn")),
        ],
        tools=ToolRegistry().add(echo),
    )
    events = await _collect(a)
    assert [e.type for e in events] == ["tool_call", "tool_result", "token", "done"]
    assert events[0].name == "echo" and events[0].input == {"text": "hi"}
    assert events[1].ok is True and "echoed" in events[1].summary
    # the loop ran two turns
    assert a._client.messages._i == 2


@pytest.mark.asyncio
async def test_tool_error_is_reported_then_loop_continues() -> None:
    @tool
    def boom() -> str:
        raise RuntimeError("nope")

    a = _assistant(
        [
            ([], _msg([_tool_block("tu1", "boom", {})], "tool_use")),
            ([_text_delta("recovered")], _msg([_text_block("recovered")], "end_turn")),
        ],
        tools=ToolRegistry().add(boom),
    )
    events = await _collect(a)
    assert [e.type for e in events] == ["tool_call", "tool_result", "token", "done"]
    assert events[1].ok is False and "RuntimeError" in events[1].summary


@pytest.mark.asyncio
async def test_unknown_tool_call() -> None:
    a = _assistant(
        [
            ([], _msg([_tool_block("tu1", "ghost", {})], "tool_use")),
            ([_text_delta("done")], _msg([_text_block("done")], "end_turn")),
        ]
    )
    events = await _collect(a)
    assert [e.type for e in events] == ["tool_call", "tool_result", "token", "done"]
    assert events[1].ok is False and "unknown tool" in events[1].summary


@pytest.mark.asyncio
async def test_max_iterations_cap() -> None:
    @tool
    def loop_tool() -> str:
        return "again"

    a = _assistant(
        [([], _msg([_tool_block("t", "loop_tool", {})], "tool_use"))],  # always tool_use
        tools=ToolRegistry().add(loop_tool),
        max_iterations=2,
    )
    events = await _collect(a)
    assert events[-1].type == "error" and "iteration" in events[-1].message
    assert a._client.messages._i == 2  # exactly max_iterations API calls


@pytest.mark.asyncio
async def test_pause_turn_resumes() -> None:
    a = _assistant(
        [
            ([], _msg([], "pause_turn")),  # server tool hit its cap → re-send
            ([_text_delta("finally")], _msg([_text_block("finally")], "end_turn")),
        ]
    )
    events = await _collect(a)
    assert [e.type for e in events] == ["token", "done"]
    assert a._client.messages._i == 2


@pytest.mark.asyncio
async def test_no_client_yields_error() -> None:
    a = AiAssistant(AISettings(api_key=""), client=None)
    events = await _collect(a)
    assert len(events) == 1 and events[0].type == "error" and "not configured" in events[0].message


@pytest.mark.asyncio
async def test_api_error_during_stream() -> None:
    a = _assistant([([], _msg([_text_block("x")], "end_turn"))], raise_on=0)
    events = await _collect(a)
    assert events[-1].type == "error" and "Anthropic request failed" in events[-1].message


@pytest.mark.asyncio
async def test_api_error_getting_final_message() -> None:
    a = _assistant([([_text_delta("partial")], None)])  # final=None → get_final_message raises
    events = await _collect(a)
    assert [e.type for e in events] == ["token", "error"]
    assert "Anthropic request failed" in events[-1].message


@pytest.mark.asyncio
async def test_caller_messages_not_mutated() -> None:
    a = _assistant([([], _msg([_text_block("x")], "end_turn"))])
    original = [{"role": "user", "content": "hi"}]
    await _collect(a, original)
    assert original == [{"role": "user", "content": "hi"}]


def test_request_kwargs_shape() -> None:
    a = _assistant([], thinking=True, effort="high", web_fetch_domains=["docs.test"], web_fetch_max_uses=3)
    kw = a._request_kwargs(model=None)
    assert kw["model"] == "claude-opus-4-7"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "high"}
    wf = [t for t in kw["tools"] if t.get("name") == "web_fetch"][0]
    assert wf["type"].startswith("web_fetch_") and wf["allowed_domains"] == ["docs.test"] and wf["max_uses"] == 3


def test_request_kwargs_minimal() -> None:
    a = _assistant([])
    kw = a._request_kwargs(model="claude-haiku-4-5")
    assert kw["model"] == "claude-haiku-4-5"
    assert "thinking" not in kw and "output_config" not in kw and "tools" not in kw


def test_effort_skipped_for_haiku() -> None:
    a = _assistant([], effort="high")
    assert "output_config" not in a._request_kwargs(model="claude-haiku-4-5")
    assert a._request_kwargs(model="claude-opus-4-7")["output_config"] == {"effort": "high"}


def test_page_context_is_a_second_uncached_system_block() -> None:
    a = _assistant([])
    assert len(a._request_kwargs(model=None)["system"]) == 1
    assert len(a._request_kwargs(model=None, context="   ")["system"]) == 1      # blank → nothing added
    system = a._request_kwargs(model=None, context="app: shop\nscreen: orders")["system"]
    assert len(system) == 2 and "cache_control" in system[0] and "cache_control" not in system[1]
    assert system[1]["text"].startswith("<page_context>\napp: shop\nscreen: orders\n</page_context>")
    # capped: the browser's text never grows the block past CONTEXT_MAX_CHARS
    from liberty.ai.assistant import CONTEXT_MAX_CHARS
    big = a._request_kwargs(model=None, context="x" * (CONTEXT_MAX_CHARS * 3))["system"][1]["text"]
    body = big.split("<page_context>\n", 1)[1].split("\n</page_context>", 1)[0]
    assert len(body) == CONTEXT_MAX_CHARS and body.endswith("…")


@pytest.mark.asyncio
async def test_page_context_reaches_the_request() -> None:
    a = _assistant([([_text_delta("ok")], _msg([_text_block("ok")], "end_turn"))])
    events = [e async for e in a.chat([{"role": "user", "content": "hi"}], context="screen: f0092")]
    assert events[-1].type == "done"
    sent = a._client.messages.calls[0]["system"]
    assert len(sent) == 2 and "screen: f0092" in sent[1]["text"]


def test_followup_splitter_holds_the_marker_line_back() -> None:
    from liberty.ai.assistant import _FollowupSplitter
    sp = _FollowupSplitter()
    shown = "".join(sp.feed(d) for d in ["Here is the answer.\n", "Done.", "\n>", ">> [\"Next step?\", ", "\"Why?\"]"])
    assert shown == "Here is the answer.\nDone."
    assert sp.finish() == ("", ["Next step?", "Why?"])
    # a line that is not valid JSON comes back as text
    sp = _FollowupSplitter()
    sp.feed("Hello\n>>> not json")
    assert sp.finish() == ("\n>>> not json", [])
    # no marker: a held-back newline is released at the end
    sp = _FollowupSplitter()
    out = sp.feed("Plain reply\n")
    rest, qs = sp.finish()
    assert out + rest == "Plain reply\n" and qs == []
    # a marker at the very start of the reply
    sp = _FollowupSplitter()
    assert sp.feed('>>> ["Only?"]') == "" and sp.finish() == ("", ["Only?"])


@pytest.mark.asyncio
async def test_followups_become_an_event_and_never_a_token() -> None:
    a = _assistant([([_text_delta("The job ran.\n>>"), _text_delta('> ["When is the next run?", "Show the log"]')], _msg([_text_block("x")], "end_turn"))])
    events = await _collect(a)
    assert [e.type for e in events] == ["token", "followups", "done"]
    assert events[0].text == "The job ran." and events[1].data == {"questions": ["When is the next run?", "Show the log"]}
