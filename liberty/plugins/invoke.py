"""Shared plugin-callable invocation.

The nomaflow ``python`` step executor (:mod:`liberty.jobs.steps.python_step`) grew a small but
load-bearing contract: resolve a ``"module:function"`` string, coerce the operator-supplied
kwargs against the callable's annotations, inject the framework objects (``connectors`` / ``ctx``
/ ``settings``) the callable declares *by name*, await/thread-hop it, and normalise whatever it
returns into a :class:`StepResult`. The ``call_plugin`` screen action needs the EXACT same
contract — so it lives here, once, and both surfaces call it:

* the job step keeps its own logging + cancellation handling and calls the lower-level pieces
  (:func:`resolve_callable` / :func:`build_kwargs` / :func:`call_target` / :func:`normalise_result`);
* the action endpoint calls the all-in-one :func:`invoke_callable`.

Failures raise :class:`PluginInvocationError` with an operator-friendly message — the job executor
re-wraps it as ``StepFailed`` (so retry semantics are unchanged); the web layer maps it to a 400.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
from pathlib import Path
from typing import Any, Callable, Iterable

from liberty.coercion import CoercionError, annotation_name, coerce_kwargs
from liberty.jobs.steps.base import StepResult


class PluginInvocationError(Exception):
    """A plugin callable could not be resolved, its kwargs couldn't be coerced, or it returned an
    unsupported shape. Carries a message safe to surface to the operator."""


def plugins_root() -> Path:
    """``${LIBERTY_APPS_DIR}/../plugins`` (same resolution as ``ensure_plugins_on_sys_path`` and
    the Job Designer's callable catalog); ``./plugins`` when the env var is unset."""
    apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
    return Path(apps).parent / "plugins" if apps else Path("plugins")


def resolve_callable(ref: str) -> Callable[..., Any]:
    """Import ``module:function`` and return the function object. Raises
    :class:`PluginInvocationError` on a malformed ref, an un-importable module, or a missing
    attribute — with the original ``ref`` in the message (the raw ImportError/AttributeError name
    the module/function in a less obvious way)."""
    module_path, sep, function_name = ref.partition(":")
    if not sep or not module_path or not function_name:
        raise PluginInvocationError(f"callable {ref!r} must be in 'module:function' form")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise PluginInvocationError(
            f"cannot import module {module_path!r} from callable {ref!r} — {exc}"
        ) from exc
    try:
        return getattr(module, function_name)
    except AttributeError as exc:
        raise PluginInvocationError(
            f"module {module_path!r} has no attribute {function_name!r} (from callable {ref!r})"
        ) from exc


def build_kwargs(
    target: Callable[..., Any],
    op_kwargs: dict[str, Any],
    *,
    injections: Iterable[tuple[str, Any]],
) -> dict[str, Any]:
    """Coerce *op_kwargs* against the callable's parameter annotations, then layer the framework
    *injections* on top — each ``(name, value)`` is added only when the callable declares that
    parameter (or swallows ``**kwargs``), and never when *value* is ``None`` (don't inject a stub
    over a param the callable expected to be real). Operator-provided op_kwargs win over an
    injection of the same name (``setdefault``), which is what lets a test pass its own registry.

    Coercion matters because every value round-trips through TOML / the UI as a string — a callable
    annotated ``apps_id: int`` would otherwise receive ``"1"`` and crash deep inside the driver.
    """
    try:
        kwargs: dict[str, Any] = coerce_kwargs(dict(op_kwargs), target)
    except CoercionError as exc:
        raise PluginInvocationError(
            f"op_kwargs[{exc.key!r}]={exc.value!r} cannot be coerced to "
            f"{annotation_name(exc.annotation)}: {exc.cause}"
        ) from exc

    try:
        sig = inspect.signature(target, eval_str=True)
    except (TypeError, ValueError, NameError):  # pragma: no cover — builtins / undefined names
        return kwargs
    params = sig.parameters
    accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    for name, value in injections:
        if value is None:
            continue
        if accepts_kwargs or name in params:
            kwargs.setdefault(name, following_pool(value) if name == "connectors" else value)
    return kwargs


class _PoolFollowing:
    """The connector registry as an application's function sees it: a SQL connector that runs on several databases
    (one per environment) is handed bound to the environment of the moment — the pool picked on the page
    (``X-Liberty-Pool``) or named by the job step — as the screens' queries and the API connectors already are.
    A pool that is not one of the connector's leaves it on its main database. Everything else is the registry's."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def __getattr__(self, name: str) -> Any:
        return getattr(self._registry, name)

    def __contains__(self, name: object) -> bool:
        return name in self._registry

    def __len__(self) -> int:
        return len(self._registry)

    def _bound(self, conn: Any) -> Any:
        from liberty.connectors.api import current_pool

        pool = current_pool.get()
        if pool and pool in (getattr(conn, "allowed_pools", None) or ()) and hasattr(conn, "for_pool") and hasattr(conn, "run_select"):
            return conn.for_pool(pool)
        return conn

    def get(self, name: str) -> Any:
        return self._bound(self._registry.get(name))

    def sql(self, name: str) -> Any:
        return self._bound(self._registry.sql(name))


def following_pool(registry: Any) -> Any:
    """*registry* as a function is handed it (see :class:`_PoolFollowing`); anything that is not a connector
    registry — a test's stand-in — is handed back as it is."""
    from liberty.connectors import ConnectorRegistry

    return _PoolFollowing(registry) if isinstance(registry, ConnectorRegistry) else registry


async def call_target(target: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    """Await an async callable directly; run a sync one in a worker thread so a blocking call
    doesn't stall the event loop. (Cancellation surfaces as ``CancelledError`` either way.)"""
    if inspect.iscoroutinefunction(target):
        return await target(**kwargs)
    return await asyncio.to_thread(_call_sync, target, kwargs)


def normalise_result(raw: Any) -> StepResult:
    """Turn the callable's return value into a :class:`StepResult`:
    ``None`` → empty, ``int`` → ``rows_affected``, ``dict`` → ``extras``, ``StepResult`` → verbatim.
    A ``bool`` (which would silently become ``rows_affected=1``) and any other type raise
    :class:`PluginInvocationError` so a typo doesn't land as a misleading success."""
    if raw is None:
        return StepResult()
    if isinstance(raw, StepResult):
        return raw
    if isinstance(raw, bool):
        raise PluginInvocationError(
            "callable returned a bool — expected None, int, dict, or StepResult"
        )
    if isinstance(raw, int):
        return StepResult(rows_affected=raw)
    if isinstance(raw, dict):
        return StepResult(extras=dict(raw))
    raise PluginInvocationError(
        f"callable returned unsupported type {type(raw).__name__} "
        f"(expected None, int, dict, or StepResult)"
    )


async def invoke_callable(
    ref: str,
    op_kwargs: dict[str, Any],
    *,
    injections: Iterable[tuple[str, Any]],
) -> StepResult:
    """End-to-end resolve → build kwargs → call → normalise. The high-level helper the
    ``call_plugin`` action endpoint uses; the job executor uses the individual pieces so it can
    interleave its own logging + cancellation handling."""
    target = resolve_callable(ref)
    kwargs = build_kwargs(target, op_kwargs, injections=injections)
    raw = await call_target(target, kwargs)
    return normalise_result(raw)


def _call_sync(fn: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    """Adapter so ``asyncio.to_thread(_call_sync, fn, kwargs)`` can forward a kwargs dict
    (to_thread passes its args positionally)."""
    return fn(**kwargs)
