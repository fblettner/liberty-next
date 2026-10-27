"""The ops tools read the running server: nomaflow's jobs + runs (a real SQLite job DB), the
log ring buffer, the integrity checks — and are only offered under [ai] ops_tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

from liberty.ai.assistant import build_assistant
from liberty.ai.ops_tools import build_ops_tools
from liberty.config import AISettings
from liberty.connectors.config import ConnectorsFile, PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.connectors.registry import ConnectorRegistry
from liberty.jobs.db import JobDatabase
from liberty.jobs.models import JobRun, StepRun
from liberty.jobs.schema import Job, Step, StepType
from liberty.sio import LogEntry, LogRingBuffer


@pytest_asyncio.fixture
async def jobs_state(tmp_path):
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")})
    db = JobDatabase(pools, "default")
    await db.create_schema()
    t0 = datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc)
    async with db.session() as s:
        ok = JobRun(id="r1", job_id="nightly", trigger_kind="cron", started_at=t0, finished_at=t0 + timedelta(minutes=3), state="SUCCEEDED", rows_affected=120)
        bad = JobRun(id="r2", job_id="nightly", trigger_kind="manual", triggered_by="admin", started_at=t0 + timedelta(days=1), finished_at=t0 + timedelta(days=1, minutes=1), state="FAILED", error_message="ORA-00942: table or view does not exist")
        bad.step_runs = [StepRun(step_index=0, step_name="collect", step_type="sql_query", attempt=1, state="FAILED", error_message="ORA-00942: table or view does not exist")]
        s.add_all([ok, bad])
        await s.commit()
    job = Job(id="nightly", description="Collect", schedule="0 2 * * *", steps=[Step(type=StepType.SQL_QUERY, name="collect", connector="nomasx1", query="q")])
    scheduler = SimpleNamespace(scheduled_job_ids=["nightly"], in_flight_job_ids=frozenset(), next_fire_times={"nightly": t0 + timedelta(days=2)})
    comps = SimpleNamespace(registry=SimpleNamespace(jobs=lambda: [job]), scheduler=scheduler, db=db)
    logs = LogRingBuffer(max_entries=50)
    for i, (lvl, name, msg) in enumerate([("INFO", "liberty.runtime", "config reloaded"), ("WARNING", "liberty.jobs.runner", "step collect: retry 1"),
                                          ("ERROR", "liberty.connectors.sql", "nomasx1: connection refused"), ("INFO", "uvicorn", "GET /health")]):
        logs._buf.append(LogEntry(ts=f"2026-10-27T0{i}:00:00", level=lvl, name=name, message=msg))
    st = SimpleNamespace(jobs=comps, sio=SimpleNamespace(logs=logs), connectors=ConnectorRegistry(ConnectorsFile()))
    yield st
    await pools.dispose()


def _tools(st):
    return {t.name: t for t in build_ops_tools(lambda: st)}


@pytest.mark.asyncio
async def test_jobs_status_lists_jobs_and_details_one(jobs_state) -> None:
    tools = _tools(jobs_state)
    out = await tools["jobs_status"].run({})
    assert out["enabled"] is True and [j["id"] for j in out["jobs"]] == ["nightly"]
    j = out["jobs"][0]
    assert j["last_run"]["state"] == "FAILED" and j["registered_with_scheduler"] is True and "recent_runs" not in out
    out = await tools["jobs_status"].run({"job_id": "nightly", "runs": 5})
    assert [r["state"] for r in out["recent_runs"]] == ["FAILED", "SUCCEEDED"]
    assert out["recent_runs"][0]["error"].startswith("ORA-00942") and out["recent_runs"][0]["steps"] == [{"name": "collect", "state": "FAILED", "attempt": 1, "error": "ORA-00942: table or view does not exist"}]
    assert out["recent_runs"][1]["rows_affected"] == 120 and out["recent_runs"][1]["started_at"].startswith("2026-10-27T02:00:00")   # SQLite drops the zone
    with pytest.raises(KeyError, match="no job 'ghost'"):
        await tools["jobs_status"].run({"job_id": "ghost"})
    assert (await _tools(SimpleNamespace())["jobs_status"].run({}))["enabled"] is False


@pytest.mark.asyncio
async def test_server_log_filters_by_level_and_text(jobs_state) -> None:
    tools = _tools(jobs_state)
    out = await tools["server_log"].run({})
    assert [l["level"] for l in out["lines"]] == ["WARNING", "ERROR"] and out["total_matching"] == 2
    out = await tools["server_log"].run({"level": "info", "contains": "nomasx1"})
    assert [l["message"] for l in out["lines"]] == ["nomasx1: connection refused"]
    out = await tools["server_log"].run({"level": "DEBUG", "limit": 2})
    assert [l["message"] for l in out["lines"]] == ["nomasx1: connection refused", "GET /health"] and out["total_matching"] == 4
    assert (await _tools(SimpleNamespace())["server_log"].run({}))["lines"] == []


@pytest.mark.asyncio
async def test_integrity_checks_run_against_the_state(jobs_state) -> None:
    from liberty.menus.config import AppMenu, MenuItem, MenusFile
    from liberty.screens.config import ScreensFile
    st = SimpleNamespace(connectors=jobs_state.connectors, menus=MenusFile(menus={"shop": AppMenu(items=[MenuItem(id="x", label="X", type="screen", target="missing")])}),
                         screens=ScreensFile(), charts=None, dashboards=None, actions=None, explorer=None)
    out = await _tools(st)["integrity_checks"].run({"severity": "error"})
    assert out["errors"] >= 1 and out["warnings"] == 0 and all(i["severity"] == "error" for i in out["issues"])
    assert any("does not exist" in i["message"] for i in out["issues"])


def test_ops_tools_are_opt_in() -> None:
    reg = ConnectorRegistry(ConnectorsFile())
    a = build_assistant(AISettings(api_key=""), reg, master_key="", state=lambda: SimpleNamespace())
    assert "jobs_status" not in a.tools
    a = build_assistant(AISettings(api_key="", ops_tools=True), reg, master_key="", state=lambda: SimpleNamespace())
    assert {"jobs_status", "server_log", "integrity_checks"} <= set(a.tools.names())
    assert build_assistant(AISettings(api_key="", ops_tools=True), reg, master_key="").tools.names() == ["list_connectors", "sql_query"]
