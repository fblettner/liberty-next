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
import importlib
import inspect
import logging
import types
import typing
from typing import Any, Callable, Union

from liberty.config import Settings
from liberty.connectors import ConnectorRegistry
from liberty.jobs.schema import Step, StepType
from liberty.jobs.steps.base import RunContext, StepCancelled, StepFailed, StepResult

_log = logging.getLogger(__name__)


class PythonStepExecutor:
    """Executes one ``python`` step. Stateless — same instance can run any
    number of steps concurrently; each resolves its own callable."""

    def __init__(self, connectors: ConnectorRegistry, *, settings: Settings | None = None) -> None:
        self._connectors = connectors
        # *settings* is optional so existing tests (which built the executor with just a
        # registry) keep working; when present, callables that declare a ``settings``
        # kwarg get the live Settings object injected — needed by python steps that
        # operate on config files (clone-app, future config-management steps) and need
        # to know paths like settings.connectors.config_path.
        self._settings = settings

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

        target = self._resolve(step)
        kwargs = self._build_kwargs(step, ctx, target)

        _log.info(
            "nomaflow.python start run=%s step=%r callable=%s kwargs=%s",
            ctx.run_id, step.name, step.callable, sorted(kwargs) or None,
        )

        try:
            if inspect.iscoroutinefunction(target):
                raw = await target(**kwargs)
            else:
                # Sync callable: hop to a worker thread so a slow blocking call doesn't
                # stall the scheduler loop. The runner's per-step timeout still applies
                # — asyncio.wait_for cancels the await, which the to_thread wrapper
                # surfaces as CancelledError back here.
                raw = await asyncio.to_thread(_call_sync, target, kwargs)
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

        result = self._normalise(raw, step)
        _log.info(
            "nomaflow.python done run=%s step=%r rows=%s",
            ctx.run_id, step.name, result.rows_affected,
        )
        return result

    # -- internals ------------------------------------------------------- #

    def _resolve(self, step: Step) -> Callable[..., Any]:
        """Import the module + look up the function. Wraps the two failure
        modes (ImportError + AttributeError) in :class:`StepFailed` with the
        operator-friendly callable string in the message, since the raw
        exceptions name the module/function in a less-obvious way."""
        assert step.callable is not None
        module_path, _, function_name = step.callable.partition(":")
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise StepFailed(
                f"python step {step.name!r}: cannot import module {module_path!r} "
                f"from callable {step.callable!r} — {exc}"
            ) from exc
        try:
            return getattr(module, function_name)
        except AttributeError as exc:
            raise StepFailed(
                f"python step {step.name!r}: module {module_path!r} has no attribute "
                f"{function_name!r} (from callable {step.callable!r})"
            ) from exc

    def _build_kwargs(self, step: Step, ctx: RunContext, target: Callable[..., Any]) -> dict[str, Any]:
        """Merge ``op_kwargs`` with the standard-name injections (connectors / ctx).
        Inject only when the callable declares the name — otherwise the unknown
        kwarg would TypeError at call time.

        Coerce each op_kwargs value against the matching parameter's annotation
        (int / float / bool / str + Optional). Stringly-typed values are a
        regression magnet — TOML stores ``apps_id = "1"`` and every UI save round-
        trips it as a string, so a callable annotated ``apps_id: int`` would push
        ``"1"`` into asyncpg and crash with a DataError much later. Coerce here so
        the type written to disk (string) doesn't need to match the type the
        callable expects (int)."""
        kwargs: dict[str, Any] = dict(step.op_kwargs)
        try:
            # eval_str=True resolves PEP 563 string annotations to real types — without
            # it, callables with ``from __future__ import annotations`` give us
            # param.annotation == "int" (a string) and the coercion below would skip.
            sig = inspect.signature(target, eval_str=True)
        except (TypeError, ValueError, NameError):  # pragma: no cover — builtins / undefined names
            return kwargs
        params = sig.parameters
        accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())

        # Coerce op_kwargs against the callable's annotations. Skip names that aren't
        # declared as positional / keyword params (those will land in **kwargs without
        # a per-key annotation anyway).
        for key in list(kwargs):
            param = params.get(key)
            if param is None or param.annotation is inspect.Parameter.empty:
                continue
            try:
                kwargs[key] = _coerce_to_annotation(kwargs[key], param.annotation)
            except (TypeError, ValueError) as exc:
                raise StepFailed(
                    f"python step {step.name!r}: op_kwargs[{key!r}]={kwargs[key]!r} "
                    f"cannot be coerced to {_annot_name(param.annotation)}: {exc}"
                ) from exc

        injections: list[tuple[str, Any]] = [
            ("connectors", self._connectors),
            ("ctx", ctx),
        ]
        # ``settings`` only when the executor was constructed with one (the wiring in
        # build_executors passes it; tests that build the executor with just a registry
        # leave it as None — and we don't inject ``None`` because that'd silently break
        # a callable that expected a real Settings).
        if self._settings is not None:
            injections.append(("settings", self._settings))
        for name, value in injections:
            if accepts_kwargs or name in params:
                # Operator-provided op_kwargs win — they may want to override (e.g. a test
                # injects a stub registry). Only fill in the default when absent.
                kwargs.setdefault(name, value)
        return kwargs

    def _normalise(self, raw: Any, step: Step) -> StepResult:
        """Turn what the callable returned into a :class:`StepResult`. Accepts
        the four shapes documented in the module docstring; anything else is a
        :class:`StepFailed` so a typo doesn't silently land as an empty result."""
        if raw is None:
            return StepResult()
        if isinstance(raw, StepResult):
            return raw
        if isinstance(raw, bool):
            # Catch this before the int branch — `True` would otherwise become
            # rows_affected=1, which is not what an "ok" boolean means.
            raise StepFailed(
                f"python step {step.name!r}: callable returned a bool — expected "
                f"None, int, dict, or StepResult"
            )
        if isinstance(raw, int):
            return StepResult(rows_affected=raw)
        if isinstance(raw, dict):
            return StepResult(extras=dict(raw))
        raise StepFailed(
            f"python step {step.name!r}: callable returned unsupported type "
            f"{type(raw).__name__} (expected None, int, dict, or StepResult)"
        )


