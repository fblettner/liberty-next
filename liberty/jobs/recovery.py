"""Crash-recovery sweep — see ``docs/PHASE13.md`` §6.

When the process dies mid-run, ``nomaflow_job_runs`` ends up with rows in
``RUNNING`` (or ``QUEUED``) whose owning process is gone. Without intervention
these rows:

* Stay ``RUNNING`` forever in the Screen — operators can't tell what's still
  running from what crashed days ago.
* Block the next scheduled fire: the ``UNIQUE (job_id, scheduled_at)`` dedup
  constraint will reject the INSERT, and the runner's "duplicate fire" branch
  will return the *crashed* run unchanged, so cron loses an entire schedule
  slot until manual cleanup.

The sweep is a single ``UPDATE … WHERE state IN (...)`` against each table,
runs once at scheduler startup before any cron triggers are registered, and
is idempotent (running it twice is a no-op the second time).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select, update

from liberty.jobs.db import JobDatabase
from liberty.jobs.models import JobRun, RunState, StepRun

_log = logging.getLogger(__name__)

# States that are "in flight" — anything else is terminal and should be left alone.
_ORPHAN_STATES: tuple[str, ...] = (RunState.RUNNING.value, RunState.QUEUED.value)

_RECOVERY_MESSAGE = "abandoned: process restart during run"


async def mark_orphan_runs_failed(db: JobDatabase) -> tuple[int, int]:
    """Mark every ``RUNNING`` / ``QUEUED`` row as ``FAILED`` with a clear reason.

    Returns ``(job_runs_marked, step_runs_marked)`` — useful both for the
    startup log line and for tests asserting "the sweep did the work".

    Concurrency note: this assumes the calling worker is the only one
    starting up at this moment. If your deployment runs multiple workers
    concurrently and they all sweep at once, the second sweep will find
    nothing to do — that's fine, the UPDATE is idempotent. The advisory-lock
    election that picks the single scheduler-owning worker (PHASE13 §11 #2,
    chunk 3+) makes this sweep happen exactly once per cold start in
    practice; the safety holds either way.
    """
    now = datetime.now(timezone.utc)
    job_count = 0
    step_count = 0

    async with db.session() as session:
        # Mark orphan step rows first so the cascade-free UPDATE on JobRun
        # below doesn't race with any reader peeking at the relationship.
        step_result = await session.execute(
            update(StepRun)
            .where(StepRun.state.in_(_ORPHAN_STATES))
            .values(
                state=RunState.FAILED.value,
                finished_at=now,
                error_message=_RECOVERY_MESSAGE,
            )
        )
        step_count = step_result.rowcount or 0

        job_result = await session.execute(
            update(JobRun)
            .where(JobRun.state.in_(_ORPHAN_STATES))
            .values(
                state=RunState.FAILED.value,
                finished_at=now,
                error_message=_RECOVERY_MESSAGE,
            )
        )
        job_count = job_result.rowcount or 0

    if job_count or step_count:
        _log.warning(
            "nomaflow.recovery marked %d orphan job_runs + %d orphan step_runs FAILED",
            job_count, step_count,
        )
    else:
        _log.info("nomaflow.recovery no orphan runs to clean up")

    return job_count, step_count


async def list_orphan_run_ids(db: JobDatabase) -> list[str]:
    """Diagnostic helper: list the JobRun ids currently in an orphan state
    without mutating anything. Useful for the Screen UI to surface "look,
    you crashed" alerts before the sweep runs (or after, for the audit log)."""
    async with db.session() as session:
        result = await session.execute(
            select(JobRun.id).where(JobRun.state.in_(_ORPHAN_STATES))
        )
        return [row[0] for row in result.all()]


def orphan_states() -> Iterable[str]:
    """The set of run states the sweep treats as "in flight". Exposed for tests
    that want to assert the sweep covers exactly these states (no surprises if
    we add a new state down the line)."""
    return _ORPHAN_STATES
