"""The assistant's evaluation set — questions with what a good answer must do, played against
a LIVE server (like the Playwright scenarios of ``frontend/e2e``), so the whole path is
measured: sign-in, permissions, row scope, the tools, the model.

A case file is TOML, one ``[[case]]`` per question::

    [[case]]
    id = "top-business-unit"
    question = "Which business unit sold the most in March 2026?"
    app = "nomademo"                    # the page context: "app: nomademo"
    tools_any = ["explorer_query"]      # at least one of these was called
    contains = ["France"]               # every text is in the answer (case-insensitive)
    figures = [72696]                   # every number is in the answer, whatever its separators

Run it by hand before and after a change to the prompt, the model or a tool — never in CI
(each case is a paid model call)::

    LIBERTY_E2E_USER=… LIBERTY_E2E_PASSWORD=… python -m liberty.ai.evals ../liberty-apps/tests/ai_eval
    python -m liberty.ai.evals cases.toml --only top-business-unit --out baseline.json

The account decides what is measured: a superuser for the tools themselves, a restricted
user for what the permissions and the scope let through (cases written for one account go in
their own folder — a folder is read without its sub-folders). Exit code 0 when every case passes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import httpx
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_URL = "http://127.0.0.1:8000"


class EvalCase(BaseModel):
    """One question and what its answer is checked against. Every check is optional; a case
    with none still fails on an error, a failed tool or a cut answer."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    app: str | None = Field(default=None, description="The app the chat is opened on (sent as the page context).")
    context: str | None = Field(default=None, description="The page context as the browser renders it; replaces `app`.")
    tools: list[str] = Field(default_factory=list, description="Every one of these tools was called.")
    tools_any: list[str] = Field(default_factory=list, description="At least one of these tools was called.")
    forbid_tools: list[str] = Field(default_factory=list, description="None of these tools was called.")
    contains: list[str] = Field(default_factory=list, description="Every text is in the answer (case-insensitive).")
    contains_any: list[str] = Field(default_factory=list, description="At least one text is in the answer.")
    not_contains: list[str] = Field(default_factory=list, description="None of these texts is in the answer.")
    figures: list[int] = Field(default_factory=list, description="Every whole number is in the answer, whatever its thousands separators.")
    forbid_figures: list[int] = Field(default_factory=list, description="None of these whole numbers is in the answer (a figure the account must not see).")
    max_tool_calls: int | None = Field(default=None, description="The answer took at most this many tool calls.")
    allow_tool_errors: bool = Field(default=False, description="A tool call that failed does not fail the case.")

    def page_context(self) -> str | None:
        if self.context and self.context.strip():
            return self.context
        return f"app: {self.app}" if self.app else None


class EvalFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: list[EvalCase] = Field(default_factory=list)


