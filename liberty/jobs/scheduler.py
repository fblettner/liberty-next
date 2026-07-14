"""JobScheduler — APScheduler glue around :class:`JobRunner`.

Owns the in-process :class:`AsyncIOScheduler`, registers a cron trigger for
each ``schedule``-d job in the :class:`JobRegistry`, and hands fired triggers
off to :meth:`JobRunner.run` with a :class:`ScheduledTrigger` carrying the
exact cron-fire instant.

What's here (chunk 2c):

* :meth:`start` — runs the orphan-run sweep (PHASE13 §6 / :mod:`liberty.jobs.recovery`),
  registers cron triggers for the enabled+scheduled jobs, kicks the scheduler.
* :meth:`stop` — graceful shutdown; cancels in-flight runs cleanly.
* :meth:`add_job` / :meth:`remove_job` — hot-reload entry points for chunk 3's
  ``POST /admin/reload`` to swap the registry without restarting the process.
* :meth:`fire_now` — synthetic trigger handy for tests + the future
  ``POST /admin/jobs/<id>/run`` admin endpoint (chunk 3).

What's *not* here:

* Multi-worker advisory lock (PHASE13 §11 #2) — single-process assumption
  for now. Chunk 3+.
* Hooking into FastAPI's lifespan (``app.state.scheduler`` + start/stop) —
  chunk 3 wires this in main.py.
* The catchup_window_minutes grace period (PHASE13 §6) — catchup=False today,
  add when a tenant asks.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from liberty.jobs.recovery import mark_orphan_runs_failed
from liberty.jobs.registry import JobRegistry
from liberty.jobs.retention import SweepReport, sweep_run_history
from liberty.jobs.runner import JobRunner
from liberty.jobs.schema import Job, RetentionPolicy
from liberty.jobs.triggers import ManualTrigger, ScheduledTrigger

_log = logging.getLogger(__name__)


# Optional broadcaster type — same shape as JobRunner's, kept as a type alias here
# rather than imported to avoid a circular dependency at module load time.
Hook = Callable[[str, dict], Awaitable[None]]


def _log_orphan_task_exception(task: asyncio.Task) -> None:
    """Surface an exception from an orphan background run task to the server log.
    The runner persists FAILED to the DB itself, so this is purely operator
    visibility — the run's row is correct regardless."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _log.error(
            "nomaflow.scheduler orphan run task raised %r — DB row already finalized by runner",
            exc,
            exc_info=exc,
        )


