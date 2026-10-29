"""``/admin`` — screen + dependency bundle versioning, the configuration reload (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from liberty.web.admin._base import Superuser, _dump_screen, _log, router

from liberty.web.dependencies import Seed, collect_dependencies
from liberty.web.package import build_package_zip

# ── screen + dependency BUNDLE versioning (Phase 2) ───────────────────────────────────────
# File-level versioning (above) reverts a whole TOML; a screen reverting cleanly often needs its
# *dependency closure* too (the queries / lookups / sequences / DD entries it references). On every
# screen save we capture, per CHANGED screen, a ZIP of that screen's pre-save dependency closure —
# the same package the deploy tooling builds. Restoring one re-applies the bundle with the
# ``overwrite`` strategy, which preserves ``override = true`` entities (a customer fork survives).
# Bundles share the version store under a synthetic ``screen:<app>:<id>`` key so they never mix
# with file history (the file-version list filters them out).
_BUNDLE_PREFIX = "screen:"


def _screen_bundle_key(app: str, screen_id: str) -> str:
    return f"{_BUNDLE_PREFIX}{app}:{screen_id}"


def _capture_screen_bundles(request: Request, new_screens: dict[str, dict[str, dict[str, Any]]]) -> None:
    """Before a screen save overwrites ``screens.toml``, snapshot the pre-save dependency bundle of
    each screen whose definition changed. Reads the CURRENT (still-live) ``app.state`` so the bundle
    is the *prior* closure. Content-addressed + per-screen diffed, so unchanged screens cost nothing
    and an identical re-save is a no-op. Best-effort: never blocks the save."""
    store = getattr(request.app.state, "config_versions", None)
    if store is None:
        return
    live = getattr(request.app.state, "screens", None)
    live_apps = (getattr(live, "screens", None) or {}) if live is not None else {}
    src = getattr(request.app.state.settings.app, "name", "liberty-next")
    who = getattr(getattr(request, "state", None), "username", None)
    for app, screens in (new_screens or {}).items():
        old_app = live_apps.get(app) or {}
        for sid, new_def in (screens or {}).items():
            old_screen = old_app.get(sid)
            if old_screen is None:
                continue  # brand-new screen — no prior state to bundle (captured on its next save)
            try:
                if _dump_screen(old_screen) == new_def:
                    continue  # unchanged — skip (dedupe is the real guard; this just avoids the work)
            except Exception:  # noqa: BLE001 — a dump mismatch shouldn't skip the capture
                pass
            try:
                manifest = collect_dependencies(
                    request.app.state, [Seed(kind="screen", name=sid, scope=app)],
                )
                blob = build_package_zip(manifest, source_install=src)
                store.snapshot_bytes(_screen_bundle_key(app, sid), blob, source="save", who=who,
                                     comment=f"{app}.{sid} + {max(len(manifest.deps) - 1, 0)} dependencies")
            except Exception:  # noqa: BLE001 — never fail a save over bundle versioning
                _log.warning("screen bundle snapshot failed for %s.%s", app, sid, exc_info=True)


@router.post("/reload", summary="Reload config")
async def reload_connectors(request: Request, _: Superuser) -> dict[str, object]:
    """Full reload: re-verify the license, rebuild the connector registry and **every**
    component that holds its pools (auth backend, change packages, saved views, the
    nomaflow job DB + executors, the AI tools, reports), re-read every config TOML and
    reconcile the job scheduler — all through :func:`liberty.runtime.rebuild_runtime`,
    under its lock, so a concurrent file-watcher reload can't interleave."""
    from liberty.runtime import rebuild_runtime

    result = await rebuild_runtime(request.app)
    state = request.app.state
    new = state.connectors
    return {
        "reloaded": True,
        "connectors": new.names(),
        "pools": new.pools.names(),
        "dictionary_entries": len(new.dictionary.entries),
        "menu_apps": list(state.menus.menus),
        "screen_apps": list(state.screens.screens),
        "charts": [f"{s}.{c}" for s, c, _ in state.charts.iter_charts()],
        "dashboards": [d.qualified_id for _, _, d in state.dashboards.iter_dashboards()],
        "license_mode": state.license.mode,
        "nomaflow": result.nomaflow,
        "reports": result.reports,
    }