def _call_sync(fn: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    """Tiny adapter so ``asyncio.to_thread(_call_sync, fn, kwargs)`` works (to_thread's
    signature passes args positionally; this lets us forward a kwargs dict)."""
    return fn(**kwargs)


_BOOL_TRUE = frozenset({"true", "t", "yes", "y", "1", "on"})
_BOOL_FALSE = frozenset({"false", "f", "no", "n", "0", "off"})


def _coerce_to_annotation(value: Any, annotation: Any) -> Any:
    """Coerce *value* to *annotation* (a type, ``X | None``, ``Optional[X]``, or
    ``Union[...]``). Returns *value* unchanged when:
      - it's already an instance of the annotation
      - the annotation is one we don't know how to coerce to (custom dataclass,
        generic container, …) — pass-through is safer than guessing.

    Raises ``TypeError`` / ``ValueError`` when the value can't reach the target
    type (e.g. ``int("abc")``) — ``_build_kwargs`` re-raises as ``StepFailed``
    with the param name + value attached."""
    if value is None:
        return None

    origin = typing.get_origin(annotation)
    # ``Union[X, None]`` (typing) and ``X | None`` (PEP 604) have different origins:
    # ``typing.Union`` vs ``types.UnionType`` — accept both.
    if origin is Union or origin is types.UnionType:
        # X | None or Union[X, Y]: try each arg in order (None already handled above).
        # If the value is already an instance of one of them, accept; otherwise
        # try coercion against the first non-None type — Optional[int] coerces to int.
        # ``isinstance(value, list[str])`` raises TypeError on parameterized generics —
        # use the runtime origin (``list``) for the check; pass through plain types as-is.
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        for arg in non_none:
            check_type = typing.get_origin(arg) or arg
            if isinstance(check_type, type) and isinstance(value, check_type):
                return value
        if non_none:
            return _coerce_to_annotation(value, non_none[0])
        return value

    # Parameterized generic at the top level (``list[str]``, ``dict[str, int]``, …).
    # Same isinstance() trap as in the Union branch — fall back to the runtime origin.
    if origin is not None:
        if isinstance(origin, type) and isinstance(value, origin):
            return value
        # We don't try to coerce the contained types (list-of-str etc.) — pass through.
        return value

    # Plain types only from here on. Already the right type → pass through.
    if not isinstance(annotation, type):
        return value
    if isinstance(value, annotation):
        # bool is an int subclass — guard so a literal True doesn't sneak past
        # an ``int`` annotation as 1 (it would, but at least flag bools intended
        # for non-bool slots).
        if annotation is int and isinstance(value, bool):
            return int(value)
        return value

    if annotation is bool:
        if isinstance(value, str):
            v = value.strip().lower()
            if v in _BOOL_TRUE:
                return True
            if v in _BOOL_FALSE:
                return False
            raise ValueError(f"{value!r} is not a boolean")
        if isinstance(value, (int, float)):
            return bool(value)
        raise TypeError(f"can't coerce {type(value).__name__} to bool")
    if annotation is int:
        if isinstance(value, str):
            return int(value.strip())
        if isinstance(value, float):
            return int(value)
        raise TypeError(f"can't coerce {type(value).__name__} to int")
    if annotation is float:
        if isinstance(value, (str, int)):
            return float(value if not isinstance(value, str) else value.strip())
        raise TypeError(f"can't coerce {type(value).__name__} to float")
    if annotation is str:
        return str(value)

    # Anything else (list / dict / dataclass / NewType / etc.) — pass through.
    return value


def _annot_name(annotation: Any) -> str:
    """Best-effort short name for error messages — ``int``, ``int | None``, ``str``."""
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).replace("typing.", "")
