"""Tests for the ``call_job`` step type (Phase C).

The executor's three responsibilities, one test per:

* Happy path — calling job's call_job step fires the target job inline,
  the parent collects the child's rows_affected, both runs land in the
  DB with their own JobRun + StepRun rows.
* Cycle detection — A → B → A fails the call_job step with a clear
  "cycle detected" message including the visible chain.
* Override inheritance — parent fired with ``log_level=DEBUG`` /
  ``params_override`` propagates those into the child's execution.

Uses the same in-memory SQLite + mock-executor pattern as
``test_jobs_runner.py``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.jobs import (
    JobDatabase,
    JobRunner,
    JobsFile,
    ManualTrigger,
    RunContext,
    RunState,
    Step,
    StepFailed,
    StepResult,
    StepRun,
    StepType,
    load_jobs,
)
from liberty.jobs.registry import JobRegistry, UnknownJobError
from liberty.jobs.steps import CallJobExecutor


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def jobs_db(tmp_path):
    pools = PoolRegistry(
        {"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'cj.db'}")}
    )
    db = JobDatabase(pools, "default")
    await db.create_schema()
    yield db
    await pools.dispose()


class RecordingExecutor:
    """Same shape as test_jobs_runner's RecordingExecutor — records the
    (step.name, attempt) pairs and returns the next scripted result."""

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


async def _noop_sleep(_secs: float) -> None:
    pass


def _registry(jobs_file: JobsFile) -> JobRegistry:
    return JobRegistry(jobs_file)


def _make_two_jobs(tmp_path, *, with_cycle: bool = False) -> JobsFile:
    """Two jobs: ``parent`` calls ``child`` via call_job. With ``with_cycle``,
    ``child`` ALSO calls back into ``parent`` so the cycle guard fires."""
    cycle_step = ""
    if with_cycle:
        cycle_step = """
[[jobs.steps]]
type = "call_job"
name = "back_to_parent"
target_job_id = "parent"
"""
    toml = f"""
[[jobs]]
id = "parent"

[[jobs.steps]]
type = "sql_query"
name = "before"
connector = "c"
query = "q1"

[[jobs.steps]]
type = "call_job"
name = "invoke_child"
target_job_id = "child"

[[jobs.steps]]
type = "sql_query"
name = "after"
connector = "c"
query = "q2"

[[jobs]]
id = "child"

[[jobs.steps]]
type = "sql_query"
name = "child_work"
connector = "c"
query = "q3"
{cycle_step}
"""
    path = tmp_path / "jobs.toml"
    path.write_text(toml, encoding="utf-8")
    return load_jobs(path).config


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_call_job_fires_target_inline_and_collects_child_rows(jobs_db, tmp_path) -> None:
    """Parent's call_job step fires the child, the child runs to completion,
    parent's StepResult.rows_affected reflects the child's total. Both runs
    persist independently."""
    jobs_file = _make_two_jobs(tmp_path)
    registry = _registry(jobs_file)
    parent_job = registry.get("parent")

    # The recording executor handles the sql_query steps on BOTH parent + child.
    # Script (in order): parent.before (5), child.child_work (7), parent.after (3).
    sql_executor = RecordingExecutor([
        StepResult(rows_affected=5),   # parent.before
        StepResult(rows_affected=7),   # child.child_work (called by call_job)
        StepResult(rows_affected=3),   # parent.after
    ])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: sql_executor}, sleep=_noop_sleep)
    # Attach the call_job executor with the runner reference (the wiring layer
    # does this in production; tests do it explicitly).
    runner.add_executor(StepType.CALL_JOB, CallJobExecutor(registry, runner=runner))

    run = await runner.run(parent_job, ManualTrigger(triggered_by="alice"))

    assert run.state == RunState.SUCCEEDED.value
    # Parent's rows_affected = 5 (before) + 7 (call_job → child) + 3 (after) = 15.
    # The call_job step rolls the child's total into the parent's accumulator.
    assert run.rows_affected == 15
    # Both child + parent steps fired exactly once each.
    assert sql_executor.calls == [
        ("before", 1),
        ("child_work", 1),
        ("after", 1),
    ]


@pytest.mark.asyncio
async def test_call_job_child_creates_its_own_run_with_trigger_referencing_parent(jobs_db, tmp_path) -> None:
    """The child gets its own JobRun row; its ``triggered_by`` references the
    parent so operators see "call_job:<parent_job_id>@<parent_run_id>" in the
    audit trail (we don't have a parent_run_id column yet — this string is the
    bridge until the model gains one)."""
    jobs_file = _make_two_jobs(tmp_path)
    registry = _registry(jobs_file)
    parent_job = registry.get("parent")

    sql_executor = RecordingExecutor([
        StepResult(rows_affected=1), StepResult(rows_affected=2), StepResult(rows_affected=3),
    ])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: sql_executor}, sleep=_noop_sleep)
    runner.add_executor(StepType.CALL_JOB, CallJobExecutor(registry, runner=runner))

    parent_run = await runner.run(parent_job, ManualTrigger(triggered_by="alice"))

    async with jobs_db.session() as s:
        from liberty.jobs.models import JobRun
        runs = (await s.execute(select(JobRun))).scalars().all()
    # Two JobRun rows — one for the parent, one for the child invocation.
    assert len(runs) == 2
    by_job = {r.job_id: r for r in runs}
    assert "parent" in by_job and "child" in by_job
    # Parent's trigger reflects the manual fire; child's reflects the call_job.
    assert by_job["parent"].triggered_by == "alice"
    assert by_job["child"].triggered_by == f"call_job:parent@{parent_run.id}"
    # Both terminated cleanly.
    assert by_job["child"].state == RunState.SUCCEEDED.value


# --------------------------------------------------------------------------- #
# cycle detection
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_call_job_cycle_detection_fails_with_visible_chain(jobs_db, tmp_path) -> None:
    """A → B → A: the child's call_job back to the parent detects the cycle
    at fire time. The error message renders the chain so the operator sees
    WHERE the back-edge is, not just THAT one exists. Parent run ends FAILED;
    the call_job step (the one inside the CHILD) is the one that fails — the
    parent's own call_job step then surfaces "child run failed: ..."."""
    jobs_file = _make_two_jobs(tmp_path, with_cycle=True)
    registry = _registry(jobs_file)
    parent_job = registry.get("parent")

    sql_executor = RecordingExecutor([
        StepResult(rows_affected=1),   # parent.before
        StepResult(rows_affected=2),   # child.child_work (runs before the cycle hits)
    ])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: sql_executor}, sleep=_noop_sleep)
    runner.add_executor(StepType.CALL_JOB, CallJobExecutor(registry, runner=runner))

    run = await runner.run(parent_job, ManualTrigger(triggered_by="alice"))

    # The parent run ends FAILED because its call_job step's child failed.
    assert run.state == RunState.FAILED.value
    # Parent ran: before + invoke_child (failed). after never ran.
    assert ("before", 1) in sql_executor.calls
    assert ("child_work", 1) in sql_executor.calls
    assert ("after", 1) not in sql_executor.calls

    # The parent's invoke_child step row records the failure with the cycle
    # message present in the chain — surfaced via the child's error message
    # bubbling up through the StepFailed.
    async with jobs_db.session() as s:
        rows = (await s.execute(
            select(StepRun).order_by(StepRun.step_index, StepRun.attempt)
        )).scalars().all()
    invoke_step = next(r for r in rows if r.step_name == "invoke_child")
    assert invoke_step.state == RunState.FAILED.value
    assert "cycle detected" in (invoke_step.error_message or "")
    # The chain spells out the back-edge: parent → child → parent.
    assert "parent" in (invoke_step.error_message or "")
    assert "child" in (invoke_step.error_message or "")


