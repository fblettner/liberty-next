"""nomaflow — config-driven ETL + scheduler. See ``docs/PHASE13.md`` for the design.

This module is the framework side: parsing ``jobs.toml``, persisting run state,
executing job steps, scheduling cron fires. The actual job catalogue + the
custom Python step bodies live in the apps repo at
``${LIBERTY_APPS_DIR}/plugins/nomaflow/``.

Phase 13a — foundation: parse + validate ``jobs.toml`` into a :class:`JobRegistry`,
create the ``nomaflow_job_runs`` / ``nomaflow_step_runs`` tables via
:class:`JobDatabase.create_schema`. No scheduler, no runner, no step executors,
no admin endpoints yet — those land in subsequent chunks.
"""

from __future__ import annotations

from liberty.jobs.db import JobDatabase
from liberty.jobs.models import Base, JobRun, RunLog, RunState, StepRun, TriggerKind
from liberty.jobs.recovery import list_orphan_run_ids, mark_orphan_runs_failed
from liberty.jobs.registry import JobRegistry, UnknownJobError, load_jobs
from liberty.jobs.runner import Broadcaster, JobRunner, compute_backoff_delay
from liberty.jobs.scheduler import JobScheduler
from liberty.jobs.schema import (
    BackoffKind,
    CopyMode,
    DecimalMode,
    Job,
    JobAlerts,
    JobRetry,
    JobsFile,
    Step,
    StepType,
)
from liberty.jobs.steps import (
    PythonStepExecutor,
    RunContext,
    SqlCopyExecutor,
    SqlQueryExecutor,
    StepCancelled,
    StepExecutor,
    StepFailed,
    StepResult,
)
from liberty.jobs.triggers import ManualTrigger, ScheduledTrigger, Trigger

__all__ = [
    "BackoffKind",
    "Base",
    "Broadcaster",
    "CopyMode",
    "DecimalMode",
    "Job",
    "JobAlerts",
    "JobDatabase",
    "JobRegistry",
    "JobRetry",
    "JobRun",
    "JobRunner",
    "JobScheduler",
    "JobsFile",
    "RunLog",
    "ManualTrigger",
    "PythonStepExecutor",
    "RunContext",
    "RunState",
    "ScheduledTrigger",
    "SqlCopyExecutor",
    "SqlQueryExecutor",
    "Step",
    "StepCancelled",
    "StepExecutor",
    "StepFailed",
    "StepResult",
    "StepRun",
    "StepType",
    "Trigger",
    "TriggerKind",
    "UnknownJobError",
    "compute_backoff_delay",
    "list_orphan_run_ids",
    "load_jobs",
    "mark_orphan_runs_failed",
]
