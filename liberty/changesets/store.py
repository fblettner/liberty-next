"""High-level change-package operations over :class:`ChangeSetDatabase` sessions.

Phase 1 surface: resolve/auto-create the active draft for an application, append a captured entry,
and read packages back for the UI. Lifecycle transitions (submit/approve), compaction, and
export/apply land in later phases.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from liberty.changesets.models import AppliedBundle, ChangeEntry, ChangePackage, EntryStatus, Operation, PackageStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_active_package(session: AsyncSession, application: str) -> ChangePackage | None:
    """The open draft for *application*, or ``None`` if there isn't one yet."""
    res = await session.execute(
        select(ChangePackage).where(
            ChangePackage.application == application,
            ChangePackage.status == PackageStatus.DRAFT.value,
        )
    )
    return res.scalar_one_or_none()


async def get_or_create_active_package(
    session: AsyncSession, application: str, *, user: str | None = None,
) -> ChangePackage:
    """Return the application's open draft, creating ``"Package N · <app>"`` on first use. The
    partial unique index on ``(application) WHERE status='draft'`` is the safety net; this is the
    only path that opens a draft, so concurrent first-writes resolve to one package."""
    existing = await get_active_package(session, application)
    if existing is not None:
        return existing
    # Sequence the name off how many packages this application has had.
    count = await session.scalar(
        select(func.count()).select_from(ChangePackage).where(ChangePackage.application == application)
    )
    pkg = ChangePackage(
        application=application,
        name=f"Package {int(count or 0) + 1} · {application}",
        status=PackageStatus.DRAFT.value,
        created_by=user,
    )
    session.add(pkg)
    await session.flush()  # assign pkg.id before entries reference it
    return pkg


async def record_entry(
    session: AsyncSession,
    package: ChangePackage,
    *,
    connector: str,
    query: str,
    operation: Operation | str,
    entity: str | None = None,
    entity_key: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
    old_values: dict[str, Any] | None = None,
    read_query: str | None = None,
    user: str | None = None,
    replay: bool = True,
    source_action: str | None = None,
) -> ChangeEntry:
    """Append a captured entry to *package* with the next per-package ``seq`` (capture order, which
    replay must preserve). ``replay`` (CALL_API / CALL_PLUGIN only) records the action's
    ``change_replay`` opt-in — whether apply re-fires the call; row ops ignore it (always replayed)."""
    next_seq = await session.scalar(
        select(func.coalesce(func.max(ChangeEntry.seq), 0) + 1).where(ChangeEntry.package_id == package.id)
    )
    entry = ChangeEntry(
        package_id=package.id,
        seq=int(next_seq or 1),
        connector=connector,
        query=query,
        read_query=read_query,
        operation=operation.value if isinstance(operation, Operation) else str(operation),
        entity=entity,
        entity_key=entity_key,
        new_values=new_values,
        old_values=old_values,
        replay=replay,
        source_action=source_action,
        status=EntryStatus.CAPTURED.value,
        captured_by=user,
    )
    session.add(entry)
    return entry


async def capture(
    db: Any,  # ChangeSetDatabase — typed loosely to avoid a cycle
    application: str,
    *,
    connector: str,
    query: str,
    operation: Operation | str,
    entity: str | None = None,
    entity_key: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
    old_values: dict[str, Any] | None = None,
    read_query: str | None = None,
    user: str | None = None,
    post_apply_ids: list[str] | None = None,
    replay: bool = True,
    source_action: str | None = None,
) -> str:
    """One-call capture: resolve/create the application's draft and append an entry, in a single
    control-DB transaction. Returns the entry id. This is what the write-path hook calls AFTER the
    tracked write has committed (the control DB is separate from the tracked connector, so capture
    can't share the write's transaction — see the design notes). ``post_apply_ids`` (the capturing
    screen's ``Screen.post_apply``) are unioned into the package so export knows which run-once steps
    this package's changes require."""
    async with db.session() as session:
        pkg = await get_or_create_active_package(session, application, user=user)
        if post_apply_ids:
            merged = list(pkg.post_apply_ids or [])
            for pid in post_apply_ids:
                if pid not in merged:
                    merged.append(pid)
            pkg.post_apply_ids = merged
        entry = await record_entry(
            session, pkg, connector=connector, query=query, operation=operation,
            entity=entity, entity_key=entity_key, new_values=new_values, old_values=old_values,
            read_query=read_query, user=user, replay=replay, source_action=source_action,
        )
        await session.flush()
        return entry.id


