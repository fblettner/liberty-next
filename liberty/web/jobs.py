"""``/admin/jobs`` routes — nomaflow control plane (superuser only).

Three endpoints (spec §6 + §4):

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
from pathlib import Path
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from liberty.auth.dependencies import CurrentPrincipal, require_superuser
from liberty.auth.principal import Principal
from liberty.jobs import (
    JobRegistry,
    JobRunner,
    JobScheduler,
    UnknownJobError,
)

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/jobs", tags=["admin", "jobs"])
# The user-facing side (``/api/jobs``): a job a menu item of type ``job`` opens, for a user who
# holds ``job:<id>`` — run it (a file per ``file_load`` step), follow the run's log, download its
# files. Everything else (parameters, step toggles, cancel, the catalogue) stays on ``/admin``.
user_router = APIRouter(prefix="/api/jobs", tags=["jobs"])

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
    next_fires: dict[str, datetime],
) -> dict[str, Any]:
    """Compact JSON representation of one Job — for ``GET /admin/jobs``.

    Carries what the Jobs list (NOMAFLOW-UI.md §3.1) renders: the catalogue fields,
    the operational flags, the last-run badge (``last_run``: latest run's state +
    timestamps, or None if never run), the next scheduled fire (``next_run`` — the
    soonest across the job's own schedule AND any schedulable preset), and
    ``preset_schedules`` (per scheduled preset: its cron + next fire)."""
    # "in flight" must cover BOTH a scheduler-fired run (tracked in the scheduler's
    # _in_flight set) AND a manual "Run now" — which goes through fire_now → runner.run
    # directly and never touches that set. The reliable signal for either is the latest
    # run's state: RUNNING/QUEUED → in flight. Without this the Jobs-list Cancel button
    # never appears for a manually-started job (it gates on `in_flight`).
    last_state = (last_run or {}).get("state")
    in_flight = job.id in in_flight_ids or last_state in ("RUNNING", "QUEUED")

    # Schedulable presets: each has its own trigger id `<job>::preset::<name>`.
    preset_schedules: list[dict[str, Any]] = []
    preset_registered = False
    base_next = next_fires.get(job.id)  # the job-level schedule's own next fire
    candidates: list[datetime] = [base_next] if base_next is not None else []
    for p in job.presets:
        if not p.schedule:
            continue
        pid = JobScheduler._preset_job_id(job.id, p.name)  # noqa: SLF001 — same subsystem
        if pid in scheduled_ids:
            preset_registered = True
        pn = next_fires.get(pid)
        if pn is not None:
            candidates.append(pn)
        preset_schedules.append({
            "name": p.name,
            "schedule": p.schedule,
            "timezone": p.timezone,
            "enabled": p.enabled,
            "next_run": pn.isoformat() if pn is not None else None,
        })
    next_run = min(candidates) if candidates else None

    return {
        "id": job.id,
        "description": job.description,
        "schedule": job.schedule,
        "timezone": job.timezone,
        "enabled": job.enabled,
        "tags": list(job.tags),
        "step_count": len(job.steps),
        "registered_with_scheduler": (job.id in scheduled_ids) or preset_registered,
        "in_flight": in_flight,
        "last_run": last_run,
        "next_run": next_run.isoformat() if next_run is not None else None,
        # The job-level schedule's OWN next fire (vs ``next_run`` = soonest across base + presets).
        "schedule_next_run": base_next.isoformat() if base_next is not None else None,
        "preset_schedules": preset_schedules,
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


@router.get("", summary="List jobs")
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
                next_fires=next_fires,
            )
            for j in registry.jobs()
        ],
        "active_run_ids": sorted(runner.active_run_ids),
    }


@router.post("/{job_id}/run", summary="Run job now")
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
    # Restricted to WARNING / INFO / DEBUG; anything else is a 422. None means
    # "use the job's configured default" (which itself defaults to INFO).
    log_level: str | None = None
    if body is not None and "log_level" in body:
        raw_l = body.get("log_level")
        if not isinstance(raw_l, str) or raw_l.upper() not in ("WARNING", "INFO", "DEBUG"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="`log_level` must be one of: 'WARNING', 'INFO', 'DEBUG'",
            )
        log_level = raw_l.upper()

    # Optional step_enabled — per-fire enable/disable per step. Maps
    # ``{step_name: bool}``. ``False`` makes the runner skip the step
    # (CANCELED with "skipped: disabled"); ``True`` forces it on even when
    # jobs.toml has ``enabled = false``. Reject unknown step names so the
    # operator doesn't wonder why their toggle didn't apply — and reject
    # non-bool values for the same reason (a string "false" is a footgun).
    step_enabled_overrides: dict[str, bool] | None = None
    if body is not None and "step_enabled" in body:
        raw_se = body.get("step_enabled")
        if not isinstance(raw_se, dict):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="`step_enabled` must be an object of `{step_name: bool}`",
            )
        known_step_names = {s.name for s in job.steps}
        for step_name, enabled in raw_se.items():
            if not isinstance(enabled, bool):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"`step_enabled[{step_name!r}]` must be a boolean",
                )
            if step_name not in known_step_names:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"`step_enabled` references unknown step {step_name!r}; "
                           f"valid: {sorted(known_step_names)!r}",
                )
        step_enabled_overrides = {str(s): bool(e) for s, e in raw_se.items()}

    _log.info(
        "nomaflow.admin manual fire job=%s triggered_by=%s overrides=%s params=%s "
        "step_enabled=%s log_level=%s",
        job_id, principal.username,
        sorted(overrides) if overrides else None,
        sorted(params_override) if params_override else None,
        step_enabled_overrides or None,
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
        step_enabled_overrides=step_enabled_overrides,
        log_level=log_level,
    )
    return {
        "job_id": job_id,
        "run_id": run_id,
        "triggered_by": principal.username,
        "status": "queued",
    }


@router.post(
    "/{job_id}/upload",
    summary="Upload a file for a run",
    responses={400: {"description": "No file part, or a type the loader does not read."}, 413: {"description": "Too large."}},
)
async def upload_job_file(job_id: str, request: Request, principal: Superuser) -> dict[str, Any]:
    """Store a spreadsheet / CSV in the job's work area (``[jobs] files_dir``) for a
    ``file_load`` step to read on the next fire: the Run-with-parameters modal uploads first,
    then fires with ``op_kwargs[<step>] = {"file": <path>}``. Superuser only; the name is
    sanitised and time-stamped, the size capped by ``[jobs] max_upload_mb``."""
    comps = _components(request)
    try:
        comps.registry.get(job_id)
    except UnknownJobError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown job {job_id!r}") from None
    return await _store_upload(request, job_id, principal)


async def _store_upload(request: Request, job_id: str, principal: Principal) -> dict[str, Any]:
    """The upload itself (the caller checked the job and the permission)."""
    from starlette.datastructures import UploadFile

    from liberty.jobs.files import UPLOAD_SUFFIXES, upload_target

    settings = request.app.state.settings
    limit = settings.jobs.max_upload_mb * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 4096:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail=f"upload exceeds {settings.jobs.max_upload_mb} MB")
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="missing 'file' part")
    from pathlib import Path
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unsupported file type {suffix or '(none)'}: {', '.join(sorted(UPLOAD_SUFFIXES))}")
    target = upload_target(settings, job_id, upload.filename)
    size = 0
    with target.open("wb") as fh:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                fh.close()
                target.unlink(missing_ok=True)
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail=f"upload exceeds {settings.jobs.max_upload_mb} MB")
            fh.write(chunk)
    _log.info("nomaflow.admin upload job=%s by=%s file=%s size=%d", job_id, principal.username, target, size)
    return {"job_id": job_id, "name": target.name, "path": str(target), "size": size}


@router.get("/runs/{run_id}/files/{name}", summary="Download a run's file")
async def download_run_file(run_id: str, name: str, request: Request, _: Superuser):
    """A file a step left in the job's work area for this run — today the ``file_load``
    rejects report (``<run>-<step>-rejects.csv``). The name must be a plain file name of that
    run; anything else is a 404."""
    return await _run_file_response(request, run_id, name)


async def _run_file_response(request: Request, run_id: str, name: str, *, job_id: str | None = None):
    """A run's file as a download; with *job_id* the run must belong to that job (the user routes)."""
    from fastapi.responses import FileResponse
    from sqlalchemy import select

    from liberty.jobs.files import run_file
    from liberty.jobs.models import JobRun

    comps = _components(request)
    async with comps.db.session() as s:
        row = (await s.execute(select(JobRun).where(JobRun.id == run_id))).scalar_one_or_none()
    if row is None or (job_id is not None and row.job_id != job_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown run {run_id!r}")
    path = run_file(request.app.state.settings, row.job_id, name) if name.startswith(run_id) else None
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no file {name!r} for run {run_id!r}")
    return FileResponse(path, filename=name, media_type="text/csv" if path.suffix == ".csv" else "application/octet-stream")


@router.post("/runs/{run_id}/cancel", summary="Cancel job run")
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


def _step_dict(step: Any, extras: dict[str, Any] | None = None) -> dict[str, Any]:
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
        # Free-form structured extras from the step's executor (today: just
        # CallJobExecutor's child run pointer). ``None`` when the executor
        # didn't write any — keeps the wire payload terse for the common case.
        "extras": extras,
    }


@router.get("/runs/{run_id}", summary="Get run detail")
async def get_run_detail(run_id: str, request: Request, _: Superuser) -> dict[str, Any]:
    """One run — its summary, its step rows, and its **log** (NOMAFLOW-UI.md
    live-logs increment). Powers the Run detail page.

    The log comes from the in-memory buffer while the run is still active —
    so a poll mid-step (even a *hung* step) sees the latest lines — and from
    the durable ``nomaflow_run_logs`` row once the run has finished. The
    runner flushes buffer → DB at finalize, so exactly one of the two has it."""
    return await _run_detail(request, run_id)


async def _run_detail(request: Request, run_id: str, *, job_id: str | None = None) -> dict[str, Any]:
    """``{run, steps, logs, overrides}``; with *job_id* the run must belong to that job."""
    import json
    from sqlalchemy import select
    from liberty.jobs.models import JobRun, RunLog, RunOverrides, StepRun, StepRunExtras
    from liberty.jobs.runlog import run_logs as live_run_logs

    comps = _components(request)
    async with comps.db.session() as session:
        run = await session.get(JobRun, run_id)
        if run is None or (job_id is not None and run.job_id != job_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no run {run_id!r}")
        steps = (await session.execute(
            select(StepRun)
            .where(StepRun.run_id == run_id)
            .order_by(StepRun.step_index, StepRun.attempt)
        )).scalars().all()
        # Pre-fetch step-extras for every step in one query so the per-step
        # JSON render below doesn't N+1. Most runs have zero extras rows —
        # CallJobExecutor is the only writer today — so the join would be
        # almost empty either way; the bulk fetch just keeps the route clean.
        step_ids = [s.id for s in steps]
        extras_rows: dict[str, dict[str, Any]] = {}
        if step_ids:
            for row in (await session.execute(
                select(StepRunExtras).where(StepRunExtras.step_run_id.in_(step_ids))
            )).scalars().all():
                try:
                    extras_rows[row.step_run_id] = json.loads(row.extras_json or "{}")
                except json.JSONDecodeError:
                    extras_rows[row.step_run_id] = {}
        # Per-fire overrides — what the operator picked in the Run-with-params
        # modal (or empty for a scheduled fire / a run from before the
        # RunOverrides table existed). Decoded in-place; bad JSON falls back
        # to an empty dict (defensive — never crash the run-detail page).
        overrides_row = await session.get(RunOverrides, run_id)
        run_payload = _run_dict(run)
        step_payload = [_step_dict(s, extras=extras_rows.get(s.id)) for s in steps]

    overrides: dict[str, Any] = {}
    if overrides_row is not None and overrides_row.overrides_json:
        try:
            overrides = json.loads(overrides_row.overrides_json)
        except json.JSONDecodeError:
            overrides = {}

    # Live buffer for an active run; the persisted row once it's finished.
    live = live_run_logs(run_id)
    if live is not None:
        logs = live
    else:
        async with comps.db.session() as session:
            row = await session.get(RunLog, run_id)
        logs = row.logs if row is not None else ""

    return {"run": run_payload, "steps": step_payload, "logs": logs, "overrides": overrides}


@router.get("/callables", summary="List python callables")
async def list_callables(request: Request, _: Superuser) -> dict[str, Any]:
    """Return the catalog of available python-step callables — every top-level
    ``async def j_*`` / ``def j_*`` discovered under ``plugins/``. Powers the
    Job Designer's callable dropdown so operators pick from a list instead of
    typing the full ``module:function`` string.

    Discovery is AST-only (no plugin code is executed here — see
    :mod:`liberty.jobs.discover` for why). A plugin with a broken import still
    has its callables surface in this catalog; the run-time failure happens at
    fire time with a clear traceback the operator can act on.

    The plugins root is resolved the same way :func:`liberty.main._ensure_plugins_on_sys_path`
    does — ``${LIBERTY_APPS_DIR}/../plugins/`` when ``LIBERTY_APPS_DIR`` is set,
    else ``./plugins/`` relative to the process cwd. A missing directory
    returns an empty list (no plugins installed yet — perfectly valid for a
    fresh install)."""
    import os
    from pathlib import Path
    from liberty.jobs.discover import discover_callables

    apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
    plugins = Path(apps).parent / "plugins" if apps else Path("plugins")
    catalog = discover_callables(plugins)
    return {
        "plugins_root": str(plugins.resolve()) if plugins.is_dir() else None,
        "callables": [c.to_dict() for c in catalog],
    }


@router.get("/cron-preview", summary="Preview cron schedule")
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


# --------------------------------------------------------------------------- #
# retention — current policy + last sweep + manual one-shot
# --------------------------------------------------------------------------- #


@router.get("/retention", summary="Get retention status")
async def get_retention_status(request: Request, _: Superuser) -> dict[str, Any]:
    """Return the current retention policy (read from ``jobs.toml`` [meta.retention])
    + the last automatic sweep's report (None until the first sweep fires).
    Surfaced by the Nomaflow Retention panel so operators see "what's the policy
    + what did the last sweep actually delete" without trawling the server log."""
    comps = _components(request)
    policy = comps.registry.config.meta.retention
    last = comps.scheduler.last_retention
    return {
        "policy": policy.model_dump(),
        "last_sweep": last.to_dict() if last is not None else None,
    }


@router.get("/missed", summary="Get missed-schedule status")
async def get_missed_status(request: Request, _: Superuser) -> dict[str, Any]:
    """The ``[meta.missed]`` policy + the last check's report (None until the first check)."""
    comps = _components(request)
    last = comps.scheduler.last_missed
    return {
        "policy": comps.registry.config.meta.missed.model_dump(),
        "last_check": last.to_dict() if last is not None else None,
    }


@router.post("/missed/check", summary="Run the missed-schedule check")
async def run_missed_check(request: Request, _: Superuser) -> dict[str, Any]:
    """One pass of the missed-fire check with the current policy; returns its report. The gaps
    it finds are recorded as MISSED runs and raise the ``missed`` job event once."""
    comps = _components(request)
    return (await comps.scheduler.check_missed_now()).to_dict()


@router.post("/retention/sweep", summary="Run retention sweep")
async def run_retention_sweep(request: Request, _: Superuser) -> dict[str, Any]:
    """Fire a one-shot retention sweep with the CURRENT policy. Returns the
    sweep report (deleted counts + cutoff timestamp).

    Useful when an operator has just changed [meta.retention] in jobs.toml + hit
    Reload, and wants to apply the new policy immediately instead of waiting for
    the next scheduled interval. Idempotent — running it twice in a row deletes
    nothing the second time (everything that qualified is gone)."""
    comps = _components(request)
    policy = comps.registry.config.meta.retention
    report = await comps.scheduler.run_retention_now(policy)
    return report.to_dict()


# ── the user side: ``/api/jobs`` ─────────────────────────────────────────────────────────────
def _user_job(request: Request, job_id: str, principal: Principal):
    """The job behind a user call: ``job:<id>`` checked BEFORE the registry lookup (no id
    enumeration), then the job or a 404."""
    from liberty.web.deps import require_permission
    require_permission(principal, f"job:{job_id}")
    comps = _components(request)
    try:
        return comps, comps.registry.get(job_id)
    except UnknownJobError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _upload_steps(job) -> list[dict[str, Any]]:
    """The ``file_load`` steps a user feeds a file to (one upload per step and run): the enabled
    ones with neither a configured ``path`` nor a ``watch_dir`` — a step that knows its file (the
    bundled samples, a watched folder) takes none."""
    out: list[dict[str, Any]] = []
    for s in job.steps:
        if getattr(s.type, "value", s.type) != "file_load" or not s.enabled or s.path or s.watch_dir:
            continue
        out.append({"step": s.name, "sheet": s.sheet})
    return out


def _user_params(request: Request, job, language: str | None) -> list[dict[str, Any]]:
    """The job's ``user_params`` with their choices resolved: fixed ``choices``, or
    ``choices_from = "explorer_models:<app>"`` → the app's Explorer models (id → label)."""
    out: list[dict[str, Any]] = []
    for p in job.user_params:
        choices: list[dict[str, str]] = [{"value": c, "label": c} for c in p.choices]
        if p.choices_from:
            kind, _, arg = p.choices_from.partition(":")
            if kind == "explorer_models":
                explorer = getattr(request.app.state, "explorer", None)
                for app, mid, m in (explorer.items() if explorer is not None else []):
                    if app == arg:
                        choices.append({"value": mid, "label": m.label or mid})
        out.append({"name": p.name, "label": p.label_for(language), "step": p.step, "default": p.default, "required": p.required, "choices": choices})
    return out


async def _runs_of_job(db, job_id: str, limit: int = 20) -> list[dict[str, Any]]:
    from sqlalchemy import desc, select
    from liberty.jobs.models import JobRun
    stmt = select(JobRun).where(JobRun.job_id == job_id).order_by(desc(JobRun.started_at)).limit(limit)
    async with db.session() as session:
        return [_run_dict(r) for r in (await session.execute(stmt)).scalars().all()]


@user_router.get("/{job_id}", summary="A job for the user to run")
async def user_get_job(job_id: str, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    """``{job: {id, description, uploads, in_flight}, runs: [the last 20]}`` — what the job page
    shows: the description, one file picker per ``file_load`` step, the run history."""
    comps, job = _user_job(request, job_id, principal)
    runs = await _runs_of_job(comps.db, job_id)
    in_flight = job_id in comps.scheduler.in_flight_job_ids or any(r["state"] in ("RUNNING", "QUEUED") for r in runs)
    return {
        "job": {
            "id": job.id, "description": job.description, "tags": list(job.tags), "uploads": _upload_steps(job), "in_flight": in_flight,
            "params": _user_params(request, job, request.headers.get("x-liberty-lang")),
        },
        "runs": runs,
    }


@user_router.post("/{job_id}/upload", summary="Upload a file for a run")
async def user_upload(job_id: str, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    _user_job(request, job_id, principal)
    return await _store_upload(request, job_id, principal)


@user_router.post("/{job_id}/run", summary="Run the job")
async def user_run(job_id: str, request: Request, principal: CurrentPrincipal, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Fire the job for the user: ``{files: {<step>: <uploaded path>}, params: {<name>: <value>}}``
    hands each ``file_load`` step its file (a path this job's upload returned — nothing else is
    accepted) and the declared ``user_params`` their values (a value outside a param's choices
    is refused; a blank value is "not set"). No other per-fire override exists on this side."""
    from liberty.jobs.files import job_dir
    comps, job = _user_job(request, job_id, principal)
    if job_id in comps.scheduler.in_flight_job_ids:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="the job is already running")
    steps = {u["step"] for u in _upload_steps(job)}
    overrides: dict[str, dict[str, Any]] = {}
    params = (body or {}).get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="`params` must be an object of `{name: value}`")
    declared = {p["name"]: p for p in _user_params(request, job, None)}
    for name, value in params.items():
        p = declared.get(name)
        if p is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{name!r} is not a parameter of this job")
        v = "" if value is None else str(value).strip()
        if p["choices"] and v and v not in {c["value"] for c in p["choices"]}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{name!r}: {v!r} is not one of the choices")
        overrides.setdefault(p["step"], {})[name] = v
    for p in declared.values():
        if p["required"] and not (params.get(p["name"]) or p["default"]):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{p['name']!r} is required")
    files = (body or {}).get("files") or {}
    if not isinstance(files, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="`files` must be an object of `{step: path}`")
    uploads = (job_dir(request.app.state.settings, job_id) / "uploads").resolve()
    for step, path in files.items():
        if step not in steps:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{step!r} is not a file step of this job")
        p = Path(str(path)).resolve()
        if uploads not in p.parents or not p.is_file():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{step!r}: upload the file first")
        overrides.setdefault(step, {})["file"] = str(p)
    _log.info("nomaflow.user fire job=%s triggered_by=%s overrides=%s", job_id, principal.username, {s: sorted(k) for s, k in overrides.items()} or None)
    run_id = await comps.scheduler.fire_now_async(job, triggered_by=principal.username, op_kwargs_overrides=overrides or None)
    return {"job_id": job_id, "run_id": run_id, "triggered_by": principal.username, "status": "queued"}


@user_router.get("/{job_id}/runs/{run_id}", summary="A run of the job with its log")
async def user_run_detail(job_id: str, run_id: str, request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    _user_job(request, job_id, principal)
    out = await _run_detail(request, run_id, job_id=job_id)
    out.pop("overrides", None)
    return out


@user_router.get("/{job_id}/runs/{run_id}/files/{name}", summary="Download a run's file")
async def user_run_file(job_id: str, run_id: str, name: str, request: Request, principal: CurrentPrincipal):
    _user_job(request, job_id, principal)
    return await _run_file_response(request, run_id, name, job_id=job_id)
