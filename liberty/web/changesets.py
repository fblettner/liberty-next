"""Read-only API for change packages — Phase 1 surface.

``GET /admin/changesets`` lists packages (optionally filtered to one application/connector);
``GET /admin/changesets/{id}`` returns one package with its entries. Lifecycle transitions
(submit/approve), compaction, and export/apply land in later phases. Superuser-only — these expose
the captured row values across the connector.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select

from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.changesets import store
from liberty.changesets.apply import apply_bundle
from liberty.changesets.compaction import compact
from liberty.changesets.models import ChangeEntry

BUNDLE_FORMAT = "liberty-changeset/1"


def _bundle(pkg: Any, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the portable promotion bundle: package metadata + the compacted (replay-ready) ops
    + a checksum over the ops so the apply side can detect tampering / truncation."""
    clean = [{k: v for k, v in o.items() if k != "source_ids"} for o in ops]
    checksum = hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "format": BUNDLE_FORMAT,
        "package": {
            "id": pkg.id, "name": pkg.name, "application": pkg.application,
            "approved_by": pkg.approved_by,
            "approved_at": pkg.approved_at.isoformat() if pkg.approved_at is not None else None,
        },
        "op_count": len(clean),
        "ops": clean,
        "checksum": checksum,
    }

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


@router.delete("/{package_id}", summary="Delete a change package")
async def delete_changeset(package_id: str, request: Request, _: Superuser) -> dict[str, Any]:
    """Delete a package + its entries. Any status — discard a draft or prune old records. Only the
    package log is removed; the captured rows already live in their own tables. 404 if not found."""
    db = _db(request)
    async with db.session() as session:
        deleted = await store.delete_package(session, package_id)
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"change package {package_id!r} not found")
        await session.flush()
    return {"deleted": package_id}


# ── lifecycle (Phase 2) ──────────────────────────────────────────────────────────────────────


async def _transition(request: Request, package_id: str, fn, *, user: str | None) -> dict[str, Any]:
    """Run a package lifecycle transition (submit/approve/reject) + return the package detail.
    404 if the package is gone; 409 on an invalid transition (:class:`store.LifecycleError`)."""
    db = _db(request)
    async with db.session() as session:
        try:
            pkg = await fn(session, package_id, user=user)
        except store.LifecycleError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if pkg is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"change package {package_id!r} not found")
        await session.flush()
        full = await store.get_package(session, package_id)
        return {**_pkg_summary(full, len(full.entries)), "entries": [_entry_dict(e) for e in full.entries]}


@router.post("/{package_id}/submit", summary="Submit a draft package for approval")
async def submit_changeset(package_id: str, request: Request, principal: Superuser) -> dict[str, Any]:
    return await _transition(request, package_id, store.submit_package, user=principal.username)


@router.post("/{package_id}/approve", summary="Approve a pending package")
async def approve_changeset(package_id: str, request: Request, principal: Superuser) -> dict[str, Any]:
    return await _transition(request, package_id, store.approve_package, user=principal.username)


@router.post("/{package_id}/reject", summary="Reject a pending package")
async def reject_changeset(package_id: str, request: Request, principal: Superuser) -> dict[str, Any]:
    return await _transition(request, package_id, store.reject_package, user=principal.username)


async def _set_excluded(request: Request, entry_id: str, *, excluded: bool) -> dict[str, Any]:
    db = _db(request)
    async with db.session() as session:
        try:
            entry = await store.set_entry_excluded(session, entry_id, excluded=excluded)
        except store.LifecycleError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if entry is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"change entry {entry_id!r} not found")
        await session.flush()
        return _entry_dict(entry)


@router.post("/{package_id}/entries/{entry_id}/exclude", summary="Exclude an entry from its package")
async def exclude_entry(package_id: str, entry_id: str, request: Request, _: Superuser) -> dict[str, Any]:
    """Cherry-pick an entry OUT of its (draft) package — skipped on submit/export. ``package_id`` is
    path context; the entry is addressed by its own id."""
    return await _set_excluded(request, entry_id, excluded=True)


@router.post("/{package_id}/entries/{entry_id}/include", summary="Re-include a previously excluded entry")
async def include_entry(package_id: str, entry_id: str, request: Request, _: Superuser) -> dict[str, Any]:
    return await _set_excluded(request, entry_id, excluded=False)


# ── export (Phase 3) ─────────────────────────────────────────────────────────────────────────


@router.get("/{package_id}/export", summary="Export an approved package as a promotion bundle")
async def export_changeset(package_id: str, request: Request, _: Superuser) -> dict[str, Any]:
    """Compact the (approved) package's entries to the net change per row and return a portable
    bundle for promotion to another environment. Marks the package ``exported``. 409 if it isn't
    approved/exported."""
    db = _db(request)
    async with db.session() as session:
        pkg = await store.get_package(session, package_id)
        if pkg is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"change package {package_id!r} not found")
        ops = compact(pkg.entries)
        try:
            await store.mark_exported(session, package_id)
        except store.LifecycleError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return _bundle(pkg, ops)


# ── apply (Phase 3) — run on the TARGET (prod) Liberty ──────────────────────────────────────


class ApplyBody(BaseModel):
    """Payload for ``POST /admin/changesets/apply``. ``bundle`` is a promotion bundle exported from
    another environment. ``dry_run`` checks drift + reports without writing. ``force`` lists op
    indices to apply despite a drift conflict."""

    bundle: dict[str, Any]
    dry_run: bool = True
    force: list[int] = []


@router.post("/apply", summary="Apply a promotion bundle to this environment")
async def apply_changeset(body: ApplyBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Replay a bundle's ops against THIS Liberty's connectors with full pre-image drift detection.
    Defaults to a dry run (drift report only). Validates the bundle format + checksum first."""
    bundle = body.bundle
    if bundle.get("format") != BUNDLE_FORMAT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unsupported bundle format {bundle.get('format')!r}")
    ops = bundle.get("ops")
    if not isinstance(ops, list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="bundle has no ops")
    expected = hashlib.sha256(json.dumps(ops, sort_keys=True, default=str).encode()).hexdigest()
    if bundle.get("checksum") and bundle["checksum"] != expected:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="bundle checksum mismatch — file is corrupt or tampered")
    connectors = getattr(request.app.state, "connectors", None)
    if connectors is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="connectors not available")
    return await apply_bundle(connectors, bundle, dry_run=body.dry_run, forced={str(i) for i in body.force})
