"""Missed-schedule detection (:mod:`liberty.jobs.missed`) + the scheduler's wiring of it."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.jobs import JobDatabase, JobRun, JobRunner, JobScheduler, MissedPolicy, RunState, Step, StepResult, StepType, TriggerKind, load_jobs
from liberty.jobs.missed import check_missed, expected_fires, is_covered, record_missed

UTC = timezone.utc


@pytest_asyncio.fixture
async def jobs_db(tmp_path):
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'missed.db'}")})
    db = JobDatabase(pools, "default")
    await db.create_schema()
    yield db
    await pools.dispose()


def _registry(tmp_path, toml: str):
    path = tmp_path / "jobs.toml"
    path.write_text(toml, encoding="utf-8")
    return load_jobs(path)


HOURLY = """
[[jobs]]
id = "hourly"
schedule = "0 * * * *"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
"""


async def _seed(db: JobDatabase, job_id: str, *, scheduled_at: datetime | None, started_at: datetime, kind: str = "scheduled", state: str = "SUCCEEDED") -> None:
    async with db.session() as s:
        s.add(JobRun(job_id=job_id, trigger_kind=kind, scheduled_at=scheduled_at, started_at=started_at, finished_at=started_at, state=state))


async def _rows(db: JobDatabase, job_id: str) -> list[JobRun]:
    async with db.session() as s:
        return list((await s.execute(select(JobRun).where(JobRun.job_id == job_id).order_by(JobRun.scheduled_at))).scalars())


def test_expected_fires_enumerates_the_window() -> None:
    since = datetime(2026, 10, 27, 1, 30, tzinfo=UTC)
    until = datetime(2026, 10, 27, 4, 30, tzinfo=UTC)
    fires = expected_fires("0 * * * *", "UTC", since, until)
    assert fires == [datetime(2026, 10, 27, h, tzinfo=UTC) for h in (2, 3, 4)]
    # the bounds: (since, until] — an instant equal to `since` is not re-reported
    assert expected_fires("0 * * * *", "UTC", datetime(2026, 10, 27, 2, tzinfo=UTC), datetime(2026, 10, 27, 3, tzinfo=UTC)) == [datetime(2026, 10, 27, 3, tzinfo=UTC)]
    assert expected_fires("0 * * * *", "UTC", until, since) == []
    # a timezone: 02:00 Paris on 28 October (CET, after the DST switch) is 01:00 UTC
    assert expected_fires("0 2 * * *", "Europe/Paris", since, since + timedelta(days=1)) == [datetime(2026, 10, 28, 1, tzinfo=UTC)]


def test_is_covered_rules() -> None:
    at = datetime(2026, 10, 27, 2, tzinfo=UTC)
    grace = timedelta(minutes=10)
    assert is_covered(at, [("scheduled", at + timedelta(seconds=3), at + timedelta(seconds=3))], grace)
    assert is_covered(at, [("scheduled", at - timedelta(seconds=30), at)], grace)          # clock skew
    assert not is_covered(at, [("scheduled", at + timedelta(minutes=11), at)], grace)        # too late
    assert is_covered(at, [("manual", None, at + timedelta(minutes=5))], grace)             # ran by hand
    assert not is_covered(at, [("manual", None, at - timedelta(minutes=5))], grace)         # before the instant
    assert not is_covered(at, [], grace)


@pytest.mark.asyncio
async def test_check_records_gaps_once_and_raises_the_hook(jobs_db, tmp_path) -> None:
    """Last run at 01:00, now 04:05 with a 10 min grace: 02:00 and 03:00 are missed (04:00 is
    still within the grace). Each gap is a MISSED row + one hook call; a second check with the
    watermark adds nothing, and a check from scratch (a restart) finds the rows already there."""
    registry = _registry(tmp_path, HOURLY)
    now = datetime(2026, 10, 27, 4, 5, tzinfo=UTC)
    await _seed(jobs_db, "hourly", scheduled_at=datetime(2026, 10, 27, 1, 0, 2, tzinfo=UTC), started_at=datetime(2026, 10, 27, 1, 0, 2, tzinfo=UTC))
    hooked: list[tuple[str, str, str]] = []

    async def on_missed(job, run, reason):
        hooked.append((job.id, run.scheduled_at.isoformat(), reason))

    marks: dict[str, datetime] = {}
    report = await check_missed(jobs_db, registry, MissedPolicy(), watermarks=marks, started_at=now - timedelta(days=3), now=now, on_missed=on_missed)
    assert report.triggers == 1
    assert [(m.job_id, m.expected_at) for m in report.missed] == [("hourly", datetime(2026, 10, 27, 2, tzinfo=UTC)), ("hourly", datetime(2026, 10, 27, 3, tzinfo=UTC))]
    assert all(m.run_id for m in report.missed)
    assert len(hooked) == 2 and hooked[0][0] == "hourly" and "expected at 2026-10-27T02:00" in hooked[0][2]
    rows = await _rows(jobs_db, "hourly")
    assert [r.state for r in rows] == ["SUCCEEDED", "MISSED", "MISSED"]
    assert rows[1].trigger_kind == TriggerKind.SCHEDULED.value and rows[1].finished_at is not None
    assert marks["hourly"] == now - timedelta(minutes=10)

    # same watermark, same instant: nothing new
    report2 = await check_missed(jobs_db, registry, MissedPolicy(), watermarks=marks, started_at=now, now=now, on_missed=on_missed)
    assert report2.missed == [] and len(hooked) == 2
    # a fresh process (no watermark): the window opens at the last run — a MISSED row — so
    # nothing is reported twice; later at 05:05 the 04:00 gap surfaces
    later = datetime(2026, 10, 27, 5, 5, tzinfo=UTC)
    report3 = await check_missed(jobs_db, registry, MissedPolicy(), watermarks={}, started_at=later, now=later, on_missed=on_missed)
    assert [m.expected_at for m in report3.missed] == [datetime(2026, 10, 27, 4, tzinfo=UTC)]
    assert len(hooked) == 3 and len(await _rows(jobs_db, "hourly")) == 4


@pytest.mark.asyncio
async def test_check_covered_and_bounded(jobs_db, tmp_path) -> None:
    """A scheduled run near the instant, or a manual run inside the grace, covers it; a job
    that never ran is checked from the scheduler's start only; the lookback bounds a long gap."""
    registry = _registry(tmp_path, HOURLY)
    now = datetime(2026, 10, 27, 4, 5, tzinfo=UTC)
    await _seed(jobs_db, "hourly", scheduled_at=datetime(2026, 10, 27, 1, 0, 1, tzinfo=UTC), started_at=datetime(2026, 10, 27, 1, 0, 1, tzinfo=UTC))
    await _seed(jobs_db, "hourly", scheduled_at=datetime(2026, 10, 27, 2, 0, 4, tzinfo=UTC), started_at=datetime(2026, 10, 27, 2, 0, 4, tzinfo=UTC), state="FAILED")
    await _seed(jobs_db, "hourly", scheduled_at=None, started_at=datetime(2026, 10, 27, 3, 6, tzinfo=UTC), kind="manual")
    report = await check_missed(jobs_db, registry, MissedPolicy(), watermarks={}, started_at=now - timedelta(days=1), now=now)
    assert report.missed == []

    # never ran: the window opens at the scheduler's start (10 min ago) → nothing yet
    fresh = _registry(tmp_path, HOURLY.replace('"hourly"', '"newjob"'))
    report = await check_missed(jobs_db, fresh, MissedPolicy(), watermarks={}, started_at=now - timedelta(minutes=10), now=now)
    assert report.missed == []
    # ...and from a start 3 h ago the 02:00 / 03:00 instants count
    report = await check_missed(jobs_db, fresh, MissedPolicy(), watermarks={}, started_at=now - timedelta(hours=3), now=now)
    assert [m.expected_at.hour for m in report.missed] == [2, 3]

    # a last run a week ago with lookback 2 h: only the instants inside the lookback
    old = _registry(tmp_path, HOURLY.replace('"hourly"', '"oldjob"'))
    await _seed(jobs_db, "oldjob", scheduled_at=now - timedelta(days=7), started_at=now - timedelta(days=7))
    report = await check_missed(jobs_db, old, MissedPolicy(lookback_hours=2), watermarks={}, started_at=now - timedelta(days=7), now=now)
    assert [m.expected_at.hour for m in report.missed] == [3]

    # disabled job / preset handling: only the active triggers are checked
    off = _registry(tmp_path, HOURLY.replace('schedule = "0 * * * *"', 'schedule = "0 * * * *"\nenabled = false'))
    report = await check_missed(jobs_db, off, MissedPolicy(), watermarks={}, started_at=now - timedelta(days=1), now=now)
    assert report.triggers == 0


