"""Tests for chunk 3's wiring layer: :func:`build_nomaflow`,
:func:`hot_reload_registry`, the SioJobBroadcaster adapter, and
:meth:`JobRunner.cancel` (the new entry point the admin endpoint hits).

Where test_web_jobs.py covers the FastAPI surface end-to-end, this file
exercises the helpers in isolation so a failure points at the right module.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from liberty.config import JobsSettings, Settings
from liberty.connectors.config import (
    ConnectorsFile,
    PoolConfig,
    SqlConnectorConfig,
)
from liberty.connectors.registry import ConnectorRegistry
from liberty.jobs import (
    JobDatabase,
    JobRun,
    JobRunner,
    ManualTrigger,
    RunState,
    Step,
    StepResult,
    StepType,
    load_jobs,
)
from liberty.jobs.wiring import (
    NomaflowComponents,
    SioJobBroadcaster,
    build_executors,
    build_nomaflow,
    hot_reload_registry,
    shutdown_nomaflow,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def connectors(tmp_path):
    cfg = ConnectorsFile(
        pools={"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'wiring.db'}")},
        connectors={"db": SqlConnectorConfig(type="sql", pool="default", queries=[])},
    )
    reg = ConnectorRegistry(cfg)
    yield reg
    await reg.aclose()


def _settings(jobs_toml: Path) -> Settings:
    return Settings(jobs=JobsSettings(config_path=jobs_toml, pool="default"))


# --------------------------------------------------------------------------- #
# build_executors — the default map
# --------------------------------------------------------------------------- #


def test_build_executors_covers_implemented_step_types(connectors) -> None:
    """sql_query + sql_copy (chunks 2a/b) and python (added to unblock the
    nomasx1 v1→v2 port — its agent modules run as named callables). ldap_sync
    and http are still pending. A future refactor that drops one of these
    fails this test."""
    execs = build_executors(connectors)
    assert set(execs.keys()) == {StepType.SQL_QUERY, StepType.SQL_COPY, StepType.PYTHON}


# --------------------------------------------------------------------------- #
# build_nomaflow + shutdown_nomaflow
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_build_nomaflow_starts_scheduler_and_registers_jobs(connectors, tmp_path) -> None:
    """End-to-end: a Settings + a connector registry → fully wired stack with
    the scheduler already running."""
    jobs_toml = tmp_path / "jobs.toml"
    jobs_toml.write_text("""
[[jobs]]
id = "scheduled-job"
schedule = "*/15 * * * *"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "db"
query = "q"
""")
    # Have to materialize the nomaflow tables first — the real app's init-db
    # does this; for the test we call create_schema directly.
    from liberty.jobs.db import JobDatabase
    await JobDatabase(connectors.pools, "default").create_schema()

    components = await build_nomaflow(_settings(jobs_toml), connectors)
    try:
        assert isinstance(components, NomaflowComponents)
        assert components.scheduler.is_started
        assert components.scheduler.scheduled_job_ids == ["scheduled-job"]
        assert "scheduled-job" in {j.id for j in components.registry.jobs()}
    finally:
        await shutdown_nomaflow(components)


@pytest.mark.asyncio
async def test_build_nomaflow_respects_jobs_enabled_false(connectors, tmp_path) -> None:
    """When [jobs] enabled = false, the components still load (registry is
    accessible) but the scheduler isn't started — useful for maintenance mode."""
    jobs_toml = tmp_path / "jobs.toml"
    jobs_toml.write_text("""
[[jobs]]
id = "wouldnt-start"
schedule = "0 * * * *"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "db"
query = "q"
""")
    from liberty.jobs.db import JobDatabase
    await JobDatabase(connectors.pools, "default").create_schema()

    settings = Settings(jobs=JobsSettings(
        config_path=jobs_toml, pool="default", enabled=False,
    ))
    components = await build_nomaflow(settings, connectors)
    try:
        assert not components.scheduler.is_started
        assert {j.id for j in components.registry.jobs()} == {"wouldnt-start"}
    finally:
        await shutdown_nomaflow(components)


