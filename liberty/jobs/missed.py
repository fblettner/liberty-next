"""Missed-schedule detection — the cron instants that should have produced a run and did not.

APScheduler keeps its triggers in memory: a fire that falls while the server is down is
simply never known, and a fire that lands while the previous run of the same job is still in
flight is skipped (``max_instances=1``). Both leave a hole in the run history that nobody sees
until a report is late. This module closes it:

* :func:`expected_fires` lists the instants a cron expression fires in a window, with the same
  :class:`CronTrigger` the scheduler registers (so DST and timezones agree).
* :func:`check_missed` walks every active trigger of the registry, looks for a run near each
  expected instant and records the gaps as ``MISSED`` runs (:func:`record_missed`): a row in
  ``nomaflow_job_runs`` with ``scheduled_at`` = the expected instant, so the gap shows in the
  Runs list and the ``(job_id, scheduled_at)`` constraint makes a second detection a no-op.

The scheduler runs the check on an interval (:meth:`JobScheduler.schedule_missed_check`) and
hands each new ``MISSED`` row to its ``on_missed`` hook — the notification rules' ``missed``
job event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from liberty.jobs.db import JobDatabase
from liberty.jobs.models import JobRun, RunState, TriggerKind
from liberty.jobs.registry import JobRegistry
from liberty.jobs.schema import MissedPolicy

_log = logging.getLogger(__name__)

# Upper bound on the instants enumerated per trigger and window — a ``* * * * *`` job over a
# 24 h lookback is 1 440, anything beyond that is a misconfiguration we refuse to amplify.
_MAX_FIRES = 5000
# A scheduled run's ``scheduled_at`` is the wall clock at fire time, a little after the cron
# instant; a run this much *before* the instant still counts for it (clock skew).
_BEFORE = timedelta(seconds=60)


@dataclass(slots=True, frozen=True)
class ActiveTrigger:
    """One cron trigger the scheduler registers: the job's own schedule (``preset`` None) or a
    schedulable preset's."""

    job_id: str
    preset: str | None
    schedule: str
    timezone: str | None

    @property
    def key(self) -> str:
        return self.job_id if self.preset is None else f"{self.job_id}::preset::{self.preset}"


def active_triggers(registry: JobRegistry) -> list[ActiveTrigger]:
    """The triggers :meth:`JobScheduler._add_apscheduler_job` registers, same filter."""
    out: list[ActiveTrigger] = []
    for job in registry.scheduled_jobs():
        if job.enabled and job.schedule:
            out.append(ActiveTrigger(job.id, None, job.schedule, job.timezone))
        for p in job.presets:
            if p.enabled and p.schedule:
                out.append(ActiveTrigger(job.id, p.name, p.schedule, p.timezone or job.timezone))
    return out


def expected_fires(schedule: str, tz: str | None, since: datetime, until: datetime) -> list[datetime]:
    """The cron instants in ``(since, until]`` — UTC, ascending."""
    if until <= since:
        return []
    trigger = CronTrigger.from_crontab(schedule, timezone=tz)
    out: list[datetime] = []
    prev = since
    while len(out) < _MAX_FIRES:
        nxt = trigger.get_next_fire_time(prev, prev)
        if nxt is None:
            break
        nxt_utc = nxt.astimezone(timezone.utc)
        if nxt_utc > until:
            break
        out.append(nxt_utc)
        prev = nxt
    return out


@dataclass(slots=True)
class MissedFire:
    job_id: str
    preset: str | None
    expected_at: datetime
    run_id: str | None = None   # the MISSED row recorded for it (None = already recorded earlier)

    def to_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "preset": self.preset, "expected_at": self.expected_at.isoformat(), "run_id": self.run_id}


@dataclass(slots=True)
class MissedReport:
    checked_at: datetime
    triggers: int = 0
    missed: list[MissedFire] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"checked_at": self.checked_at.isoformat(), "triggers": self.triggers, "missed": [m.to_dict() for m in self.missed]}


