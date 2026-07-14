"""Tests for :class:`liberty.jobs.JobScheduler` — APScheduler wiring.

Strategy: test the wiring + lifecycle, not the cron clock. APScheduler's own
test suite covers "does this cron string fire at the right time"; what nomaflow
adds is "are the right jobs registered, does the recovery sweep run first,
does the drop-on-concurrent-fire rule work, do hot-reload methods touch the
right APScheduler objects". We trigger fires manually via :meth:`fire_now` and
poke the registered-jobs list directly.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.jobs import (
    JobDatabase,
    JobRegistry,
    JobRun,
    JobRunner,
    JobScheduler,
    JobsFile,
    ManualTrigger,
    RunState,
    Step,
    StepResult,
    StepType,
    TriggerKind,
    load_jobs,
)


# --------------------------------------------------------------------------- #
# fixtures + mocks
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def jobs_db(tmp_path):
    pools = PoolRegistry(
        {"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'sched.db'}")}
    )
    db = JobDatabase(pools, "default")
    await db.create_schema()
    yield db
    await pools.dispose()


class FixedSuccessExecutor:
    """A step executor that always succeeds and records every call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, step: Step, ctx) -> StepResult:
        self.calls.append(step.name)
        return StepResult(rows_affected=1)


class HangingExecutor:
    """Holds open until released — useful for "is this run still in flight" assertions."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, step, ctx) -> StepResult:
        self.started.set()
        await self.release.wait()
        return StepResult(rows_affected=0)


def _registry_from_toml(tmp_path, toml: str) -> JobRegistry:
    path = tmp_path / "jobs.toml"
    path.write_text(toml, encoding="utf-8")
    return load_jobs(path)


def _runner(jobs_db: JobDatabase, executor) -> JobRunner:
    async def _noop_sleep(_secs: float) -> None:
        pass
    return JobRunner(jobs_db, {StepType.SQL_QUERY: executor}, sleep=_noop_sleep)


# --------------------------------------------------------------------------- #
# lifecycle: start runs recovery + registers triggers; stop cleans up
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_start_runs_recovery_sweep_first(jobs_db, tmp_path) -> None:
    """Pre-seed an orphan RUNNING row, then start the scheduler. The sweep
    should fire BEFORE any cron triggers run — verified by the orphan's state."""
    async with jobs_db.session() as s:
        s.add(JobRun(
            job_id="ghost",
            trigger_kind=TriggerKind.SCHEDULED.value,
            scheduled_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            state=RunState.RUNNING.value,
        ))
        await s.flush()

    registry = _registry_from_toml(tmp_path, "")  # no jobs configured
    scheduler = JobScheduler(registry, _runner(jobs_db, FixedSuccessExecutor()))
    await scheduler.start()
    try:
        async with jobs_db.session() as s:
            ghost = (await s.execute(
                select(JobRun).where(JobRun.job_id == "ghost")
            )).scalar_one()
        assert ghost.state == RunState.FAILED.value
        assert "abandoned" in (ghost.error_message or "").lower()
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_start_registers_scheduled_jobs_only(jobs_db, tmp_path) -> None:
    """Three jobs: one cron+enabled, one cron+disabled, one manual-only.
    Only the first should land in APScheduler's job list."""
    registry = _registry_from_toml(tmp_path, """
[[jobs]]
id = "daily-cron"
schedule = "0 2 * * *"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"

[[jobs]]
id = "disabled-cron"
schedule = "0 3 * * *"
enabled = false
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"

[[jobs]]
id = "manual-only"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
""")
    scheduler = JobScheduler(registry, _runner(jobs_db, FixedSuccessExecutor()))
    await scheduler.start()
    try:
        assert scheduler.scheduled_job_ids == ["daily-cron"]
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent(jobs_db, tmp_path) -> None:
    registry = _registry_from_toml(tmp_path, "")
    scheduler = JobScheduler(registry, _runner(jobs_db, FixedSuccessExecutor()))
    await scheduler.start()
    await scheduler.start()  # no error, no double-state
    assert scheduler.is_started
    await scheduler.stop()


@pytest.mark.asyncio
async def test_stop_without_start_is_noop(jobs_db, tmp_path) -> None:
    registry = _registry_from_toml(tmp_path, "")
    scheduler = JobScheduler(registry, _runner(jobs_db, FixedSuccessExecutor()))
    await scheduler.stop()  # never started; should not error
    assert not scheduler.is_started


