"""Non-regression tests for an application, played through its API connector's endpoints.

A test case is a list of named steps — each one a call of an endpoint (whatever the connector
offers: an HTTP request, a function, a service made of steps) — with what is expected of its
answer. Cases are run before and after a change on the system behind the connector (a patch, a
new release, an upgrade): one run is recorded as the reference, the next ones are compared with
it, value by value.

A suite is a TOML file of ``<config>/tests/`` (:mod:`liberty.apptests.store`), one ``[[case]]``
per case, and names the connector its cases call::

    connector = "ais_connection"
    label = "What must keep working"

    [[case]]
    id = "address-is-found"
    label = "The test address is found"

    [[case.steps]]
    name = "find"
    endpoint = "form_P01012_W01012B"
    params = { AN8 = "9999" }
    capture = { name = "first_row.ALPH" }         # a value kept for the next steps: {{name}}
    [case.steps.expect]
    rows_min = 1
    first_row = { AN8 = "9999", ALPH = "TEST" }

What a step can expect: ``success`` (the call works — the default; ``false`` = it must be
refused), ``error_contains``, ``rows`` / ``rows_min`` / ``rows_max``, ``first_row`` (column → the
value the first row holds), ``equals`` / ``contains`` (a value at a dot-path of the raw answer),
``present`` / ``absent``, ``warning_contains``.

**Where the rows are.** An endpoint a screen can read says it itself (``rows_field``, and
``row_map`` names its columns): a step on such an endpoint needs no path. The step's own ``rows``
path is for an endpoint that does not say.

``keep`` lists the dot-paths whose content is the case's fingerprint: recorded by a reference run,
compared by the others. **A case that writes** must clean after itself (a last step, ``always``)
and be marked ``writes``: it is skipped unless the run allows it.

This module is the runner — no file, no database. It began as the JD Edwards tests of the nomajde
application (AIS); nothing in it is JD Edwards'.
"""

from __future__ import annotations

import json
import re
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from liberty.connectors import ConnectorRegistry
from liberty.connectors.api import extract_path
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")
PASSED, FAILED, SKIPPED, CHANGED = "passed", "failed", "skipped", "changed"


class Expect(BaseModel):
    """What a step's answer must be."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(default=True, description="The call works; false = it must be refused.")
    error_contains: str | None = Field(default=None, description="The refusal's text holds this (case-insensitive).")
    warning_contains: str | None = Field(default=None, description="The answer's warnings hold this.")
    rows: int | None = Field(default=None, description="Exactly this number of rows at the step's `rows` path.")
    rows_min: int | None = None
    rows_max: int | None = None
    first_row: dict[str, Any] = Field(default_factory=dict, description="column → the value the first row holds (compared as trimmed text).")
    equals: dict[str, Any] = Field(default_factory=dict, description="dot-path → the value it must hold (compared as trimmed text).")
    contains: dict[str, str] = Field(default_factory=dict, description="dot-path → a text its value must hold (case-insensitive).")
    present: list[str] = Field(default_factory=list, description="dot-paths that must hold something.")
    absent: list[str] = Field(default_factory=list, description="dot-paths that must hold nothing.")


class Step(BaseModel):
    """One call on JDE."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    endpoint: str = Field(min_length=1, description="An endpoint of the connector.")
    params: dict[str, Any] = Field(default_factory=dict, description="The endpoint's variables; `{{name}}` takes a captured value.")
    rows: str | None = Field(default=None, description="Dot-path to the answer's rows, when the endpoint does not say where they are (or to count others).")
    capture: dict[str, str] = Field(default_factory=dict, description="name → dot-path: a value of the answer kept for the next steps.")
    keep: list[str] = Field(default_factory=list, description="Dot-paths compared with the reference run.")
    expect: Expect = Field(default_factory=Expect)
    always: bool = Field(default=False, description="Run even when an earlier step failed (a cleanup).")