def _to_utc(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


async def last_run_instant(db: JobDatabase, job_id: str) -> datetime | None:
    """The latest instant a run of *job_id* accounts for: ``scheduled_at`` of a scheduled fire,
    ``started_at`` of a manual one. ``None`` when the job never ran."""
    async with db.session() as s:
        sched = (await s.execute(select(func.max(JobRun.scheduled_at)).where(JobRun.job_id == job_id))).scalar_one()
        started = (await s.execute(
            select(func.max(JobRun.started_at)).where(JobRun.job_id == job_id, JobRun.trigger_kind == TriggerKind.MANUAL.value)
        )).scalar_one()
    vals = [v for v in (_to_utc(sched), _to_utc(started)) if v is not None]
    return max(vals) if vals else None


async def runs_between(db: JobDatabase, job_id: str, since: datetime, until: datetime) -> list[tuple[str, datetime | None, datetime]]:
    """``(trigger_kind, scheduled_at, started_at)`` of the runs that can cover an instant in
    ``[since, until]``."""
    async with db.session() as s:
        rows = (await s.execute(
            select(JobRun.trigger_kind, JobRun.scheduled_at, JobRun.started_at)
            .where(JobRun.job_id == job_id)
            .where(or_(JobRun.scheduled_at.between(since, until), JobRun.started_at.between(since, until)))
        )).all()
    return [(k, _to_utc(sa), _to_utc(st) or datetime.now(timezone.utc)) for k, sa, st in rows]


def is_covered(expected: datetime, runs: list[tuple[str, datetime | None, datetime]], grace: timedelta) -> bool:
    """A scheduled run (or a MISSED row) whose ``scheduled_at`` is within ``[-60 s, +grace]`` of
    the instant, or a manual run started within ``[0, +grace]`` of it (the operator ran it by
    hand), accounts for the fire."""
    for kind, scheduled_at, started_at in runs:
        if scheduled_at is not None and expected - _BEFORE <= scheduled_at <= expected + grace:
            return True
        if kind == TriggerKind.MANUAL.value and expected <= started_at <= expected + grace:
            return True
    return False


async def record_missed(db: JobDatabase, job_id: str, expected_at: datetime, reason: str) -> JobRun | None:
    """Insert the ``MISSED`` row for the instant; ``None`` when one already exists (the unique
    ``(job_id, scheduled_at)`` constraint is the deduplication)."""
    now = datetime.now(timezone.utc)
    run = JobRun(
        job_id=job_id, trigger_kind=TriggerKind.SCHEDULED.value, scheduled_at=expected_at,
        started_at=now, finished_at=now, state=RunState.MISSED.value, error_message=reason[:4096],
    )
    try:
        async with db.session() as s:
            s.add(run)
            await s.flush()
            s.expunge(run)
    except IntegrityError:
        return None
    return run


OnMissed = Callable[[Any, JobRun, str], Awaitable[None]]


async def check_missed(
    db: JobDatabase, registry: JobRegistry, policy: MissedPolicy, *, watermarks: dict[str, datetime],
    started_at: datetime, now: datetime | None = None, on_missed: OnMissed | None = None,
) -> MissedReport:
    """One pass over the active triggers. *watermarks* (``trigger key → checked up to``) is
    owned by the caller and advanced here; a trigger without one opens its window at the job's
    last run, bounded by ``lookback_hours``, or at *started_at* (the scheduler's start) when the
    job never ran — a job that is new to the deployment is not blamed for the fires before it
    existed."""
    now = now or datetime.now(timezone.utc)
    grace = timedelta(minutes=policy.grace_minutes)
    until = now - grace
    report = MissedReport(checked_at=now)
    for trig in active_triggers(registry):
        report.triggers += 1
        since = watermarks.get(trig.key)
        if since is None:
            last = await last_run_instant(db, trig.job_id)
            floor = now - timedelta(hours=policy.lookback_hours)
            since = max(last, floor) if last is not None else max(started_at, floor)
        if until <= since:
            continue
        try:
            expected = expected_fires(trig.schedule, trig.timezone, since, until)
        except Exception as exc:  # noqa: BLE001 — a bad expression is the registry's problem, not the check's
            _log.warning("nomaflow.missed: cannot evaluate schedule %r of %s: %s", trig.schedule, trig.key, exc)
            watermarks[trig.key] = until
            continue
        if expected:
            runs = await runs_between(db, trig.job_id, expected[0] - _BEFORE, expected[-1] + grace)
            for inst in expected:
                if is_covered(inst, runs, grace):
                    continue
                label = f"preset {trig.preset!r} of job {trig.job_id!r}" if trig.preset else f"job {trig.job_id!r}"
                reason = f"missed: the fire of {label} expected at {inst.isoformat(timespec='minutes')} did not run (the server was down, or the previous run was still running)"
                row = await record_missed(db, trig.job_id, inst, reason)
                report.missed.append(MissedFire(trig.job_id, trig.preset, inst, row.id if row else None))
                if row is not None:
                    _log.warning("nomaflow.missed: %s", reason)
                    if on_missed is not None:
                        try:
                            await on_missed(registry.get(trig.job_id), row, reason)
                        except Exception as exc:  # noqa: BLE001 — a hook must not stop the check
                            _log.warning("nomaflow.missed: on_missed hook failed for %s: %s", trig.key, exc)
        watermarks[trig.key] = until
    return report
