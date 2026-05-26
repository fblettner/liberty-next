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

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

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
async def run_job_now(
    job_id: str,
    request: Request,
    principal: Superuser,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Fire *job_id* manually as a :class:`ManualTrigger`. **Fire-and-return** —
    allocates the run row, schedules execution as a background task, returns
    immediately with the new run id.

    The previous shape blocked until the run reached a terminal state; a 30-min
    JDE sync invoked through here would tie up an admin HTTP request for the
    whole duration, and the UI couldn't show "running" until the request
    returned (you'd see nothing change for half an hour, then the row would
    flip straight to SUCCEEDED). The new contract lets the UI poll
    ``GET /admin/jobs`` to watch the state transition live, like the scheduled
    fire path. ``GET /admin/jobs/runs/<run_id>`` returns the streaming log
    while the run is in flight.

    **Per-fire kwargs override** (the "Run with parameters" UI flow): an
    optional body ``{"op_kwargs": {step_name: {key: value}}}`` ephemerally
    overrides the matching step's saved ``op_kwargs`` for this fire only.
    Useful for the nomasx1-init-db job (change ``target_connector`` per fire
    without editing jobs.toml). The saved config is untouched.
    """
    comps = _components(request)
    registry: JobRegistry = comps.registry
    scheduler: JobScheduler = comps.scheduler

    try:
        job = registry.get(job_id)
    except UnknownJobError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # Parse the optional overrides. Two fields, both optional:
    #   * `params` — override of job.params (shared kwargs that apply to every step).
    #   * `op_kwargs` — per-step override (wins over both job.params and request.params
    #     for the matching step).
    # Reject malformed payloads early — silently dropping them would have the operator
    # wonder why their parameter didn't apply.
    overrides: dict[str, dict[str, Any]] | None = None
    if body is not None and "op_kwargs" in body:
        raw = body.get("op_kwargs")
        if not isinstance(raw, dict):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="`op_kwargs` must be an object of `{step_name: {key: value}}`",
            )
        for step_name, kw in raw.items():
            if not isinstance(kw, dict):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"`op_kwargs[{step_name!r}]` must be an object of `{{key: value}}`",
                )
        overrides = {str(s): dict(kw) for s, kw in raw.items()}

    params_override: dict[str, Any] | None = None
    if body is not None and "params" in body:
        raw_p = body.get("params")
        if not isinstance(raw_p, dict):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="`params` must be an object of `{key: value}`",
            )
        params_override = dict(raw_p)

    # Optional log_level — per-fire override of job.log_level (jobs.toml default).
    # Restricted to INFO / DEBUG; anything else is a 422. None means "use the
    # job's configured default" (which itself defaults to INFO).
    log_level: str | None = None
    if body is not None and "log_level" in body:
        raw_l = body.get("log_level")
        if not isinstance(raw_l, str) or raw_l.upper() not in ("INFO", "DEBUG"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="`log_level` must be one of: 'INFO', 'DEBUG'",
            )
        log_level = raw_l.upper()

    _log.info(
        "nomaflow.admin manual fire job=%s triggered_by=%s overrides=%s params=%s log_level=%s",
        job_id, principal.username,
        sorted(overrides) if overrides else None,
        sorted(params_override) if params_override else None,
        log_level or "(job default)",
    )
    # fire_now_async creates the JobRun row synchronously (so we have its id to
    # return) then spawns execute_run as a background task. The runner persists
    # every state transition to the DB; if this process dies the row stays
    # RUNNING and is swept on the next startup via mark_orphan_runs_failed.
    run_id = await scheduler.fire_now_async(
        job, triggered_by=principal.username,
        op_kwargs_overrides=overrides,
        params_override=params_override,
        log_level=log_level,
    )
    return {
        "job_id": job_id,
        "run_id": run_id,
        "triggered_by": principal.username,
        "status": "queued",
    }


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


def _run_dict(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "job_id": run.job_id,
        "state": run.state,
        "trigger_kind": run.trigger_kind,
        "triggered_by": run.triggered_by,
        "scheduled_at": run.scheduled_at.isoformat() if run.scheduled_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "rows_affected": run.rows_affected,
        "error_message": run.error_message,
    }


def _step_dict(step: Any) -> dict[str, Any]:
    return {
        "step_index": step.step_index,
        "step_name": step.step_name,
        "step_type": step.step_type,
        "attempt": step.attempt,
        "state": step.state,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "finished_at": step.finished_at.isoformat() if step.finished_at else None,
        "rows_affected": step.rows_affected,
        "error_message": step.error_message,
    }


@router.get("/runs/{run_id}")
async def get_run_detail(run_id: str, request: Request, _: Superuser) -> dict[str, Any]:
    """One run — its summary, its step rows, and its **log** (NOMAFLOW-UI.md
    live-logs increment). Powers the Run detail page.

    The log comes from the in-memory buffer while the run is still active —
    so a poll mid-step (even a *hung* step) sees the latest lines — and from
    the durable ``nomaflow_run_logs`` row once the run has finished. The
    runner flushes buffer → DB at finalize, so exactly one of the two has it."""
    from sqlalchemy import select
    from liberty.jobs.models import JobRun, RunLog, StepRun
    from liberty.jobs.runlog import run_logs as live_run_logs

    comps = _components(request)
    async with comps.db.session() as session:
        run = await session.get(JobRun, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no run {run_id!r}")
        steps = (await session.execute(
            select(StepRun)
            .where(StepRun.run_id == run_id)
            .order_by(StepRun.step_index, StepRun.attempt)
        )).scalars().all()
        run_payload = _run_dict(run)
        step_payload = [_step_dict(s) for s in steps]

    # Live buffer for an active run; the persisted row once it's finished.
    live = live_run_logs(run_id)
    if live is not None:
        logs = live
    else:
        async with comps.db.session() as session:
            row = await session.get(RunLog, run_id)
        logs = row.logs if row is not None else ""

    return {"run": run_payload, "steps": step_payload, "logs": logs}


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
