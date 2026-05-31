"""Filesystem-watcher → per-file config reload (Phase 7 polish, opt-in).

The setting: an operator hand-edits :file:`screens.toml` in vim, or pulls from git, or
``scp``\\s an updated :file:`menus.toml`. Without this module the changes don't apply
until they click "Reload" in Settings or hit ``POST /admin/reload``. With this module
turned on (``[app] hot_reload = true``), the matching subsystem reloads automatically.

The dispatch:

* ``menus.toml``       → re-parse + swap ``app.state.menus``.
* ``screens.toml``     → re-parse + swap ``app.state.screens``.
* ``dictionary.toml``  → re-parse + swap ``app.state.connectors`` (dictionary feeds connector
                          column hints) **and** ``app.state.screens`` (column hints flow into
                          screens too).
* ``charts.toml``      → re-parse + swap ``app.state.charts``.
* ``dashboards.toml``  → re-parse + swap ``app.state.dashboards``.
* ``jobs.toml``        → reconcile nomaflow registry + scheduler.
* ``connectors.toml``  → re-parse + swap ``app.state.connectors``, **but** if the
                          ``[pools.*]`` section changed, log a clear warning telling the
                          operator to use the Pools page's manual reload. Swapping a live
                          SQL engine mid-query is too risky to do unattended.

Debounce: changes within ``DEBOUNCE_MS`` are coalesced. Editors save in bursts
(vim writes a temp file + renames; git checkout touches many files in a tight loop) —
without debouncing we'd reload N times per save.

``watchfiles.awatch`` is the underlying primitive (already a transitive dep via uvicorn).
The watcher runs as a background task in the FastAPI lifespan; cancellation on app
shutdown is plain ``asyncio.Task.cancel()`` (the awatch generator surfaces it as
``StopAsyncIteration`` and we exit cleanly).
"""

from __future__ import annotations

import asyncio
import logging
import tomllib
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

_log = logging.getLogger(__name__)

# Coalesce events within this window into one reload. Vim's atomic-save fires
# create+rename+modify in <100 ms; git checkout fires modify per file in tight bursts.
# 500 ms is comfortable: the operator's "save in editor" → "page picks it up" loop feels
# instant but we never reload twice for one save.
DEBOUNCE_MS = 500


async def start_watcher(app: FastAPI) -> asyncio.Task[None]:
    """Start the hot-reload background task. Returns the task so the lifespan can cancel
    it on shutdown. No-op when ``[app] hot_reload`` is false (the lifespan still calls
    this for symmetry; we just bail before spawning anything).

    The set of watched paths comes from the Settings sections (each file's
    ``config_path``). A non-existent path is skipped — operators may run the framework
    with only a subset of features enabled.
    """
    settings = app.state.settings
    if not settings.app.hot_reload:
        _log.info("hot_reload.disabled (set [app] hot_reload = true to enable)")
        # Return a no-op task so the lifespan's cancel() call has something to await.
        async def _noop() -> None:
            return
        return asyncio.create_task(_noop(), name="liberty.hot_reload.noop")

    # File → handler mapping. Each handler is async, takes the FastAPI app, and does
    # whatever per-subsystem reload is appropriate. The mapping is keyed by the
    # *absolute* resolved path so watcher events match exactly.
    handlers: dict[Path, Callable[[FastAPI, Path], Awaitable[None]]] = {}
    for path_attr, handler in (
        (settings.menus.config_path, _reload_menus),
        (settings.screens.config_path, _reload_screens),
        (settings.charts.config_path, _reload_charts),
        (settings.dashboards.config_path, _reload_dashboards),
        (settings.connectors.config_path, _reload_connectors),
    ):
        if path_attr:
            handlers[Path(path_attr).resolve()] = handler
    # dictionary.toml — separate path, may default to "<connectors_path>.parent/dictionary.toml"
    dict_path = settings.connectors.dictionary_path
    if dict_path is None:
        dict_path = Path(settings.connectors.config_path).parent / "dictionary.toml"
    handlers[Path(dict_path).resolve()] = _reload_dictionary
    # jobs.toml — only relevant when nomaflow is active (the lifespan only attaches
    # app.state.jobs when build_nomaflow returned a stack).
    jobs_path = _resolve_jobs_path(settings)
    if jobs_path is not None:
        handlers[Path(jobs_path).resolve()] = _reload_jobs

    if not handlers:
        _log.warning("hot_reload no paths to watch (Settings sections all unset)")
        return asyncio.create_task(asyncio.sleep(0), name="liberty.hot_reload.noop")

    task = asyncio.create_task(
        _watch_loop(app, handlers), name="liberty.hot_reload.watcher",
    )
    _log.info("hot_reload watching %d file(s): %s", len(handlers), sorted(str(p) for p in handlers))
    return task


