"""Deadline alerts (:mod:`liberty.jobs.sla`): a job with ``alerts.finish_by`` must have a
successful run by that local time on the days it is scheduled."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import pytest_asyncio

from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.jobs import JobDatabase, JobRun, load_jobs
from liberty.jobs.schema import JobAlerts
from liberty.jobs.sla import check_late, deadline_on

UTC = timezone.utc
DAY = date(2026, 10, 28)

NIGHTLY = """
[[jobs]]
id = "nightly"
schedule = "0 2 * * *"
timezone = "UTC"
[jobs.alerts]
finish_by = "07:00"
recipients = ["ops@example.com"]
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"

[[jobs]]
id = "weekly"
schedule = "0 2 * * 1"
timezone = "UTC"
[jobs.alerts]
finish_by = "07:00"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
"""


@pytest_asyncio.fixture
async def jobs_db(tmp_path):
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'sla.db'}")})
    db = JobDatabase(pools, "default")
    await db.create_schema()
    yield db
    await pools.dispose()


def _registry(tmp_path):
    path = tmp_path / "jobs.toml"
    path.write_text(NIGHTLY, encoding="utf-8")
    return load_jobs(path)


async def _run(db: JobDatabase, job_id: str, *, started: datetime, finished: datetime | None, state: str) -> None:
    async with db.session() as s:
        s.add(JobRun(job_id=job_id, trigger_kind="scheduled", scheduled_at=started, started_at=started, finished_at=finished, state=state))


def test_finish_by_format_and_deadline() -> None:
    assert JobAlerts(finish_by="07:30").finish_by == "07:30"
    for bad in ("7:30", "24:00", "07:60", "noon"):
        with pytest.raises(ValueError):
            JobAlerts(finish_by=bad)
    assert deadline_on(DAY, "07:00", "UTC") == datetime(2026, 10, 28, 7, tzinfo=UTC)
    assert deadline_on(DAY, "07:00", "Europe/Paris") == datetime(2026, 10, 28, 6, tzinfo=UTC)   # CET after the DST switch


async def test_on_time_late_and_not_scheduled(tmp_path, jobs_db) -> None:
    """28 Oct 2026 is a Wednesday: ``weekly`` (Mondays) is not expected — never judged late. The
    nightly is on time with a success before 07:00; without one it is late, once per day, with
    the reason; before the deadline nothing is judged."""
    reg = _registry(tmp_path)
    alerts: list[tuple[str, str]] = []

    async def on_late(job, reason, deadline, first):
        alerts.append((job.id, reason))
        assert deadline == datetime(2026, 10, 28, 7, tzinfo=UTC) and first == datetime(2026, 10, 28, 2, tzinfo=UTC)

    judged: set[tuple[str, str]] = set()
    rep = await check_late(jobs_db, reg, judged=judged, now=datetime(2026, 10, 28, 6, 59, tzinfo=UTC), on_late=on_late)
    assert rep.jobs == 2 and rep.late == [] and judged == set()                         # before the deadline

    rep = await check_late(jobs_db, reg, judged=judged, now=datetime(2026, 10, 28, 7, 5, tzinfo=UTC), on_late=on_late)
    assert [x.job_id for x in rep.late] == ["nightly"] and "did not start" in rep.late[0].reason
    assert alerts == [("nightly", rep.late[0].reason)]
    rep = await check_late(jobs_db, reg, judged=judged, now=datetime(2026, 10, 28, 7, 10, tzinfo=UTC), on_late=on_late)
    assert rep.late == [] and len(alerts) == 1                                           # once per day


async def test_success_before_the_deadline_and_the_reasons(tmp_path, jobs_db) -> None:
    reg = _registry(tmp_path)
    now = datetime(2026, 10, 28, 7, 5, tzinfo=UTC)
    await _run(jobs_db, "nightly", started=datetime(2026, 10, 28, 2, tzinfo=UTC), finished=datetime(2026, 10, 28, 3, tzinfo=UTC), state="SUCCEEDED")
    assert (await check_late(jobs_db, reg, judged=set(), now=now)).late == []

    reg2 = _registry(tmp_path)
    async with jobs_db.session() as s:
        for r in (await s.execute(JobRun.__table__.select())).all():
            await s.execute(JobRun.__table__.delete().where(JobRun.__table__.c.id == r.id))
    await _run(jobs_db, "nightly", started=datetime(2026, 10, 28, 2, tzinfo=UTC), finished=None, state="RUNNING")
    rep = await check_late(jobs_db, reg2, judged=set(), now=now)
    assert "still running at 07:00" in rep.late[0].reason
    async with jobs_db.session() as s:
        await s.execute(JobRun.__table__.update().values(state="FAILED", finished_at=datetime(2026, 10, 28, 3, tzinfo=UTC)))
    rep = await check_late(jobs_db, reg2, judged=set(), now=now)
    assert "failed" in rep.late[0].reason


async def test_an_old_deadline_is_not_judged(tmp_path, jobs_db) -> None:
    """A server started at noon does not send the 07:00 alert (more than the window ago)."""
    reg = _registry(tmp_path)
    rep = await check_late(jobs_db, reg, judged=set(), now=datetime(2026, 10, 28, 12, tzinfo=UTC))
    assert rep.late == []


def test_the_late_event_reaches_rules_and_recipients() -> None:
    """``LATE`` is the ``late`` job event: a rule listing it fires, and the job's own recipients
    get "Job … is late" without a rule."""
    import asyncio

    from liberty.notify.events import fire_job_event
    from liberty.notify.rules import parse_notification_rules

    sent: list[tuple[str, list[str]]] = []

    class Disp:
        async def dispatch(self, msg, channels=None, recipients=None, action=None):
            from types import SimpleNamespace
            sent.append((msg.subject, list(recipients or [])))
            return SimpleNamespace(errors=[], portal_users=[], emails=[], action_ran=False)

    rules = parse_notification_rules({"notifications": {"late": {
        "trigger": {"kind": "job", "events": ["late"], "jobs": ["nightly"]},
        "recipients": ["role:ops"], "channels": ["portal"], "subject": "{JOB} late: {ERROR}", "body": "due {EXPECTED_AT}"}}})

    async def go():
        tasks = fire_job_event(rules, Disp(), job_id="nightly", run_id="", state="LATE", error="late: x", duration_minutes=None,
                               rows=None, expected_at="2026-10-28T07:00", extra_recipients=["ops@example.com"])
        await asyncio.gather(*tasks)
    asyncio.run(go())
    subjects = sorted(s for s, _ in sent)
    assert any(s == "Job nightly is late" for s in subjects), sent
    assert len(sent) == 2
