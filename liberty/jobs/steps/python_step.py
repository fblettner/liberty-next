"""``python`` step executor — invokes an operator-named callable.

The contract:

* ``step.callable`` is ``"module.path:function_name"`` (validated by Step's
  schema validator at parse time).
* ``step.op_kwargs`` is a free-form dict of kwargs forwarded to the function.
* The executor also injects two standard kwargs **when the function declares
  them by name** (via :func:`inspect.signature`): ``connectors`` (the
  :class:`ConnectorRegistry`) and ``ctx`` (the per-run :class:`RunContext`).
  A function that doesn't want either omits the param; one that swallows
  arbitrary kwargs (``**_``) gets them whether it asked or not — same shape
  Airflow's ``op_kwargs`` had, with the connector registry available because
  v1 used the global Airflow connection store and we don't.

Return-value normalisation (so step bodies aren't forced to import
:class:`StepResult`):

* :class:`StepResult` — used verbatim.
* ``int`` — wrapped as ``StepResult(rows_affected=<int>)``.
* ``dict`` — wrapped as ``StepResult(extras=<dict>)``.
* ``None`` (or no return) — empty ``StepResult()``.

Sync callables are run in :func:`asyncio.to_thread` so they don't block the
event loop; async callables are awaited directly. The function-vs-coroutine
detection uses :func:`inspect.iscoroutinefunction`.

Exceptions out of the callable propagate as :class:`StepFailed` (the runner
counts them against the retry policy) — *except* :class:`StepCancelled` and
:class:`asyncio.CancelledError`, which propagate untouched.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from liberty.config import Settings
from liberty.connectors import ConnectorRegistry
from liberty.jobs.schema import Step, StepType
from liberty.jobs.steps.base import RunContext, StepCancelled, StepFailed, StepResult
from liberty.plugins.invoke import (
    PluginInvocationError,
    build_kwargs,
    call_target,
    normalise_result,
    resolve_callable,
)

_log = logging.getLogger(__name__)


class PythonStepExecutor:
    """Executes one ``python`` step. Stateless — same instance can run any
    number of steps concurrently; each resolves its own callable."""

    def __init__(
        self, connectors: ConnectorRegistry, *, settings: Settings | None = None, changesets: Any = None,
    ) -> None:
        self._connectors = connectors
        # *settings* is optional so existing tests (which built the executor with just a
        # registry) keep working; when present, callables that declare a ``settings``
        # kwarg get the live Settings object injected — needed by python steps that
        # operate on config files (clone-app, future config-management steps) and need
        # to know paths like settings.connectors.config_path.
        self._settings = settings
        # The change-package DB — injected for callables declaring a ``changesets`` param (e.g. a
        # batch re-merge job that reads the active draft package's touched roles). None when the
        # deployment has change packages disabled / a test built the executor without it.
        self._changesets = changesets

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        if step.type is not StepType.PYTHON:
            raise StepFailed(
                f"PythonStepExecutor received a step of type {step.type.value!r}"
            )
        if not step.callable:
            # The Step validator catches this at parse time, but a hand-built Step
            # from a test could slip through.
            raise StepFailed(
                f"python step {step.name!r}: `callable` is required (format 'module:function')"
            )

        # Resolve + bind via the shared invoker; re-wrap its operator-friendly error as
        # StepFailed so the runner's retry/broadcast semantics are unchanged.
        try:
            target = resolve_callable(step.callable)
            kwargs = build_kwargs(
                target,
                dict(step.op_kwargs),
                injections=(
                    ("connectors", self._connectors),
                    ("ctx", ctx),
                    # ``settings`` only when the executor was built with one (build_executors passes
                    # it; bare-registry tests leave it None — and the invoker skips None injections).
                    ("settings", self._settings),
                    ("changesets", self._changesets),
                ),
            )
        except PluginInvocationError as exc:
            raise StepFailed(f"python step {step.name!r}: {exc}") from exc

        _log.info(
            "nomaflow.python start run=%s step=%r callable=%s kwargs=%s",
            ctx.run_id, step.name, step.callable, sorted(kwargs) or None,
        )

        try:
            raw = await call_target(target, kwargs)
        except (StepCancelled, asyncio.CancelledError):
            raise
        except StepFailed:
            raise
        except Exception as exc:
            _log.exception(
                "nomaflow.python run=%s step=%r callable=%s raised",
                ctx.run_id, step.name, step.callable,
            )
            raise StepFailed(f"python step {step.name!r}: {exc}") from exc

        try:
            result = normalise_result(raw)
        except PluginInvocationError as exc:
            raise StepFailed(f"python step {step.name!r}: {exc}") from exc
        _log.info(
            "nomaflow.python done run=%s step=%r rows=%s",
            ctx.run_id, step.name, result.rows_affected,
        )
        return result