async def _watch_loop(
    app: FastAPI,
    handlers: dict[Path, Callable[[FastAPI, Path], Awaitable[None]]],
) -> None:
    """The watcher's main loop. Each batch of events is debounced + dispatched to the
    matching per-file handler. Errors from a handler don't take the watcher down —
    they're logged + the loop continues so a corrupt edit doesn't kill hot-reload for
    every other file."""
    # Imported here so the module can be imported even in environments where
    # watchfiles isn't installed (it's a transitive dep via uvicorn; production has it,
    # but a minimal test env might not).
    from watchfiles import awatch

    watch_paths = list(handlers.keys())
    pending: dict[Path, float] = {}
    try:
        async for changes in awatch(*watch_paths, debounce=DEBOUNCE_MS, recursive=False):
            for _change_kind, path_str in changes:
                resolved = Path(path_str).resolve()
                handler = handlers.get(resolved)
                if handler is None:
                    continue
                # Already-coalesced by watchfiles' debounce; dispatch immediately.
                _log.info("hot_reload change %s → reloading", resolved.name)
                try:
                    await handler(app, resolved)
                    _log.info("hot_reload reloaded %s", resolved.name)
                except Exception:                # noqa: BLE001 — keep the loop alive
                    _log.exception("hot_reload handler for %s failed", resolved.name)
    except asyncio.CancelledError:
        _log.info("hot_reload watcher stopping")
        raise


# ── per-file handlers ──────────────────────────────────────────────────────────────


async def _reload_menus(app: FastAPI, _path: Path) -> None:
    from liberty.menus import load_menus
    app.state.menus = load_menus(app.state.settings.menus.config_path)


async def _reload_screens(app: FastAPI, _path: Path) -> None:
    from liberty.screens import load_screens
    app.state.screens = load_screens(app.state.settings.screens.config_path)


async def _reload_charts(app: FastAPI, _path: Path) -> None:
    from liberty.charts import load_charts
    app.state.charts = load_charts(app.state.settings.charts.config_path)


async def _reload_dashboards(app: FastAPI, _path: Path) -> None:
    from liberty.dashboards import load_dashboards
    app.state.dashboards = load_dashboards(app.state.settings.dashboards.config_path)


async def _reload_dictionary(app: FastAPI, _path: Path) -> None:
    """Dictionary reload cascades to connectors + screens: dictionary entries flow into
    both as column hints. Cheaper than the full /admin/reload because we skip menus +
    charts + dashboards + jobs."""
    from liberty.connectors import load_connectors
    from liberty.screens import load_screens
    settings = app.state.settings
    license_result = app.state.license  # carry the existing license result through
    new = load_connectors(
        settings.connectors.config_path,
        dictionary_path=settings.connectors.dictionary_path,
        master_key=settings.crypto.master_key,
        license=license_result,
        default_language=settings.app.default_language,
    )
    old = app.state.connectors
    app.state.connectors = new
    app.state.screens = load_screens(settings.screens.config_path)
    await old.aclose()


