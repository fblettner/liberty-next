"""JobRunner — orchestrates a single run of a :class:`~liberty.jobs.schema.Job`.

The state machine and persistence contract are spelled out in
``docs/PHASE13.md`` §4. Summary:

* One :class:`JobRun` row per fire (scheduled or manual). The
  ``UNIQUE (job_id, scheduled_at)`` constraint dedups scheduled triggers;
  duplicate fires return the existing run rather than starting a new one.
* For each step, one or more :class:`StepRun` rows (one per attempt). Retry
  policy applies *per step independently*, not across the whole job.
* A step's final failure → the job is FAILED, remaining steps are SKIPPED
  (recorded as SKIPPED so the Screen can show which step actually broke vs
  which were never reached).
* Always set ``finished_at`` and broadcast the terminal state, even on
  cancellation / unexpected errors.

The runner takes a broadcaster + clock as constructor deps so tests can run
without Socket.IO and without ``asyncio.sleep``-ing through real backoffs.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from liberty.jobs.db import JobDatabase
from liberty.jobs.models import JobRun, RunLog, RunState, StepRun
from liberty.jobs.runlog import (
    finish_run, registered_namespaces, reset_run_context, set_run_context,
)
from liberty.jobs.schema import BackoffKind, Job, JobRetry, Step, StepType
from liberty.jobs.steps.base import (
    RunContext,
    StepCancelled,
    StepExecutor,
    StepFailed,
    StepResult,
)
from liberty.jobs.triggers import ManualTrigger, ScheduledTrigger, Trigger

_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# extension points (optional)
# --------------------------------------------------------------------------- #


@runtime_checkable
class Broadcaster(Protocol):
    """Pushes state transitions to the live UI — implemented by Phase 9's Socket.IO
    glue (lands in chunk 3). The runner calls this on every state change but is
    fully functional without it (default None in :class:`JobRunner`)."""

    async def broadcast(self, event: str, payload: dict) -> None:  # pragma: no cover
        ...


# A clock that ``await``-s — defaults to ``asyncio.sleep``; tests pass a no-op.
SleepFn = Callable[[float], Awaitable[None]]


# --------------------------------------------------------------------------- #
# small helpers — kept module-level so tests can poke them
# --------------------------------------------------------------------------- #


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def compute_backoff_delay(policy: JobRetry | None, attempt: int) -> float:
    """Seconds to wait before *attempt* (1-indexed; attempt=1 → 0 — never delay
    before the first try).

    ``fixed`` returns ``base_seconds`` constant. ``exponential`` doubles each
    time and adds up-to-1-second jitter so a synchronized cluster doesn't all
    retry at the same millisecond. Returns 0 when no policy is set or when
    we're on the first attempt.
    """
    if policy is None or attempt <= 1:
        return 0.0
    base = policy.base_seconds
    if policy.backoff is BackoffKind.FIXED:
        return float(base)
    # exponential: base * 2^(attempt-2). attempt=2 → base, attempt=3 → 2*base, …
    return float(base) * (2 ** (attempt - 2)) + random.uniform(0.0, 1.0)


def _truncate_log(value: str | None, *, limit: int = 4096) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[-limit:]


# --------------------------------------------------------------------------- #
# JobRunner
# --------------------------------------------------------------------------- #


class JobRunner:
    """Runs one job-fire to completion (or cancellation), end-to-end.

    Construction wires the four collaborators it needs: the persistence DB,
    the executor map (one per :class:`StepType`), an optional broadcaster, and
    an optional sleep function for tests. ``run()`` is the single public entry
    point; everything else is an internal helper.
    """

    def __init__(
        self,
        db: JobDatabase,
        executors: Mapping[StepType, StepExecutor],
        *,
        broadcaster: Broadcaster | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._db = db
        self._executors = dict(executors)
        self._broadcaster = broadcaster
        self._sleep = sleep
        # run_id → the asyncio.Task currently running it. Populated at the top of
        # run() and cleared in its finally block. Drives :meth:`cancel`: the admin
        # endpoint looks up the task and calls .cancel() on it, which raises
        # CancelledError inside the run loop → terminal state CANCELED.
        self._active_runs: dict[str, asyncio.Task] = {}
        # run_id → effective overrides (log_level + params_override) for that
        # in-flight run. Populated when execute_run starts; consumed by the
        # :class:`CallJobExecutor` via :meth:`read_parent_overrides` so a
        # child run inherits its parent's DEBUG flag + per-fire params. Cleared
        # in execute_run's finally block (same shape as ``_active_runs``).
        self._effective_overrides: dict[str, dict[str, Any]] = {}

    def add_executor(self, step_type: StepType, executor: StepExecutor) -> None:
        """Register an executor AFTER construction. Used by the wiring layer for
        the :class:`CallJobExecutor`, which needs a back-reference to this
        runner — the chicken-and-egg ordering means we can't include it in the
        constructor's executor map. Idempotent on the step_type key (a second
        call overwrites the first; used by tests + hot-reload)."""
        self._executors[step_type] = executor

    def read_parent_overrides(self, run_id: str) -> dict[str, Any]:
        """Return the effective overrides for an in-flight run — used by
        :class:`CallJobExecutor` to inherit ``log_level`` + ``params_override``
        into the child run. Returns an empty dict for unknown runs (defensive:
        if the run isn't tracked, the child gets clean defaults — which is the
        safe behaviour for an unexpected lookup)."""
        return self._effective_overrides.get(run_id, {})

    # -- public ----------------------------------------------------------- #

    async def create_run(self, job: Job, trigger: Trigger) -> JobRun:
        """Allocate (or load) the :class:`JobRun` row for *job*+*trigger* and
        return immediately, before any step executes.

        Splits the runner's lifecycle in two so a caller that needs the run id
        without waiting for the run to finish (the "Run now" admin endpoint —
        manual fire of a multi-minute sync) can:
        ``run = await create_run(...); asyncio.create_task(execute_run(..., run))``.
        Same UNIQUE-constraint dedup as :meth:`run` — a duplicate scheduled fire
        returns the existing in-flight row instead of starting a parallel one.
        """
        return await self._open_or_resume(job, trigger)

    async def execute_run(
        self,
        job: Job,
        trigger: Trigger,
        run: JobRun,
        *,
        op_kwargs_overrides: dict[str, dict[str, Any]] | None = None,
        params_override: dict[str, Any] | None = None,
        step_enabled_overrides: dict[str, bool] | None = None,
        log_level: str | None = None,
        parent_chain: list[tuple[str, str]] | None = None,
    ) -> JobRun:
        """Execute an allocated *run* — the long-lived part of the lifecycle.

        Must be paired with a prior :meth:`create_run` call (this method assumes
        the JobRun row already exists). Transitions to RUNNING, processes steps,
        finalizes the row, returns it in its terminal state.

        ``op_kwargs_overrides`` is the ephemeral per-fire merge — a mapping of
        ``step.name → {key: value}`` that wins over the saved ``step.op_kwargs``
        for THIS run only (the jobs.toml is untouched). Used by the "Run with
        parameters" admin flow: an operator triggers a job, edits the params
        once, the modified values flow into the step's callable without
        persisting back to disk. ``None`` (the scheduled-fire default) leaves
        every step's saved kwargs intact.

        ``step_enabled_overrides`` is the ephemeral per-fire "skip this step"
        toggle — a mapping of ``step.name → bool`` that wins over the saved
        ``step.enabled``. When the resolved value is ``False`` the runner
        records the step as CANCELED with ``"skipped: disabled"`` and
        continues — DOES NOT short-circuit the run (different shape from a
        step failure, which marks all downstream steps SKIPPED). Useful from
        the Run-with-parameters modal when only one phase of a multi-step job
        needs re-running after a manual fix.

        ``log_level`` is the per-fire log verbosity override. ``"DEBUG"`` raises
        every registered logger (``liberty`` + plugin namespaces — see
        :func:`liberty.jobs.runlog.registered_namespaces`) to DEBUG for this
        run's duration so the full SQL of every ``run_query`` lands in the log
        buffer. ``None`` (default) uses ``job.log_level`` from jobs.toml (also
        defaults to ``"INFO"``). Pre-existing logger levels are restored in
        the finally block regardless of how the run terminates.

        ``parent_chain`` is the call_job ancestry — every (job_id, run_id) of
        a parent run when this execution was spawned by a ``call_job`` step.
        Top-level fires (scheduled / manual / API) pass ``None``. The chain is
        threaded onto :attr:`RunContext.parent_chain` so the
        :class:`CallJobExecutor` can detect cycles before spawning further
        children. The chain is also used to mark this run's overrides cache so
        :meth:`read_parent_overrides` can return the parent's settings for
        inheritance (DEBUG cascades, ``params_override`` cascades).
        """
        if run.state in (RunState.SUCCEEDED.value, RunState.FAILED.value, RunState.CANCELED.value):
            # Resumed a run that already terminated — return it as-is. Happens
            # when a duplicate scheduled fire arrives after the first run finished.
            return run

        await self._transition(run, RunState.RUNNING)

        # Register this run's task so cancel() can find + interrupt it. Using
        # current_task() means the registration matches the actual asyncio Task
        # the scheduler / admin endpoint is awaiting, so .cancel() on it raises
        # CancelledError exactly inside our run loop (not in some sibling).
        current = asyncio.current_task()
        if current is not None:
            self._active_runs[run.id] = current

        # Bind this run id to the async context so every log line emitted from
        # here on — including from the executor tasks — is captured into the
        # run's log buffer (see liberty.jobs.runlog). Reset in the finally.
        log_token = set_run_context(run.id)

        # Apply per-fire log level override (or fall back to job-level setting).
        # Raises the registered loggers to DEBUG when requested so the full SQL
        # surfaces in the run log; the previous levels are saved in
        # ``saved_log_levels`` and restored in the finally — never raises a
        # logger ABOVE its prior level (otherwise we'd hide log lines a parallel
        # run is depending on).
        effective_log_level = (log_level or job.log_level or "INFO").upper()
        saved_log_levels: dict[str, int] = {}
        if effective_log_level == "DEBUG":
            for ns in registered_namespaces():
                lg = logging.getLogger(ns)
                saved_log_levels[ns] = lg.level
                if lg.level == logging.NOTSET or lg.level > logging.DEBUG:
                    lg.setLevel(logging.DEBUG)

        _log.info(
            "nomaflow.runner started job=%s run=%s trigger=%s steps=%d log_level=%s",
            job.id, run.id, trigger.kind, len(job.steps), effective_log_level,
        )

        # Stash the effective overrides so a call_job step in this run can
        # query them via :meth:`read_parent_overrides` and pass them on to the
        # child run. Cleared in the finally block (memory leak otherwise — the
        # in-flight runs map keeps cleanup honest already, this is its sibling).
        self._effective_overrides[run.id] = {
            "log_level": effective_log_level if effective_log_level == "DEBUG" else None,
            "params_override": dict(params_override) if params_override else None,
        }

        ctx = RunContext(
            run_id=run.id, job_id=job.id, trigger=trigger,
            parent_chain=list(parent_chain or []),
        )
        terminal = RunState.SUCCEEDED  # optimistic; flipped on first failure
        total_rows = 0
        executed_step_count = 0
        # When a step fails, the last error string lands here for _finalize to
        # write to JobRun.error_message. Empty string stays empty for success.
        failed_step_error: str | None = None

        try:
            for idx, step in enumerate(job.steps):
                executed_step_count = idx + 1
                # Per-step enabled resolution — per-fire override (from the
                # Run-with-parameters modal) wins over the saved jobs.toml
                # value. ``False`` short-circuits the step BEFORE the merge +
                # executor work (cheaper) but still records a StepRun row so
                # the timeline is complete. Does NOT mark the run as failed —
                # disabled is operator intent, not an error.
                resolved_enabled = (step_enabled_overrides or {}).get(step.name, step.enabled)
                if not resolved_enabled:
                    _log.info(
                        "nomaflow.runner step=%s SKIPPED (disabled %s)",
                        step.name,
                        "by per-fire override" if (step_enabled_overrides or {}).get(step.name) is False
                            else "in jobs.toml",
                    )
                    await self._record_disabled_step(run, idx, step)
                    continue
                # Per-fire op_kwargs override + job-level defaults — merged via
                # model_copy so the in-memory ``job`` registry is never mutated (a
                # subsequent scheduled fire of the same job still uses the saved
                # kwargs). Layer order, each later wins over earlier:
                #   1. job.params              (saved job-level defaults)
                #   2. step.op_kwargs          (step-specific saved kwargs)
                #   3. params_override         (per-fire override of job-level)
                #   4. op_kwargs_overrides[step.name]  (per-fire override of step-level)
                # Skip the whole rebuild for the common no-override case.
                override = (op_kwargs_overrides or {}).get(step.name)
                if job.params or override or params_override:
                    effective_step = step.model_copy(update={
                        "op_kwargs": {
                            **job.params,
                            **step.op_kwargs,
                            **(params_override or {}),
                            **(override or {}),
                        },
                    })
                else:
                    effective_step = step
                step_result = await self._run_step_with_retry(run, idx, effective_step, job.retry, ctx)
                if isinstance(step_result, str):
                    # Step failed after all retries (string is the last error).
                    # Stash on the run + mark remaining steps SKIPPED.
                    terminal = RunState.FAILED
                    failed_step_error = step_result
                    await self._record_skipped(run, job, start_index=idx + 1)
                    break
                if step_result.rows_affected is not None:
                    total_rows += step_result.rows_affected
                ctx.prev_rows_affected = step_result.rows_affected
        except asyncio.CancelledError:
            terminal = RunState.CANCELED
            await self._finalize(run, terminal, total_rows=total_rows, error="cancelled mid-run")
            raise
        except Exception as exc:
            # Should never reach here — _run_step_with_retry wraps everything
            # — but if it does, we still want a terminal state on the row.
            _log.exception("nomaflow.runner unexpected error in run %s", run.id)
            terminal = RunState.FAILED
            await self._finalize(run, terminal, total_rows=total_rows, error=str(exc))
            return await self._reload(run.id)
        finally:
            # Always clear the active-run registration — even on cancellation,
            # even on early return for resumed-terminal runs. cancel() lookups
            # on a finished run should be a clean miss. Unbind the log context
            # too (the run's buffer is flushed to the DB by _finalize, which
            # took the run id explicitly — it doesn't need the contextvar).
            self._active_runs.pop(run.id, None)
            # Drop the overrides cache entry — a call_job child run could query
            # this for parent inheritance, but once the parent terminates the
            # entry is dead weight. Same lifecycle as ``_active_runs``.
            self._effective_overrides.pop(run.id, None)
            reset_run_context(log_token)
            # Restore the saved logger levels (if we raised any for DEBUG). A
            # parallel run shouldn't have its DEBUG capture downgraded by us
            # finishing, so we only restore loggers we explicitly raised.
            for ns, prev_level in saved_log_levels.items():
                logging.getLogger(ns).setLevel(prev_level)

        await self._finalize(run, terminal, total_rows=total_rows, error=failed_step_error)
        _log.info(
            "nomaflow.runner job=%s run=%s steps=%d state=%s",
            job.id, run.id, executed_step_count, terminal.value,
        )
        return await self._reload(run.id)

    async def run(
        self,
        job: Job,
        trigger: Trigger,
        *,
        op_kwargs_overrides: dict[str, dict[str, Any]] | None = None,
        params_override: dict[str, Any] | None = None,
        log_level: str | None = None,
    ) -> JobRun:
        """Allocate + execute *job* under *trigger* — the blocking shape, kept
        for callers (the scheduled-fire path, tests) that legitimately need to
        wait for the terminal state. Equivalent to
        ``await execute_run(job, trigger, await create_run(job, trigger), …)``."""
        run = await self.create_run(job, trigger)
        return await self.execute_run(
            job, trigger, run,
            op_kwargs_overrides=op_kwargs_overrides,
            params_override=params_override,
            log_level=log_level,
        )

    def cancel(self, run_id: str) -> bool:
        """Cancel an in-flight run by id. Returns True if a task was found and
        cancellation requested; False if the run isn't currently in flight
        (already terminated, or never existed under this runner instance).

        The actual transition to CANCELED happens inside :meth:`run`'s
        ``CancelledError`` handler — this method just signals; the persistence
        update is the run loop's responsibility. Idempotent: cancelling an
        already-cancelled task is a harmless no-op.
        """
        task = self._active_runs.get(run_id)
        if task is None:
            return False
        if not task.done():
            task.cancel()
        return True

    @property
    def active_run_ids(self) -> frozenset[str]:
        """Snapshot of in-flight run ids — diagnostic + test helper."""
        return frozenset(self._active_runs.keys())

    # -- internals: run lifecycle ----------------------------------------- #

    async def _open_or_resume(self, job: Job, trigger: Trigger) -> JobRun:
        """Insert a new :class:`JobRun` (state=QUEUED) — or return the existing
        one if a duplicate scheduled fire is detected.

        The UNIQUE constraint on ``(job_id, scheduled_at)`` is the single
        source of truth for dedup: we don't pre-check; we let the insert fail
        and recover. That eliminates the read-modify-write race two competing
        workers would otherwise be in.
        """
        scheduled_at = trigger.fired_at if isinstance(trigger, ScheduledTrigger) else None
        triggered_by = trigger.triggered_by if isinstance(trigger, ManualTrigger) else None

        async with self._db.session() as session:
            run = JobRun(
                job_id=job.id,
                trigger_kind=trigger.kind,
                triggered_by=triggered_by,
                scheduled_at=scheduled_at,
                state=RunState.QUEUED.value,
            )
            session.add(run)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                # Find the row the other fire already created. There's exactly one.
                async with self._db.session() as s2:
                    found = (await s2.execute(
                        select(JobRun)
                        .where(JobRun.job_id == job.id)
                        .where(JobRun.scheduled_at == scheduled_at)
                    )).scalar_one()
                _log.info(
                    "nomaflow.runner duplicate scheduled fire for %s @ %s — returning existing run %s",
                    job.id, scheduled_at, found.id,
                )
                return found
            # session __aexit__ commits — `run` is now persisted with state QUEUED
        return await self._reload(run.id)

    async def _transition(self, run: JobRun, new_state: RunState) -> None:
        async with self._db.session() as session:
            row = await session.get(JobRun, run.id)
            assert row is not None
            row.state = new_state.value
            if new_state is RunState.RUNNING and row.started_at is None:
                row.started_at = _utcnow()
        if self._broadcaster is not None:
            await self._broadcaster.broadcast(
                "job_run.state",
                {"run_id": run.id, "job_id": run.job_id, "state": new_state.value},
            )

    async def _finalize(
        self,
        run: JobRun,
        state: RunState,
        *,
        total_rows: int,
        error: str | None = None,
    ) -> None:
        # Flush the run's in-memory log buffer to its durable nomaflow_run_logs
        # row. finish_run() pops the buffer — after this the logs endpoint reads
        # the DB for this (now-terminal) run, not the live buffer.
        log_text = finish_run(run.id)
        async with self._db.session() as session:
            row = await session.get(JobRun, run.id)
            assert row is not None
            row.state = state.value
            row.finished_at = _utcnow()
            row.rows_affected = total_rows
            if error is not None:
                row.error_message = error[:4096]
            if log_text:
                session.add(RunLog(run_id=run.id, logs=log_text))
        if self._broadcaster is not None:
            await self._broadcaster.broadcast(
                "job_run.state",
                {"run_id": run.id, "job_id": run.job_id, "state": state.value,
                 "rows_affected": total_rows, "error": error},
            )

    async def _reload(self, run_id: str) -> JobRun:
        async with self._db.session() as session:
            row = await session.get(JobRun, run_id)
            assert row is not None
            # Touch the relationship so callers can read step_runs after the
            # session closes (lazy='selectin' on the relationship handles this).
            _ = row.step_runs
            return row

    # -- internals: step lifecycle --------------------------------------- #

    async def _run_step_with_retry(
        self,
        run: JobRun,
        index: int,
        step: Step,
        policy: JobRetry | None,
        ctx: RunContext,
    ) -> "StepResult | str":
        """Run *step* with retries per *policy*; return the StepResult on success,
        an error STRING (the last failed attempt's message) when all retries are
        exhausted. The caller writes this string onto the JobRun.error_message
        so operators see WHICH step failed without drilling into per-step
        records, and so a call_job parent can re-raise it as the cause of its
        own failure."""
        attempts = policy.attempts if policy is not None else 1
        last_error = f"step {step.name!r} failed after {attempts} attempt(s)"
        for attempt in range(1, attempts + 1):
            delay = compute_backoff_delay(policy, attempt)
            if delay > 0:
                await self._sleep(delay)

            step_run = await self._record_step_start(run, index, step, attempt)
            _log.info(
                "nomaflow.runner run=%s step[%d]=%r type=%s attempt=%d/%d",
                run.id, index, step.name, step.type.value, attempt, attempts,
            )
            executor = self._executors.get(step.type)
            if executor is None:
                err = f"no executor registered for step type {step.type.value!r}"
                await self._record_step_failure(step_run.id, error=err)
                return err

            try:
                result = await asyncio.wait_for(
                    executor.execute(step, ctx),
                    timeout=step.timeout_seconds,
                )
            except asyncio.TimeoutError:
                last_error = f"step timed out after {step.timeout_seconds}s"
                await self._record_step_failure(step_run.id, error=last_error)
                if attempt >= attempts:
                    return last_error
                continue
            except asyncio.CancelledError:
                await self._record_step_state(step_run.id, RunState.CANCELED, error="cancelled")
                raise
            except StepCancelled as exc:
                # Executor signalled an intentional cancel (e.g. operator pressed
                # Cancel mid-execution); record it and stop — do *not* retry.
                await self._record_step_state(step_run.id, RunState.CANCELED, error=str(exc))
                return f"step {step.name!r} cancelled: {exc}"
            except StepFailed as exc:
                last_error = str(exc)
                await self._record_step_failure(step_run.id, error=last_error)
                if attempt >= attempts:
                    return last_error
                continue
            except Exception as exc:
                # Unexpected — still counts as a failed attempt for retry, but log
                # with stack trace so the unexpected nature is visible in logs.
                _log.exception(
                    "nomaflow.runner unexpected exception in step %r (run=%s, attempt=%d)",
                    step.name, run.id, attempt,
                )
                last_error = f"unexpected: {type(exc).__name__}: {exc}"
                await self._record_step_failure(step_run.id, error=last_error)
                if attempt >= attempts:
                    return last_error
                continue

            await self._record_step_success(step_run.id, result)
            return result

        return None  # unreachable; satisfies type checker

    async def _record_step_start(
        self, run: JobRun, index: int, step: Step, attempt: int,
    ) -> StepRun:
        async with self._db.session() as session:
            sr = StepRun(
                run_id=run.id,
                step_index=index,
                step_name=step.name,
                step_type=step.type.value,
                attempt=attempt,
                state=RunState.RUNNING.value,
            )
            session.add(sr)
            await session.flush()
            row_id = sr.id
        if self._broadcaster is not None:
            await self._broadcaster.broadcast(
                "step_run.state",
                {"run_id": run.id, "step_run_id": row_id, "step_name": step.name,
                 "attempt": attempt, "state": RunState.RUNNING.value},
            )
        return sr

    async def _record_step_success(self, step_run_id: str, result: StepResult) -> None:
        async with self._db.session() as session:
            row = await session.get(StepRun, step_run_id)
            assert row is not None
            row.state = RunState.SUCCEEDED.value
            row.finished_at = _utcnow()
            row.rows_affected = result.rows_affected
            row.log_excerpt = _truncate_log(result.log_excerpt)
        if self._broadcaster is not None:
            await self._broadcaster.broadcast(
                "step_run.state",
                {"step_run_id": step_run_id, "state": RunState.SUCCEEDED.value,
                 "rows_affected": result.rows_affected},
            )

    async def _record_step_failure(self, step_run_id: str, *, error: str) -> None:
        await self._record_step_state(step_run_id, RunState.FAILED, error=error)

    async def _record_step_state(
        self, step_run_id: str, state: RunState, *, error: str | None = None,
    ) -> None:
        async with self._db.session() as session:
            row = await session.get(StepRun, step_run_id)
            assert row is not None
            row.state = state.value
            row.finished_at = _utcnow()
            if error is not None:
                row.error_message = error[:4096]
        if self._broadcaster is not None:
            await self._broadcaster.broadcast(
                "step_run.state",
                {"step_run_id": step_run_id, "state": state.value, "error": error},
            )

    async def _record_skipped(self, run: JobRun, job: Job, *, start_index: int) -> None:
        """Insert SKIPPED StepRun rows for steps that never executed because an
        earlier step failed terminally. Gives the Screen a complete step list
        instead of a gap that would confuse the operator."""
        if start_index >= len(job.steps):
            return
        async with self._db.session() as session:
            now = _utcnow()
            for idx in range(start_index, len(job.steps)):
                step = job.steps[idx]
                session.add(StepRun(
                    run_id=run.id,
                    step_index=idx,
                    step_name=step.name,
                    step_type=step.type.value,
                    attempt=1,
                    state=RunState.CANCELED.value,
                    started_at=now,
                    finished_at=now,
                    error_message="skipped: earlier step failed",
                ))

    async def _record_disabled_step(self, run: JobRun, idx: int, step: Step) -> None:
        """Insert a single CANCELED StepRun row for a step that was disabled
        (jobs.toml ``enabled = false`` or a per-fire ``step_enabled`` override).

        Different from :meth:`_record_skipped` (which marks every step from a
        failure-point downward): a disabled step doesn't fail the run and
        doesn't cascade — the next step still runs. We share the CANCELED
        state with skipped/cancelled so the UI's terminal-state filter
        treats them the same; the ``error_message`` distinguishes the cause."""
        async with self._db.session() as session:
            now = _utcnow()
            session.add(StepRun(
                run_id=run.id,
                step_index=idx,
                step_name=step.name,
                step_type=step.type.value,
                attempt=1,
                state=RunState.CANCELED.value,
                started_at=now,
                finished_at=now,
                error_message="skipped: disabled",
            ))
