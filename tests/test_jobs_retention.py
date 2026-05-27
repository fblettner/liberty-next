"""Tests for :mod:`liberty.jobs.retention` — the run-history cleanup sweep.

Three things to assert:

* The two rules (age cutoff + per-job cap) DELETE the right rows and leave the
  rest alone.
* Cascade kicks in — child rows in StepRun / RunLog / RunOverrides /
  StepRunExtras disappear with their parent JobRun (verifies the FK ON DELETE
  CASCADE is honoured, and that SQLite's per-connection ``PRAGMA foreign_keys=ON``
  is actually in effect via the connect-event listener in connectors/db.py).
* In-flight rows (RUNNING / QUEUED) are NEVER touched regardless of age — these
  reflect ACTIVE work and a sweep that deleted them would be data loss.

The fixture spins up a real SQLite JobDatabase via the same PoolRegistry the
production app uses, so the cascade machinery exercises the real engine path
rather than a mock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
)
from liberty.jobs.models import RunLog, RunOverrides, StepRunExtras
from liberty.jobs.retention import _ids_beyond_cap, sweep_run_history
from liberty.jobs.schema import RetentionPolicy


# --------------------------------------------------------------------------- #
# fixtures + helpers
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def jobs_db(tmp_path):
    pools = PoolRegistry(
        {"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'retention.db'}")}
    )
    db = JobDatabase(pools, "default")
    await db.create_schema()
    yield db
    await pools.dispose()


# A fixed "now" so the cutoff math is testable without freezing time. All test
# rows date themselves relative to this so we can assert "this one is N days
# old" deterministically.
_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


async def _seed_run(
    db: JobDatabase, *, job_id: str, state: str, finished_days_ago: float | None,
    scheduled_offset_seconds: int = 0,
) -> str:
    """Insert a JobRun with the given (state, age) shape; return its id.

    ``finished_days_ago = None`` means the row has finished_at = NULL (e.g. an
    in-flight run that's still running). Otherwise the row gets a finished_at of
    ``_NOW - timedelta(days=N)``. ``scheduled_offset_seconds`` lets the caller
    bypass the UNIQUE (job_id, scheduled_at) constraint when seeding multiple
    rows for the same job (each call bumps a per-test counter externally)."""
    finished = _NOW - timedelta(days=finished_days_ago) if finished_days_ago is not None else None
    scheduled = (_NOW - timedelta(days=10) + timedelta(seconds=scheduled_offset_seconds)) if finished_days_ago is not None else None
    async with db.session() as s:
        run = JobRun(
            job_id=job_id,
            trigger_kind=TriggerKind.SCHEDULED.value,
            scheduled_at=scheduled,
            state=state,
            finished_at=finished,
        )
        s.add(run)
        await s.flush()
        return run.id


# --------------------------------------------------------------------------- #
# rule 1 — age cutoff
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_deletes_finished_runs_older_than_cutoff(jobs_db):
    """The age rule removes finished rows past the cutoff; recent ones survive."""
    old = await _seed_run(jobs_db, job_id="a", state=RunState.SUCCEEDED.value,
                          finished_days_ago=45, scheduled_offset_seconds=1)
    recent = await _seed_run(jobs_db, job_id="a", state=RunState.SUCCEEDED.value,
                             finished_days_ago=5, scheduled_offset_seconds=2)

    report = await sweep_run_history(
        jobs_db, RetentionPolicy(days=30, keep_last_per_job=999), now=_NOW,
    )

    assert report.deleted_by_age == 1
    assert report.deleted_by_cap == 0
    async with jobs_db.session() as s:
        survivors = [r[0] for r in (await s.execute(select(JobRun.id))).all()]
    assert survivors == [recent]
    assert old not in survivors


@pytest.mark.asyncio
async def test_age_cutoff_covers_all_terminal_states(jobs_db):
    """SUCCEEDED, FAILED, and CANCELED all qualify for age-based deletion."""
    s_id = await _seed_run(jobs_db, job_id="a", state=RunState.SUCCEEDED.value,
                           finished_days_ago=60, scheduled_offset_seconds=1)
    f_id = await _seed_run(jobs_db, job_id="b", state=RunState.FAILED.value,
                           finished_days_ago=60, scheduled_offset_seconds=2)
    c_id = await _seed_run(jobs_db, job_id="c", state=RunState.CANCELED.value,
                           finished_days_ago=60, scheduled_offset_seconds=3)

    report = await sweep_run_history(
        jobs_db, RetentionPolicy(days=30, keep_last_per_job=999), now=_NOW,
    )
    assert report.deleted_by_age == 3
    async with jobs_db.session() as s:
        survivors = {r[0] for r in (await s.execute(select(JobRun.id))).all()}
    assert {s_id, f_id, c_id}.isdisjoint(survivors)


@pytest.mark.asyncio
async def test_in_flight_rows_never_swept_regardless_of_age(jobs_db):
    """A RUNNING / QUEUED row with an ancient scheduled_at (someone parked it long
    ago, never started) MUST NOT be deleted — that's an active commitment."""
    # Both rows have NULL finished_at since they never completed; scheduled long ago.
    running = await _seed_run(jobs_db, job_id="a", state=RunState.RUNNING.value,
                              finished_days_ago=None, scheduled_offset_seconds=1)
    queued = await _seed_run(jobs_db, job_id="b", state=RunState.QUEUED.value,
                             finished_days_ago=None, scheduled_offset_seconds=2)

    report = await sweep_run_history(
        jobs_db, RetentionPolicy(days=1, keep_last_per_job=1), now=_NOW,
    )
    assert report.total_deleted == 0
    async with jobs_db.session() as s:
        survivors = {r[0] for r in (await s.execute(select(JobRun.id))).all()}
    assert survivors == {running, queued}


# --------------------------------------------------------------------------- #
# rule 2 — per-job cap
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_per_job_cap_keeps_only_n_most_recent(jobs_db):
    """Five SUCCEEDED runs all within the age window; cap=2 keeps the two
    newest, deletes the three oldest."""
    ages = [10, 8, 6, 4, 2]                                   # days_ago, oldest → newest
    ids = []
    for i, age in enumerate(ages):
        ids.append(await _seed_run(
            jobs_db, job_id="busy", state=RunState.SUCCEEDED.value,
            finished_days_ago=age, scheduled_offset_seconds=i,
        ))

    report = await sweep_run_history(
        jobs_db, RetentionPolicy(days=30, keep_last_per_job=2), now=_NOW,
    )
    assert report.deleted_by_age == 0
    assert report.deleted_by_cap == 3
    async with jobs_db.session() as s:
        survivors = {r[0] for r in (await s.execute(select(JobRun.id))).all()}
    assert survivors == {ids[3], ids[4]}                       # the two newest


@pytest.mark.asyncio
async def test_per_job_cap_independent_per_job(jobs_db):
    """Each job_id gets its own cap window — a job with 5 runs and another with 2
    are trimmed independently (the 2-run job loses nothing)."""
    busy_ids = []
    for i, age in enumerate([10, 8, 6, 4, 2]):
        busy_ids.append(await _seed_run(
            jobs_db, job_id="busy", state=RunState.SUCCEEDED.value,
            finished_days_ago=age, scheduled_offset_seconds=i,
        ))
    quiet_ids = []
    for i, age in enumerate([5, 3]):
        quiet_ids.append(await _seed_run(
            jobs_db, job_id="quiet", state=RunState.SUCCEEDED.value,
            finished_days_ago=age, scheduled_offset_seconds=100 + i,
        ))

    report = await sweep_run_history(
        jobs_db, RetentionPolicy(days=30, keep_last_per_job=2), now=_NOW,
    )
    assert report.deleted_by_cap == 3                           # only busy's 3 oldest
    async with jobs_db.session() as s:
        survivors = {r[0] for r in (await s.execute(select(JobRun.id))).all()}
    assert quiet_ids[0] in survivors and quiet_ids[1] in survivors
    assert survivors == {busy_ids[3], busy_ids[4], *quiet_ids}


@pytest.mark.asyncio
async def test_age_and_cap_dont_double_count(jobs_db):
    """A row qualifying under BOTH rules counts under deleted_by_age (the rule
    applied first), not twice."""
    # All ancient and over the cap — under a 30-day / 2-per-job policy, all 4 die
    # but the deletion is attributed to age (the first pass).
    for i, age in enumerate([100, 90, 80, 70]):
        await _seed_run(jobs_db, job_id="z", state=RunState.SUCCEEDED.value,
                        finished_days_ago=age, scheduled_offset_seconds=i)

    report = await sweep_run_history(
        jobs_db, RetentionPolicy(days=30, keep_last_per_job=2), now=_NOW,
    )
    assert report.deleted_by_age == 4
    assert report.deleted_by_cap == 0
    assert report.total_deleted == 4


# --------------------------------------------------------------------------- #
# cascade — child rows go with their parent
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cascade_deletes_step_runs_and_aux_tables(jobs_db):
    """Verify the FK ON DELETE CASCADE is honoured by SQLite (via the connect-event
    PRAGMA in connectors/db.py) — deleting the JobRun must also remove its
    StepRun, RunLog, RunOverrides, and StepRunExtras rows."""
    # One old run with the full child-table tree populated.
    async with jobs_db.session() as s:
        run = JobRun(
            job_id="parent",
            trigger_kind=TriggerKind.MANUAL.value,
            scheduled_at=None,
            state=RunState.SUCCEEDED.value,
            finished_at=_NOW - timedelta(days=60),
        )
        s.add(run)
        await s.flush()
        run_id = run.id
        step = StepRun(
            run_id=run_id, step_index=0, step_name="s", step_type="sql_query",
            state=RunState.SUCCEEDED.value,
        )
        s.add(step)
        s.add(RunLog(run_id=run_id, logs="ran fine"))
        s.add(RunOverrides(run_id=run_id, overrides_json='{"log_level":"DEBUG"}'))
        await s.flush()
        s.add(StepRunExtras(step_run_id=step.id, extras_json='{"k":"v"}'))

    # Sanity — all rows are present pre-sweep.
    async with jobs_db.session() as s:
        assert (await s.execute(select(StepRun))).first() is not None
        assert (await s.execute(select(RunLog))).first() is not None
        assert (await s.execute(select(RunOverrides))).first() is not None
        assert (await s.execute(select(StepRunExtras))).first() is not None

    report = await sweep_run_history(
        jobs_db, RetentionPolicy(days=30, keep_last_per_job=999), now=_NOW,
    )
    assert report.deleted_by_age == 1

    # All child rows are gone too — cascade fired.
    async with jobs_db.session() as s:
        assert (await s.execute(select(StepRun))).first() is None
        assert (await s.execute(select(RunLog))).first() is None
        assert (await s.execute(select(RunOverrides))).first() is None
        assert (await s.execute(select(StepRunExtras))).first() is None


# --------------------------------------------------------------------------- #
# degenerate cases + report shape
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_empty_db_returns_zero(jobs_db):
    """Sweep against an empty DB does nothing and reports zero — no crash."""
    report = await sweep_run_history(jobs_db, RetentionPolicy(), now=_NOW)
    assert report.total_deleted == 0


@pytest.mark.asyncio
async def test_report_serialises_to_dict(jobs_db):
    """SweepReport.to_dict carries every field the admin endpoint needs to render."""
    await _seed_run(jobs_db, job_id="a", state=RunState.SUCCEEDED.value,
                    finished_days_ago=60, scheduled_offset_seconds=1)
    report = await sweep_run_history(jobs_db, RetentionPolicy(days=30), now=_NOW)
    payload = report.to_dict()
    assert set(payload) == {"swept_at", "cutoff", "keep_last_per_job",
                            "deleted_by_age", "deleted_by_cap", "total_deleted"}
    assert payload["deleted_by_age"] == 1
    assert payload["total_deleted"] == 1
    assert payload["cutoff"].startswith("2026-05-02")          # _NOW - 30 days


# --------------------------------------------------------------------------- #
# pure-function helper — _ids_beyond_cap
# --------------------------------------------------------------------------- #


def test_ids_beyond_cap_returns_oldest_per_job():
    """The helper bucketing job → keep-newest-N is deterministic + per-job."""
    rows = [
        ("r1", "a", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ("r2", "a", datetime(2026, 1, 2, tzinfo=timezone.utc)),
        ("r3", "a", datetime(2026, 1, 3, tzinfo=timezone.utc)),
        ("r4", "b", datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ]
    # Cap=2: a's oldest (r1) goes; b's only entry stays.
    assert set(_ids_beyond_cap(rows, 2)) == {"r1"}
    # Cap=1: a's two oldest (r1, r2) go; b is fine with one.
    assert set(_ids_beyond_cap(rows, 1)) == {"r1", "r2"}
    # Cap >= max bucket size: nothing to delete.
    assert _ids_beyond_cap(rows, 10) == []


def test_ids_beyond_cap_treats_null_finished_as_oldest():
    """A row with no finished_at gets sentinel datetime.min so it falls past the
    cap rather than confusing the sort with None."""
    rows = [
        ("r1", "a", None),                                          # treated as oldest
        ("r2", "a", datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ]
    assert _ids_beyond_cap(rows, 1) == ["r1"]