async def _reload_connectors(app: FastAPI, path: Path) -> None:
    """Connectors.toml watcher — re-parses + swaps the registry, **but** if the
    ``[pools.*]`` section changed (URL / password / pool_size / etc.) we don't swap
    pools. Hot-swapping a live SQL engine kills any borrowed connection from the pool
    mid-query; the operator uses the Pools page's manual Reload button when they want
    a pool change applied. We still log clearly so they know."""
    from liberty.connectors import load_connectors
    settings = app.state.settings
    old = app.state.connectors

    # Detect pool changes by comparing the [pools.*] section on disk to the live pools.
    try:
        with path.open("rb") as fh:
            on_disk_pools = tomllib.load(fh).get("pools", {})
    except Exception as exc:                     # noqa: BLE001
        _log.warning("hot_reload skip %s — file unparseable: %s", path.name, exc)
        return
    # ``_configs`` is the private dict of name → PoolConfig on the live registry; we
    # read it directly because there's no public per-name accessor (the registry's
    # public surface deliberately hides pool internals from external callers — but we
    # ARE the framework).
    live_pools = {
        name: _pool_signature(old.pools._configs.get(name))  # noqa: SLF001 — internal
        for name in old.pools.names()
    }
    on_disk_sig = {name: _pool_signature(cfg) for name, cfg in on_disk_pools.items()}
    if live_pools != on_disk_sig:
        added = sorted(set(on_disk_sig) - set(live_pools))
        removed = sorted(set(live_pools) - set(on_disk_sig))
        changed = sorted(
            n for n in (set(live_pools) & set(on_disk_sig))
            if live_pools[n] != on_disk_sig[n]
        )
        _log.warning(
            "hot_reload connectors.toml [pools.*] changed (added=%s removed=%s changed=%s) "
            "— pool changes are NOT auto-applied to avoid disrupting in-flight queries. "
            "Use Settings → Pools → Reload from disk when ready.",
            added, removed, changed,
        )

    # Re-parse + swap connector / dictionary side. The new ConnectorRegistry rebuilds its
    # PoolRegistry from the on-disk [pools.*], so the new engines DO see the new pool
    # config — but the OLD registry's engines (still in use by in-flight queries) stay
    # alive on app.state. We don't aclose the old registry in the pool-change branch;
    # we DO close it in the no-pool-change branch (safe — engines are equivalent).
    new = load_connectors(
        settings.connectors.config_path,
        dictionary_path=settings.connectors.dictionary_path,
        master_key=settings.crypto.master_key,
        license=app.state.license,
        default_language=settings.app.default_language,
    )
    app.state.connectors = new

    # Rebuild the nomaflow executors against the new registry — they cache a
    # ``connectors`` reference at construction time, so a swap here is invisible
    # to jobs without this step. Symptom: UI queries pick up the new credentials
    # (they read app.state.connectors) but jobs touching the same pool still use
    # the OLD registry and fail. Only matters when [app] hot_reload=true; the
    # /admin/reload manual path does the equivalent.
    jobs_components = getattr(app.state, "jobs", None)
    if jobs_components is not None:
        from liberty.jobs.wiring import refresh_executors
        refresh_executors(jobs_components, connectors=new, settings=settings)

    if live_pools == on_disk_sig:
        await old.aclose()
    # Else: deliberately leak the old registry's engines; they'll be GC'd when in-flight
    # requests finish. This is rare (only triggered by an actual pool edit on disk).


async def _reload_jobs(app: FastAPI, _path: Path) -> None:
    """Re-reads jobs.toml, reconciles the live scheduler (add/remove/reschedule cron
    triggers per the diff). Reuses :func:`liberty.jobs.wiring.hot_reload_registry`
    which the manual /admin/reload endpoint also calls."""
    jobs = getattr(app.state, "jobs", None)
    if jobs is None:
        _log.info("hot_reload jobs.toml changed but nomaflow not active — ignored")
        return
    from liberty.jobs.wiring import hot_reload_registry
    await hot_reload_registry(jobs, app.state.settings)


# ── helpers ────────────────────────────────────────────────────────────────────────


def _resolve_jobs_path(settings: Any) -> Path | None:
    """jobs.toml lives under settings.jobs.config_path when nomaflow is configured.
    Returns None for installs without nomaflow (the JobsSettings section absent)."""
    jobs_section = getattr(settings, "jobs", None)
    if jobs_section is None:
        return None
    return getattr(jobs_section, "config_path", None)


def _pool_signature(cfg: Any) -> tuple[Any, ...] | None:
    """Cheap value-equality token for a PoolConfig. None for an unknown pool (lookup
    returned nothing). Anything that triggers an engine rebuild belongs here — URL,
    password, pool size / overflow / pre-ping / recycle, the dialect (when
    overridden). Field list is small enough that listing it explicitly is clearer
    than a generic ``model_dump()`` comparison."""
    if cfg is None:
        return None
    # Read the few fields that matter from either a Pydantic PoolConfig or a raw dict.
    def _get(name: str, default: Any = None) -> Any:
        if isinstance(cfg, dict):
            return cfg.get(name, default)
        return getattr(cfg, name, default)
    return (
        _get("url"),
        _get("password"),
        _get("dialect"),
        _get("pool_size"),
        _get("max_overflow"),
        _get("pool_pre_ping"),
        _get("pool_recycle"),
        _get("max_rows"),
        _get("trim_strings"),
        _get("coalesce_nulls"),
    )
