"""StepExecutor protocol + the small types every executor uses.

The runner doesn't import individual executors — it dispatches through the
:class:`StepExecutor` protocol against a mapping it's given at construction
time. Tests inject mock executors via that same mapping (no monkeypatching).

Failure semantics: an executor *raises* on failure. Two domain exceptions are
distinguished from generic ``Exception``:

* :exc:`StepFailed` — the step intentionally failed (a SQL error, a missing
  connector, a 5xx HTTP response). The runner counts these against the
  retry policy and broadcasts them as step failures.
* :exc:`StepCancelled` — the step was cancelled before it finished (operator
  pressed "Cancel" on the run, or the job-level timeout fired). The runner
  records CANCELED and does *not* retry.

Anything else (a bug, a TypeError, etc.) is treated like :exc:`StepFailed`
for retry purposes — we'd rather retry a transient bug once than silently lose
the run. ``asyncio.CancelledError`` propagates untouched so task cancellation
works the way Python expects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from liberty.jobs.schema import Step
from liberty.jobs.triggers import Trigger


# --------------------------------------------------------------------------- #
# run context — passed to every step
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class RunContext:
    """Per-run state every executor may inspect.

    Carries the run id (for log binding + broadcasts) and a snapshot of the
    job + trigger that started this run. Step executors are deliberately *not*
    given a session/connection — they ask the registry for connectors and open
    their own; that's the same separation the route layer uses today and keeps
    executors testable without faking SQLAlchemy.
    """

    run_id: str
    job_id: str
    trigger: Trigger
    # Output of the previous step's StepResult.rows_affected — populated by the
    # runner before each step.execute() call. ``${prev.rows_affected}`` in the
    # spec resolves through this field at template-time (not implemented in 2a).
    prev_rows_affected: int | None = None
    # call_job parent chain — every (job_id, run_id) ancestor of this run, oldest
    # first. Empty for top-level runs; one entry per call_job hop down the chain.
    # The CallJobExecutor checks for cycles by looking up its target_job_id in
    # this list before spawning the child run (fail-fast: A → B → A raises at
    # design time, not after the child boots and burns a transaction). Propagated
    # automatically by the runner when execute_run is invoked with the parent's
    # context (see :meth:`JobRunner.execute_run`'s ``parent_chain`` kwarg).
    parent_chain: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# step result + failure exceptions
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class StepResult:
    """What a step returns when it succeeds.

    ``rows_affected`` is summed across all steps into the parent
    ``JobRun.rows_affected`` for the at-a-glance Screen view (PHASE13.md §7);
    ``log_excerpt`` is the last ~4 KB of step-emitted log lines (the runner
    truncates if the executor returns more).
    """

    rows_affected: int | None = None
    log_excerpt: str | None = None
    # Free-form extras for executors that need to surface structured info (e.g.
    # ldap_sync's user count + group count). Not persisted in 2a; reserved for
    # the Screen-side detail view when it lands.
    extras: dict[str, Any] = field(default_factory=dict)


class StepFailed(Exception):
    """Raised by a :class:`StepExecutor` when the step finished but failed —
    counted against the retry policy."""


class StepCancelled(Exception):
    """Raised when a step was cancelled before completing — *not* retried."""


# --------------------------------------------------------------------------- #
# executor protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class StepExecutor(Protocol):
    """One callable per :class:`~liberty.jobs.schema.StepType`.

    The runner looks the executor up in the mapping it was given at
    construction time and awaits ``execute``. No registration decorator,
    no global registry — the wiring is explicit at app boot (see
    ``main.py`` when chunk 3 lands).
    """

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:  # pragma: no cover - interface
        ...