async def list_packages(session: AsyncSession, *, application: str | None = None) -> list[ChangePackage]:
    """All packages (optionally filtered to one application), newest first."""
    stmt = select(ChangePackage).order_by(ChangePackage.created_at.desc())
    if application is not None:
        stmt = stmt.where(ChangePackage.application == application)
    return list((await session.execute(stmt)).scalars().all())


async def active_draft_entity_key_values(session: AsyncSession, application: str) -> list[str]:
    """Distinct ``entity_key`` VALUES across the application's active DRAFT package's entries — the
    records (e.g. role ids) touched by the accumulated, not-yet-promoted changes. Powers a batch
    job that wants "what changed since the last promotion" (e.g. the JDE security re-merge: every
    captured change is stamped with its role, so this is the set of roles to act on). Empty when
    there's no draft or its entries carry no key. Only non-empty string values are returned —
    numeric / null key parts (an audit seq, a date) aren't records to act on."""
    pkg_id = (await session.execute(
        select(ChangePackage.id)
        .where(ChangePackage.application == application, ChangePackage.status == PackageStatus.DRAFT.value)
        .order_by(ChangePackage.created_at.desc())
    )).scalars().first()
    if pkg_id is None:
        return []
    keys = (await session.execute(
        select(ChangeEntry.entity_key).where(ChangeEntry.package_id == pkg_id)
    )).scalars().all()
    out: set[str] = set()
    for ek in keys:
        for v in (ek or {}).values():
            if isinstance(v, str) and v.strip():
                out.add(v.strip())
    return sorted(out)


async def get_package(session: AsyncSession, package_id: str) -> ChangePackage | None:
    """One package with its entries eager-loaded (for the detail view)."""
    res = await session.execute(
        select(ChangePackage).where(ChangePackage.id == package_id).options(selectinload(ChangePackage.entries))
    )
    return res.scalar_one_or_none()


# ── lifecycle (Phase 2) ──────────────────────────────────────────────────────────────────────


class LifecycleError(ValueError):
    """An invalid package/entry transition (wrong current state, nothing to submit). The route maps
    it to 409 Conflict."""


async def submit_package(session: AsyncSession, package_id: str, *, user: str | None = None) -> ChangePackage | None:
    """Draft → pending (submitted for approval). Returns None if not found; raises
    :class:`LifecycleError` if it isn't a draft or has no included changes. Once submitted the draft
    slot frees up, so the next tracked write opens a fresh draft for the application."""
    pkg = await session.get(ChangePackage, package_id)
    if pkg is None:
        return None
    if pkg.status != PackageStatus.DRAFT.value:
        raise LifecycleError(f"only a draft package can be submitted (this one is {pkg.status!r})")
    included = await session.scalar(
        select(func.count()).select_from(ChangeEntry).where(
            ChangeEntry.package_id == package_id, ChangeEntry.status != EntryStatus.EXCLUDED.value,
        )
    )
    if not included:
        raise LifecycleError("package has no included changes to submit")
    pkg.status = PackageStatus.PENDING.value
    pkg.submitted_by = user
    pkg.submitted_at = _utcnow()
    return pkg


async def approve_package(session: AsyncSession, package_id: str, *, user: str | None = None) -> ChangePackage | None:
    """Pending → approved. Returns None if not found; raises :class:`LifecycleError` if not pending."""
    pkg = await session.get(ChangePackage, package_id)
    if pkg is None:
        return None
    if pkg.status != PackageStatus.PENDING.value:
        raise LifecycleError(f"only a pending package can be approved (this one is {pkg.status!r})")
    pkg.status = PackageStatus.APPROVED.value
    pkg.approved_by = user
    pkg.approved_at = _utcnow()
    return pkg


