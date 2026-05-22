"""Tests for :mod:`liberty.jobs.recovery` — the orphan-run sweep.

The §6 startup-recovery contract: every RUNNING or QUEUED row left over from
a crashed previous process gets marked FAILED with a clear "abandoned" reason
so it doesn't block the UNIQUE (job_id, scheduled_at) dedup or sit forever
in the Screen.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.jobs import (
    JobDatabase,
    JobRun,
    RunState,
    StepRun,
    TriggerKind,
    list_orphan_run_ids,
    mark_orphan_runs_failed,
)
from liberty.jobs.recovery import orphan_states


@pytest_asyncio.fixture
async def jobs_db(tmp_path):
    pools = PoolRegistry(
        {"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")}
    )
    db = JobDatabase(pools, "default")
    await db.create_schema()
    yield db
    await pools.dispose()


async def _seed_runs(db: JobDatabase, runs: list[tuple[str, str]]) -> list[str]:
    """Insert (job_id, state) pairs; return the generated ids in insertion order."""
    fire_offset = 0
    ids: list[str] = []
    async with db.session() as s:
        for job_id, state in runs:
            scheduled = datetime(2026, 5, 22, 1, fire_offset, tzinfo=timezone.utc)
            fire_offset += 1
            run = JobRun(
                job_id=job_id,
                trigger_kind=TriggerKind.SCHEDULED.value,
                scheduled_at=scheduled,
                state=state,
            )
            s.add(run)
            await s.flush()
            ids.append(run.id)
    return ids


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sweep_marks_running_and_queued_runs_failed(jobs_db) -> None:
    ids = await _seed_runs(jobs_db, [
        ("a", RunState.RUNNING.value),
        ("b", RunState.QUEUED.value),
        ("c", RunState.SUCCEEDED.value),  # untouched
        ("d", RunState.FAILED.value),     # untouched
        ("e", RunState.CANCELED.value),   # untouched
    ])

    job_count, step_count = await mark_orphan_runs_failed(jobs_db)
    assert job_count == 2
    assert step_count == 0

    async with jobs_db.session() as s:
        states = {(await s.get(JobRun, rid)).state for rid in ids}
    # The two orphans (a, b) are now FAILED; the other three are unchanged
    assert states == {RunState.FAILED.value, RunState.SUCCEEDED.value, RunState.CANCELED.value}


@pytest.mark.asyncio
async def test_sweep_records_reason_for_audit_trail(jobs_db) -> None:
    """The Screen surfaces error_message — make sure it's clear what happened."""
    [rid] = await _seed_runs(jobs_db, [("x", RunState.RUNNING.value)])
    await mark_orphan_runs_failed(jobs_db)
    async with jobs_db.session() as s:
        row = await s.get(JobRun, rid)
    assert row.error_message and "abandoned" in row.error_message.lower()
    assert row.finished_at is not None
    # NB: SQLite strips tzinfo on DateTime read-back even when stored with UTC;
    # Postgres timestamptz preserves it. The recovery code always writes a tz-aware
    # value; the assertion here just confirms it was written (not the tz round-trip).


@pytest.mark.asyncio
async def test_sweep_marks_orphan_step_rows_too(jobs_db) -> None:
    """An interrupted run leaves the parent JobRun ``RUNNING`` AND its
    in-flight StepRun ``RUNNING``. Both must be swept — otherwise the Screen's
    step-list view still shows the broken step as live."""
    async with jobs_db.session() as s:
        run = JobRun(
            job_id="parent",
            trigger_kind=TriggerKind.MANUAL.value,
            state=RunState.RUNNING.value,
        )
        run.step_runs = [
            StepRun(step_index=0, step_name="done", step_type="sql_query",
                    state=RunState.SUCCEEDED.value),
            StepRun(step_index=1, step_name="orphaned", step_type="sql_query",
                    state=RunState.RUNNING.value),
            StepRun(step_index=2, step_name="queued-too", step_type="sql_query",
                    state=RunState.QUEUED.value),
        ]
        s.add(run)
        await s.flush()
        run_id = run.id

    job_count, step_count = await mark_orphan_runs_failed(jobs_db)
    assert job_count == 1
    assert step_count == 2

    async with jobs_db.session() as s:
        rows = (await s.execute(
            select(StepRun).where(StepRun.run_id == run_id).order_by(StepRun.step_index)
        )).scalars().all()
    assert [r.state for r in rows] == [
        RunState.SUCCEEDED.value,
        RunState.FAILED.value,
        RunState.FAILED.value,
    ]
    # Recovery reason populated on the swept rows, left alone on the terminal one
    assert rows[0].error_message is None
    assert all("abandoned" in (r.error_message or "").lower() for r in rows[1:])


# --------------------------------------------------------------------------- #
# idempotency
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sweep_with_no_orphans_returns_zero(jobs_db) -> None:
    await _seed_runs(jobs_db, [
        ("a", RunState.SUCCEEDED.value),
        ("b", RunState.FAILED.value),
    ])
    job_count, step_count = await mark_orphan_runs_failed(jobs_db)
    assert job_count == 0
    assert step_count == 0


@pytest.mark.asyncio
async def test_sweep_is_idempotent(jobs_db) -> None:
    """Second call must be a no-op — same result every time the runtime calls it."""
    await _seed_runs(jobs_db, [("a", RunState.RUNNING.value)])
    first = await mark_orphan_runs_failed(jobs_db)
    second = await mark_orphan_runs_failed(jobs_db)
    assert first == (1, 0)
    assert second == (0, 0)


# --------------------------------------------------------------------------- #
# diagnostic helper
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_orphan_run_ids_returns_only_orphans(jobs_db) -> None:
    ids = await _seed_runs(jobs_db, [
        ("a", RunState.RUNNING.value),
        ("b", RunState.SUCCEEDED.value),
        ("c", RunState.QUEUED.value),
    ])
    orphans = await list_orphan_run_ids(jobs_db)
    assert set(orphans) == {ids[0], ids[2]}


def test_orphan_states_covers_in_flight_only() -> None:
    """Regression guard: if we ever add a new state to RunState, this test forces
    a conscious decision about whether it's "in flight" or terminal."""
    assert set(orphan_states()) == {RunState.RUNNING.value, RunState.QUEUED.value}
