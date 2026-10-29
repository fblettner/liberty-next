"""Retention in one place (C8) — every store the framework keeps growing, what it keeps, where
that is set, how big it is, and a purge that can be previewed (dry run) before it runs.

==================  =========================================  ===============================
store               what goes                                  set in
==================  =========================================  ===============================
``runs``            finished nomaflow runs past ``days`` /      jobs.toml ``[meta.retention]``
                    beyond ``keep_last_per_job``
``work_area``       job files (uploads, run files, watched      ``[meta.retention] files_days``
                    folders' done / failed) past the age
``notifications``   read notifications past ``retention_days``  app.toml ``[notify]``
``config_versions`` config snapshots beyond ``max_versions`` /  the ``purge_config_versions``
                    past ``max_age_days`` (newest kept)         job's step settings
``change_packages`` exported / promoted packages and import    app.toml ``[changesets]
                    logs past ``retention_days`` (0 = keep)    retention_days``
``saved_views``     nothing — the users' own analyses / views  (shown for its size)
``server_log``      rotated by size                            app.toml ``[app] log_file_*``
==================  =========================================  ===============================

:func:`status` describes them all; :func:`run` purges one (``dry_run`` counts without deleting —
the database ones delete and roll back, so the count is exact). The recurring purges stay where
they were (the nomaflow sweep, the notifications loop, the config-versions job); change packages
join the notifications loop when ``retention_days`` is set.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

STORES = ("runs", "work_area", "notifications", "config_versions", "change_packages", "saved_views", "doc_reports", "server_log")
CONFIG_PURGE_CALLABLE = "liberty.jobs.maintenance:purge_config_versions"


def _dir_size(paths: list[Path], *, recursive: bool = True) -> tuple[int, int]:
    n = size = 0
    for d in paths:
        if not d.is_dir():
            continue
        for p in (d.rglob("*") if recursive else d.iterdir()):
            if p.is_file():
                try:
                    size += p.stat().st_size
                    n += 1
                except OSError:
                    pass
    return n, size


def _config_versions_policy(st: Any) -> dict[str, Any]:
    """The limits of the config-versions purge: the first job step calling it (its kwargs), else
    the callable's defaults."""
    comps = getattr(st, "jobs", None)
    for job in (comps.registry.jobs() if comps is not None else []):
        for step in job.steps:
            if getattr(step, "callable", None) == CONFIG_PURGE_CALLABLE:
                kw = {**job.params, **step.op_kwargs}
                return {"max_versions": int(kw.get("max_versions", 20)), "max_age_days": int(kw.get("max_age_days", 90)),
                        "job": job.id, "scheduled": bool(job.enabled and job.schedule), "schedule": job.schedule}
    return {"max_versions": 20, "max_age_days": 90, "job": None, "scheduled": False, "schedule": None}


async def status(st: Any) -> list[dict[str, Any]]:
    """One row per store: ``{id, policy, where, size, can_run}``."""
    from liberty.jobs.files import files_dir
    from liberty.jobs.retention import files_days_of, watched_archives
    from liberty.logfile import log_files, log_path
    settings = st.settings
    out: list[dict[str, Any]] = []
    comps = getattr(st, "jobs", None)

    # runs + work area (nomaflow)
    if comps is not None:
        pol = comps.registry.config.meta.retention
        from sqlalchemy import func, select

        from liberty.jobs.models import JobRun
        async with comps.db.session() as s:
            runs = (await s.execute(select(func.count()).select_from(JobRun))).scalar() or 0
        out.append({"id": "runs", "policy": {"enabled": pol.enabled, "days": pol.days, "keep_last_per_job": pol.keep_last_per_job,
                    "every_minutes": pol.sweep_interval_minutes}, "where": "jobs.toml [meta.retention]", "size": {"rows": int(runs)}, "can_run": True})
        archives = watched_archives(comps.registry)
        n, size = await asyncio.to_thread(_dir_size, [files_dir(settings)])
        n2, size2 = await asyncio.to_thread(_dir_size, archives, recursive=False)
        out.append({"id": "work_area", "policy": {"days": files_days_of(pol), "enabled": pol.enabled},
                    "where": "jobs.toml [meta.retention] files_days", "size": {"files": n + n2, "bytes": size + size2}, "can_run": True})
    # notifications
    store = getattr(st, "notifications", None)
    if store is not None:
        days = int(getattr(settings.notify, "retention_days", 0) or 0)
        out.append({"id": "notifications", "policy": {"days": days}, "where": "app.toml [notify] retention_days",
                    "size": await store.counts(), "can_run": days > 0})
    # config versions
    from liberty.versioning import ConfigVersionStore
    base = Path(settings.connectors.config_path).parent
    vpol = _config_versions_policy(st)

    def _versions_size() -> dict[str, int]:
        cvs = ConfigVersionStore(base)
        n, size = _dir_size([cvs.vdir])
        return {"versions": len(cvs.list_versions()), "files": n, "bytes": size}
    out.append({"id": "config_versions", "policy": vpol, "where": f"job {vpol['job']}" if vpol["job"] else "default limits",
                "size": await asyncio.to_thread(_versions_size), "can_run": True})
    # change packages
    db = getattr(st, "changesets_db", None)
    if db is not None:
        from sqlalchemy import func, select

        from liberty.changesets.models import AppliedBundle, ChangePackage
        async with db.session() as s:
            by_status = {k: int(v) for k, v in (await s.execute(select(ChangePackage.status, func.count()).group_by(ChangePackage.status))).all()}
            bundles = (await s.execute(select(func.count()).select_from(AppliedBundle))).scalar() or 0
        days = int(getattr(settings.changesets, "retention_days", 0) or 0)
        out.append({"id": "change_packages", "policy": {"days": days}, "where": "app.toml [changesets] retention_days",
                    "size": {"packages": sum(by_status.values()), "by_status": by_status, "import_logs": int(bundles)}, "can_run": days > 0})
    # saved views
    views = getattr(st, "user_views", None)
    if views is not None:
        from sqlalchemy import func, select

        from liberty.userviews.store import UserView
        async with views.session() as s:
            by_kind = {k: int(v) for k, v in (await s.execute(select(UserView.kind, func.count()).group_by(UserView.kind))).all()}
        out.append({"id": "saved_views", "policy": {}, "where": "", "size": {"views": sum(by_kind.values()), "by_kind": by_kind}, "can_run": False})
    # documentation reports: the kept copies (deleted from Liberty Next → Documentation reports)
    from liberty.docreport.archive import archive_dir, usage
    out.append({"id": "doc_reports", "policy": {}, "where": str(archive_dir(settings)), "size": usage(settings), "can_run": False})
    # server log
    path = log_path(settings)
    files = log_files(path) if path is not None else []
    out.append({"id": "server_log", "policy": {"max_mb": settings.app.log_file_max_mb, "backups": settings.app.log_file_backups, "enabled": path is not None},
                "where": "app.toml [app] log_file_max_mb / log_file_backups", "size": {"files": len(files), "bytes": sum(f.stat().st_size for f in files)}, "can_run": False})
    return out


