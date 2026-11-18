"""The tools the applications ship — ``[ai.<app>.tools.<id>]`` of ``ai.toml``, each a function
of the application's plugins the model can call for the application's own subject (a JDE
object's access, a user's conflicts…), offered as ``<app>_<id>``.

Bounded the way the ``call_plugin`` action is: the callable must be one of the ``j_`` entry
points discovered under ``plugins/`` (a ``module:function`` string cannot import an arbitrary
module), the caller needs its permission (``plugin:<callable>`` unless the tool names another),
and the framework objects the function declares (``connectors``, ``settings``, ``ctx``) are
injected — the model never supplies them. Read-only by contract: nothing here can check that a
function writes nothing, so a tool is the application author's statement that it does not.

Read at call time (:func:`app_tools_provider`): a reload of ``ai.toml`` or of the connectors is
seen without rebuilding the assistant. A tool whose application is not loaded (a licensed
connector without its key), not allowed to the assistant, or whose function cannot be imported
is left out, with a warning — one broken plugin never takes the assistant down.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

from liberty.ai.tools import Tool, caller, schema_of

_log = logging.getLogger(__name__)

# What the framework passes to a plugin callable by name — never an argument of the model.
INJECTED = ("connectors", "ctx", "settings", "changesets")


def plugins_root() -> Path:
    """``${LIBERTY_APPS_DIR}/../plugins`` — the root the plugin callables are discovered under
    (same resolution as :mod:`liberty.web.plugins`)."""
    apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
    return Path(apps).parent / "plugins" if apps else Path("plugins")


def _plain(raw: Any) -> Any:
    """A callable's return value as a tool result: a job-style ``StepResult`` becomes a dict."""
    from liberty.jobs.steps.base import StepResult

    if isinstance(raw, StepResult):
        return {**({"rows_affected": raw.rows_affected} if raw.rows_affected is not None else {}), **dict(raw.extras or {})}
    return {"result": None} if raw is None else raw


def build_app_tools(ai_config: Any, *, state: Callable[[], Any], allowed: list[str] | None = None, root: Path | None = None) -> list[Tool]:
    """The tools of *ai_config* that can be offered now (see the module doc for what is left out)."""
    from liberty.jobs.discover import discover_callables
    from liberty.plugins.invoke import PluginInvocationError, build_kwargs, call_target, resolve_callable

    declared = ai_config.tools() if ai_config is not None else []
    if not declared:
        return []
    st = state()
    connectors = getattr(st, "connectors", None)
    loaded = set(connectors.names()) if connectors is not None else set()
    allow = set(allowed or [])
    known = {c.callable for c in discover_callables(root or plugins_root())}
    tools: list[Tool] = []
    for app, tid, spec in declared:
        name = f"{app}_{tid}"
        if app not in loaded or (allow and app not in allow):
            continue                    # the application is not here (license, allowed_connectors): silently
        if spec.callable not in known:
            _log.warning("ai.toml: tool %s — %s is not a plugin entry point (a `j_` function under plugins/); left out", name, spec.callable)
            continue
        try:
            target = resolve_callable(spec.callable)
            _doc, schema = schema_of(target, exclude=INJECTED)
        except (PluginInvocationError, Exception) as exc:  # noqa: BLE001 — a broken plugin must not take the assistant down
            _log.warning("ai.toml: tool %s — %s cannot be loaded (%s); left out", name, spec.callable, exc)
            continue

        def make(target: Callable[..., Any] = target, ref: str = spec.callable) -> Callable[..., Any]:
            async def run(**arguments: Any) -> Any:
                from liberty.jobs.steps.base import RunContext
                from liberty.jobs.triggers import ManualTrigger

                live = state()
                who = caller()
                username = who.username if who is not None else "assistant"
                # what the framework injects is never the model's to give
                args = {k: v for k, v in arguments.items() if k not in INJECTED}
                ctx = RunContext(run_id=f"ai:{username}", job_id="__ai__", trigger=ManualTrigger(triggered_by=username))
                kwargs = build_kwargs(target, args, injections=(
                    ("connectors", getattr(live, "connectors", None)), ("ctx", ctx), ("settings", getattr(live, "settings", None)),
                ))
                return _plain(await call_target(target, kwargs))

            run.__name__ = ref.rpartition(":")[2]
            return run

        tools.append(Tool(
            name=name, description=spec.description.strip(), input_schema=schema, func=make(), is_async=True,
            summary_keys=tuple(list(schema.get("properties", {}))[:2]), permission=spec.required_permission(),
        ))
    return tools


def app_tools_provider(state: Callable[[], Any], *, allowed: list[str] | None = None) -> Callable[[], list[Tool]]:
    """A provider for :class:`liberty.ai.tools.ToolRegistry`: the app tools of the live
    ``ai.toml``, rebuilt only when the configuration or the connector registry was replaced."""
    cache: dict[str, Any] = {"key": None, "tools": []}

    def provide() -> list[Tool]:
        st = state()
        cfg, connectors = getattr(st, "ai_config", None), getattr(st, "connectors", None)
        key = (id(cfg), id(connectors))
        if cache["key"] != key:
            try:
                cache["tools"] = build_app_tools(cfg, state=state, allowed=allowed)
            except Exception as exc:  # noqa: BLE001
                _log.warning("ai.toml: the applications' tools could not be built (%s)", exc)
                cache["tools"] = []
            cache["key"] = key
        return list(cache["tools"])

    return provide
