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
        "kind": getattr(p, "kind", "auto") or "auto",
        "description": p.description,
        "created_by": p.created_by,
        "created_at": _iso(p.created_at),
        "submitted_by": p.submitted_by,
        "submitted_at": _iso(p.submitted_at),
        "approved_by": p.approved_by,
        "approved_at": _iso(p.approved_at),
        "post_apply_ids": p.post_apply_ids or [],
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
        "replay": e.replay if e.replay is not None else True,
        "source_action": e.source_action,
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


class CreatePackageBody(BaseModel):
    """Create a custom (operator-managed) holding package for an application."""

    application: str
    name: str
    description: str | None = None


@router.post("", summary="Create a custom change package")
async def create_changeset(body: CreatePackageBody, request: Request, principal: Superuser) -> dict[str, Any]:
    """Open a CUSTOM holding package for ``application``. It never receives auto-captures — the
    operator moves entries into it to park work for a future promotion — but has the full lifecycle.
    Many custom packages may coexist with the one auto draft per application."""
    if not body.application.strip() or not body.name.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="application and name are required")
    db = _db(request)
    async with db.session() as session:
        pkg = await store.create_custom_package(
            session, body.application.strip(), name=body.name, description=body.description, user=principal.username,
        )
        await session.flush()
        return {**_pkg_summary(pkg, 0), "entries": []}


class RenamePackageBody(BaseModel):
    """Rename / re-describe a draft package. ``None`` leaves a field untouched."""

    name: str | None = None
    description: str | None = None


@router.patch("/{package_id}", summary="Rename a draft change package")
async def rename_changeset(package_id: str, body: RenamePackageBody, request: Request, _: Superuser) -> dict[str, Any]:
    db = _db(request)
    async with db.session() as session:
        try:
            pkg = await store.rename_package(session, package_id, name=body.name, description=body.description)
        except store.LifecycleError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if pkg is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"change package {package_id!r} not found")
        await session.flush()
        full = await store.get_package(session, package_id)
        return {**_pkg_summary(full, len(full.entries)), "entries": [_entry_dict(e) for e in full.entries]}


def _applied_dict(a: Any) -> dict[str, Any]:
    return {
        "id": a.id, "source_package_id": a.source_package_id, "name": a.name,
        "application": a.application, "checksum": a.checksum, "op_count": a.op_count,
        "status": a.status, "summary": a.summary, "details": a.details,
        "applied_by": a.applied_by, "applied_at": _iso(a.applied_at),
    }


@router.get("/applied", summary="List bundles applied to this environment")
async def list_applied(request: Request, _: Superuser, application: str | None = None) -> dict[str, Any]:
    """The target-side import log — every promotion bundle applied to THIS environment (newest
    first), optionally filtered to one ``application``. Defined before ``/{package_id}`` so the
    static path wins over the path param."""
    db = _db(request)
    async with db.session() as session:
        return {"applied": [_applied_dict(a) for a in await store.list_applied_bundles(session, application=application)]}


class PostApplyBody(BaseModel):
    """The full set of post-apply steps to persist (replaces the existing ``[changesets] post_apply``)."""

    post_apply: list[dict[str, Any]] = []


@router.get("/config/post-apply", summary="Get the configured post-apply steps")
async def get_post_apply(request: Request, _: Superuser) -> dict[str, Any]:
    """The run-once post-apply steps (``[changesets] post_apply``) + the connector names for the
    editor's dropdowns. Static path — defined before ``/{package_id}``."""
    settings = getattr(request.app.state, "settings", None)
    steps = getattr(getattr(settings, "changesets", None), "post_apply", None) or []
    connectors = sorted(request.app.state.connectors.names()) if hasattr(request.app.state, "connectors") else []
    return {
        "post_apply": [s.model_dump(exclude_none=True) for s in steps],
        "connectors": connectors,
        "types": ["call_plugin", "call_api", "run_query"],
    }


