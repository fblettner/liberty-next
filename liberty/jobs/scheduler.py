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

from liberty.jobs.recovery import mark_orphan_runs_failed
from liberty.jobs.registry import JobRegistry
from liberty.jobs.runner import JobRunner
from liberty.jobs.schema import Job
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

        No-op for an unscheduled job (no cron expression) or a disabled job —
        same filter as :meth:`JobRegistry.scheduled_jobs`."""
        if not job.enabled or not job.schedule:
            return
        self._add_apscheduler_job(job)

    def remove_job(self, job_id: str) -> None:
        """Remove a job from the live scheduler (chunk 3 hot-reload)."""
        try:
            self._scheduler.remove_job(job_id)
        except Exception:  # apscheduler raises JobLookupError; catch broadly so a
            # missing job is silent — that's the right behaviour for "remove if
            # present" semantics during a reload.
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

        Returns the new run's id. The background task is intentionally orphaned
        — the runner persists every state transition to the DB, so the row is
        recoverable from any client (cancel, requeue, etc.) without needing a
        handle to the asyncio Task.
        """
        trigger = ManualTrigger(triggered_by=triggered_by)
        run = await self._runner.create_run(job, trigger)
        task = asyncio.create_task(
            self._runner.execute_run(job, trigger, run, op_kwargs_overrides=op_kwargs_overrides),
        )
        # Belt-and-suspenders: if the task raises, the runner already persisted
        # FAILED on the row, but we still log so it's visible in the server's
        # own stdout (not just in the per-run log buffer).
        task.add_done_callback(_log_orphan_task_exception)
        return run.id

    # -- internals ------------------------------------------------------ #

    def _add_apscheduler_job(self, job: Job) -> None:
        """Register one cron trigger that fires :meth:`_run_or_drop` on schedule."""
        trigger = CronTrigger.from_crontab(job.schedule, timezone=job.timezone)
        self._scheduler.add_job(
            self._run_or_drop,
            trigger=trigger,
            id=job.id,
            args=[job.id],
            replace_existing=True,
            misfire_grace_time=None,  # PHASE13 §6: catchup=False default
            coalesce=True,  # if multiple fires queue up, take only the latest
            max_instances=1,  # one concurrent run per job — drop policy below
        )

    async def _run_or_drop(self, job_id: str) -> None:
        """Coroutine APScheduler invokes on each cron fire.

        Implements the "drop + alert" rule (PHASE13 §4): if a previous run of
        the same job is still in flight, drop this fire and notify via the
        ``on_dropped_fire`` hook (Phase 9 ``/technical`` Socket.IO room — wired
        in chunk 3). The ``max_instances=1`` in :meth:`_add_apscheduler_job`
        also enforces this at the APScheduler layer; this in-Python check is
        belt-and-suspenders + gives us the broadcast hook.
        """
        # Look up the *current* job — registry may have been swapped via hot-reload
        # since the trigger was registered, and the latest steps/policy are what
        # should run.
        try:
            job = self._registry.get(job_id)
        except Exception:
            _log.warning("nomaflow.scheduler fire for unknown job %r — dropped", job_id)
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
            await self._runner.run(job, ScheduledTrigger(fired_at=datetime.now(timezone.utc)))
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
        """The ids APScheduler currently has triggers for — order isn't guaranteed."""
        return [j.id for j in self._scheduler.get_jobs()]

    @property
    def next_fire_times(self) -> dict[str, datetime]:
        """``{job_id: next_run_time}`` for every registered job APScheduler will fire.

        Reads APScheduler's in-memory ``next_run_time`` — cheap, no I/O. A job whose
        ``next_run_time`` is None (paused, or never scheduled) is omitted. Powers the
        "next run" column of the Jobs list (``GET /admin/jobs``)."""
        return {
            j.id: j.next_run_time
            for j in self._scheduler.get_jobs()
            if j.next_run_time is not None
        }

    @property
    def in_flight_job_ids(self) -> frozenset[str]:
        return frozenset(self._in_flight)

    @property
    def is_started(self) -> bool:
        return self._started
