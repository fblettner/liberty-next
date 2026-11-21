"""Where an installation's test suites, their last results and their reference runs are kept:
files under ``<config>/tests/`` — ``<suite>.toml`` (packaged and versioned with the rest of the
configuration), ``.results/<suite>.json`` (the last runs of each case) and
``.reference/<suite>.json`` (what a reference run kept). No table: a result is a small thing read
with its suite, and the configuration's history already keeps what a suite was."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tomli_w

from liberty.apptests import runner as r

_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")
KEPT_RUNS = 20            # the last runs kept per case


def tests_dir(settings: Any) -> Path:
    """``tests/`` next to the configuration files."""
    return Path(settings.connectors.config_path).parent / "tests"


def suite_path(settings: Any, name: str) -> Path:
    """The file of the suite *name* — a plain name only: it is a file name."""
    name = str(name or "").strip().removesuffix(".toml")
    if not _NAME.match(name):
        raise ValueError("a suite's name is made of letters, digits, - and _ (it is its file's name)")
    return tests_dir(settings) / f"{name}.toml"


def suite_names(settings: Any) -> list[str]:
    folder = tests_dir(settings)
    return sorted(p.stem for p in folder.glob("*.toml")) if folder.is_dir() else []


def read_suite(settings: Any, name: str) -> r.Suite:
    path = suite_path(settings, name)
    if not path.is_file():
        raise FileNotFoundError(name)
    return r.load_suite(path)


def _header(path: Path) -> str:
    """The comment lines at the top of a suite file: kept when it is written again."""
    if not path.is_file():
        return ""
    kept: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            break
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + "\n\n" if kept else ""


def write_suite(settings: Any, name: str, content: dict[str, Any], *, versions: Any = None) -> r.Suite:
    """Write the suite *name* from *content* (``connector``, ``label``, ``case``) — checked as a
    whole first (what the runner will read), the file as it was kept in the configuration's history,
    replaced in one move. Raises ValueError with what is wrong."""
    path = suite_path(settings, name)
    suite = r.parse_suite(content, path.name)
    if versions is not None:
        try:
            versions.snapshot(path, source="manual", comment="tests")
        except Exception:  # noqa: BLE001 — the history never refuses a save
            pass
    text = _header(path) + tomli_w.dumps(suite.model_dump(exclude_defaults=True, exclude_none=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return suite


def delete_suite(settings: Any, name: str, *, versions: Any = None) -> None:
    path = suite_path(settings, name)
    if versions is not None and path.is_file():
        try:
            versions.snapshot(path, source="manual", comment="tests")
        except Exception:  # noqa: BLE001
            pass
    path.unlink(missing_ok=True)
    for folder in (".results", ".reference"):
        (tests_dir(settings) / folder / f"{path.stem}.json").unlink(missing_ok=True)


def _json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        return {}


def _keep(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def results(settings: Any, name: str) -> dict[str, list[dict[str, Any]]]:
    """``{case id: [its last runs, the latest first]}``."""
    return _json(tests_dir(settings) / ".results" / f"{suite_path(settings, name).stem}.json")


def keep_results(settings: Any, name: str, played: list[r.CaseResult], *, mode: str, who: str | None = None) -> None:
    """Add a run's results to each case's history (the last :data:`KEPT_RUNS` are kept)."""
    path = tests_dir(settings) / ".results" / f"{suite_path(settings, name).stem}.json"
    data = _json(path)
    at = datetime.now(UTC).isoformat(timespec="seconds")
    run_id = uuid.uuid4().hex[:12]                 # what puts a run back together: two runs may share a second
    for c in played:
        run = {"at": at, "run": run_id, "mode": mode, "who": who, "status": c.status,
               "steps": [{"step": s.step, "endpoint": s.endpoint, "status": s.status, "duration_ms": s.duration_ms, "problems": s.problems} for s in c.steps]}
        data[c.id] = [run, *(data.get(c.id) or [])][:KEPT_RUNS]
    _keep(path, data)


def reference(settings: Any, name: str) -> dict[str, Any] | None:
    """What the reference run of the suite kept — ``{case id: {step: {path: value}}}`` — or None."""
    data = _json(tests_dir(settings) / ".reference" / f"{suite_path(settings, name).stem}.json")
    return data or None


def keep_reference(settings: Any, name: str, played: list[r.CaseResult]) -> None:
    """Record what *played* kept as the reference — over the other cases' reference, not instead of it."""
    path = tests_dir(settings) / ".reference" / f"{suite_path(settings, name).stem}.json"
    _keep(path, {**_json(path), **r.fingerprint(played)})