@dataclass(slots=True)
class Outcome:
    """What one question produced, read from the chat's event stream."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)   # {"name", "ok"[, "error"]} in call order
    error: str | None = None
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def tool_names(self) -> list[str]:
        return [c["name"] for c in self.tool_calls]


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #


def load_cases(paths: Iterable[Path | str]) -> list[EvalCase]:
    """The cases of every file given (a folder = its ``*.toml``, by name); ids must be unique."""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        files.extend(sorted(p.glob("*.toml")) if p.is_dir() else [p])
    cases: list[EvalCase] = []
    seen: dict[str, Path] = {}
    for f in files:
        for case in EvalFile.model_validate(tomllib.loads(f.read_text(encoding="utf-8"))).case:
            if case.id in seen:
                raise ValueError(f"case id {case.id!r} is in both {seen[case.id]} and {f}")
            seen[case.id] = f
            cases.append(case)
    return cases


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

# A thousands separator: one space (plain, no-break, narrow), comma, dot or apostrophe between a
# digit and a group of exactly three digits — "72,696", "72 696", "72.696", "1'234'567".
_THOUSANDS = re.compile(r"(?<=\d)[   ,.'’](?=\d{3}(?!\d))")


def has_figure(text: str, figure: int) -> bool:
    """Whether *text* carries the whole number *figure*, written with or without separators
    (decimals after it are fine: 72696 matches "72,696.25")."""
    plain = _THOUSANDS.sub("", text)
    return re.search(rf"(?<!\d){abs(figure)}(?!\d)", plain) is not None


def check(case: EvalCase, outcome: Outcome) -> list[str]:
    """Why the outcome fails the case — an empty list is a pass."""
    fails: list[str] = []
    if outcome.error:
        fails.append(f"error: {outcome.error}")
    elif outcome.stop_reason != "end_turn":
        fails.append(f"stopped on {outcome.stop_reason!r}, not end_turn")
    called = set(outcome.tool_names)
    for name in case.tools:
        if name not in called:
            fails.append(f"tool not called: {name}")
    if case.tools_any and not called.intersection(case.tools_any):
        fails.append(f"none of these tools called: {', '.join(case.tools_any)}")
    for name in case.forbid_tools:
        if name in called:
            fails.append(f"forbidden tool called: {name}")
    if not case.allow_tool_errors:
        for c in outcome.tool_calls:
            if c.get("ok") is False:
                fails.append(f"tool call failed: {c['name']}" + (f" — {c['error']}" if c.get("error") else ""))
    if case.max_tool_calls is not None and len(outcome.tool_calls) > case.max_tool_calls:
        fails.append(f"{len(outcome.tool_calls)} tool calls, above {case.max_tool_calls}")
    low = outcome.text.casefold()
    for text in case.contains:
        if text.casefold() not in low:
            fails.append(f"answer lacks: {text!r}")
    if case.contains_any and not any(t.casefold() in low for t in case.contains_any):
        fails.append(f"answer has none of: {', '.join(repr(t) for t in case.contains_any)}")
    for text in case.not_contains:
        if text.casefold() in low:
            fails.append(f"answer carries: {text!r}")
    for figure in case.figures:
        if not has_figure(outcome.text, figure):
            fails.append(f"answer lacks the figure {figure}")
    for figure in case.forbid_figures:
        if has_figure(outcome.text, figure):
            fails.append(f"answer carries the figure {figure}")
    return fails


# --------------------------------------------------------------------------- #
# The live run
# --------------------------------------------------------------------------- #


def read_events(lines: Iterable[str]) -> Outcome:
    """Fold the ``data:`` lines of ``POST /ai/chat`` into an :class:`Outcome`. A ``tool_call``
    opens a call, the ``tool_result`` that follows gives its ``ok`` (the loop runs the tools of
    a turn one after the other, so results arrive in call order)."""
    out = Outcome()
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            ev = json.loads(payload)
        except ValueError:
            continue
        kind = ev.get("type")
        if kind == "token":
            out.text += ev.get("text") or ""
        elif kind == "tool_call":
            out.tool_calls.append({"name": ev.get("name"), "ok": None})
        elif kind == "tool_result":
            for call in out.tool_calls:
                if call["name"] == ev.get("name") and call["ok"] is None:
                    call["ok"] = bool(ev.get("ok"))
                    if not call["ok"]:
                        call["error"] = ev.get("summary") or ""    # the start of the tool's error, to see why
                    break
        elif kind == "error":
            out.error = ev.get("message") or "error"
            out.usage = ev.get("usage") or out.usage
        elif kind == "done":
            out.stop_reason = ev.get("stop_reason")
            out.usage = ev.get("usage") or {}
    return out


def sign_in(client: httpx.Client, username: str, password: str) -> str:
    res = client.post("auth/login", json={"username": username, "password": password})
    if res.status_code != 200:
        raise RuntimeError(f"sign-in failed ({res.status_code}): {res.text[:200]}")
    return str(res.json()["access_token"])


def ask(client: httpx.Client, token: str, case: EvalCase, *, model: str | None = None) -> Outcome:
    """Play one case: a new conversation with the question, the page context of the case."""
    body: dict[str, Any] = {"messages": [{"role": "user", "content": case.question}]}
    if model:
        body["model"] = model
    context = case.page_context()
    if context:
        body["context"] = context
    started = time.monotonic()
    try:
        with client.stream("POST", "ai/chat", json=body, headers={"Authorization": f"Bearer {token}"}) as res:
            if res.status_code != 200:
                res.read()
                out = Outcome(error=f"HTTP {res.status_code}: {res.text[:200]}")
            else:
                out = read_events(_lines(res))
    except httpx.HTTPError as exc:
        out = Outcome(error=f"{type(exc).__name__}: {exc}")
    out.seconds = round(time.monotonic() - started, 1)
    return out


def _lines(res: httpx.Response) -> Iterator[str]:
    yield from res.iter_lines()


def run(cases: list[EvalCase], *, url: str, username: str, password: str, model: str | None = None,
        timeout: float = 300.0, progress: Any = None) -> list[dict[str, Any]]:
    """Play *cases* in order; one result per case: the outcome and why it fails (if it does)."""
    results: list[dict[str, Any]] = []
    with httpx.Client(base_url=url.rstrip("/") + "/", timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        token = sign_in(client, username, password)
        for case in cases:
            outcome = ask(client, token, case, model=model)
            result = {"id": case.id, "question": case.question, "fails": check(case, outcome), **asdict(outcome)}
            results.append(result)
            if progress is not None:
                progress(result)
    return results


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def tokens_in(usage: dict[str, int]) -> int:
    """Everything the model read: the fresh input plus what was read from / written to the cache."""
    return sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def _cached_share(usage: dict[str, int]) -> str:
    total = tokens_in(usage)
    return f"{round(100 * int(usage.get('cache_read_input_tokens') or 0) / total)}%" if total else "0%"


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cases": len(results),
        "passed": sum(1 for r in results if not r["fails"]),
        "tool_calls": sum(len(r["tool_calls"]) for r in results),
        "tokens_in": sum(tokens_in(r["usage"]) for r in results),
        # of which read from the prompt cache (billed at a fraction of the input price)
        "tokens_cached": sum(int(r["usage"].get("cache_read_input_tokens") or 0) for r in results),
        "tokens_out": sum(int(r["usage"].get("output_tokens") or 0) for r in results),
        "seconds": round(sum(r["seconds"] for r in results), 1),
    }


def result_line(result: dict[str, Any]) -> str:
    names = [c["name"] for c in result["tool_calls"]]
    line = (
        f"{'PASS' if not result['fails'] else 'FAIL'}  {result['id']:<32} "
        f"{len(names):>2} calls  {tokens_in(result['usage']):>7} in ({_cached_share(result['usage']):>3} cached)  {int(result['usage'].get('output_tokens') or 0):>5} out  "
        f"{result['seconds']:>6.1f} s  {' > '.join(names)}"
    )
    return "\n".join([line, *(f"      - {f}" for f in result["fails"])])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m liberty.ai.evals", description="Play the assistant's evaluation cases against a live server.")
    parser.add_argument("cases", nargs="+", help="case files (TOML) or folders of them")
    parser.add_argument("--url", default=os.environ.get("LIBERTY_E2E_URL", DEFAULT_URL), help="the server (LIBERTY_E2E_URL)")
    parser.add_argument("--user", default=os.environ.get("LIBERTY_E2E_USER"), help="the account (LIBERTY_E2E_USER)")
    parser.add_argument("--password", default=os.environ.get("LIBERTY_E2E_PASSWORD"), help="its password (LIBERTY_E2E_PASSWORD)")
    parser.add_argument("--model", help="a model of [ai] allowed_models instead of the default one")
    parser.add_argument("--only", action="append", default=[], metavar="ID", help="play only this case (repeatable)")
    parser.add_argument("--out", type=Path, help="write the full results (answers, tools, usage) as JSON — a baseline to compare with")
    args = parser.parse_args(argv)
    if not args.user or not args.password:
        parser.error("an account is needed: --user / --password or LIBERTY_E2E_USER / LIBERTY_E2E_PASSWORD")

    cases = load_cases(args.cases)
    if args.only:
        unknown = set(args.only) - {c.id for c in cases}
        if unknown:
            parser.error(f"no such case: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c.id in args.only]
    if not cases:
        parser.error("no case to play")

    try:
        results = run(cases, url=args.url, username=args.user, password=args.password, model=args.model,
                      progress=lambda r: print(result_line(r), flush=True))
    except (RuntimeError, httpx.HTTPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    total = summarize(results)
    print(
        f"\n{total['passed']} / {total['cases']} passed · {total['tool_calls']} tool calls · "
        f"{total['tokens_in']} tokens in ({total['tokens_cached']} read from the cache) · {total['tokens_out']} out · {total['seconds']} s"
    )
    if args.out:
        args.out.write_text(json.dumps({"url": args.url, "user": args.user, "model": args.model, "summary": total, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"results written to {args.out}")
    return 0 if total["passed"] == total["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
