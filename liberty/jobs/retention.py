"""Run-history retention sweep — periodic cleanup of ``nomaflow_job_runs`` + its FK
children (``nomaflow_step_runs`` / ``nomaflow_run_logs`` / ``nomaflow_run_overrides`` /
``nomaflow_step_run_extras``).

See :class:`~liberty.jobs.schema.RetentionPolicy` for the policy shape + per-axis
rationale. This module owns the sweep itself: a stateless function that takes a
:class:`JobDatabase` + a :class:`RetentionPolicy` and DELETEs the qualifying rows.

Two rules, applied as a union (a run is deleted if EITHER says so):

* **Age cutoff** — DELETE finished runs whose ``finished_at`` is older than
  ``now - policy.days``. Catches the bulk of old data; reasonable for compliance
  windows like "keep 90 days of audit history".
* **Per-job cap** — even within the age window, never let a single job's history
  exceed ``policy.keep_last_per_job`` finished runs (sorted by ``finished_at``
  DESC). Protects against high-frequency cron jobs (every-5-min fires producing
  288 rows / day) burying the list with effectively-identical SUCCEEDED rows.

Running rows (``QUEUED`` / ``RUNNING``) are NEVER touched — retention only looks at
terminal states. The cascade FK on the child tables means a single DELETE on
``nomaflow_job_runs`` cleans up the whole tree atomically.

Portability: the per-job cap uses a Python-side sort rather than a SQL window
function so the same sweep works against SQLite (the test fixture), Postgres
(production), and Oracle (the rare on-prem deployment). For nomaflow scale
(typically <100k run rows), the round-trip cost is negligible vs. the simplicity
of "one path, three dialects".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from liberty.jobs.db import JobDatabase
from liberty.jobs.models import JobRun, RunState
from liberty.jobs.schema import RetentionPolicy

_log = logging.getLogger(__name__)


# States we consider "finished" — anything else is in-flight and protected from sweep.
_FINISHED_STATES: tuple[str, ...] = (
    RunState.SUCCEEDED.value,
    RunState.FAILED.value,
    RunState.CANCELED.value,
)


@dataclass(slots=True)
class SweepReport:
    """What a sweep did. Returned by :func:`sweep_run_history` so callers can log /
    surface the counts. ``deleted_by_age`` and ``deleted_by_cap`` are disjoint
    counts — a run that qualifies under both rules counts once (under age, since
    that rule is applied first)."""

    swept_at: datetime
    cutoff: datetime
    keep_last_per_job: int
    deleted_by_age: int
    deleted_by_cap: int

    @property
    def total_deleted(self) -> int:
        return self.deleted_by_age + self.deleted_by_cap

    def to_dict(self) -> dict:
        return {
            "swept_at": self.swept_at.isoformat(),
            "cutoff": self.cutoff.isoformat(),
            "keep_last_per_job": self.keep_last_per_job,
            "deleted_by_age": self.deleted_by_age,
            "deleted_by_cap": self.deleted_by_cap,
            "total_deleted": self.total_deleted,
        }


async def sweep_run_history(
    db: JobDatabase, policy: RetentionPolicy, *, now: datetime | None = None,
) -> SweepReport:
    """Run one cleanup pass against the nomaflow run tables. Returns the counts.

    ``now`` is injectable for tests (so the cutoff can be predicted) — production
    callers pass nothing and get the real wall-clock.

    Sequencing matters: we apply the age rule FIRST so the per-job cap sees a
    smaller candidate set. A run that would have qualified under both rules counts
    under ``deleted_by_age`` (not double-counted). The two queries run in the same
    transaction so a partial failure rolls back cleanly.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=policy.days)

    async with db.session() as session:
        # ── Rule 1: age cutoff ─────────────────────────────────────────────────
        # Delete all finished runs older than the cutoff. CASCADE on the child
        # tables (StepRun / RunLog / RunOverrides / StepRunExtras) handles the
        # rest. The .rowcount tells us how many parent rows were removed; the
        # cascade rows aren't counted but the operator sees the parent count
        # in the report which is the unit they care about.
        age_result = await session.execute(
            delete(JobRun)
            .where(JobRun.state.in_(_FINISHED_STATES))
            .where(JobRun.finished_at.is_not(None))
            .where(JobRun.finished_at < cutoff)
        )
        deleted_by_age = age_result.rowcount or 0

        # ── Rule 2: per-job cap ────────────────────────────────────────────────
        # Of the SURVIVORS (finished and within the age window), select id /
        # job_id / finished_at and bucket them in Python. For each job_id, keep
        # the N most recent and mark the rest for deletion. This is portable
        # across SQLite / Postgres / Oracle without a window function; the
        # round-trip is bounded by the surviving rows which by definition fit
        # within the age window the operator picked.
        cap_ids: list[str] = []
        if policy.keep_last_per_job > 0:
            rows = (await session.execute(
                select(JobRun.id, JobRun.job_id, JobRun.finished_at)
                .where(JobRun.state.in_(_FINISHED_STATES))
                .where(JobRun.finished_at.is_not(None))
            )).all()
            cap_ids = _ids_beyond_cap(rows, policy.keep_last_per_job)

        deleted_by_cap = 0
        if cap_ids:
            # Single DELETE … WHERE id IN (...) — SQLAlchemy expands the bind list to
            # the dialect's native form (Postgres ANY, Oracle ANY, SQLite IN). Cap is
            # bounded by the total surviving row count which we just measured.
            cap_result = await session.execute(
                delete(JobRun).where(JobRun.id.in_(cap_ids))
            )
            deleted_by_cap = cap_result.rowcount or 0

    report = SweepReport(
        swept_at=now,
        cutoff=cutoff,
        keep_last_per_job=policy.keep_last_per_job,
        deleted_by_age=deleted_by_age,
        deleted_by_cap=deleted_by_cap,
    )
    if report.total_deleted:
        _log.info(
            "nomaflow.retention swept %d runs (age=%d, cap=%d) cutoff=%s",
            report.total_deleted, deleted_by_age, deleted_by_cap, cutoff.isoformat(),
        )
    else:
        _log.debug(
            "nomaflow.retention no runs to delete (cutoff=%s, cap=%d)",
            cutoff.isoformat(), policy.keep_last_per_job,
        )
    return report


def _ids_beyond_cap(
    rows: list[tuple[str, str, datetime | None]], keep_last_per_job: int,
) -> list[str]:
    """Given the surviving (id, job_id, finished_at) rows, return the ids that fall
    OUTSIDE the per-job keep-last window. Pure function so tests can exercise the
    sort + bucket logic without spinning up a DB."""
    # Bucket by job_id while remembering the original (id, finished_at) so we can
    # sort each bucket independently. ``finished_at`` is non-NULL by the SELECT's
    # filter — None would only show up if a caller bypasses it, in which case we
    # treat the row as "oldest possible" (datetime.min) so it falls past the cap.
    buckets: dict[str, list[tuple[str, datetime]]] = {}
    sentinel_oldest = datetime.min.replace(tzinfo=timezone.utc)
    for run_id, job_id, finished_at in rows:
        buckets.setdefault(job_id, []).append((run_id, finished_at or sentinel_oldest))

    to_delete: list[str] = []
    for entries in buckets.values():
        # Sort newest-first; keep the first ``keep_last_per_job`` and mark the rest.
        # Stable sort means runs with identical timestamps preserve insertion order —
        # which doesn't matter for correctness but keeps test assertions deterministic.
        entries.sort(key=lambda t: t[1], reverse=True)
        for run_id, _ in entries[keep_last_per_job:]:
            to_delete.append(run_id)
    return to_delete
