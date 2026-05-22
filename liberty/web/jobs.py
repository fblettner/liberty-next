"""``/admin/jobs`` routes — nomaflow control plane (superuser only).

Three endpoints (PHASE13 §6 + §4):

* ``GET  /admin/jobs`` — list the loaded job catalogue + their schedule status.
  Powers the Settings → Jobs UI's job picker.
* ``POST /admin/jobs/<id>/run`` — fire a manual trigger. Returns when the run
  reaches a terminal state (caller blocks; long-running jobs should be invoked
  through the scheduler, not this endpoint).
* ``POST /admin/jobs/runs/<id>/cancel`` — cancel an in-flight run. Idempotent
  for already-terminated runs (404 — no task to cancel).

The route layer is deliberately thin — :class:`JobRunner` / :class:`JobScheduler`
own the actual semantics. Errors map cleanly:

* :class:`UnknownJobError` → 404
* In-flight runs the cancel can't find → 404
* Background framework state missing (nomaflow disabled in settings) → 503

Status codes match the rest of ``/admin/*``; the body shape on success mirrors
the brief JSON the reload endpoint returns (a couple of fields, no envelope).
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.jobs import (
    JobRegistry,
    JobRunner,
    JobScheduler,
    UnknownJobError,
)
from liberty.jobs.triggers import ManualTrigger

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/jobs", tags=["admin", "jobs"])

Superuser = Annotated[Principal, Depends(require_superuser)]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _components(request: Request):
    """Pull the :class:`~liberty.jobs.wiring.NomaflowComponents` off app.state.

    503 if nomaflow isn't wired (``[jobs] enabled = false`` or the lifespan
    skipped setup) — the operator gets a clear reason rather than an AttributeError."""
    comps = getattr(request.app.state, "jobs", None)
    if comps is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="nomaflow is disabled or not yet started",
        )
    return comps


def _job_summary(
    job,
    *,
    scheduled_ids: set[str],
    in_flight_ids: frozenset[str],
    last_run: dict[str, Any] | None,
    next_run: datetime | None,
) -> dict[str, Any]:
    """Compact JSON representation of one Job — for ``GET /admin/jobs``.

    Carries what the Jobs list (NOMAFLOW-UI.md §3.1) renders: the catalogue fields,
    the operational flags, the last-run badge (``last_run``: latest run's state +
    timestamps, or None if never run), and the next scheduled fire (``next_run``)."""
    # "in flight" must cover BOTH a scheduler-fired run (tracked in the scheduler's
    # _in_flight set) AND a manual "Run now" — which goes through fire_now → runner.run
    # directly and never touches that set. The reliable signal for either is the latest
    # run's state: RUNNING/QUEUED → in flight. Without this the Jobs-list Cancel button
    # never appears for a manually-started job (it gates on `in_flight`).
    last_state = (last_run or {}).get("state")
    in_flight = job.id in in_flight_ids or last_state in ("RUNNING", "QUEUED")
    return {
        "id": job.id,
        "description": job.description,
        "schedule": job.schedule,
        "timezone": job.timezone,
        "enabled": job.enabled,
        "tags": list(job.tags),
        "step_count": len(job.steps),
        "registered_with_scheduler": job.id in scheduled_ids,
        "in_flight": in_flight,
        "last_run": last_run,
        "next_run": next_run.isoformat() if next_run is not None else None,
    }


async def _latest_runs_by_job(db) -> dict[str, dict[str, Any]]:
    """``{job_id: {run_id, state, started_at, finished_at, rows_affected, error_message}}``
    for the most recent run of each job.

    One query: an inner aggregate (max ``started_at`` per ``job_id``) joined back to the
    run rows. Works on every dialect — no ``DISTINCT ON``. A theoretical tie (two runs of
    one job with an identical microsecond ``started_at``) would yield two rows; the dict
    build below just keeps the last seen — harmless for a display badge."""
    from sqlalchemy import func, select
    from liberty.jobs.models import JobRun

    newest = (
        select(JobRun.job_id, func.max(JobRun.started_at).label("max_started"))
        .group_by(JobRun.job_id)
        .subquery()
    )
    stmt = select(JobRun).join(
        newest,
        (JobRun.job_id == newest.c.job_id) & (JobRun.started_at == newest.c.max_started),
    )
    out: dict[str, dict[str, Any]] = {}
    async with db.session() as session:
        for run in (await session.execute(stmt)).scalars().all():
            out[run.job_id] = {
                "run_id": run.id,
                "state": run.state,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "rows_affected": run.rows_affected,
                "error_message": run.error_message,
            }
    return out


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #


@router.get("")
async def list_jobs(request: Request, _: Superuser) -> dict[str, Any]:
    """Return every job in the registry + operational state: scheduler-registered /
    in-flight flags, the last run's status (badge), and the next scheduled fire.
    Powers the Jobs list (NOMAFLOW-UI.md §3.1)."""
    comps = _components(request)
    registry: JobRegistry = comps.registry
    scheduler: JobScheduler = comps.scheduler
    runner: JobRunner = comps.runner

    scheduled_ids = set(scheduler.scheduled_job_ids)
    in_flight = scheduler.in_flight_job_ids
    next_fires = scheduler.next_fire_times
    last_runs = await _latest_runs_by_job(comps.db)
    return {
        "jobs": [
            _job_summary(
                j,
                scheduled_ids=scheduled_ids,
                in_flight_ids=in_flight,
                last_run=last_runs.get(j.id),
                next_run=next_fires.get(j.id),
            )
            for j in registry.jobs()
        ],
        "active_run_ids": sorted(runner.active_run_ids),
    }


@router.post("/{job_id}/run")
async def run_job_now(job_id: str, request: Request, principal: Superuser) -> dict[str, Any]:
    """Fire *job_id* manually as a :class:`ManualTrigger`. Blocks until the run
    reaches a terminal state.

    For long-running jobs an operator should normally use the scheduler;
    this endpoint exists for ad-hoc kicks ("Run now" button) and small jobs.
    A 30+ minute JDE sync invoked through here will tie up an admin request
    for that long — that's the operator's choice."""
    comps = _components(request)
    registry: JobRegistry = comps.registry
    scheduler: JobScheduler = comps.scheduler

    try:
        job = registry.get(job_id)
    except UnknownJobError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    _log.info(
        "nomaflow.admin manual fire job=%s triggered_by=%s",
        job_id, principal.username,
    )
    # Going through JobScheduler.fire_now (not JobRunner.run directly) keeps a
    # single notion of "all manual fires came from this entry point" for any
    # future broadcast / observability work.
    await scheduler.fire_now(job, triggered_by=principal.username)
    return {"job_id": job_id, "triggered_by": principal.username, "status": "completed"}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request, principal: Superuser) -> dict[str, Any]:
    """Request cancellation of an in-flight run.

    Returns 404 when *run_id* isn't in flight (already terminated, or never
    existed under the current runner instance — runs from a crashed previous
    process are not cancellable; they'll be swept on next restart). On 202,
    the task got the signal — the actual transition to ``CANCELED`` happens
    inside the run loop's ``CancelledError`` handler shortly afterwards."""
    comps = _components(request)
    runner: JobRunner = comps.runner

    if not runner.cancel(run_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"run {run_id} is not in flight on this worker",
        )
    _log.info(
        "nomaflow.admin cancel requested run=%s by=%s",
        run_id, principal.username,
    )
    return {"run_id": run_id, "cancelled_by": principal.username, "status": "requested"}


