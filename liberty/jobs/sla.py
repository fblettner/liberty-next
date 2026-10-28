"""Deadline alerts — a job expected to have finished by a time of day (``alerts.finish_by``).

The long-run alert measures one run's duration; the missed check sees a fire that never
started. Neither answers "was the nightly load done by 07:00?". This module does: on a day the
job is expected to run (one of its active schedules fires between midnight and the deadline, in
the job's timezone), at the deadline there must be a SUCCEEDED run finished since that first
expected fire. If not, the job is late — still running, failed, or never started — and the
``late`` job event goes out (the notification rules, the job's own ``alerts.recipients``).

The scheduler runs the check with the missed-schedule check, on the same interval. Each
(job, day) is judged once; the memory of it is in-process, so a server restarted shortly after a
deadline judges that day again (at most one repeated alert) — a deadline more than
:data:`WINDOW` old is not judged at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from liberty.jobs.db import JobDatabase
from liberty.jobs.missed import active_triggers, expected_fires
from liberty.jobs.models import JobRun, RunState
from liberty.jobs.registry import JobRegistry

_log = logging.getLogger(__name__)

# A deadline older than this is not judged (a server down all morning does not send yesterday's
# alerts at noon; the missed check already records the fires that never ran).
WINDOW = timedelta(hours=3)

OnLate = Callable[[Any, str, datetime, datetime], Awaitable[None]]   # (job, reason, deadline, first expected fire)


@dataclass(slots=True)
class LateJob:
    job_id: str
    deadline: datetime
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "deadline": self.deadline.isoformat(), "reason": self.reason}


@dataclass(slots=True)
class LateReport:
    checked_at: datetime
    jobs: int = 0
    late: list[LateJob] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"checked_at": self.checked_at.isoformat(), "jobs": self.jobs, "late": [x.to_dict() for x in self.late]}


def deadline_on(day: date, finish_by: str, tz: str | None) -> datetime:
    """The deadline of *day* (``"HH:MM"`` in *tz*, the server's zone when None) as an aware UTC
    instant."""
    hh, mm = (int(x) for x in finish_by.split(":"))
    zone = ZoneInfo(tz) if tz else datetime.now().astimezone().tzinfo
    return datetime.combine(day, time(hh, mm), tzinfo=zone).astimezone(timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


async def check_late(
    db: JobDatabase, registry: JobRegistry, *, judged: set[tuple[str, str]], now: datetime | None = None,
    on_late: OnLate | None = None,
) -> LateReport:
    """One pass: every job with ``alerts.finish_by`` whose deadline of today has passed (within
    :data:`WINDOW`) and has not been judged yet. *judged* (``(job id, local day)``) is owned by
    the caller."""
    now = now or datetime.now(timezone.utc)
    report = LateReport(checked_at=now)
    triggers = active_triggers(registry)
    for job in registry.jobs():
        finish_by = job.alerts.finish_by if job.alerts is not None else None
        if not finish_by:
            continue
        report.jobs += 1
        zone = ZoneInfo(job.timezone) if job.timezone else datetime.now().astimezone().tzinfo
        local_day = now.astimezone(zone).date()
        key = (job.id, local_day.isoformat())
        if key in judged:
            continue
        deadline = deadline_on(local_day, finish_by, job.timezone)
        if now < deadline:
            continue
        judged.add(key)
        if now - deadline > WINDOW:
            continue
        midnight = datetime.combine(local_day, time(0, 0), tzinfo=zone).astimezone(timezone.utc)
        fires: list[datetime] = []
        for trig in triggers:
            if trig.job_id != job.id:
                continue
            try:
                fires += expected_fires(trig.schedule, trig.timezone, midnight - timedelta(seconds=1), deadline)
            except Exception as exc:  # noqa: BLE001 — a bad expression is the registry's problem
                _log.warning("nomaflow.sla: cannot evaluate schedule %r of %s: %s", trig.schedule, trig.key, exc)
        fires = [f for f in fires if midnight <= f < deadline]
        if not fires:
            continue                                   # not expected to run today before the deadline
        first = min(fires)
        since = first - timedelta(seconds=60)
        async with db.session() as session:
            runs = (await session.execute(
                select(JobRun.id, JobRun.state, JobRun.finished_at)
                .where(JobRun.job_id == job.id)
                .where(or_(JobRun.started_at >= since, JobRun.scheduled_at >= since))
                .order_by(JobRun.started_at)
            )).all()
        if any(r.state == RunState.SUCCEEDED.value and (_aware(r.finished_at) or now) <= deadline for r in runs):
            continue
        states = [r.state for r in runs if r.state != RunState.MISSED.value]
        at = deadline.astimezone(zone).strftime("%H:%M")
        if RunState.RUNNING.value in states or RunState.QUEUED.value in states:
            why = f"still running at {at}"
        elif RunState.FAILED.value in states:
            why = f"failed — no successful run by {at}"
        elif states:
            why = f"no successful run by {at} (last: {states[-1].lower()})"
        else:
            why = f"did not start — expected from {first.astimezone(zone).strftime('%H:%M')}, due by {at}"
        reason = f"late: job {job.id!r} {why}"
        report.late.append(LateJob(job.id, deadline, reason))
        _log.warning("nomaflow.sla: %s", reason)
        if on_late is not None:
            try:
                await on_late(job, reason, deadline, first)
            except Exception as exc:  # noqa: BLE001 — a hook must not stop the check
                _log.warning("nomaflow.sla: on_late hook failed for %s: %s", job.id, exc)
    return report
