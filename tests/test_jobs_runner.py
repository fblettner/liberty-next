"""Tests for :class:`liberty.jobs.JobRunner` — the orchestration state machine.

Uses mock step executors so the runner is exercised in isolation from any real
connector / network / database driver. The persistence layer *is* real (in-memory
SQLite via the same fixture pattern as the auth/jobs DB tests) — the state
machine's whole job is "transition the row correctly", and verifying that needs
real INSERTs.

Covers:
  * happy path: queued → running → succeeded; step rows recorded
  * step failure → job FAILED; remaining steps SKIPPED
  * retry policy: failed attempts → retried up to N, then FAILED
  * backoff: ``compute_backoff_delay`` math (pure function, no I/O)
  * idempotency: duplicate scheduled fire returns the existing run
  * manual triggers: many "Run now" clicks each get their own row
  * cancellation: asyncio.CancelledError → CANCELED, propagates
  * timeout: a step that hangs past timeout_seconds is failed (+ retried if policy says so)
  * unknown step type: failed with the right error message
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
    BackoffKind,
    JobDatabase,
    JobRetry,
    JobRunner,
    JobsFile,
    ManualTrigger,
    RunContext,
    RunState,
    ScheduledTrigger,
    Step,
    StepFailed,
    StepResult,
    StepRun,
    StepType,
    compute_backoff_delay,
    load_jobs,
)


# --------------------------------------------------------------------------- #
# fixtures + mocks
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def jobs_db(tmp_path):
    pools = PoolRegistry(
        {"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runner.db'}")}
    )
    db = JobDatabase(pools, "default")
    await db.create_schema()
    yield db
    await pools.dispose()


class RecordingExecutor:
    """Records every (step.name, attempt) it sees + returns the queued result.

    Each call pops the next entry off ``script``; a :class:`StepFailed` is
    raised, anything else is returned. Lets a test program a sequence of
    outcomes (fail / fail / success → exercises retry) declaratively.
    """

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, int]] = []
        self._attempt_counter: dict[str, int] = {}

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        attempt = self._attempt_counter.get(step.name, 0) + 1
        self._attempt_counter[step.name] = attempt
        self.calls.append((step.name, attempt))
        outcome = self.script.pop(0) if self.script else StepResult(rows_affected=0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class HangingExecutor:
    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        await asyncio.sleep(60)  # will be cancelled by wait_for(timeout=...)
        return StepResult()


async def _noop_sleep(_secs: float) -> None:
    """Stub for the runner's clock so backoff delays don't actually pause tests."""


def _make_jobs_file(toml: str, tmp_path) -> JobsFile:
    path = tmp_path / "jobs.toml"
    path.write_text(toml, encoding="utf-8")
    return load_jobs(path).config


def _make_job(tmp_path, toml: str):
    return _make_jobs_file(toml, tmp_path).jobs[0]


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_happy_path_marks_succeeded_and_records_step_rows(jobs_db, tmp_path) -> None:
    job = _make_job(tmp_path, """
[[jobs]]
id = "happy"

[[jobs.steps]]
type = "sql_query"
name = "first"
connector = "c"
query = "q1"

[[jobs.steps]]
type = "sql_query"
name = "second"
connector = "c"
query = "q2"
""")
    executor = RecordingExecutor([
        StepResult(rows_affected=3),
        StepResult(rows_affected=4),
    ])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: executor}, sleep=_noop_sleep)

    run = await runner.run(job, ManualTrigger(triggered_by="alice"))

    assert run.state == RunState.SUCCEEDED.value
    assert run.rows_affected == 7
    assert run.finished_at is not None
    assert run.triggered_by == "alice"
    assert executor.calls == [("first", 1), ("second", 1)]

    # Verify the persisted step rows match
    async with jobs_db.session() as s:
        rows = (await s.execute(select(StepRun).order_by(StepRun.step_index))).scalars().all()
    assert [r.step_name for r in rows] == ["first", "second"]
    assert all(r.state == RunState.SUCCEEDED.value for r in rows)
    assert [r.rows_affected for r in rows] == [3, 4]


# --------------------------------------------------------------------------- #
# step failure + retry
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_step_failure_after_retries_marks_job_failed_and_skips_remaining(jobs_db, tmp_path) -> None:
    job = _make_job(tmp_path, """
[[jobs]]
id = "with-retry"

[jobs.retry]
attempts = 3
backoff = "fixed"
base_seconds = 0

[[jobs.steps]]
type = "sql_query"
name = "broken"
connector = "c"
query = "q"

