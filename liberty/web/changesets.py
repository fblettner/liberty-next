"""Read-only API for change packages — Phase 1 surface.

``GET /admin/changesets`` lists packages (optionally filtered to one application/connector);
``GET /admin/changesets/{id}`` returns one package with its entries. Lifecycle transitions
(submit/approve), compaction, and export/apply land in later phases. Superuser-only — these expose
the captured row values across the connector.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select

from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.changesets import store
from liberty.changesets.models import ChangeEntry

router = APIRouter(prefix="/admin/changesets", tags=["admin", "changesets"])

Superuser = Annotated[Principal, Depends(require_superuser)]


def _db(request: Request):
    """The :class:`ChangeSetDatabase` off app.state — 503 when change packages are disabled
    (``[changesets] enabled = false``) or the control pool was unreachable at boot."""
    db = getattr(request.app.state, "changesets_db", None)
    if db is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="change packages are disabled or the control pool is unreachable",
        )
    return db


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def _pkg_summary(p: Any, entry_count: int) -> dict[str, Any]:
    return {
        "id": p.id,
        "application": p.application,
        "name": p.name,
        "status": p.status,
        "description": p.description,
        "created_by": p.created_by,
        "created_at": _iso(p.created_at),
        "submitted_by": p.submitted_by,
        "submitted_at": _iso(p.submitted_at),
        "approved_by": p.approved_by,
        "approved_at": _iso(p.approved_at),
        "entry_count": entry_count,
    }


def _entry_dict(e: ChangeEntry) -> dict[str, Any]:
    return {
        "id": e.id,
        "seq": e.seq,
        "connector": e.connector,
        "query": e.query,
        "operation": e.operation,
        "entity": e.entity,
        "entity_key": e.entity_key,
        "new_values": e.new_values,
        "old_values": e.old_values,
        "status": e.status,
        "captured_by": e.captured_by,
        "captured_at": _iso(e.captured_at),
    }


async def _entry_counts(session: Any, package_ids: list[str]) -> dict[str, int]:
    """``{package_id: entry_count}`` in one grouped query (avoids loading entries for the list)."""
    if not package_ids:
        return {}
    rows = await session.execute(
        select(ChangeEntry.package_id, func.count())
        .where(ChangeEntry.package_id.in_(package_ids))
        .group_by(ChangeEntry.package_id)
    )
    return {pid: int(n) for pid, n in rows.all()}


@router.get("", summary="List change packages")
async def list_changesets(request: Request, _: Superuser, application: str | None = None) -> dict[str, Any]:
    """Every change package (newest first), optionally filtered to one ``application`` (= the
    connector name). Each carries an ``entry_count``; entries themselves come from the detail route."""
    db = _db(request)
    async with db.session() as session:
        pkgs = await store.list_packages(session, application=application)
        counts = await _entry_counts(session, [p.id for p in pkgs])
        return {"packages": [_pkg_summary(p, counts.get(p.id, 0)) for p in pkgs]}


@router.get("/{package_id}", summary="Get a change package + its entries")
async def get_changeset(package_id: str, request: Request, _: Superuser) -> dict[str, Any]:
    """One package with its captured entries, in capture order."""
    db = _db(request)
    async with db.session() as session:
        pkg = await store.get_package(session, package_id)
        if pkg is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"change package {package_id!r} not found")
        return {**_pkg_summary(pkg, len(pkg.entries)), "entries": [_entry_dict(e) for e in pkg.entries]}