class Case(BaseModel):
    """A named scenario."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.\-]+$")
    label: str | None = None
    writes: bool = Field(default=False, description="The case changes JDE: skipped unless the run allows writes.")
    connector: str | None = Field(default=None, description="The API connector, when not the run's.")
    steps: list[Step] = Field(default_factory=list, description="Its calls, in order. A case without step yet (just created) is skipped.")


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector: str | None = Field(default=None, description="The API connector the cases call (a case may name another).")
    label: str | None = None
    case: list[Case] = Field(default_factory=list)


def parse_suite(data: dict[str, Any], name: str = "suite") -> Suite:
    """A suite from its parsed content, validated — a clear message names what is wrong and where."""
    try:
        suite = Suite.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ()) if not isinstance(x, int))
        raise ValueError(f"{name}: {where + ': ' if where else ''}{first.get('msg', 'not valid')}") from exc
    problem = duplicates(suite.case)
    if problem:
        raise ValueError(f"{name}: {problem}")
    return suite


def load_suite(path: Path) -> Suite:
    """The suite of a file."""
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{Path(path).name}: {exc}") from exc
    return parse_suite(data, Path(path).name)


def duplicates(cases: list[Case]) -> str | None:
    """What is named twice in *cases* — a case id in the suite, a step in its case (the reference
    run and the results are kept by these names) — or nothing."""
    seen: set[str] = set()
    for c in cases:
        if c.id in seen:
            return f"the case id {c.id!r} is used twice"
        seen.add(c.id)
        names = [s.name for s in c.steps]
        twice = next((n for n in names if names.count(n) > 1), None)
        if twice is not None:
            return f"the case {c.id!r} has two steps named {twice!r}"
    return None


@dataclass(slots=True)
class StepResult:
    case: str
    step: str
    status: str
    endpoint: str = ""
    problems: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    kept: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CaseResult:
    id: str
    label: str
    status: str
    steps: list[StepResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fill(value: Any, values: dict[str, Any]) -> Any:
    """*value* with its ``{{name}}`` placeholders replaced by the captured *values* (text only)."""
    if isinstance(value, str):
        def one(m: re.Match[str]) -> str:
            key = m.group(1).strip()
            if key not in values:
                raise KeyError(key)
            return "" if values[key] is None else str(values[key])
        return _PLACEHOLDER.sub(one, value)
    if isinstance(value, dict):
        return {k: _fill(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, values) for v in value]
    return value


def _text(value: Any) -> str:
    return "" if value is None else (value.strip() if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str) if isinstance(value, dict | list) else str(value))


def _empty(value: Any) -> bool:
    return value in (None, "", [], {}) or (isinstance(value, str) and not value.strip())


def endpoint_of(api: Any, name: str) -> Any:
    """The definition of the endpoint *name* on *api* (its ``rows_field`` / ``row_map``), or None."""
    return next((e for e in getattr(getattr(api, "config", None), "endpoints", None) or [] if e.name == name), None)


def rows_of(step: Step, answer: Any, endpoint: Any = None) -> list[Any] | None:
    """The rows of *answer*: at the step's own path when it gives one, else where the endpoint
    says, named as the endpoint names them — ``None`` when neither says where they are."""
    if step.rows:
        found = extract_path(answer, step.rows)
        return found if isinstance(found, list) else []
    if endpoint is not None and getattr(endpoint, "rows_field", None):
        from liberty.connectors.api_rows import answer_rows

        return answer_rows(endpoint, answer)
    return None


def check(step: Step, *, success: bool, error: str | None, warnings: Any, answer: Any, endpoint: Any = None) -> list[str]:
    """Why *answer* is not what *step* expects — empty when it is. *endpoint* = the definition of
    the endpoint called: where its rows are when the step does not say."""
    e, out = step.expect, []
    if e.success and not success:
        return [f"the call failed: {error or 'no reason given'}"]
    if not e.success:
        if success:
            return ["the call worked — it was expected to be refused"]
        if e.error_contains and e.error_contains.lower() not in (error or "").lower():
            out.append(f"refused, but not for the expected reason ({e.error_contains!r}): {error}")
        return out
    if e.warning_contains and e.warning_contains.lower() not in _text(warnings).lower():
        out.append(f"no warning holding {e.warning_contains!r}" + (f" (warnings: {_text(warnings)[:200]})" if not _empty(warnings) else ""))
    if e.rows is not None or e.rows_min is not None or e.rows_max is not None or e.first_row:
        located = True
        try:
            found = rows_of(step, answer, endpoint)
        except Exception as exc:  # noqa: BLE001 — the rows are not where the endpoint says: the step's failure
            found, located = None, False
            out.append(str(exc))
        if found is None and located:
            out.append("the step checks rows, but neither it nor the endpoint says where they are in the answer")
        elif found is not None:
            n = len(found)
            if e.rows is not None and n != e.rows:
                out.append(f"{n} row(s), {e.rows} expected")
            if e.rows_min is not None and n < e.rows_min:
                out.append(f"{n} row(s), at least {e.rows_min} expected")
            if e.rows_max is not None and n > e.rows_max:
                out.append(f"{n} row(s), at most {e.rows_max} expected")
            if e.first_row and not n:
                out.append("no row: the first row's values cannot be checked")
            elif e.first_row:
                first = found[0] if isinstance(found[0], dict) else {}
                by_name = {str(k).lower(): v for k, v in first.items()}
                for column, wanted in e.first_row.items():
                    if str(column).lower() not in by_name:
                        out.append(f"the first row has no column {column!r} — it has: {', '.join(str(k) for k in list(first)[:20])}")
                    elif _text(by_name[str(column).lower()]) != _text(wanted):
                        out.append(f"first row, {column}: {_text(by_name[str(column).lower()])[:120]!r}, expected {_text(wanted)[:120]!r}")
    for path, wanted in e.equals.items():
        got = extract_path(answer, path)
        if _text(got) != _text(wanted):
            out.append(f"{path}: {_text(got)[:120]!r}, expected {_text(wanted)[:120]!r}")
    for path, part in e.contains.items():
        got = _text(extract_path(answer, path))
        if part.lower() not in got.lower():
            out.append(f"{path}: {got[:120]!r} does not hold {part!r}")
    out.extend(f"{path}: nothing there" for path in e.present if _empty(extract_path(answer, path)))
    out.extend(f"{path}: holds {_text(extract_path(answer, path))[:120]!r}, nothing expected" for path in e.absent if not _empty(extract_path(answer, path)))
    return out


def differences(reference: Any, now: Any, path: str = "", limit: int = 40) -> list[str]:
    """What differs between a kept value of the reference run and today's, value by value."""
    out: list[str] = []

    def walk(a: Any, b: Any, where: str) -> None:
        if len(out) >= limit:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b), key=str):
                if k not in b:
                    out.append(f"{where}.{k}: gone (was {_text(a[k])[:80]!r})")
                elif k not in a:
                    out.append(f"{where}.{k}: new ({_text(b[k])[:80]!r})")
                else:
                    walk(a[k], b[k], f"{where}.{k}")
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                out.append(f"{where}: {len(b)} item(s), {len(a)} in the reference")
            for i, (x, y) in enumerate(zip(a, b, strict=False)):
                walk(x, y, f"{where}.{i}")
        elif _text(a) != _text(b):
            out.append(f"{where}: {_text(b)[:80]!r}, was {_text(a)[:80]!r}")

    walk(reference, now, path)
    return out[:limit]


