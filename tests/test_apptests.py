"""An application's non-regression tests (:mod:`liberty.apptests`): the runner on its own, then the
suites kept as files and played through ``/admin/tests`` — on endpoints served by shared actions,
so no outside server is needed."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from liberty.apptests import runner as r
from tests.test_api_action_endpoint import env  # noqa: F401 — the application with its `studio` endpoints
from tests.test_web_plugins import _h

SUITE = {
    "connector": "c",
    "case": [
        {"id": "find", "label": "Employees are found", "steps": [
            {"name": "list", "endpoint": "people", "params": {"TYPE": "E"}, "capture": {"first": "first_row.id"}, "keep": ["grid.columns"],
             "expect": {"rows_min": 1, "first_row": {"type": "E"}}},
            {"name": "one", "endpoint": "people", "params": {"ID": "{{first}}"}, "expect": {"rows": 1}}]},
        {"id": "refused", "steps": [{"name": "bad", "endpoint": "nothing", "expect": {"success": False, "error_contains": "unknown table"}}]},
        {"id": "creates", "writes": True, "steps": [
            {"name": "add", "endpoint": "add"},
            {"name": "check", "endpoint": "people", "expect": {"rows": 99}},
            {"name": "clean", "endpoint": "remove", "always": True}]},
    ],
}


class Api:
    name = "c"
    config = SimpleNamespace(endpoints=[SimpleNamespace(name="people", rows_field="grid.rows", row_map={}, writable=False)])

    def __init__(self, columns: dict[str, str]) -> None:
        self.columns, self.calls = columns, []

    async def call(self, endpoint, params=None):
        self.calls.append((endpoint, dict(params or {})))
        if endpoint == "nothing":
            return SimpleNamespace(success=False, error="Unknown table F00ZZ99", warnings=None, json=None)
        rows = [{"id": 7, "type": "E "}] if (params or {}).get("ID") in (None, "7") else []
        return SimpleNamespace(success=True, error=None, warnings=None, json={"grid": {"columns": self.columns, "rows": rows}})


def _reg(api: Api):
    return SimpleNamespace(api=lambda name: api)


@pytest.mark.asyncio
async def test_a_suite_is_played_checked_and_compared() -> None:
    suite = r.parse_suite(SUITE)
    api = Api({"id": "Number", "type": "Type"})
    first = await r.run_suite(_reg(api), suite.case, connector=suite.connector)
    assert [(c.id, c.status) for c in first] == [("find", r.PASSED), ("refused", r.PASSED), ("creates", r.SKIPPED)]
    # a value captured by column name feeds the next step; the rows are where the endpoint says; a value is compared trimmed
    assert api.calls[1] == ("people", {"ID": "7"})
    reference = r.fingerprint(first)
    assert reference == {"find": {"list": {"grid.columns": {"id": "Number", "type": "Type"}}}} and json.loads(json.dumps(reference)) == reference
    assert [c.status for c in await r.run_suite(_reg(api), suite.case, connector="c", reference=reference)] == [r.PASSED, r.PASSED, r.SKIPPED]
    after = await r.run_suite(_reg(Api({"id": "Address Number"})), suite.case, connector="c", reference=reference, only=["find"])
    assert after[0].status == r.CHANGED and after[0].steps[0].problems == ["grid.columns.id: 'Address Number', was 'Number'", "grid.columns.type: gone (was 'Type')"]
    # a case that writes cleans after a failure; a suite without connector says so
    out = await r.run_suite(_reg(api), suite.case, connector="c", allow_writes=True, only=["creates"])
    assert [(s.step, s.status) for s in out[0].steps] == [("add", r.PASSED), ("check", r.FAILED), ("clean", r.PASSED)]
    assert "which connector" in (await r.run_suite(_reg(api), suite.case, connector=None, only=["find"]))[0].steps[0].problems[0]
    with pytest.raises(ValueError, match="two steps named"):
        r.parse_suite({"case": [{"id": "x", "steps": [{"name": "a", "endpoint": "e"}, {"name": "a", "endpoint": "e"}]}]})
    with pytest.raises(ValueError, match="used twice"):
        r.parse_suite({"case": [{"id": "x"}, {"id": "x"}]})


def test_the_suites_are_edited_played_and_kept(env) -> None:  # noqa: F811
    case = {"id": "code-001", "label": "The first code is there", "steps": [
        {"name": "rows", "endpoint": "code_rows", "params": {"KY": "001"}, "capture": {"code": "first_row.CODE"},
         "expect": {"rows": 1, "first_row": {"LABEL": "one"}}},
        {"name": "card", "endpoint": "describe_code", "params": {"KY": "{{code}}"}, "keep": ["label"], "expect": {"equals": {"label": "one"}}}]}
    with TestClient(env) as client:
        h = _h(client, "admin")
        assert client.get("/admin/tests", headers=_h(client, "caller")).status_code == 403
        assert client.get("/admin/tests", headers=h).json() == {"suites": []}
        # saved whole, validated: nothing is written when something is wrong
        bad = client.put("/admin/tests/smoke", headers=h, json={"connector": "studio", "case": [{"id": "x", "steps": [{"name": "a"}]}]})
        assert bad.status_code == 422 and "endpoint" in bad.json()["detail"]
        assert client.put("/admin/tests/..%2Fout", headers=h, json={"case": []}).status_code in (404, 422)
        saved = client.put("/admin/tests/smoke", headers=h, json={"connector": "studio", "label": "Codes", "case": [case]})
        assert saved.status_code == 200 and saved.json()["suite"]["case"][0]["steps"][1]["params"] == {"KY": "{{code}}"}
        listed = client.get("/admin/tests", headers=h).json()["suites"]
        assert listed == [{"name": "smoke", "label": "Codes", "connector": "studio", "cases": [
            {"id": "code-001", "label": "The first code is there", "writes": False, "steps": 2, "last": None}]}]
        # "what does it answer now": the rows by column name; a call that changes data is not made
        tried = client.post("/admin/tests/try", headers=h, json={"connector": "studio", "endpoint": "code_rows", "params": {"KY": "002"}}).json()
        assert tried == {"ok": True, "rows": 1, "first_row": {"CODE": "002", "LABEL": "two"}}
        assert "changes data" in client.post("/admin/tests/try", headers=h, json={"connector": "studio", "endpoint": "rename_code"}).json()["error"]
        assert client.post("/admin/tests/try", headers=h, json={"connector": "studio", "endpoint": "nope"}).status_code == 404
        # recorded as the reference, then compared: passed; the data changes: the kept value differs
        ran = client.post("/admin/tests/smoke/run", headers=h, json={"mode": "record"}).json()
        assert ran["summary"]["passed"] == 1 and ran["results"][0]["steps"][1]["kept"] == {"label": "one"}
        assert client.get("/admin/tests/smoke", headers=h).json()["has_reference"] is True
        assert client.post("/admin/tests/smoke/run", headers=h, json={"case": "code-001"}).json()["summary"]["passed"] == 1
        client.post("/api/http/studio/rename_code", headers=h, json={"KY": "001", "DL01": "uno"})
        failed = client.post("/admin/tests/smoke/run", headers=h, json={}).json()
        assert failed["summary"]["failed"] == 1 and "first row, LABEL: 'uno', expected 'one'" in failed["results"][0]["steps"][0]["problems"][0]
        # each case keeps its last runs, the latest first — and the list shows the last one
        kept = client.get("/admin/tests/smoke", headers=h).json()["results"]["code-001"]
        assert [k["status"] for k in kept] == ["failed", "passed", "passed"] and kept[0]["who"] == "admin" and kept[2]["mode"] == "record"
        assert client.get("/admin/tests", headers=h).json()["suites"][0]["cases"][0]["last"]["status"] == "failed"
        assert client.post("/admin/tests/smoke/run", headers=h, json={"case": "nope"}).status_code == 404
        # how the tests went: the runs put back together, each case with its last statuses and what was found
        went = client.get("/admin/test-results", headers=h).json()
        assert went["totals"]["cases"] == 1 and went["totals"]["failed"] == 1 and went["totals"]["never"] == 0 and went["totals"]["last_at"]
        mine = went["suites"][0]
        assert [(x["mode"], x["passed"], x["failed"]) for x in mine["runs"]] == [("compare", 0, 1), ("compare", 1, 0), ("record", 1, 0)]
        assert mine["runs"][0]["who"] == "admin" and mine["runs"][0]["duration_ms"] >= 0
        assert mine["cases"][0]["history"] == ["failed", "passed", "passed"] and "first row, LABEL" in mine["cases"][0]["last"]["problems"][0]
        assert client.get("/admin/studio/overview", headers=h).json()["tests"]["failed"] == 1
        # a job's run form lists the suites (of one connector when named)
        from liberty.jobs.choices import param_choices
        from liberty.jobs.schema import UserParam
        import asyncio
        pick = lambda spec: asyncio.run(param_choices(UserParam(name="suite", choices_from=spec), connectors=env.state.connectors))  # noqa: E731
        assert pick("test_suites:studio") == [{"value": "smoke", "label": "Codes (smoke)"}] and pick("test_suites:other") == []
        # the file is the suite; deleting it takes its results along
        folder = Path(env.state.settings.connectors.config_path).parent / "tests"
        assert (folder / "smoke.toml").is_file() and (folder / ".results" / "smoke.json").is_file()
        assert client.delete("/admin/tests/smoke", headers=h).json() == {"deleted": "smoke"}
        assert not (folder / "smoke.toml").exists() and not (folder / ".results" / "smoke.json").exists()
        assert client.get("/admin/tests/smoke", headers=h).status_code == 404