[[jobs.steps]]
type = "sql_query"
name = "never-runs"
connector = "c"
query = "q2"
""")
    # The first step will fail on every attempt → all 3 attempts consumed.
    executor = RecordingExecutor([
        StepFailed("boom 1"),
        StepFailed("boom 2"),
        StepFailed("boom 3"),
    ])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: executor}, sleep=_noop_sleep)

    run = await runner.run(job, ManualTrigger(triggered_by="bob"))

    assert run.state == RunState.FAILED.value
    # Three attempts of `broken` happened; `never-runs` never ran.
    assert executor.calls == [("broken", 1), ("broken", 2), ("broken", 3)]

    # The persisted step rows: 3 attempts of `broken` (all FAILED) + 1 SKIPPED for `never-runs`.
    async with jobs_db.session() as s:
        rows = (await s.execute(
            select(StepRun).order_by(StepRun.step_index, StepRun.attempt)
        )).scalars().all()
    assert len(rows) == 4
    broken = [r for r in rows if r.step_name == "broken"]
    assert [r.attempt for r in broken] == [1, 2, 3]
    assert all(r.state == RunState.FAILED.value for r in broken)
    assert "boom 3" in (broken[-1].error_message or "")
    skipped = [r for r in rows if r.step_name == "never-runs"]
    assert len(skipped) == 1 and skipped[0].state == RunState.CANCELED.value


@pytest.mark.asyncio
async def test_step_retried_then_succeeds(jobs_db, tmp_path) -> None:
    """A flaky step that fails twice then succeeds — the job ends SUCCEEDED."""
    job = _make_job(tmp_path, """
[[jobs]]
id = "flaky"

[jobs.retry]
attempts = 3
backoff = "fixed"
base_seconds = 0

[[jobs.steps]]
type = "sql_query"
name = "flaky"
connector = "c"
query = "q"
""")
    executor = RecordingExecutor([
        StepFailed("transient 1"),
        StepFailed("transient 2"),
        StepResult(rows_affected=42),
    ])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: executor}, sleep=_noop_sleep)
    run = await runner.run(job, ManualTrigger(triggered_by="x"))

    assert run.state == RunState.SUCCEEDED.value
    assert run.rows_affected == 42
    assert executor.calls == [("flaky", 1), ("flaky", 2), ("flaky", 3)]


@pytest.mark.asyncio
async def test_unexpected_exception_treated_as_retryable_failure(jobs_db, tmp_path) -> None:
    """A non-StepFailed Exception (a bug) still consumes a retry attempt — we'd
    rather retry once than silently lose the run."""
    job = _make_job(tmp_path, """
[[jobs]]
id = "buggy"

[jobs.retry]
attempts = 2
backoff = "fixed"
base_seconds = 0

[[jobs.steps]]
type = "sql_query"
name = "bug"
connector = "c"
query = "q"
""")
    executor = RecordingExecutor([
        ValueError("unexpected"),
        ValueError("still unexpected"),
    ])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: executor}, sleep=_noop_sleep)
    run = await runner.run(job, ManualTrigger(triggered_by="x"))

    assert run.state == RunState.FAILED.value
    async with jobs_db.session() as s:
        rows = (await s.execute(select(StepRun))).scalars().all()
    assert all(r.state == RunState.FAILED.value for r in rows)
    # The error message wraps the original exception type for visibility.
    assert any("ValueError" in (r.error_message or "") for r in rows)


# --------------------------------------------------------------------------- #
# backoff math (pure function — no I/O)
# --------------------------------------------------------------------------- #


def test_backoff_delay_no_policy() -> None:
    assert compute_backoff_delay(None, 1) == 0.0
    assert compute_backoff_delay(None, 5) == 0.0


def test_backoff_delay_first_attempt_is_zero() -> None:
    policy = JobRetry(attempts=3, backoff=BackoffKind.FIXED, base_seconds=60)
    assert compute_backoff_delay(policy, 1) == 0.0


def test_backoff_delay_fixed() -> None:
    policy = JobRetry(attempts=3, backoff=BackoffKind.FIXED, base_seconds=30)
    assert compute_backoff_delay(policy, 2) == 30.0
    assert compute_backoff_delay(policy, 3) == 30.0


def test_backoff_delay_exponential_grows() -> None:
    policy = JobRetry(attempts=4, backoff=BackoffKind.EXPONENTIAL, base_seconds=10)
    # attempt=2 → 10 + jitter[0,1) ; attempt=3 → 20 + jitter ; attempt=4 → 40 + jitter
    d2 = compute_backoff_delay(policy, 2)
    d3 = compute_backoff_delay(policy, 3)
    d4 = compute_backoff_delay(policy, 4)
    assert 10.0 <= d2 < 11.0
    assert 20.0 <= d3 < 21.0
    assert 40.0 <= d4 < 41.0


# --------------------------------------------------------------------------- #
# idempotency
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_duplicate_scheduled_fire_returns_same_run(jobs_db, tmp_path) -> None:
    """Two ``ScheduledTrigger``s with the same fired_at → second call resolves
    to the same JobRun row (UNIQUE constraint catches the duplicate)."""
    job = _make_job(tmp_path, """