class JobScheduler:
    """In-process scheduler wrapping APScheduler 3.x AsyncIOScheduler.

    Construction takes everything it needs to run a job; :meth:`start` is the
    single entry point that brings it up. Sequencing is deliberate:

    1. Orphan-run sweep first — must finish before any new ``RUNNING`` rows
       appear, so it doesn't accidentally mark a freshly-started run as
       "abandoned".
    2. Register triggers for every enabled+scheduled job in the registry.
    3. Call ``scheduler.start()`` (non-blocking; APScheduler schedules in the
       current running event loop).
    """

    def __init__(
        self,
        registry: JobRegistry,
        runner: JobRunner,
        *,
        on_dropped_fire: Hook | None = None,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._on_dropped_fire = on_dropped_fire
        self._scheduler = AsyncIOScheduler()
        self._started = False
        # Tracks in-flight runs by job_id so the §4 "drop concurrent fires" rule
        # has somewhere to check. Set after run start, cleared after finish (in
        # the wrapper coroutine's finally block).
        self._in_flight: set[str] = set()
        # Last retention sweep — populated by the recurring task + the manual
        # admin endpoint. None until the first sweep completes; surfaced by
        # the Nomaflow Retention panel so operators can see "what did the
        # automatic sweep do" without trawling the server log.
        self._last_retention: SweepReport | None = None

    # -- lifecycle ------------------------------------------------------- #

    async def start(self, *, run_recovery: bool = True) -> None:
        """Start the scheduler.

        Idempotent: calling twice is a no-op the second time (a starting log
        line + return), so a hot-reload that calls start() on a registry swap
        doesn't double-start anything.

        ``run_recovery=False`` is a test escape hatch — production always
        runs the sweep, but unit tests that pre-seed orphan rows for assertion
        purposes can disable it.
        """
        if self._started:
            return
        if run_recovery:
            await mark_orphan_runs_failed(self._runner._db)  # noqa: SLF001 — same module-family
        for job in self._registry.scheduled_jobs():
            self._add_apscheduler_job(job)
        # Register the retention sweep before scheduler.start() so the first
        # tick lines up with the IntervalTrigger's "first fire = now + interval"
        # behaviour — operators see the first sweep on time without a startup
        # burst. The policy lives on [meta] in jobs.toml.
        self.schedule_retention(self._registry.config.meta.retention)
        self._scheduler.start()
        self._started = True
        _log.info(
            "nomaflow.scheduler started with %d scheduled job(s)",
            len(self._registry.scheduled_jobs()),
        )

    async def stop(self, *, wait: bool = True) -> None:
        """Shut the scheduler down. ``wait=True`` lets in-flight runs finish."""
        if not self._started:
            return
        self._scheduler.shutdown(wait=wait)
        self._started = False
        _log.info("nomaflow.scheduler stopped")

    # -- jobs management (hot-reload-friendly) -------------------------- #

    def add_job(self, job: Job) -> None:
        """Add a job to the live scheduler (chunk 3 hot-reload).

        No-op for a disabled job or one with no cron trigger at all (neither a
        job-level ``schedule`` nor any schedulable preset) — same filter as
        :meth:`JobRegistry.scheduled_jobs`."""
        if not job.enabled:
            return
        if not job.schedule and not any(p.schedule for p in job.presets):
            return
        self._add_apscheduler_job(job)

    def remove_job(self, job_id: str) -> None:
        """Remove a job's triggers from the live scheduler (chunk 3 hot-reload) — the
        base trigger AND every schedulable-preset trigger (id ``<job>::preset::<name>``)."""
        prefix = self._preset_job_id(job_id, "")  # "<job_id>::preset::"
        for sj in list(self._scheduler.get_jobs()):
            if sj.id == job_id or sj.id.startswith(prefix):
                try:
                    self._scheduler.remove_job(sj.id)
                except Exception:  # apscheduler raises JobLookupError; "remove if present"
                    pass

    async def fire_now(self, job: Job, *, triggered_by: str) -> None:
        """Manually trigger *job* — synchronous start of a new run.

        Used today by tests; chunk 3 wires the admin endpoint
        ``POST /admin/jobs/<id>/run`` through here. Returns when the run
        terminates (or raises if it does)."""
        await self._runner.run(job, ManualTrigger(triggered_by=triggered_by))

    async def fire_now_async(
        self,
        job: Job,
        *,
        triggered_by: str,
        op_kwargs_overrides: dict | None = None,
        params_override: dict | None = None,
        step_enabled_overrides: dict[str, bool] | None = None,
        log_level: str | None = None,
    ) -> str:
        """Manually trigger *job* — fire-and-return shape of :meth:`fire_now`.

        Allocates the :class:`JobRun` row synchronously (so the caller learns
        the run id immediately), then schedules the execution as a background
        task. Used by ``POST /admin/jobs/<id>/run``: a multi-minute JDE sync
        shouldn't tie up an admin HTTP request — the UI polls /admin/jobs to
        watch the run's state, the request returns at once with the new id.

        ``op_kwargs_overrides`` is the per-fire kwargs merge from the admin
        endpoint's "Run with parameters" UI — ``{step_name: {key: value}}``,
        threaded through to :meth:`JobRunner.execute_run`. ``None`` (the
        scheduled-fire default) leaves saved kwargs intact.

        ``step_enabled_overrides`` is the per-fire enable/disable toggle from
        the same UI — ``{step_name: bool}``. A resolved ``False`` makes the
        runner skip that step (CANCELED with "skipped: disabled", not a
        failure). ``None`` defers to each step's saved ``enabled`` value.

        Returns the new run's id. The background task is intentionally orphaned
        — the runner persists every state transition to the DB, so the row is
        recoverable from any client (cancel, requeue, etc.) without needing a
        handle to the asyncio Task.
        """
        trigger = ManualTrigger(triggered_by=triggered_by)
        run = await self._runner.create_run(job, trigger)
        task = asyncio.create_task(
            self._runner.execute_run(
                job, trigger, run,
                op_kwargs_overrides=op_kwargs_overrides,
                params_override=params_override,
                step_enabled_overrides=step_enabled_overrides,
                log_level=log_level,
            ),
        )
        # Belt-and-suspenders: if the task raises, the runner already persisted
        # FAILED on the row, but we still log so it's visible in the server's
        # own stdout (not just in the per-run log buffer).
        task.add_done_callback(_log_orphan_task_exception)
        return run.id

    # -- retention sweep ------------------------------------------------- #

    # Stable APScheduler job-id for the recurring retention task. The dot makes it
    # invalid as a nomaflow Job.id (operator ids may only contain alnum + ``-``/``_``,
    # see Job._validate_id), so this can't collide with a user-defined job's id.
    _RETENTION_JOB_ID = "__nomaflow.retention.sweep__"

    def schedule_retention(self, policy: RetentionPolicy) -> None:
        """Register the recurring cleanup task. Idempotent (re-call after a
        hot-reload to swap the interval / disable the sweep).

        ``policy.enabled = False`` removes any existing task without scheduling
        a new one. Operators can still hit :meth:`run_retention_now` for one-off
        cleanups in that mode (the master toggle controls only the AUTO sweep).
        """
        # Remove any prior registration so a hot-reload that shortens the
        # interval doesn't leave the old one firing alongside the new.
        try:
            self._scheduler.remove_job(self._RETENTION_JOB_ID)
        except Exception:
            pass            # JobLookupError when none was registered — fine
        if not policy.enabled:
            _log.info("nomaflow.retention auto-sweep disabled via [meta.retention] enabled=false")
            return
        # Snapshot the policy values so the closure binds the CURRENT shape;
        # a future hot-reload that calls schedule_retention again with a new
        # policy replaces this APScheduler job entirely (the policy snapshot
        # in the new closure replaces the old one).
        days = policy.days
        cap = policy.keep_last_per_job

        async def _do_sweep() -> None:
            # Build a fresh policy from the snapshot — the schema's defaults
            # for the other fields are fine here (we only care about days +
            # keep_last_per_job, the rest is meta about scheduling).
            snap = RetentionPolicy(days=days, keep_last_per_job=cap)
            try:
                self._last_retention = await sweep_run_history(self._runner._db, snap)  # noqa: SLF001 — same module-family
            except Exception:
                _log.exception("nomaflow.retention sweep raised — will retry on next interval")

        self._scheduler.add_job(
            _do_sweep,
            trigger=IntervalTrigger(minutes=policy.sweep_interval_minutes),
            id=self._RETENTION_JOB_ID,
            replace_existing=True,
            misfire_grace_time=None,
            coalesce=True,
            max_instances=1,
        )
        _log.info(
            "nomaflow.retention auto-sweep registered: every %d min, %d days, cap=%d/job",
            policy.sweep_interval_minutes, days, cap,
        )

    async def run_retention_now(self, policy: RetentionPolicy) -> SweepReport:
        """Fire a one-shot retention sweep + return the report. Used by the
        ``POST /admin/jobs/retention/sweep`` admin endpoint when an operator
        wants to clean up immediately after changing the policy."""
        self._last_retention = await sweep_run_history(self._runner._db, policy)  # noqa: SLF001 — same module-family
        return self._last_retention

    @property
    def last_retention(self) -> SweepReport | None:
        """The most recent sweep result, or None if no sweep has run yet.
        Surfaced by the Nomaflow Retention panel."""
        return self._last_retention

    # -- internals ------------------------------------------------------ #

    # Separator for a schedulable-preset's APScheduler id. ``::`` can't appear in a
    # nomaflow Job.id (alnum + ``-``/``_`` only — see Job._validate_id), so a preset
    # trigger id never collides with a base job-trigger id or another job's id.
    _PRESET_SEP = "::preset::"

    @classmethod
    def _preset_job_id(cls, job_id: str, preset_name: str) -> str:
        return f"{job_id}{cls._PRESET_SEP}{preset_name}"

    def _add_apscheduler_job(self, job: Job) -> None:
        """Register the cron trigger(s) that fire :meth:`_run_or_drop` for *job* — the
        job's own ``schedule`` (base fire, no overrides) plus one trigger per schedulable
        preset (fired with that preset's params / op_kwargs / step toggles / log level)."""
        common = dict(
            replace_existing=True,
            misfire_grace_time=None,  # PHASE13 §6: catchup=False default
            coalesce=True,  # if multiple fires queue up, take only the latest
            max_instances=1,  # one concurrent run per job — drop policy below
        )
        if job.schedule:
            self._scheduler.add_job(
                self._run_or_drop,
                trigger=CronTrigger.from_crontab(job.schedule, timezone=job.timezone),
                id=job.id,
                args=[job.id],
                **common,
            )
        for preset in job.presets:
            if not preset.schedule:
                continue
            tz = preset.timezone or job.timezone
            self._scheduler.add_job(
                self._run_or_drop,
                trigger=CronTrigger.from_crontab(preset.schedule, timezone=tz),
                id=self._preset_job_id(job.id, preset.name),
                args=[job.id, preset.name],
                **common,
            )

    async def _run_or_drop(self, job_id: str, preset_name: str | None = None) -> None:
        """Coroutine APScheduler invokes on each cron fire.

        ``preset_name`` is set when a schedulable preset's trigger fired — the run then
        applies that preset's params / op_kwargs / step toggles / log level (same shape
        as a manual "Run with parameters" fire). ``None`` is the plain job-level fire.

        Implements the "drop + alert" rule (PHASE13 §4): if a previous run of
        the same job is still in flight, drop this fire and notify via the
        ``on_dropped_fire`` hook (Phase 9 ``/technical`` Socket.IO room — wired
        in chunk 3). The in-flight key is the JOB id, so a preset fire is dropped
        while the base job (or another preset) of the same job is running — one
        concurrent run per job, regardless of which trigger fired it.
        """
        # Look up the *current* job — registry may have been swapped via hot-reload
        # since the trigger was registered, and the latest steps/policy are what
        # should run.
        try:
            job = self._registry.get(job_id)
        except Exception:
            _log.warning("nomaflow.scheduler fire for unknown job %r — dropped", job_id)
            return

        # Resolve the preset fresh (a hot-reload may have renamed/removed it, or dropped
        # its schedule) — a stale preset trigger fires into nothing rather than the wrong params.
        preset = None
        if preset_name is not None:
            preset = next((p for p in job.presets if p.name == preset_name and p.schedule), None)
            if preset is None:
                _log.warning(
                    "nomaflow.scheduler fire for unknown/unscheduled preset %r on job %r — dropped",
                    preset_name, job_id,
                )
                return

        if job_id in self._in_flight:
            _log.warning(
                "nomaflow.scheduler %r already RUNNING — dropping concurrent fire", job_id
            )
            if self._on_dropped_fire is not None:
                try:
                    await self._on_dropped_fire(
                        "job_run.dropped",
                        {"job_id": job_id, "reason": "previous_run_still_running"},
                    )
                except Exception:  # broadcast failure shouldn't take the scheduler down
                    _log.exception("nomaflow.scheduler dropped-fire hook raised")
            return

        self._in_flight.add(job_id)
        try:
            # APScheduler hands us the actual fire time on the calling context's
            # job (the "fired at" instant). For 3.x we recover it from datetime.now;
            # for sub-second precision we'd need to extract from the apscheduler
            # context, but daily/hourly crons don't need that.
            trigger = ScheduledTrigger(
                fired_at=datetime.now(timezone.utc), preset_name=preset_name,
            )
            if preset is not None:
                # Empty dicts → None so the runner's "no override" fast path is taken.
                await self._runner.run(
                    job, trigger,
                    op_kwargs_overrides=preset.op_kwargs or None,
                    params_override=preset.params or None,
                    step_enabled_overrides=preset.step_enabled or None,
                    log_level=preset.log_level,
                )
            else:
                await self._runner.run(job, trigger)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The runner's state machine already persisted the failure; here we
            # just keep the scheduler alive for the next fire.
            _log.exception("nomaflow.scheduler %r failed", job_id)
        finally:
            self._in_flight.discard(job_id)

    # -- introspection (for the future monitoring Screen + tests) ----- #

    @property
    def scheduled_job_ids(self) -> list[str]:
        """The ids APScheduler currently has triggers for — order isn't guaranteed.
        Filters out internal nomaflow tasks (retention sweep, etc.) — callers want the
        operator-defined jobs only; the internal id wouldn't match anything in the
        registry anyway."""
        return [j.id for j in self._scheduler.get_jobs() if not j.id.startswith("__nomaflow.")]

    @property
    def next_fire_times(self) -> dict[str, datetime]:
        """``{job_id: next_run_time}`` for every registered job APScheduler will fire.

        Reads APScheduler's in-memory ``next_run_time`` — cheap, no I/O. A job whose
        ``next_run_time`` is None (paused, or never scheduled) is omitted. Powers the
        "next run" column of the Jobs list (``GET /admin/jobs``). Internal nomaflow
        tasks (retention sweep) are filtered out for the same reason as
        :attr:`scheduled_job_ids`."""
        return {
            j.id: j.next_run_time
            for j in self._scheduler.get_jobs()
            if j.next_run_time is not None and not j.id.startswith("__nomaflow.")
        }

    @property
    def in_flight_job_ids(self) -> frozenset[str]:
        return frozenset(self._in_flight)

    @property
    def is_started(self) -> bool:
        return self._started