def overview(settings: Any) -> list[dict[str, Any]]:
    """Every suite with its cases and each case's last result. A suite that cannot be read says
    why — it must not hide the others."""
    out: list[dict[str, Any]] = []
    for name in suite_names(settings):
        try:
            suite = read_suite(settings, name)
        except ValueError as exc:
            out.append({"name": name, "error": str(exc), "cases": []})
            continue
        last = results(settings, name)
        out.append({"name": name, "label": suite.label, "connector": suite.connector, "cases": [
            {"id": c.id, "label": c.label, "writes": c.writes, "steps": len(c.steps),
             "last": ({"status": last[c.id][0]["status"], "at": last[c.id][0]["at"]} if last.get(c.id) else None)}
            for c in suite.case]})
    return out


def history(settings: Any) -> list[dict[str, Any]]:
    """What the results page shows: per suite, its runs put back together (a run = every case
    played at the same moment: how many passed, failed, changed, were skipped, and the time taken)
    and each case with its last statuses and what its last run found."""
    out: list[dict[str, Any]] = []
    for name in suite_names(settings):
        try:
            suite = read_suite(settings, name)
        except ValueError as exc:
            out.append({"name": name, "error": str(exc), "runs": [], "cases": []})
            continue
        kept = results(settings, name)
        runs: dict[str, dict[str, Any]] = {}
        cases = []
        for c in suite.case:
            mine = kept.get(c.id) or []
            for run in mine:
                agg = runs.setdefault(run.get("run") or run["at"], {"at": run["at"], "mode": run.get("mode"), "who": run.get("who"),
                                                 r.PASSED: 0, r.FAILED: 0, r.CHANGED: 0, r.SKIPPED: 0, "duration_ms": 0.0})
                agg[run["status"]] = agg.get(run["status"], 0) + 1
                agg["duration_ms"] += sum(float(s.get("duration_ms") or 0) for s in run.get("steps") or [])
            last = mine[0] if mine else None
            cases.append({
                "id": c.id, "label": c.label, "writes": c.writes, "steps": len(c.steps),
                "history": [run["status"] for run in mine[:12]],
                "last": None if last is None else {
                    "status": last["status"], "at": last["at"], "mode": last.get("mode"),
                    "duration_ms": round(sum(float(s.get("duration_ms") or 0) for s in last.get("steps") or []), 1),
                    "problems": [f"{s['step']}: {p}" for s in last.get("steps") or [] for p in (s.get("problems") or [])][:5]}})
        for agg in runs.values():
            agg["duration_ms"] = round(agg["duration_ms"], 1)
        # the latest first: a run is met in the order its cases keep it (their latest first)
        order = {key: i for i, key in enumerate(runs)}
        ranked = sorted(runs.items(), key=lambda kv: (kv[1]["at"], -order[kv[0]]), reverse=True)
        out.append({"name": name, "label": suite.label, "connector": suite.connector, "cases": cases,
                    "runs": [agg for _key, agg in ranked][:KEPT_RUNS]})
    return out


def totals(settings: Any) -> dict[str, Any]:
    """Every case of every suite by its last result — the overview's line."""
    out: dict[str, Any] = {"cases": 0, r.PASSED: 0, r.FAILED: 0, r.CHANGED: 0, r.SKIPPED: 0, "never": 0, "last_at": None}
    for suite in overview(settings):
        for c in suite["cases"]:
            out["cases"] += 1
            if c["last"] is None:
                out["never"] += 1
            else:
                out[c["last"]["status"]] = out.get(c["last"]["status"], 0) + 1
                out["last_at"] = max(out["last_at"] or "", c["last"]["at"])
    return out
