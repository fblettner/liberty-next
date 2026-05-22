"""Triggers — what caused a job run to be created.

Two flavours, both small dataclasses (no behaviour):

* :class:`ScheduledTrigger` — APScheduler fired a cron tick. Carries the
  ``fired_at`` timestamp (matches the cron's notion of "now" for that fire,
  not wall-clock at the moment ``JobRunner.run`` is entered) so duplicate
  fires for the same logical instant dedup via the ``UNIQUE (job_id,
  scheduled_at)`` constraint on ``nomaflow_job_runs`` (see PHASE13.md §4 +
  models.py).

* :class:`ManualTrigger` — an operator hit "Run now" (or another job fired
  via ``http`` into the admin endpoint). Carries ``triggered_by`` (the
  caller's username) for the audit trail; ``scheduled_at`` is None, which
  Postgres/SQLite treat as distinct in unique indexes so an operator can
  click "Run now" repeatedly without tripping the dedup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True, frozen=True)
class ScheduledTrigger:
    fired_at: datetime  # cron fire instant; persisted as nomaflow_job_runs.scheduled_at

    kind: str = "scheduled"
    triggered_by: str | None = None  # always None for scheduled fires


@dataclass(slots=True, frozen=True)
class ManualTrigger:
    triggered_by: str  # username; required — manual fires always carry an identity

    kind: str = "manual"
    fired_at: None = None
    # scheduled_at on the row stays NULL → many manual fires coexist for one job


Trigger = ScheduledTrigger | ManualTrigger