@router.put("/config/post-apply", summary="Set the post-apply steps")
async def put_post_apply(body: PostApplyBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Validate + persist the post-apply steps to ``[changesets] post_apply`` in app.toml (tomlkit —
    other sections untouched), and live-update the in-memory settings so export picks them up without
    a restart."""
    import tomlkit
    from pydantic import ValidationError
    from liberty.config import PostApplyStep
    from liberty.web.admin import _app_config_path
    try:
        steps = [PostApplyStep.model_validate(s) for s in body.post_apply]
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid post-apply steps: {exc}") from exc

    path = _app_config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    if "changesets" not in doc:
        doc["changesets"] = tomlkit.table()
    doc["changesets"]["post_apply"] = [s.model_dump(exclude_none=True) for s in steps]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    # Live-apply so the next export sees the new steps without a restart.
    settings = getattr(request.app.state, "settings", None)
    if settings is not None and getattr(settings, "changesets", None) is not None:
        settings.changesets.post_apply = steps
    return {"saved": True, "post_apply": [s.model_dump(exclude_none=True) for s in steps]}


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


class EntryIdsBody(BaseModel):
    """A set of entry ids to act on (move / delete) — one or many."""

    entry_ids: list[str] = []


@router.post("/{package_id}/entries/move-in", summary="Move entries into this package")
async def move_entries_in(package_id: str, body: EntryIdsBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Move the given entries (from their current packages) INTO ``package_id``, re-sequenced at its
    tail. ``package_id`` is the DESTINATION. Both source and destination must be drafts of the same
    application. Returns the destination package detail with a ``moved`` count."""
    db = _db(request)
    async with db.session() as session:
        try:
            dest, moved = await store.move_entries(session, entry_ids=body.entry_ids, dest_package_id=package_id)
        except store.LifecycleError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        await session.flush()
        full = await store.get_package(session, dest.id)
        return {**_pkg_summary(full, len(full.entries)), "entries": [_entry_dict(e) for e in full.entries], "moved": moved}


@router.post("/{package_id}/entries/delete", summary="Permanently delete entries from a package")
async def delete_entries(package_id: str, body: EntryIdsBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Permanently remove the given entries from their (draft) package — unlike ``exclude`` (which
    only skips on export), a deleted entry is gone, so a later re-export can't re-include it. Only
    the package log rows go; the rows already written to their own tables are untouched."""
    db = _db(request)
    async with db.session() as session:
        try:
            deleted = await store.delete_entries(session, entry_ids=body.entry_ids)
        except store.LifecycleError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        await session.flush()
        full = await store.get_package(session, package_id)
        if full is None:
            return {"deleted": deleted, "entries": []}
        return {**_pkg_summary(full, len(full.entries)), "entries": [_entry_dict(e) for e in full.entries], "deleted": deleted}


# ── export (Phase 3) ─────────────────────────────────────────────────────────────────────────


_POST_APPLY_OP = {"call_plugin": "CALL_PLUGIN", "call_api": "CALL_API", "run_query": "RUN_QUERY"}


def _post_apply_ops(settings: Any, post_apply_ids: list[str] | None, application: str | None) -> list[dict[str, Any]]:
    """Resolve the package's accumulated ``post_apply_ids`` (the union of the contributing screens'
    ``Screen.post_apply``) against the ``[changesets] post_apply`` library → apply-ops appended to
    the bundle tail, in id order, deduped. Each carries ``post_apply: true`` so the apply engine + UI
    mark them apart from captured change ops. Always ``unverified`` on apply (no key / read query) —
    they run once after every change op has landed. Unknown ids are skipped."""
    if not post_apply_ids:
        return []
    library = {s.id: s for s in (getattr(getattr(settings, "changesets", None), "post_apply", None) or [])}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pid in post_apply_ids:
        if pid in seen:
            continue
        seen.add(pid)
        s = library.get(pid)
        if s is None:
            continue
        out.append({
            "connector": s.connector or application, "query": s.target, "read_query": None,
            "operation": _POST_APPLY_OP.get(s.type, "RUN_QUERY"),
            "entity": "post_apply", "entity_key": None,
            "new_values": dict(s.params) or None, "old_values": None,
            "post_apply": True, "label": s.label or s.id,
        })
    return out


@router.get("/{package_id}/export", summary="Export an approved package as a promotion bundle")
async def export_changeset(package_id: str, request: Request, _: Superuser) -> dict[str, Any]:
    """Compact the (approved) package's entries to the net change per row and return a portable
    bundle for promotion to another environment. Appends the connector's configured run-once
    ``post_apply`` steps (e.g. a JDE remerge) to the bundle tail. Marks the package ``exported``.
    409 if it isn't approved/exported."""
    db = _db(request)
    settings = getattr(request.app.state, "settings", None)
    async with db.session() as session:
        pkg = await store.get_package(session, package_id)
        if pkg is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"change package {package_id!r} not found")
        ops = compact(pkg.entries) + _post_apply_ops(settings, pkg.post_apply_ids, pkg.application)
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
async def apply_changeset(body: ApplyBody, request: Request, principal: Superuser) -> dict[str, Any]:
    """Replay a bundle's ops against THIS Liberty's connectors with full pre-image drift detection.
    Defaults to a dry run (drift report only). Validates the bundle format + checksum first.

    The report carries ``already_applied`` (a prior apply of the same checksum, or null) so the UI
    can warn before a duplicate apply — non-blocking. A non-dry-run apply is recorded in the
    target's import log (``ly_applied_bundles``) so this environment knows what's been promoted here."""
    bundle = body.bundle
    if bundle.get("format") != BUNDLE_FORMAT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unsupported bundle format {bundle.get('format')!r}")
    ops = bundle.get("ops")
    if not isinstance(ops, list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="bundle has no ops")
    expected = hashlib.sha256(json.dumps(ops, sort_keys=True, default=str).encode()).hexdigest()
    checksum = bundle.get("checksum")
    if checksum and checksum != expected:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="bundle checksum mismatch — file is corrupt or tampered")
    connectors = getattr(request.app.state, "connectors", None)
    if connectors is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="connectors not available")
    settings = getattr(request.app.state, "settings", None)

    # Import-log lookup + recording is best-effort: the control DB may be disabled (changesets off),
    # but applying a bundle to the target must still work. The bundle's own checksum is its identity.
    db = getattr(request.app.state, "changesets_db", None)
    pkg_meta = bundle.get("package") or {}
    already = None
    if db is not None and checksum:
        try:
            async with db.session() as s:
                prev = await store.find_applied_by_checksum(s, checksum)
                if prev is not None:
                    already = {"name": prev.name, "applied_at": _iso(prev.applied_at), "applied_by": prev.applied_by}
        except Exception:  # noqa: BLE001 — never block an apply on the log lookup
            already = None

    report = await apply_bundle(
        connectors, bundle, dry_run=body.dry_run, forced={str(i) for i in body.force}, settings=settings,
    )
    report["already_applied"] = already

    if not body.dry_run and db is not None:
        summ = report.get("summary") or {}
        status_str = "partial" if (summ.get("conflict") or summ.get("error")) else "applied"
        try:
            async with db.session() as s:
                await store.record_applied_bundle(
                    s, source_package_id=pkg_meta.get("id"),
                    name=pkg_meta.get("name") or "bundle", application=pkg_meta.get("application"),
                    checksum=checksum, op_count=int(bundle.get("op_count") or len(ops)),
                    status=status_str, summary=summ, details=report.get("results") or None,
                    user=principal.username,
                )
        except Exception as exc:  # noqa: BLE001 — log failure must not undo the applied writes
            import logging
            logging.getLogger(__name__).error("failed to record applied bundle: %s", exc)
    return report