@pytest.mark.asyncio
async def test_record_missed_dedupes_on_the_instant(jobs_db) -> None:
    at = datetime(2026, 10, 27, 2, tzinfo=UTC)
    first = await record_missed(jobs_db, "j", at, "missed")
    assert first is not None and first.state == RunState.MISSED.value
    assert await record_missed(jobs_db, "j", at, "missed again") is None
    assert len(await _rows(jobs_db, "j")) == 1


class _Hanging:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, step: Step, ctx) -> StepResult:
        self.started.set()
        await self.release.wait()
        return StepResult(rows_affected=0)


@pytest.mark.asyncio
async def test_dropped_fire_is_recorded_as_missed(jobs_db, tmp_path) -> None:
    """A concurrent fire dropped by the scheduler leaves a MISSED row and raises on_missed."""
    registry = _registry(tmp_path, HOURLY.replace('"0 * * * *"', '"*/1 * * * *"'))
    hanging = _Hanging()

    async def _noop(_s: float) -> None:
        pass

    hooked: list[str] = []

    async def on_missed(job, run, reason):
        hooked.append(reason)

    scheduler = JobScheduler(registry, JobRunner(jobs_db, {StepType.SQL_QUERY: hanging}, sleep=_noop), on_missed=on_missed)
    await scheduler.start()
    try:
        assert scheduler.scheduled_job_ids == ["hourly"]   # the check task is internal, filtered out
        task = asyncio.create_task(scheduler._run_or_drop("hourly"))
        await hanging.started.wait()
        await scheduler._run_or_drop("hourly")
        assert len(hooked) == 1 and "previous run was still running" in hooked[0]
        states = sorted(r.state for r in await _rows(jobs_db, "hourly"))
        assert states == ["MISSED", "RUNNING"]
        hanging.release.set()
        await task
        # the manual check sees the gap as covered by the MISSED row
        report = await scheduler.check_missed_now()
        assert report.missed == [] and scheduler.last_missed is report
    finally:
        await scheduler.stop()