def captured(step: Step, answer: Any, path: str, endpoint: Any = None) -> Any:
    """The value a step keeps for the next ones: ``first_row.<COLUMN>`` reads the first of the
    endpoint's rows by column name; anything else is a dot-path into the raw answer."""
    if path.lower().startswith("first_row."):
        try:
            rows = rows_of(step, answer, endpoint) or []
        except Exception:  # noqa: BLE001 — no rows where the endpoint says: nothing captured
            rows = []
        first = rows[0] if rows and isinstance(rows[0], dict) else {}
        wanted = path.split(".", 1)[1].lower()
        return next((v for k, v in first.items() if str(k).lower() == wanted), None)
    return extract_path(answer, path)


async def play(api: Any, step: Step, values: dict[str, Any]) -> tuple[list[str], Any]:
    """Call the endpoint of *step* (its ``{{name}}`` filled from *values*) and check its answer:
    ``(what is wrong, the answer)`` — the answer is ``None`` when the call could not be made."""
    try:
        params = _fill(step.params, values)
        answer = await api.call(step.endpoint, {k: (v if isinstance(v, str) else json.dumps(v) if isinstance(v, dict | list) else str(v))
                                                for k, v in params.items()})
        return check(step, success=answer.success, error=answer.error, warnings=answer.warnings, answer=answer.json,
                     endpoint=endpoint_of(api, step.endpoint)), answer.json
    except KeyError as exc:
        return [f"{{{{{exc.args[0]}}}}}: no earlier step captured this value"], None
    except Exception as exc:  # noqa: BLE001 — an unknown endpoint, a connection that fails: the step's failure
        return [f"{type(exc).__name__}: {exc}"], None