@router.get("/cron-preview")
async def cron_preview(
    schedule: str,
    _: Superuser,
    timezone: str = "",
    count: int = 5,
) -> dict[str, Any]:
    """Preview the next *count* fire times of a cron *schedule* — powers the
    schedule editor's live preview (NOMAFLOW-UI.md §3.2 / increment 6).

    Reuses APScheduler's ``CronTrigger`` (already a dependency) rather than a
    hand-rolled cron evaluator or a new frontend lib — the same parser the
    scheduler fires jobs with, so the preview can't disagree with reality. A
    malformed cron / unknown timezone → 422 with the parser's message."""
    from apscheduler.triggers.cron import CronTrigger

    try:
        trigger = CronTrigger.from_crontab(schedule, timezone=timezone or None)
    except Exception as exc:  # noqa: BLE001 — APScheduler raises ValueError + others
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid cron expression: {exc}",
        ) from exc

    fires: list[str] = []
    prev: datetime | None = None
    cursor = datetime.now(dt_timezone.utc)
    for _i in range(max(1, min(count, 20))):
        nxt = trigger.get_next_fire_time(prev, cursor)
        if nxt is None:
            break
        fires.append(nxt.isoformat())
        prev = nxt
        cursor = nxt
    return {"schedule": schedule, "timezone": timezone or None, "next": fires}
