from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from liberty.ai import evals
from liberty.ai.evals import EvalCase, Outcome, check, has_figure, load_cases, read_events


def _sse(*events: dict) -> list[str]:
    return [f"data: {json.dumps(e)}" for e in events] + ["", "data: [DONE]"]


def _ok(text: str = "", tools: list[tuple[str, bool]] | None = None) -> Outcome:
    return Outcome(text=text, tool_calls=[{"name": n, "ok": ok} for n, ok in tools or []], stop_reason="end_turn")


# -- figures ------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "France sold 72696 units", "France: 72,696", "72 696 unités", "72 696", "72.696 Stück",
    "72'696", "**72,696.25** in total",
])
def test_has_figure_whatever_the_separators(text: str) -> None:
    assert has_figure(text, 72696)


@pytest.mark.parametrize("text", ["172,696", "72,6961", "726.96", "about 72.7k", "7 2696 1"])
def test_has_figure_refuses_another_number(text: str) -> None:
    assert not has_figure(text, 72696)


def test_has_figure_millions() -> None:
    assert has_figure("1,234,567 lines", 1234567)
    assert has_figure("1 234 567", 1234567)


# -- checks -------------------------------------------------------------------


def test_check_passes_a_bare_case_on_a_clean_answer() -> None:
    assert check(EvalCase(id="a", question="q"), _ok("hello")) == []


def test_check_error_and_cut_answer() -> None:
    case = EvalCase(id="a", question="q")
    assert check(case, Outcome(error="boom")) == ["error: boom"]
    assert check(case, Outcome(text="…", stop_reason="max_tokens")) == ["stopped on 'max_tokens', not end_turn"]


def test_check_tools() -> None:
    case = EvalCase(id="a", question="q", tools=["explorer_fields"], tools_any=["explorer_query", "sql_query"],
                    forbid_tools=["api_call"], max_tool_calls=2)
    good = _ok("x", [("explorer_fields", True), ("explorer_query", True)])
    assert check(case, good) == []
    bad = _ok("x", [("list_connectors", True), ("api_call", True), ("api_call", True)])
    assert check(case, bad) == [
        "tool not called: explorer_fields",
        "none of these tools called: explorer_query, sql_query",
        "forbidden tool called: api_call",
        "3 tool calls, above 2",
    ]


def test_check_a_failed_tool_call_fails_unless_allowed() -> None:
    outcome = _ok("x", [("sql_query", False), ("sql_query", True)])
    assert check(EvalCase(id="a", question="q"), outcome) == ["tool call failed: sql_query"]
    assert check(EvalCase(id="a", question="q", allow_tool_errors=True), outcome) == []


def test_check_texts_and_figures() -> None:
    assert check(EvalCase(id="a", question="q", forbid_figures=[281796]), _ok("In all 281,796 units.")) == ["answer carries the figure 281796"]
    assert check(EvalCase(id="a", question="q", forbid_figures=[281796]), _ok("Your company: 21,132 units.")) == []
    case = EvalCase(id="a", question="q", contains=["france"], contains_any=["pôle", "business unit"],
                    not_contains=["I cannot"], figures=[72696])
    assert check(case, _ok("The business unit France sold 72,696 units.")) == []
    assert check(case, _ok("I cannot tell.")) == [
        "answer lacks: 'france'",
        "answer has none of: 'pôle', 'business unit'",
        "answer carries: 'I cannot'",
        "answer lacks the figure 72696",
    ]


# -- cases --------------------------------------------------------------------


def test_load_cases_from_files_and_folders(tmp_path: Path) -> None:
    (tmp_path / "b.toml").write_text('[[case]]\nid = "b1"\nquestion = "Q?"\napp = "demo"\n', encoding="utf-8")
    (tmp_path / "a.toml").write_text('[[case]]\nid = "a1"\nquestion = "Q?"\ncontext = "app: x\\nscreen: s"\napp = "demo"\n', encoding="utf-8")
    cases = load_cases([tmp_path])
    assert [c.id for c in cases] == ["a1", "b1"]
    assert cases[0].page_context() == "app: x\nscreen: s"      # the context wins over app
    assert cases[1].page_context() == "app: demo"
    assert EvalCase(id="c", question="q").page_context() is None