[[jobs]]
id = "scheduled"

[[jobs.steps]]
type = "sql_query"
name = "one"
connector = "c"
query = "q"
""")
    fire_at = datetime(2026, 5, 22, 2, 30, tzinfo=timezone.utc)

    executor = RecordingExecutor([StepResult(rows_affected=1)])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: executor}, sleep=_noop_sleep)

    run1 = await runner.run(job, ScheduledTrigger(fired_at=fire_at))
    # Second call with the same fired_at hits the UNIQUE constraint and returns
    # the existing (now SUCCEEDED) run. The executor is NOT called a second time.
    run2 = await runner.run(job, ScheduledTrigger(fired_at=fire_at))

    assert run1.id == run2.id
    assert run1.state == RunState.SUCCEEDED.value
    assert len(executor.calls) == 1  # only the first call ran the step


@pytest.mark.asyncio
async def test_manual_triggers_each_get_their_own_run(jobs_db, tmp_path) -> None:
    job = _make_job(tmp_path, """
[[jobs]]
id = "many-manuals"

[[jobs.steps]]
type = "sql_query"
name = "one"
connector = "c"
query = "q"
""")
    executor = RecordingExecutor([
        StepResult(rows_affected=1),
        StepResult(rows_affected=2),
        StepResult(rows_affected=3),
    ])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: executor}, sleep=_noop_sleep)

    runs = []
    for _ in range(3):
        runs.append(await runner.run(job, ManualTrigger(triggered_by="alice")))

    assert len({r.id for r in runs}) == 3
    assert [r.rows_affected for r in runs] == [1, 2, 3]


# --------------------------------------------------------------------------- #
# cancellation + timeout
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_step_timeout_fails_and_retries(jobs_db, tmp_path) -> None:
    """A step that exceeds ``timeout_seconds`` is failed (and retried if policy says so)."""
    job = _make_job(tmp_path, """
[[jobs]]
id = "timeout"

[jobs.retry]
attempts = 2
backoff = "fixed"
base_seconds = 0

[[jobs.steps]]
type = "sql_query"
name = "hangs"
connector = "c"
query = "q"
timeout_seconds = 1
""")
    runner = JobRunner(
        jobs_db, {StepType.SQL_QUERY: HangingExecutor()}, sleep=_noop_sleep,
    )
    run = await runner.run(job, ManualTrigger(triggered_by="x"))

    assert run.state == RunState.FAILED.value
    async with jobs_db.session() as s:
        rows = (await s.execute(select(StepRun))).scalars().all()
    # Two attempts, both timed out
    assert len(rows) == 2
    assert all("timed out" in (r.error_message or "") for r in rows)


@pytest.mark.asyncio
async def test_unknown_step_type_fails_with_clear_message(jobs_db, tmp_path) -> None:
    job = _make_job(tmp_path, """
[[jobs]]
id = "no-executor"

[[jobs.steps]]
type = "http"
name = "fetch"
url = "https://example.test/ping"
""")
    # No HTTP executor registered → runner can't find one.
    runner = JobRunner(jobs_db, {}, sleep=_noop_sleep)
    run = await runner.run(job, ManualTrigger(triggered_by="x"))

    assert run.state == RunState.FAILED.value
    async with jobs_db.session() as s:
        rows = (await s.execute(select(StepRun))).scalars().all()
    assert len(rows) == 1
    assert "no executor" in (rows[0].error_message or "").lower()


@pytest.mark.asyncio
async def test_asyncio_cancellation_propagates_and_marks_canceled(jobs_db, tmp_path) -> None:
    """If the caller cancels the runner's task, the in-flight run is marked
    CANCELED and the CancelledError propagates (so the caller sees it too)."""
    job = _make_job(tmp_path, """
[[jobs]]
id = "to-cancel"