# --------------------------------------------------------------------------- #
# hot-reload methods (add_job / remove_job)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_add_job_registers_at_runtime(jobs_db, tmp_path) -> None:
    """Hot-reload story: an admin saves jobs.toml + a new job appears; the
    scheduler is given the new Job via add_job() without restarting."""
    registry = _registry_from_toml(tmp_path, "")
    scheduler = JobScheduler(registry, _runner(jobs_db, FixedSuccessExecutor()))
    await scheduler.start()
    try:
        # Build a Job out-of-band (in production, registry would be swapped first)
        new_job = Step.model_validate  # silence unused-import linter
        from liberty.jobs.schema import Job
        job = Job(
            id="late-add",
            schedule="*/15 * * * *",
            steps=[Step(type=StepType.SQL_QUERY, name="s", connector="c", query="q")],
        )
        scheduler.add_job(job)
        assert "late-add" in scheduler.scheduled_job_ids
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_add_job_skips_disabled_and_unscheduled(jobs_db, tmp_path) -> None:
    registry = _registry_from_toml(tmp_path, "")
    scheduler = JobScheduler(registry, _runner(jobs_db, FixedSuccessExecutor()))
    await scheduler.start()
    try:
        from liberty.jobs.schema import Job
        disabled = Job(
            id="off", schedule="0 0 * * *", enabled=False,
            steps=[Step(type=StepType.SQL_QUERY, name="s", connector="c", query="q")],
        )
        manual = Job(
            id="manual",
            steps=[Step(type=StepType.SQL_QUERY, name="s", connector="c", query="q")],
        )
        scheduler.add_job(disabled)
        scheduler.add_job(manual)
        assert scheduler.scheduled_job_ids == []
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_remove_job_handles_missing_job(jobs_db, tmp_path) -> None:
    """Removing a job that's not registered is silent — that's the right
    semantics for "remove if present" during reload."""
    registry = _registry_from_toml(tmp_path, "")
    scheduler = JobScheduler(registry, _runner(jobs_db, FixedSuccessExecutor()))
    await scheduler.start()
    try:
        scheduler.remove_job("nope")  # no error
    finally:
        await scheduler.stop()


# --------------------------------------------------------------------------- #
# concurrent-fire drop policy (PHASE13 §4)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_concurrent_fire_dropped_and_broadcast(jobs_db, tmp_path) -> None:
    """When a second fire of the same job arrives while the first is still
    running, the second is dropped + the on_dropped_fire hook is called."""
    registry = _registry_from_toml(tmp_path, """
[[jobs]]
id = "overlap"
schedule = "*/1 * * * *"
[[jobs.steps]]
type = "sql_query"
name = "slow"
connector = "c"
query = "q"
""")
    hanging = HangingExecutor()
    dropped_events: list[tuple[str, dict]] = []

    async def record(event: str, payload: dict) -> None:
        dropped_events.append((event, payload))

    scheduler = JobScheduler(
        registry, _runner(jobs_db, hanging), on_dropped_fire=record,
    )
    await scheduler.start()
    try:
        # Kick off the first fire manually via the internal hook (mirrors what
        # the cron trigger would do), then issue a second one while the first
        # is still inside HangingExecutor.execute waiting on release.
        task1 = asyncio.create_task(scheduler._run_or_drop("overlap"))
        await hanging.started.wait()  # confirms first fire is in flight
        assert "overlap" in scheduler.in_flight_job_ids

        await scheduler._run_or_drop("overlap")  # second fire, should be dropped
        assert dropped_events == [
            ("job_run.dropped", {"job_id": "overlap", "reason": "previous_run_still_running"}),
        ]

        # Release the first fire so it can finish
        hanging.release.set()
        await task1
        assert "overlap" not in scheduler.in_flight_job_ids
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_broadcast_hook_failure_does_not_kill_scheduler(jobs_db, tmp_path) -> None:
    """A broken broadcaster mustn't take the scheduler down — log + carry on."""
    registry = _registry_from_toml(tmp_path, """
[[jobs]]
id = "overlap2"
schedule = "*/1 * * * *"
[[jobs.steps]]
type = "sql_query"
name = "slow"
connector = "c"
query = "q"
""")
    hanging = HangingExecutor()

    async def broken(event: str, payload: dict) -> None:
        raise RuntimeError("broadcaster down")

    scheduler = JobScheduler(
        registry, _runner(jobs_db, hanging), on_dropped_fire=broken,
    )
    await scheduler.start()
    try:
        task1 = asyncio.create_task(scheduler._run_or_drop("overlap2"))
        await hanging.started.wait()
        # The drop hook will raise — the scheduler must absorb it
        await scheduler._run_or_drop("overlap2")  # no exception propagates
        hanging.release.set()
        await task1
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_fire_now_invokes_runner_with_manual_trigger(jobs_db, tmp_path) -> None:
    registry = _registry_from_toml(tmp_path, """
[[jobs]]
id = "manual"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
""")
    executor = FixedSuccessExecutor()
    scheduler = JobScheduler(registry, _runner(jobs_db, executor))
    await scheduler.start()
    try:
        job = registry.get("manual")
        await scheduler.fire_now(job, triggered_by="alice")
    finally:
        await scheduler.stop()

    assert executor.calls == ["s"]
    async with jobs_db.session() as s:
        runs = (await s.execute(select(JobRun))).scalars().all()
    assert len(runs) == 1
    assert runs[0].state == RunState.SUCCEEDED.value
    assert runs[0].triggered_by == "alice"
    assert runs[0].trigger_kind == TriggerKind.MANUAL.value


