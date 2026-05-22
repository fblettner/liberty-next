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
from typing import Awaitable, Callable, Mapping, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from liberty.jobs.db import JobDatabase
from liberty.jobs.models import JobRun, RunState, StepRun
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

    # -- public ----------------------------------------------------------- #

    async def run(self, job: Job, trigger: Trigger) -> JobRun:
        """Execute *job* under *trigger*, persisting state along the way.

        For scheduled triggers, a concurrent duplicate fire (same job_id +
        scheduled_at) is detected at INSERT time by the ``UNIQUE`` constraint;
        the runner catches the resulting :class:`IntegrityError`, loads the
        existing run, and returns it unchanged — the second caller sees the
        same outcome the first one is producing.

        For manual triggers (``scheduled_at = NULL``), the UNIQUE check is
        vacuous and every call starts a fresh run — operators can click
        "Run now" repeatedly and each click is its own row.

        Returns the (possibly already-running) :class:`JobRun` row in its
        terminal state.
        """
        run = await self._open_or_resume(job, trigger)
        if run.state in (RunState.SUCCEEDED.value, RunState.FAILED.value, RunState.CANCELED.value):
            # Resumed a run that already terminated — return it as-is. Happens
            # when a duplicate scheduled fire arrives after the first run finished.
            return run

        await self._transition(run, RunState.RUNNING)

        ctx = RunContext(run_id=run.id, job_id=job.id, trigger=trigger)
        terminal = RunState.SUCCEEDED  # optimistic; flipped on first failure
        total_rows = 0
        executed_step_count = 0

        try:
            for idx, step in enumerate(job.steps):
                executed_step_count = idx + 1
                step_result = await self._run_step_with_retry(run, idx, step, job.retry, ctx)
                if step_result is None:
                    # Step failed after all retries — mark remaining steps SKIPPED.
                    terminal = RunState.FAILED
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

        await self._finalize(run, terminal, total_rows=total_rows)
        _log.info(
            "nomaflow.runner job=%s run=%s steps=%d state=%s",
            job.id, run.id, executed_step_count, terminal.value,
        )
        return await self._reload(run.id)

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
        async with self._db.session() as session:
            row = await session.get(JobRun, run.id)
            assert row is not None
            row.state = state.value
            row.finished_at = _utcnow()
            row.rows_affected = total_rows
            if error is not None:
                row.error_message = error[:4096]
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
    ) -> StepResult | None:
        """Run *step* with retries per *policy*; return the StepResult on success,
        None when all retries are exhausted (caller marks remaining steps SKIPPED)."""
        attempts = policy.attempts if policy is not None else 1
        for attempt in range(1, attempts + 1):
            delay = compute_backoff_delay(policy, attempt)
            if delay > 0:
                await self._sleep(delay)

            step_run = await self._record_step_start(run, index, step, attempt)
            executor = self._executors.get(step.type)
            if executor is None:
                await self._record_step_failure(
                    step_run.id,
                    error=f"no executor registered for step type {step.type.value!r}",
                )
                return None

            try:
                result = await asyncio.wait_for(
                    executor.execute(step, ctx),
                    timeout=step.timeout_seconds,
                )
            except asyncio.TimeoutError:
                await self._record_step_failure(
                    step_run.id,
                    error=f"step timed out after {step.timeout_seconds}s",
                )
                if attempt >= attempts:
                    return None
                continue
            except asyncio.CancelledError:
                await self._record_step_state(step_run.id, RunState.CANCELED, error="cancelled")
                raise
            except StepCancelled as exc:
                # Executor signalled an intentional cancel (e.g. operator pressed
                # Cancel mid-execution); record it and stop — do *not* retry.
                await self._record_step_state(step_run.id, RunState.CANCELED, error=str(exc))
                return None
            except StepFailed as exc:
                await self._record_step_failure(step_run.id, error=str(exc))
                if attempt >= attempts:
                    return None
                continue
            except Exception as exc:
                # Unexpected — still counts as a failed attempt for retry, but log
                # with stack trace so the unexpected nature is visible in logs.
                _log.exception(
                    "nomaflow.runner unexpected exception in step %r (run=%s, attempt=%d)",
                    step.name, run.id, attempt,
                )
                await self._record_step_failure(
                    step_run.id,
                    error=f"unexpected: {type(exc).__name__}: {exc}",
                )
                if attempt >= attempts:
                    return None
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