async def reject_package(session: AsyncSession, package_id: str, *, user: str | None = None) -> ChangePackage | None:
    """Pending → rejected (terminal). Returns None if not found; raises :class:`LifecycleError` if
    not pending. The operator's later work lives in the new draft; a rejected package is kept for the
    record rather than merged back (avoids two open drafts for one application)."""
    pkg = await session.get(ChangePackage, package_id)
    if pkg is None:
        return None
    if pkg.status != PackageStatus.PENDING.value:
        raise LifecycleError(f"only a pending package can be rejected (this one is {pkg.status!r})")
    pkg.status = PackageStatus.REJECTED.value
    pkg.approved_by = user  # records who actioned it
    pkg.approved_at = _utcnow()
    return pkg


async def mark_exported(session: AsyncSession, package_id: str) -> ChangePackage | None:
    """Approved → exported (records ``exported_at``). Returns None if not found; raises
    :class:`LifecycleError` if it isn't approved/exported. Re-exporting an already-exported package
    is allowed (the bundle is the artifact)."""
    pkg = await session.get(ChangePackage, package_id)
    if pkg is None:
        return None
    if pkg.status not in (PackageStatus.APPROVED.value, PackageStatus.EXPORTED.value):
        raise LifecycleError(f"only an approved package can be exported (this one is {pkg.status!r})")
    pkg.status = PackageStatus.EXPORTED.value
    pkg.exported_at = _utcnow()
    return pkg


async def delete_package(session: AsyncSession, package_id: str) -> bool:
    """Delete a package and its entries (ORM cascade). Returns True if deleted, False if not found.
    Allowed in any status — the operator may discard a draft they no longer want or prune old
    rejected/exported records. This only drops the package log; the captured rows already committed
    to their own tables and are untouched. If the deleted package was the application's active draft,
    the next tracked write simply opens a fresh one."""
    pkg = await session.get(ChangePackage, package_id)
    if pkg is None:
        return False
    await session.delete(pkg)
    return True


async def set_entry_excluded(session: AsyncSession, entry_id: str, *, excluded: bool) -> ChangeEntry | None:
    """Cherry-pick: mark an entry excluded (skipped on submit/export) or back to captured. Returns
    None if not found; raises :class:`LifecycleError` if the entry was already applied/conflicted, or
    its package is no longer a draft (can't edit a submitted/approved package)."""
    entry = await session.get(ChangeEntry, entry_id)
    if entry is None:
        return None
    if entry.status in (EntryStatus.APPLIED.value, EntryStatus.CONFLICT.value):
        raise LifecycleError(f"cannot change an entry that is {entry.status!r}")
    pkg = await session.get(ChangePackage, entry.package_id)
    if pkg is not None and pkg.status != PackageStatus.DRAFT.value:
        raise LifecycleError(f"can only edit entries of a draft package (this one is {pkg.status!r})")
    entry.status = EntryStatus.EXCLUDED.value if excluded else EntryStatus.CAPTURED.value
    return entry


# ── applied-bundle log (target side) ─────────────────────────────────────────────────────────


async def find_applied_by_checksum(session: AsyncSession, checksum: str | None) -> AppliedBundle | None:
    """The most recent prior apply of a bundle with this checksum on THIS target, or None. Drives
    the "already applied on … by …" warning so a double-apply isn't silent."""
    if not checksum:
        return None
    stmt = select(AppliedBundle).where(AppliedBundle.checksum == checksum).order_by(AppliedBundle.applied_at.desc())
    return (await session.execute(stmt)).scalars().first()


async def record_applied_bundle(
    session: AsyncSession, *, source_package_id: str | None, name: str, application: str | None,
    checksum: str | None, op_count: int, status: str, summary: dict[str, Any] | None,
    details: list[Any] | None, user: str | None,
) -> AppliedBundle:
    """Append an import-log row after a (non-dry-run) apply. Best-effort caller — a logging failure
    must never undo the already-applied writes."""
    row = AppliedBundle(
        source_package_id=source_package_id, name=name, application=application, checksum=checksum,
        op_count=op_count, status=status, summary=summary, details=details, applied_by=user,
    )
    session.add(row)
    await session.flush()
    return row


async def list_applied_bundles(session: AsyncSession, *, application: str | None = None) -> list[AppliedBundle]:
    """The target's import log (newest first), optionally filtered to one application/connector."""
    stmt = select(AppliedBundle).order_by(AppliedBundle.applied_at.desc())
    if application:
        stmt = stmt.where(AppliedBundle.application == application)
    return list((await session.execute(stmt)).scalars().all())