# --------------------------------------------------------------------------- #
# defence: unknown job id should not crash the scheduler
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fire_for_unknown_job_is_dropped_silently(jobs_db, tmp_path) -> None:
    """If the registry is hot-swapped to remove a job between trigger
    registration and fire, the fire arrives for a job_id that no longer
    exists. Drop the fire + carry on (don't crash the scheduler)."""
    registry = _registry_from_toml(tmp_path, "")
    scheduler = JobScheduler(registry, _runner(jobs_db, FixedSuccessExecutor()))
    await scheduler.start()
    try:
        await scheduler._run_or_drop("phantom")  # no exception, no run created
        async with jobs_db.session() as s:
            runs = (await s.execute(select(JobRun))).scalars().all()
        assert runs == []
    finally:
        await scheduler.stop()


# --------------------------------------------------------------------------- #
# schedulable presets: a preset with its own cron fires the job with its params
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scheduled_preset_registers_its_own_trigger(jobs_db, tmp_path) -> None:
    """A preset carrying a ``schedule`` registers a distinct APScheduler trigger
    alongside the job's own schedule — both have a next fire."""
    registry = _registry_from_toml(tmp_path, """
[[jobs]]
id = "sync"
schedule = "0 2 * * *"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
[[jobs.presets]]
name = "nightly-eu"
schedule = "0 3 * * *"
timezone = "Europe/Paris"
params = { apps_id = 2 }
""")
    scheduler = JobScheduler(registry, _runner(jobs_db, FixedSuccessExecutor()))
    await scheduler.start()
    try:
        ids = set(scheduler.scheduled_job_ids)
        pid = JobScheduler._preset_job_id("sync", "nightly-eu")
        assert "sync" in ids and pid in ids
        assert "sync" in scheduler.next_fire_times and pid in scheduler.next_fire_times
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_preset_only_job_is_scheduled(jobs_db, tmp_path) -> None:
    """A job with NO job-level schedule but a scheduled preset is still registered —
    only the preset trigger, not a base trigger."""
    registry = _registry_from_toml(tmp_path, """
[[jobs]]
id = "manual-base"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
[[jobs.presets]]
name = "friday"
schedule = "0 5 * * 5"
""")
    assert [j.id for j in registry.scheduled_jobs()] == ["manual-base"]
    scheduler = JobScheduler(registry, _runner(jobs_db, FixedSuccessExecutor()))
    await scheduler.start()
    try:
        ids = set(scheduler.scheduled_job_ids)
        assert "manual-base" not in ids  # no base schedule → no base trigger
        assert JobScheduler._preset_job_id("manual-base", "friday") in ids
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_preset_fire_applies_its_overrides(jobs_db, tmp_path) -> None:
    """Firing a preset trigger runs the job with the preset's params / op_kwargs /
    log level, and stamps the trigger with the preset name."""
    registry = _registry_from_toml(tmp_path, """
[[jobs]]
id = "sync2"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
[[jobs.presets]]
name = "eu"
schedule = "0 3 * * *"
params = { apps_id = 9 }
op_kwargs = { s = { region = "EU" } }
log_level = "DEBUG"
""")
    runner = _runner(jobs_db, FixedSuccessExecutor())
    recorded: dict = {}
    orig_run = runner.run

    async def spy(job, trigger, **kw):
        recorded["trigger"] = trigger
        recorded["kw"] = kw
        return await orig_run(job, trigger, **kw)

    runner.run = spy
    scheduler = JobScheduler(registry, runner)
    await scheduler.start()
    try:
        await scheduler._run_or_drop("sync2", "eu")
    finally:
        await scheduler.stop()

    assert recorded["trigger"].preset_name == "eu"
    assert recorded["kw"]["params_override"] == {"apps_id": 9}
    assert recorded["kw"]["op_kwargs_overrides"] == {"s": {"region": "EU"}}
    assert recorded["kw"]["log_level"] == "DEBUG"


def test_duplicate_scheduled_preset_names_rejected(tmp_path) -> None:
    """Two scheduled presets sharing a name collide on the trigger id — rejected at load."""
    with pytest.raises(Exception):
        _registry_from_toml(tmp_path, """
[[jobs]]
id = "dup"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
[[jobs.presets]]
name = "same"
schedule = "0 1 * * *"
[[jobs.presets]]
name = "same"
schedule = "0 2 * * *"
""")
