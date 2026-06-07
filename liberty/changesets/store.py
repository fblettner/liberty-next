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

from liberty.changesets.models import ChangeEntry, ChangePackage, EntryStatus, Operation, PackageStatus


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
    user: str | None = None,
) -> ChangeEntry:
    """Append a captured entry to *package* with the next per-package ``seq`` (capture order, which
    replay must preserve)."""
    next_seq = await session.scalar(
        select(func.coalesce(func.max(ChangeEntry.seq), 0) + 1).where(ChangeEntry.package_id == package.id)
    )
    entry = ChangeEntry(
        package_id=package.id,
        seq=int(next_seq or 1),
        connector=connector,
        query=query,
        operation=operation.value if isinstance(operation, Operation) else str(operation),
        entity=entity,
        entity_key=entity_key,
        new_values=new_values,
        old_values=old_values,
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
    user: str | None = None,
) -> str:
    """One-call capture: resolve/create the application's draft and append an entry, in a single
    control-DB transaction. Returns the entry id. This is what the write-path hook calls AFTER the
    tracked write has committed (the control DB is separate from the tracked connector, so capture
    can't share the write's transaction — see the design notes)."""
    async with db.session() as session:
        pkg = await get_or_create_active_package(session, application, user=user)
        entry = await record_entry(
            session, pkg, connector=connector, query=query, operation=operation,
            entity=entity, entity_key=entity_key, new_values=new_values, old_values=old_values, user=user,
        )
        await session.flush()
        return entry.id


async def list_packages(session: AsyncSession, *, application: str | None = None) -> list[ChangePackage]:
    """All packages (optionally filtered to one application), newest first."""
    stmt = select(ChangePackage).order_by(ChangePackage.created_at.desc())
    if application is not None:
        stmt = stmt.where(ChangePackage.application == application)
    return list((await session.execute(stmt)).scalars().all())


async def get_package(session: AsyncSession, package_id: str) -> ChangePackage | None:
    """One package with its entries eager-loaded (for the detail view)."""
    res = await session.execute(
        select(ChangePackage).where(ChangePackage.id == package_id).options(selectinload(ChangePackage.entries))
    )
    return res.scalar_one_or_none()
