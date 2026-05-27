"""``call_job`` step — fires another job inline within the calling step.

Semantics (decided in Phase C planning, see commit messages):

* **Inline child run** — the called job creates its own ``JobRun`` + ``StepRun``
  rows, just like any direct fire. Operators see the full chain in the
  monitoring UI; nothing collapses into the parent. This step BLOCKS until the
  child reaches a terminal state, then maps the child's state to its own
  StepResult (SUCCEEDED → return rows_affected, FAILED → raise StepFailed,
  CANCELED → raise StepCancelled).
* **Cycle detection** — the runner threads the parent chain (every (job_id,
  run_id) ancestor of the current run) onto :attr:`RunContext.parent_chain`.
  Before spawning the child, this executor checks the target_job_id against
  every ancestor's job_id. A match raises StepFailed with the visible chain
  ("A → B → A") so the operator sees WHERE the cycle is, not just THAT one
  exists.
* **Override inheritance** — the child inherits the parent's effective
  ``log_level`` (DEBUG cascades the whole chain) and ``params_override`` (an
  operator's "run with apps_id=42" cascades). Inherited via fresh kwargs on
  ``runner.execute_run``; the child's own jobs.toml-saved defaults still merge
  in normally (op_kwargs stays step-local — parent's per-step overrides do NOT
  leak into the child's per-step kwargs).

The executor wires the runner via constructor injection. Because the
:class:`JobRunner` is constructed AFTER the executor map is built, the wiring
layer creates this executor with ``runner=None`` and back-fills the reference
post-construction (see :func:`liberty.jobs.wiring.build_nomaflow`). The
executor refuses to run when ``runner`` isn't set so a misconfiguration
surfaces immediately rather than as a confusing AttributeError mid-step.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from liberty.jobs.registry import UnknownJobError
from liberty.jobs.schema import Step, StepType
from liberty.jobs.steps.base import RunContext, StepCancelled, StepFailed, StepResult
from liberty.jobs.triggers import ManualTrigger

if TYPE_CHECKING:
    from liberty.jobs.registry import JobRegistry
    from liberty.jobs.runner import JobRunner

_log = logging.getLogger(__name__)


class CallJobExecutor:
    """Fires another job inline as a child run of the current run. See module
    docstring for the semantics (inline / cycle-detection / inheritance)."""

    def __init__(
        self,
        registry: "JobRegistry",
        *,
        runner: "JobRunner | None" = None,
    ) -> None:
        self._registry = registry
        # Late-bound — set by wiring after the JobRunner is constructed. The
        # circular dependency (executor needs runner; runner takes executors)
        # is broken by post-construction injection rather than a contextvar /
        # weakref / RunContext smuggle — easier to reason about, clearly local
        # to wiring.py, and the runner is a singleton per app anyway.
        self._runner: "JobRunner | None" = runner

    def attach_runner(self, runner: "JobRunner") -> None:
        """Bind the JobRunner this executor uses to spawn children. Called by
        :func:`liberty.jobs.wiring.build_nomaflow` after the runner is built."""
        self._runner = runner

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        if step.type is not StepType.CALL_JOB:
            raise StepFailed(f"CallJobExecutor received non-call_job step type {step.type!r}")
        if not step.target_job_id:
            # Schema validator should have caught this, but defend against a
            # hand-edited TOML / API caller that bypassed validation.
            raise StepFailed("call_job step missing required ``target_job_id``")
        if self._runner is None:
            raise StepFailed(
                "call_job executor has no runner attached — wiring bug "
                "(see liberty.jobs.wiring.build_nomaflow.attach_runner)"
            )

        target_id = step.target_job_id

        # ── cycle detection ────────────────────────────────────────────────
        # Walk the parent chain. A match against this step's target = a
        # back-edge in the call graph. The error message renders the FULL
        # chain so the operator sees which job is calling which (A → B → C → A
        # is more actionable than "cycle detected, ask your friendly DBA").
        chain_ids = [job_id for (job_id, _run_id) in ctx.parent_chain]
        if target_id in chain_ids or target_id == ctx.job_id:
            cycle_display = " → ".join([*chain_ids, ctx.job_id, target_id])
            raise StepFailed(
                f"call_job: cycle detected: {cycle_display}. A job can't call "
                f"itself transitively — the call graph must be acyclic. Pick "
                f"a different target_job_id, or restructure so the shared "
                f"work lives in a third job that both call."
            )

        # ── resolve target job ─────────────────────────────────────────────
        try:
            target_job = self._registry.get(target_id)
        except UnknownJobError as exc:
            raise StepFailed(
                f"call_job: target job {target_id!r} not found in jobs.toml — "
                f"check the spelling, or reload the registry "
                f"(POST /admin/reload) if the target was added since the "
                f"calling job loaded."
            ) from exc

        # ── inherit the parent's log_level + params_override ───────────────
        # log_level: read off the runner's per-run record. The runner persists
        # the effective level into the JobRun row so the child can read it
        # back without us threading another kwarg through RunContext. params:
        # same approach — pulled from the in-flight run's metadata.
        parent_overrides = self._runner.read_parent_overrides(ctx.run_id)
        # Mark the child's trigger so the run list shows "spawned by call_job
        # from <parent_job_id>" — keeps the audit trail explicit even without
        # a parent_run_id column on the JobRun table (we kept the schema
        # change-free for this iteration; see PHASE-C notes).
        child_trigger = ManualTrigger(
            triggered_by=f"call_job:{ctx.job_id}@{ctx.run_id}",
        )

        # ── allocate + execute the child run ───────────────────────────────
        # Two-step (create_run + execute_run) so we have the child id BEFORE
        # it starts — lets us log the linkage immediately + return it in the
        # StepResult.extras even if execute_run later raises.
        child_run = await self._runner.create_run(target_job, child_trigger)
        _log.info(
            "nomaflow.call_job parent=%s/%s → child=%s/%s",
            ctx.job_id, ctx.run_id, target_job.id, child_run.id,
        )
        # Build the child's parent_chain by appending the current run. The
        # child's CallJobExecutor (if any) reads this list and refuses to call
        # back into anything in it.
        child_chain = [*ctx.parent_chain, (ctx.job_id, ctx.run_id)]
        child_run = await self._runner.execute_run(
            target_job,
            child_trigger,
            child_run,
            log_level=parent_overrides.get("log_level"),
            params_override=parent_overrides.get("params_override"),
            parent_chain=child_chain,
        )

        # ── map child state → step result ──────────────────────────────────
        # Import locally to avoid the steps/base ↔ models cycle (models depends
        # on nothing from steps, but steps/base doesn't import models either).
        from liberty.jobs.models import RunState

        if child_run.state == RunState.SUCCEEDED.value:
            return StepResult(
                rows_affected=child_run.rows_affected,
                extras={"child_run_id": child_run.id, "child_job_id": target_job.id},
            )
        if child_run.state == RunState.CANCELED.value:
            raise StepCancelled(
                f"call_job: child run {child_run.id} ({target_job.id}) was cancelled"
            )
        # FAILED (or any non-terminal state, defensively) — surface the child's
        # error so the operator drills from THIS step's failure into the child
        # run for the full traceback.
        err = child_run.error_message or "(no error message recorded)"
        raise StepFailed(
            f"call_job: child run {child_run.id} ({target_job.id}) "
            f"failed: {err}"
        )