# --------------------------------------------------------------------------- #
# hot_reload_registry — the diff-and-reconcile
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_hot_reload_adds_and_removes_jobs(connectors, tmp_path) -> None:
    """Edit jobs.toml; reload; the scheduler now reflects the new shape."""
    jobs_toml = tmp_path / "jobs.toml"
    jobs_toml.write_text("""
[[jobs]]
id = "keeps"
schedule = "0 0 * * *"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "db"
query = "q"

[[jobs]]
id = "gets-dropped"
schedule = "0 1 * * *"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "db"
query = "q"
""")
    from liberty.jobs.db import JobDatabase
    await JobDatabase(connectors.pools, "default").create_schema()

    components = await build_nomaflow(_settings(jobs_toml), connectors)
    try:
        assert set(components.scheduler.scheduled_job_ids) == {"keeps", "gets-dropped"}

        # Rewrite: drop "gets-dropped", add "added"
        jobs_toml.write_text("""
[[jobs]]
id = "keeps"
schedule = "0 0 * * *"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "db"
query = "q"

[[jobs]]
id = "added"
schedule = "0 2 * * *"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "db"
query = "q"
""")
        new_registry = await hot_reload_registry(components, _settings(jobs_toml))
        assert set(components.scheduler.scheduled_job_ids) == {"keeps", "added"}
        assert {j.id for j in new_registry.jobs()} == {"keeps", "added"}
        # components.registry was swapped in place
        assert components.registry is new_registry
    finally:
        await shutdown_nomaflow(components)


# --------------------------------------------------------------------------- #
# SioJobBroadcaster adapter
# --------------------------------------------------------------------------- #


class _RecordingSio:
    """Fake LibertySio: records every emit + the room it went to."""

    def __init__(self) -> None:
        self.sio = self  # the adapter reads sio_layer.sio
        self.emitted: list[tuple[str, dict, str | None]] = []

    async def emit(self, event: str, payload: dict, room: str | None = None) -> None:
        self.emitted.append((event, payload, room))


@pytest.mark.asyncio
async def test_sio_broadcaster_prefixes_events_and_targets_dashboard_room() -> None:
    sio = _RecordingSio()
    bc = SioJobBroadcaster(sio)
    await bc.broadcast("job_run.state", {"run_id": "r1", "state": "RUNNING"})
    assert sio.emitted == [
        ("nomaflow.job_run.state", {"run_id": "r1", "state": "RUNNING"}, "dashboard"),
    ]


@pytest.mark.asyncio
async def test_sio_broadcaster_swallows_emit_errors() -> None:
    """Broken Socket.IO mustn't take down a job — log + carry on."""
    class _Broken:
        async def emit(self, *a, **kw):
            raise RuntimeError("sio is down")
    layer = type("X", (), {"sio": _Broken()})()
    bc = SioJobBroadcaster(layer)
    # Does not raise
    await bc.broadcast("anything", {})


@pytest.mark.asyncio
async def test_sio_broadcaster_no_sio_layer_is_silent() -> None:
    bc = SioJobBroadcaster(sio_layer=None)
    await bc.broadcast("event", {"x": 1})  # no error


# --------------------------------------------------------------------------- #
# JobRunner.cancel — the new admin entry point
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_runner_cancel_interrupts_inflight_run(connectors, tmp_path) -> None:
    """An in-flight run can be cancelled by id; the run terminates with state
    CANCELED and the cancel() call returns True."""
    from liberty.jobs.db import JobDatabase
    db = JobDatabase(connectors.pools, "default")
    await db.create_schema()

    started = asyncio.Event()

    class HangingExecutor:
        async def execute(self, step, ctx):
            started.set()
            await asyncio.sleep(60)
            return StepResult()

    async def _noop_sleep(_secs: float) -> None:
        pass

    runner = JobRunner(db, {StepType.SQL_QUERY: HangingExecutor()}, sleep=_noop_sleep)
    jobs_toml = tmp_path / "jobs.toml"
    jobs_toml.write_text("""
[[jobs]]
id = "long-running"
[[jobs.steps]]
type = "sql_query"
name = "slow"
connector = "db"
query = "q"
""")
    job = load_jobs(jobs_toml).get("long-running")
    task = asyncio.create_task(runner.run(job, ManualTrigger(triggered_by="x")))
    await started.wait()
    assert len(runner.active_run_ids) == 1
    run_id = next(iter(runner.active_run_ids))

    assert runner.cancel(run_id) is True
    # The run task absorbs the CancelledError after the runner records CANCELED;
    # asyncio still propagates it to our awaiting task.
    with pytest.raises(asyncio.CancelledError):
        await task

    async with db.session() as s:
        runs = (await s.execute(select(JobRun))).scalars().all()
    assert len(runs) == 1
    assert runs[0].state == RunState.CANCELED.value
    # cancel() on the now-terminated run is a clean no-op (False)
    assert runner.cancel(run_id) is False


@pytest.mark.asyncio
async def test_runner_cancel_for_unknown_run_returns_false(connectors) -> None:
    from liberty.jobs.db import JobDatabase
    db = JobDatabase(connectors.pools, "default")
    await db.create_schema()
    runner = JobRunner(db, {})
    assert runner.cancel("never-existed") is False
    assert runner.active_run_ids == frozenset()