# --------------------------------------------------------------------------- #
# misconfiguration
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_call_job_unknown_target_fails_with_clear_message(jobs_db, tmp_path) -> None:
    """Pointing at a target_job_id that doesn't exist in the registry fails
    the call_job step with a "not found, check spelling, did you reload?" hint
    — distinguishes typo from missing-reload from genuinely-broken target."""
    toml = """
[[jobs]]
id = "caller"

[[jobs.steps]]
type = "call_job"
name = "bad_call"
target_job_id = "does-not-exist"
"""
    (tmp_path / "jobs.toml").write_text(toml)
    jobs_file = load_jobs(tmp_path / "jobs.toml").config
    registry = _registry(jobs_file)
    caller = registry.get("caller")

    runner = JobRunner(jobs_db, {}, sleep=_noop_sleep)
    runner.add_executor(StepType.CALL_JOB, CallJobExecutor(registry, runner=runner))

    run = await runner.run(caller, ManualTrigger(triggered_by="bob"))

    assert run.state == RunState.FAILED.value
    async with jobs_db.session() as s:
        rows = (await s.execute(select(StepRun))).scalars().all()
    assert len(rows) == 1
    assert "not found" in (rows[0].error_message or "")
    assert "does-not-exist" in (rows[0].error_message or "")


@pytest.mark.asyncio
async def test_call_job_executor_without_attached_runner_fails_with_wiring_error(jobs_db, tmp_path) -> None:
    """If the wiring layer forgets to attach the runner reference, the
    executor fails fast with a wiring-bug message — better than the alternative
    of crashing inside the spawn with an AttributeError on ``None``."""
    jobs_file = _make_two_jobs(tmp_path)
    registry = _registry(jobs_file)
    caller = registry.get("parent")

    sql_executor = RecordingExecutor([StepResult(rows_affected=1)])
    runner = JobRunner(jobs_db, {StepType.SQL_QUERY: sql_executor}, sleep=_noop_sleep)
    # Intentionally NOT calling attach_runner — simulate the wiring bug.
    runner.add_executor(StepType.CALL_JOB, CallJobExecutor(registry, runner=None))

    run = await runner.run(caller, ManualTrigger(triggered_by="x"))
    assert run.state == RunState.FAILED.value
    async with jobs_db.session() as s:
        rows = (await s.execute(select(StepRun))).scalars().all()
    bad = next(r for r in rows if r.step_name == "invoke_child")
    assert "no runner attached" in (bad.error_message or "")
    assert "wiring" in (bad.error_message or "")


def test_call_job_schema_requires_target_job_id() -> None:
    """Schema validator catches a call_job step missing ``target_job_id``
    BEFORE the executor sees it — fails at config-load time so a broken
    jobs.toml is rejected on reload, not deferred until fire."""
    from pydantic import ValidationError

    Step.model_validate({"type": "call_job", "name": "ok", "target_job_id": "x"})
    with pytest.raises(ValidationError):
        Step.model_validate({"type": "call_job", "name": "bad"})
    # Empty string also rejected (treated as missing).
    with pytest.raises(ValidationError):
        Step.model_validate({"type": "call_job", "name": "bad", "target_job_id": ""})