async def purge_change_packages(db: Any, days: int, *, dry_run: bool = False, now: datetime | None = None) -> dict[str, int]:
    """Exported / promoted packages (with their entries) and import logs older than *days*."""
    from sqlalchemy import delete, func, select

    from liberty.changesets.models import AppliedBundle, ChangeEntry, ChangePackage, PackageStatus
    if days <= 0:
        return {"packages": 0, "import_logs": 0}
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    closed = (PackageStatus.EXPORTED.value, PackageStatus.PROMOTED.value)
    async with db.session() as s:
        ids = [r[0] for r in (await s.execute(
            select(ChangePackage.id).where(ChangePackage.status.in_(closed))
            .where(func.coalesce(ChangePackage.promoted_at, ChangePackage.exported_at, ChangePackage.approved_at, ChangePackage.created_at) < cutoff)
        )).all()]
        if ids:
            await s.execute(delete(ChangeEntry).where(ChangeEntry.package_id.in_(ids)))
            await s.execute(delete(ChangePackage).where(ChangePackage.id.in_(ids)))
        logs = (await s.execute(delete(AppliedBundle).where(AppliedBundle.applied_at < cutoff))).rowcount or 0
        if dry_run:
            await s.rollback()
    return {"packages": len(ids), "import_logs": int(logs)}


async def run(st: Any, store: str, *, dry_run: bool) -> dict[str, Any]:
    """Purge one store (or count what would go). Raises ValueError for a store that has nothing to
    run (saved views, the server log) or is not available here."""
    settings = st.settings
    comps = getattr(st, "jobs", None)
    if store == "runs":
        if comps is None:
            raise ValueError("nomaflow is not running on this server")
        from liberty.jobs.retention import sweep_run_history
        rep = await sweep_run_history(comps.db, comps.registry.config.meta.retention, dry_run=dry_run)
        return {"deleted": rep.total_deleted, "detail": {"by_age": rep.deleted_by_age, "by_cap": rep.deleted_by_cap}}
    if store == "work_area":
        if comps is None:
            raise ValueError("nomaflow is not running on this server")
        from liberty.jobs.files import files_dir
        from liberty.jobs.retention import files_days_of, purge_work_area, watched_archives
        n, size, _cut = await asyncio.to_thread(purge_work_area, files_dir(settings), watched_archives(comps.registry),
                                                files_days_of(comps.registry.config.meta.retention), dry_run=dry_run)
        return {"deleted": n, "bytes": size}
    if store == "notifications":
        notif = getattr(st, "notifications", None)
        days = int(getattr(settings.notify, "retention_days", 0) or 0)
        if notif is None or days <= 0:
            raise ValueError("no notification retention ([notify] retention_days = 0, or no inbox)")
        return {"deleted": await notif.delete_older_than(days, dry_run=dry_run)}
    if store == "config_versions":
        from liberty.versioning import ConfigVersionStore
        pol = _config_versions_policy(st)
        cvs = ConfigVersionStore(Path(settings.connectors.config_path).parent)
        n = await asyncio.to_thread(cvs.purge, max_versions=pol["max_versions"], max_age_days=pol["max_age_days"], dry_run=dry_run)
        return {"deleted": n}
    if store == "change_packages":
        db = getattr(st, "changesets_db", None)
        days = int(getattr(settings.changesets, "retention_days", 0) or 0)
        if db is None or days <= 0:
            raise ValueError("no change-package retention ([changesets] retention_days = 0)")
        res = await purge_change_packages(db, days, dry_run=dry_run)
        return {"deleted": res["packages"] + res["import_logs"], "detail": res}
    raise ValueError(f"{store!r} has nothing to purge")
