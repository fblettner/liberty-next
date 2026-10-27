"""The one place that (re)builds the runtime parked on ``app.state``.

Before this module existed, the connector registry was rebuilt in four places — the
lifespan, ``POST /admin/reload``, the ``app.toml`` save path (license rotation) and the
hot-reload file watcher — and each copy re-pointed a *different* subset of the
components that hold a :class:`~liberty.connectors.db.PoolRegistry`. The ones a copy
forgot (change packages, saved views, the nomaflow job DB, the AI tools, sometimes the
auth backend) kept the **old** registry; since a disposed registry lazily recreated
engines from its old config, those components silently ran on stale pool credentials
after a reload until the process restarted. The ``oracle_thick`` hot-reload regression
(7.0.56) was the same family of bug: a kwarg added to one copy and missed in another.

:func:`rebuild_runtime` is now the only reload path. It rebuilds the registry once
(:func:`load_registry`), then re-points **every** pool-holding component from it, under
one lock so a Settings save and the file watcher can't interleave and dispose each
other's registry. The old registry is closed last, after nothing references it any
more; :meth:`PoolRegistry.dispose` marks it terminal so a stale reference fails loudly
(:class:`PoolRegistryClosedError`) instead of resurrecting an engine.

Sections (``sections=`` — default: everything, the ``POST /admin/reload`` semantic):

``license``     re-verify ``[license] key`` (otherwise the live result is carried over)
``connectors``  rebuild the connector registry + dictionary, then rebind / rebuild every
                component that holds its pools: auth backend, change-package DB, user
                views, the nomaflow job DB + step executors, the AI assistant, reports
``menus`` / ``screens`` / ``charts`` / ``actions`` / ``dashboards``   re-read that TOML
``jobs``        re-read ``jobs.toml`` and reconcile the live scheduler

A component that isn't on ``app.state`` (an API-only deployment, a partial test app) is
skipped — the function rebuilds what exists, it never conjures a subsystem the lifespan
chose not to start, with one exception: a change-package DB / user-view store that the
lifespan could *not* start (unreachable pool) gets another chance on a connectors
reload, since fixing the pool and hitting Reload is exactly the operator's intent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from liberty.connectors import load_connectors

if TYPE_CHECKING:  # pragma: no cover - typing only
    from liberty.config import Settings
    from liberty.connectors import ConnectorRegistry
    from liberty.licensing import LicenseResult

_log = logging.getLogger("liberty.runtime")

ALL_SECTIONS: frozenset[str] = frozenset(
    {"license", "connectors", "menus", "screens", "charts", "actions", "dashboards", "explorer", "jobs", "notifications"}
)


def load_registry(settings: Settings, license_result: LicenseResult | None) -> ConnectorRegistry:
    """Build a :class:`ConnectorRegistry` from *settings* — the single source of the
    ``load_connectors(...)`` call so every kwarg (``master_key``, ``license``,
    ``default_language``, ``oracle_thick``, …) is threaded in exactly once."""
    return load_connectors(
        settings.connectors.config_path,
        dictionary_path=settings.connectors.dictionary_path,
        master_key=settings.crypto.master_key,
        license=license_result,
        default_language=settings.app.default_language,
        oracle_thick=settings.app.oracle_thick,
    )


def journal_schema(settings: Settings, changes: list) -> None:
    """Record the columns :func:`liberty.schema_sync.sync_tables` just added as a
    ``framework-schema`` row of the upgrade history (next to connectors.toml, like the server's
    version bumps). No-op when nothing changed."""
    if not changes:
        return
    from liberty.schema_sync import journal_schema_changes
    from liberty.versioning import ConfigVersionStore

    try:
        store = ConfigVersionStore(Path(settings.connectors.config_path).parent)
    except Exception as exc:  # noqa: BLE001
        _log.warning("schema changes not journaled (%s): %s", exc, ", ".join(map(str, changes)))
        return
    journal_schema_changes(store, changes)


async def build_changesets_db(settings: Settings, connectors: ConnectorRegistry) -> Any | None:
    """The change-package control DB on ``[changesets] pool`` — ``None`` when disabled or
    when the pool is unusable (logged; capture is simply off for this run)."""
    if not getattr(getattr(settings, "changesets", None), "enabled", False):
        return None
    from liberty.changesets.db import ChangeSetDatabase

    cs_db = ChangeSetDatabase(connectors.pools, settings.changesets.pool)
    try:
        journal_schema(settings, await cs_db.create_schema())  # idempotent self-bootstrap
    except Exception as exc:  # noqa: BLE001 — never let a bad control pool take the app down
        _log.warning(
            "changesets pool %r unusable (%s) — change-package capture disabled", settings.changesets.pool, exc,
        )
        return None
    return cs_db


async def build_user_views(settings: Settings, connectors: ConnectorRegistry) -> Any | None:
    """Per-user saved grid / chart views on the auth pool — ``None`` when the pool is
    unusable (logged; the grid then just doesn't persist)."""
    auth = getattr(settings, "auth", None)
    if auth is None:
        return None
    try:
        from liberty.userviews import UserViewStore

        uv = UserViewStore(connectors.pools, auth.pool)
        journal_schema(settings, await uv.create_schema())
        return uv
    except Exception as exc:  # noqa: BLE001 — never let the view store take the app down
        _log.warning("user-view store on pool %r unusable (%s) — saved views disabled", auth.pool, exc)
        return None


async def build_notifications(settings: Settings, connectors: ConnectorRegistry) -> Any | None:
    """The notification inbox store on ``[notify] pool`` — ``None`` when disabled or the pool is
    unusable (logged; the bell then shows nothing, the dispatcher's portal channel reports it)."""
    notify = getattr(settings, "notify", None)
    if notify is None or not notify.enabled:
        return None
    try:
        from liberty.notify import NotificationStore

        st = NotificationStore(connectors.pools, notify.pool)
        journal_schema(settings, await st.create_schema())
        return st
    except Exception as exc:  # noqa: BLE001 — never let the inbox take the app down
        _log.warning("notification store on pool %r unusable (%s) — the inbox is disabled", notify.pool, exc)
        return None


def build_dispatcher(app: Any, settings: Settings) -> Any | None:
    """The dispatcher reads the app's holders through callables, so a reload that rebuilds the
    auth backend, the registry or the store needs no new dispatcher."""
    notify = getattr(settings, "notify", None)
    if notify is None or not notify.enabled:
        return None
    from liberty.notify import Dispatcher

    state = app.state
    return Dispatcher(
        getattr(state, "notifications", None), lambda: getattr(state, "settings", settings),
        auth_backend=lambda: getattr(state, "auth_backend", None),
        connectors=lambda: getattr(state, "connectors", None),
        sio=lambda: getattr(state, "sio", None),
    )


@dataclass
class RebuildResult:
    """What a rebuild touched — the ``POST /admin/reload`` reply is built from it."""

    sections: set[str]
    license: Any | None = None
    connectors: Any | None = None
    nomaflow: dict[str, Any] = field(default_factory=lambda: {"enabled": False})
    reports: list[str] = field(default_factory=list)


def _lock_for(app: Any) -> asyncio.Lock:
    lock = getattr(app.state, "runtime_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.runtime_lock = lock
    return lock


async def rebuild_runtime(
    app: Any,
    *,
    sections: set[str] | frozenset[str] | None = None,
    license_result: LicenseResult | None = None,
    dispose_old: bool = True,
) -> RebuildResult:
    """Rebuild the requested *sections* of the runtime on ``app.state`` (all by default).

    *license_result* — a license already verified by the caller (the ``app.toml`` save
    path); when given it is used as-is and ``"license"`` isn't re-verified.
    *dispose_old* — close the replaced connector registry (the default). The
    connectors.toml watcher passes ``False`` when the ``[pools.*]`` section changed on
    disk: disposing would kill connections borrowed by in-flight queries, so the old
    registry is left open and released when those requests finish. Everything on
    ``app.state`` points at the new registry either way.
    """
    wanted = set(ALL_SECTIONS if sections is None else sections)
    unknown = wanted - ALL_SECTIONS
    if unknown:
        raise ValueError(f"unknown runtime section(s): {sorted(unknown)}")

    async with _lock_for(app):
        settings = app.state.settings
        result = RebuildResult(sections=wanted)

        # ── license ───────────────────────────────────────────────────────────────
        if license_result is not None:
            app.state.license = license_result
        elif "license" in wanted:
            from liberty.licensing import verify_license

            app.state.license = verify_license(settings.license.key, master_key=settings.crypto.master_key)
        result.license = getattr(app.state, "license", None)

        # ── connectors + everything that holds their pools ────────────────────────
        if "connectors" in wanted:
            old = getattr(app.state, "connectors", None)
            new = load_registry(settings, result.license)
            app.state.connectors = new
            result.connectors = new
            await _rebind_pool_holders(app, settings, new)
            if old is not None and old is not new:
                if dispose_old:
                    await old.aclose()
                else:
                    _log.info("runtime: previous connector registry left open for in-flight queries")

        # ── the plain config files ────────────────────────────────────────────────
        if "menus" in wanted:
            from liberty.menus import load_menus

            app.state.menus = load_menus(settings.menus.config_path)
        if "screens" in wanted:
            from liberty.screens import load_screens

            app.state.screens = load_screens(settings.screens.config_path)
        if "charts" in wanted:
            from liberty.charts import load_charts

            app.state.charts = load_charts(settings.charts.config_path)
        if "actions" in wanted:
            from liberty.actions import load_actions

            app.state.actions = load_actions(settings.actions.config_path)
        if "dashboards" in wanted:
            from liberty.dashboards import load_dashboards

            app.state.dashboards = load_dashboards(settings.dashboards.config_path)
        if "explorer" in wanted:
            from liberty.explorer import load_explorer

            app.state.explorer = load_explorer(settings.explorer.config_path)
        # The explorer's discovered fields depend on the connectors, the screens, the dictionary
        # and the model: any reload empties the cache.
        cache = getattr(app.state, "explorer_cache", None)
        if cache is not None:
            cache.clear()
        if "notifications" in wanted and getattr(settings, "notify", None) is not None:
            from liberty.notify import load_notification_rules

            app.state.notification_rules = load_notification_rules(settings.notify.config_path, strict=False)

        # ── nomaflow: jobs.toml reconcile (the executors were rebound above) ──────
        jobs = getattr(app.state, "jobs", None)
        if jobs is not None:
            if "jobs" in wanted:
                from liberty.jobs.wiring import hot_reload_registry

                await hot_reload_registry(jobs, settings)
            result.nomaflow = {
                "enabled": True,
                "jobs": [j.id for j in jobs.registry.jobs()],
                "scheduled_jobs": jobs.scheduler.scheduled_job_ids,
            }

        reports = getattr(app.state, "reports", None)
        if reports is not None:
            result.reports = list(reports.names())
        return result


async def _rebind_pool_holders(app: Any, settings: Settings, new: ConnectorRegistry) -> None:
    """Re-point every component that captured the previous registry's pools."""
    state = app.state

    # Auth backend — the DB backend wraps an AuthDatabase on ``[auth] pool``.
    if getattr(settings, "auth", None) is not None and hasattr(state, "auth_backend"):
        from liberty.auth.authstore import build_auth_backend

        state.auth_backend = build_auth_backend(settings, new.pools)

    # Change packages — rebind when running; retry the bootstrap when it failed at boot.
    if hasattr(state, "changesets_db"):
        if state.changesets_db is not None:
            state.changesets_db.rebind(new.pools)
        else:
            state.changesets_db = await build_changesets_db(settings, new)

    # Saved views — same policy.
    if hasattr(state, "user_views"):
        if state.user_views is not None:
            state.user_views.rebind(new.pools)
        else:
            state.user_views = await build_user_views(settings, new)

    # Notifications — the inbox store rebinds; the dispatcher reads the holders lazily.
    if hasattr(state, "notifications"):
        if state.notifications is not None:
            state.notifications.rebind(new.pools)
        else:
            state.notifications = await build_notifications(settings, new)
        state.notify = build_dispatcher(app, settings)

    # nomaflow — the job DB holds the pools; the step executors captured the registry.
    jobs = getattr(state, "jobs", None)
    if jobs is not None:
        from liberty.jobs.wiring import refresh_executors

        jobs.db.rebind(new.pools)
        jobs.changesets = getattr(state, "changesets_db", None)
        refresh_executors(jobs, connectors=new, settings=settings)

    # AI assistant — its tools bind to the registry.
    if hasattr(state, "ai") and getattr(settings, "ai", None) is not None:
        from liberty.ai import build_assistant

        old_ai = state.ai
        state.ai = build_assistant(settings.ai, new, master_key=settings.crypto.master_key)
        if old_ai is not None and old_ai is not state.ai:
            await old_ai.aclose()

    # Reports — license-gated plugin registry.
    if hasattr(state, "reports") and getattr(settings, "reports", None) is not None:
        from liberty.reports.wiring import refresh_reports

        refresh_reports(app, settings, getattr(state, "license", None))