async def run_case(connectors: ConnectorRegistry, case: Case, *, connector: str | None,
                   reference: dict[str, Any] | None = None, allow_writes: bool = False) -> CaseResult:
    """Play one case. *reference* = what its steps kept in the reference run (``{step: {path:
    value}}``): compared when given. A step after a failed one is skipped, unless it is ``always``."""
    label = case.label or case.id
    if case.writes and not allow_writes:
        return CaseResult(case.id, label, SKIPPED, [StepResult(case.id, s.name, SKIPPED, s.endpoint, ["the case writes: not allowed on this run"]) for s in case.steps])
    target = case.connector or connector
    if not target:
        return CaseResult(case.id, label, FAILED, [StepResult(case.id, s.name, FAILED, s.endpoint, ["the suite does not say which connector its cases call"]) for s in case.steps])
    api = connectors.api(target)
    values: dict[str, Any] = {}
    results: list[StepResult] = []
    broken = False
    for step in case.steps:
        if broken and not step.always:
            results.append(StepResult(case.id, step.name, SKIPPED, step.endpoint, ["an earlier step failed"]))
            continue
        started = time.perf_counter()
        problems, parsed = await play(api, step, values)
        took = (time.perf_counter() - started) * 1000.0
        kept = {path: extract_path(parsed, path) for path in step.keep} if parsed is not None else {}
        status = FAILED if problems else PASSED
        if status == PASSED:
            for name, path in step.capture.items():
                values[name] = captured(step, parsed, path, endpoint_of(api, step.endpoint))
            before = (reference or {}).get(step.name)
            if before is not None:
                changed = [d for path in step.keep for d in differences(before.get(path), kept.get(path), path)]
                if changed:
                    status, problems = CHANGED, changed
        else:
            broken = True
        results.append(StepResult(case.id, step.name, status, step.endpoint, problems, round(took, 1), kept))
    statuses = {r.status for r in results}
    overall = FAILED if FAILED in statuses else CHANGED if CHANGED in statuses else PASSED if statuses else SKIPPED
    return CaseResult(case.id, label, overall, results)


async def run_suite(connectors: ConnectorRegistry, cases: list[Case], *, connector: str | None,
                    reference: dict[str, Any] | None = None, allow_writes: bool = False, only: list[str] | None = None) -> list[CaseResult]:
    """Play *cases* one after the other (JDE sessions are not shared between cases). *reference* =
    ``{case id: {step: {path: value}}}`` from :func:`fingerprint` of the reference run."""
    wanted = {x.strip() for x in only or [] if x.strip()}
    out = []
    for case in cases:
        if wanted and case.id not in wanted:
            continue
        out.append(await run_case(connectors, case, connector=connector, reference=(reference or {}).get(case.id), allow_writes=allow_writes))
    return out


def fingerprint(results: list[CaseResult]) -> dict[str, Any]:
    """What a run kept, to store as the reference: ``{case id: {step: {path: value}}}`` — only
    of the cases that passed (a failed case is not a reference)."""
    return {r.id: {s.step: s.kept for s in r.steps if s.kept} for r in results if r.status == PASSED and any(s.kept for s in r.steps)}


def summary(results: list[CaseResult]) -> dict[str, int]:
    out = {PASSED: 0, FAILED: 0, CHANGED: 0, SKIPPED: 0}
    for r in results:
        out[r.status] = out.get(r.status, 0) + 1
    return out