[[jobs.steps]]
type = "sql_query"
name = "slow"
connector = "c"
query = "q"
""")
    started = asyncio.Event()

    class SignalStartThenWait:
        async def execute(self, step, ctx):
            started.set()
            await asyncio.sleep(60)
            return StepResult()

    runner = JobRunner(
        jobs_db, {StepType.SQL_QUERY: SignalStartThenWait()}, sleep=_noop_sleep,
    )
    task = asyncio.create_task(runner.run(job, ManualTrigger(triggered_by="x")))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The JobRun row should be CANCELED
    async with jobs_db.session() as s:
        from liberty.jobs.models import JobRun
        runs = (await s.execute(select(JobRun))).scalars().all()
    assert len(runs) == 1 and runs[0].state == RunState.CANCELED.value


# --------------------------------------------------------------------------- #
# per-step enable (jobs.toml `enabled = false` + per-fire `step_enabled` override)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_disabled_step_in_jobs_toml_is_skipped_and_downstream_steps_run(
    jobs_db, tmp_path,
) -> None:
    """A step with ``enabled = false`` in jobs.toml is recorded as CANCELED
    (``error_message = 'skipped: disabled'``) — the runner walks past it and
    keeps executing the next steps. The run as a whole still SUCCEEDS."""
    job = _make_job(tmp_path, """
[[jobs]]
id = "with-disabled"

[[jobs.steps]]
type = "sql_query"
name = "first"
connector = "c"
query = "q1"

[[jobs.steps]]
type = "sql_query"
name = "skipped-one"
connector = "c"
query = "q2"
enabled = false

[[jobs.steps]]
type = "sql_query"
name = "third"
connector = "c"
query = "q3"
""")
    executor = RecordingExecutor([
        StepResult(rows_affected=10),
        StepResult(rows_affected=30),
    ])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: executor}, sleep=_noop_sleep)

    run = await runner.run(job, ManualTrigger(triggered_by="alice"))

    assert run.state == RunState.SUCCEEDED.value
    # Disabled step was never called — only first + third.
    assert executor.calls == [("first", 1), ("third", 1)]
    # Total rows: 10 + 30 — the disabled step contributes nothing.
    assert run.rows_affected == 40

    async with jobs_db.session() as s:
        rows = (await s.execute(select(StepRun).order_by(StepRun.step_index))).scalars().all()
    assert [r.step_name for r in rows] == ["first", "skipped-one", "third"]
    states = [r.state for r in rows]
    assert states == [
        RunState.SUCCEEDED.value,
        RunState.CANCELED.value,  # disabled
        RunState.SUCCEEDED.value,
    ]
    assert rows[1].error_message == "skipped: disabled"


@pytest.mark.asyncio
async def test_per_fire_step_enabled_override_can_disable_an_enabled_step(
    jobs_db, tmp_path,
) -> None:
    """Per-fire ``step_enabled = {step_name: False}`` wins over the saved
    ``enabled = true`` — used by the Run-with-parameters modal toggle."""
    job = _make_job(tmp_path, """
[[jobs]]
id = "override-off"

[[jobs.steps]]
type = "sql_query"
name = "a"
connector = "c"
query = "q1"

[[jobs.steps]]
type = "sql_query"
name = "b"
connector = "c"
query = "q2"
""")
    executor = RecordingExecutor([StepResult(rows_affected=1)])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: executor}, sleep=_noop_sleep)

    trigger = ManualTrigger(triggered_by="alice")
    run = await runner.create_run(job, trigger)
    run = await runner.execute_run(
        job, trigger, run,
        step_enabled_overrides={"b": False},
    )

    assert run.state == RunState.SUCCEEDED.value
    assert executor.calls == [("a", 1)]  # b was disabled per-fire

    async with jobs_db.session() as s:
        rows = (await s.execute(select(StepRun).order_by(StepRun.step_index))).scalars().all()
    assert [r.state for r in rows] == [RunState.SUCCEEDED.value, RunState.CANCELED.value]
    assert rows[1].error_message == "skipped: disabled"


@pytest.mark.asyncio
async def test_per_fire_step_enabled_override_can_re_enable_a_disabled_step(
    jobs_db, tmp_path,
) -> None:
    """The reverse case — jobs.toml has ``enabled = false`` but the per-fire
    override flips it back on. Lets operators run a normally-off maintenance
    step ad-hoc without editing the TOML."""
    job = _make_job(tmp_path, """
[[jobs]]
id = "override-on"

[[jobs.steps]]
type = "sql_query"
name = "normally-off"
connector = "c"
query = "q1"
enabled = false
""")
    executor = RecordingExecutor([StepResult(rows_affected=99)])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: executor}, sleep=_noop_sleep)

    trigger = ManualTrigger(triggered_by="alice")
    run = await runner.create_run(job, trigger)
    run = await runner.execute_run(
        job, trigger, run,
        step_enabled_overrides={"normally-off": True},
    )

    assert run.state == RunState.SUCCEEDED.value
    assert executor.calls == [("normally-off", 1)]
    assert run.rows_affected == 99
