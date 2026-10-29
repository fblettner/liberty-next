"""``/admin`` — upgrade history + release notes (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, router


# ── upgrade history + release notes ───────────────────────────────────────────────────────
@router.get("/upgrades", summary="List application upgrade history")
async def list_upgrades(request: Request, _: Superuser) -> dict[str, object]:
    """Application upgrade history — one row per app (code/model) version change, recorded at startup.
    Newest first. ``{upgrades: [{id, from_version, to_version, kind, summary, who, created_at}]}``."""
    store = getattr(request.app.state, "config_versions", None)
    return {"upgrades": store.list_upgrades() if store is not None else []}


@router.get("/upgrades/{upgrade_id}/report", summary="One upgrade's report")
async def get_upgrade_report(request: Request, upgrade_id: int, _: Superuser) -> dict[str, Any]:
    """The report ``liberty upgrade`` / ``liberty rollback`` recorded for one history row:
    ``{upgrade, markdown, data}``. 404 when the row has no report (the version-bump rows the
    server records at startup don't)."""
    store = getattr(request.app.state, "config_versions", None)
    row = store.get_upgrade(upgrade_id) if store is not None else None
    if row is None or not row.get("report"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no report for this upgrade")
    data, md = store.read_upgrade_report(row["report"])
    if data is None and md is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="report file is missing")
    return {"upgrade": row, "markdown": md or "", "data": data or {}}


@router.get("/release-notes", summary="Release notes")
async def get_release_notes(request: Request, _: Superuser) -> dict[str, Any]:
    """Bundled changelogs per component — the framework (liberty-next) and the licensed apps
    (liberty-apps, when installed). Each entry is raw markdown (EN + FR) + the running version; the
    frontend builds a per-version table of contents from the ``## <version> — <date>`` headings."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _dist_version
    from importlib.resources import files

    from liberty import __version__

    def _read(pkg: str, name: str) -> str:
        try:
            return (files(pkg) / name).read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, ModuleNotFoundError, TypeError):
            return ""

    def _ver(dist: str) -> str | None:
        try:
            return _dist_version(dist)
        except PackageNotFoundError:
            return None

    components: list[dict[str, Any]] = [{
        "component": "framework", "label": "Liberty Next", "version": __version__,
        "en": _read("liberty", "RELEASE.md"), "fr": _read("liberty", "RELEASE.fr.md"),
    }]
    from liberty.config_merge import apps_version as _apps_version

    apps_version = _apps_version(Path(request.app.state.settings.connectors.config_path).parent)
    if apps_version is not None:
        components.append({
            "component": "apps", "label": "Liberty Apps", "version": apps_version,
            "en": _read("liberty_apps", "RELEASE.md"), "fr": _read("liberty_apps", "RELEASE.fr.md"),
        })
    return {"components": components}


# The legacy ``GET / PUT /config/connectors`` raw-TOML endpoints powered the Settings →
# Raw editor (Monaco over the file). Removed — every config section now has a structured
# builder (Pools / Connectors / Dictionary / Menus / Screens / Dashboards) that validates
# its input against the Pydantic schemas before writing, so the raw escape hatch was both
# redundant and a foot-gun (any typo there bypassed every per-section guard). The
# /config/<section>/parsed endpoints stay in place for the structured editors.
