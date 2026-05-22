"""Tests for the nomaflow ORM models + JobDatabase (Phase 13a foundation).

Mirrors the auth_db fixture pattern: in-memory SQLite via aiosqlite + tmp_path,
inline ``pytest_asyncio.fixture``, no conftest. Verifies the two tables get
created, persist a run + its step rows, enforce the dedup constraint, and
cascade-delete step rows when the parent run is removed.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.jobs.db import JobDatabase
from liberty.jobs.models import JobRun, RunState, StepRun, TriggerKind


@pytest_asyncio.fixture
async def jobs_db(tmp_path):
    pools = PoolRegistry(
        {"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")}
    )
    db = JobDatabase(pools, "default")
    await db.create_schema()
    yield db
    await pools.dispose()


@pytest.mark.asyncio
async def test_create_schema_is_idempotent(jobs_db: JobDatabase) -> None:
    """``init-db`` may run multiple times against the same DB; create_schema must not error."""
    await jobs_db.create_schema()  # already called once in the fixture
    await jobs_db.create_schema()  # third time also fine


@pytest.mark.asyncio
async def test_persist_run_with_steps_round_trip(jobs_db: JobDatabase) -> None:
    async with jobs_db.session() as s:
        run = JobRun(
            job_id="ping",
            trigger_kind=TriggerKind.MANUAL.value,
            triggered_by="alice",
            state=RunState.RUNNING.value,
        )
        run.step_runs = [
            StepRun(step_index=0, step_name="warmup", step_type="sql_query",
                    state=RunState.SUCCEEDED.value, rows_affected=1),
            StepRun(step_index=1, step_name="main", step_type="sql_query",
                    state=RunState.RUNNING.value),
        ]
        s.add(run)
    # Fresh session — verify persistence + the relationship round-trips
    async with jobs_db.session() as s:
        loaded = (await s.execute(select(JobRun).where(JobRun.job_id == "ping"))).scalar_one()
        assert loaded.triggered_by == "alice"
        assert loaded.state == RunState.RUNNING.value
        assert len(loaded.step_runs) == 2
        assert [sr.step_name for sr in loaded.step_runs] == ["warmup", "main"]
        assert loaded.step_runs[0].rows_affected == 1


@pytest.mark.asyncio
async def test_dedup_unique_job_id_scheduled_at(jobs_db: JobDatabase) -> None:
    """The ``UNIQUE (job_id, scheduled_at)`` constraint blocks duplicate scheduled
    fires — central to ``JobRunner.run``'s idempotency guarantee (PHASE13.md §4)."""
    from datetime import datetime, timezone
    fire_at = datetime(2026, 5, 22, 2, 30, tzinfo=timezone.utc)
    async with jobs_db.session() as s:
        s.add(JobRun(job_id="dup", trigger_kind=TriggerKind.SCHEDULED.value,
                     scheduled_at=fire_at, state=RunState.QUEUED.value))
    with pytest.raises(IntegrityError):
        async with jobs_db.session() as s:
            s.add(JobRun(job_id="dup", trigger_kind=TriggerKind.SCHEDULED.value,
                         scheduled_at=fire_at, state=RunState.QUEUED.value))


@pytest.mark.asyncio
async def test_manual_triggers_can_coexist_with_same_job_id(jobs_db: JobDatabase) -> None:
    """Manual triggers carry NULL scheduled_at; SQLite (like Postgres) treats NULLs as
    distinct in unique indexes, so an operator can click "Run now" repeatedly."""
    async with jobs_db.session() as s:
        for _ in range(3):
            s.add(JobRun(job_id="manual", trigger_kind=TriggerKind.MANUAL.value,
                         triggered_by="alice", state=RunState.QUEUED.value))
    async with jobs_db.session() as s:
        runs = (await s.execute(select(JobRun).where(JobRun.job_id == "manual"))).scalars().all()
        assert len(runs) == 3


@pytest.mark.asyncio
async def test_step_runs_cascade_delete_with_parent(jobs_db: JobDatabase) -> None:
    async with jobs_db.session() as s:
        run = JobRun(job_id="cleanup", trigger_kind=TriggerKind.MANUAL.value,
                     state=RunState.SUCCEEDED.value)
        run.step_runs = [
            StepRun(step_index=i, step_name=f"s{i}", step_type="sql_query",
                    state=RunState.SUCCEEDED.value) for i in range(3)
        ]
        s.add(run)

    async with jobs_db.session() as s:
        run = (await s.execute(select(JobRun).where(JobRun.job_id == "cleanup"))).scalar_one()
        await s.delete(run)

    async with jobs_db.session() as s:
        remaining = (await s.execute(select(StepRun))).scalars().all()
        assert remaining == []
