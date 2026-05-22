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
    return {
        "id": job.id,
        "description": job.description,
        "schedule": job.schedule,
        "timezone": job.timezone,
        "enabled": job.enabled,
        "tags": list(job.tags),
        "step_count": len(job.steps),
        "registered_with_scheduler": job.id in scheduled_ids,
        "in_flight": job.id in in_flight_ids,
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