def test_load_cases_refuses_a_duplicate_id_and_an_unknown_key(tmp_path: Path) -> None:
    one = tmp_path / "one.toml"
    one.write_text('[[case]]\nid = "x"\nquestion = "Q?"\n', encoding="utf-8")
    two = tmp_path / "two.toml"
    two.write_text('[[case]]\nid = "x"\nquestion = "Q?"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="case id 'x'"):
        load_cases([one, two])
    two.write_text('[[case]]\nid = "y"\nquestion = "Q?"\ncontain = ["typo"]\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases([two])


# -- the event stream ---------------------------------------------------------


def test_read_events_folds_the_stream() -> None:
    out = read_events(_sse(
        {"type": "thinking", "text": "hm"},
        {"type": "tool_call", "name": "sql_query", "input": {}},
        {"type": "tool_result", "name": "sql_query", "ok": False, "summary": '{"error": "no such query"}'},
        {"type": "tool_call", "name": "sql_query", "input": {}},
        {"type": "tool_result", "name": "sql_query", "ok": True},
        {"type": "token", "text": "Hello "},
        {"type": "token", "text": "world"},
        {"type": "followups", "data": {"questions": ["next?"]}},
        {"type": "done", "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 3}},
    ))
    assert out.text == "Hello world"
    assert out.tool_calls == [{"name": "sql_query", "ok": False, "error": '{"error": "no such query"}'}, {"name": "sql_query", "ok": True}]
    assert check(EvalCase(id="a", question="q"), out) == ['tool call failed: sql_query — {"error": "no such query"}']
    assert out.stop_reason == "end_turn" and out.error is None
    assert out.usage == {"input_tokens": 10, "output_tokens": 3}


def test_read_events_error() -> None:
    out = read_events(_sse({"type": "token", "text": "par"}, {"type": "error", "message": "cap reached", "usage": {"input_tokens": 5}}))
    assert out.error == "cap reached" and out.stop_reason is None and out.usage == {"input_tokens": 5}


# -- the run, against a fake server -------------------------------------------


def _fake_server(seen: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/base/auth/login":
            body = json.loads(request.content)
            if body["password"] != "pw":
                return httpx.Response(401, json={"detail": "Invalid credentials"})
            return httpx.Response(200, json={"access_token": "tok", "refresh_token": "r", "expires_in": 3600})
        assert request.url.path == "/base/ai/chat"
        assert request.headers["authorization"] == "Bearer tok"
        body = json.loads(request.content)
        seen.append(body)
        if "broken" in body["messages"][0]["content"]:
            return httpx.Response(403, json={"detail": "missing permission ai:chat"})
        events = _sse(
            {"type": "tool_call", "name": "explorer_query", "input": {}},
            {"type": "tool_result", "name": "explorer_query", "ok": True},
            {"type": "token", "text": "France: 72,696"},
            {"type": "done", "stop_reason": "end_turn", "usage": {"input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 20}},
        )
        return httpx.Response(200, text="\n".join(events) + "\n", headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


@pytest.fixture
def fake_server(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    seen: list[dict] = []
    real = httpx.Client

    def client(**kwargs):  # the runner builds its own client: give it the fake transport
        return real(transport=_fake_server(seen), **kwargs)

    monkeypatch.setattr(evals.httpx, "Client", client)
    return seen


def test_run_plays_each_case_and_reports(fake_server: list[dict]) -> None:
    cases = [
        EvalCase(id="good", question="Top business unit?", app="nomademo", tools_any=["explorer_query"], contains=["France"], figures=[72696]),
        EvalCase(id="wrong", question="Top business unit?", figures=[1]),
        EvalCase(id="refused", question="broken"),
    ]
    results = evals.run(cases, url="http://liberty.test/base", username="u", password="pw", model="claude-x")
    assert [r["fails"] for r in results] == [[], ["answer lacks the figure 1"], ['error: HTTP 403: {"detail":"missing permission ai:chat"}']]
    assert fake_server[0] == {"messages": [{"role": "user", "content": "Top business unit?"}], "model": "claude-x", "context": "app: nomademo"}
    assert "context" not in fake_server[1]
    total = evals.summarize(results)
    assert total["cases"] == 3 and total["passed"] == 1 and total["tool_calls"] == 2
    assert total["tokens_in"] == 2000 and total["tokens_cached"] == 1800 and total["tokens_out"] == 40
    assert "1000 in (90% cached)" in evals.result_line(results[0])
    line = evals.result_line(results[1])
    assert line.startswith("FAIL  wrong") and "explorer_query" in line and "- answer lacks the figure 1" in line


def test_main_exit_codes(fake_server: list[dict], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cases = tmp_path / "cases.toml"
    cases.write_text(
        '[[case]]\nid = "good"\nquestion = "Top?"\nfigures = [72696]\n\n[[case]]\nid = "wrong"\nquestion = "Top?"\nfigures = [1]\n', encoding="utf-8")
    out = tmp_path / "baseline.json"
    common = ["--url", "http://liberty.test/base", "--user", "u"]
    assert evals.main([str(cases), *common, "--password", "pw", "--only", "good", "--out", str(out)]) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["summary"]["passed"] == 1 and saved["results"][0]["text"] == "France: 72,696"
    assert evals.main([str(cases), *common, "--password", "pw"]) == 1
    assert "1 / 2 passed" in capsys.readouterr().out
    assert evals.main([str(cases), *common, "--password", "bad"]) == 2
    assert "sign-in failed (401)" in capsys.readouterr().err
