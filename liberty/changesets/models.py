"""SQLAlchemy ORM models for change packages — ``ly_change_packages`` (one row per package) and
``ly_change_entries`` (one row per captured write, FK to the package).

These live on whichever pool ``[changesets] pool`` names (default ``"default"``, same convention as
nomaflow / auth). A private :class:`DeclarativeBase` keeps ``create_all`` scoped to just these two
tables. Status / operation enums are stored as plain strings so they stay greppable and adding a
state later needs no ALTER TYPE on Postgres. JSON columns map to ``JSONB`` on Postgres and ``TEXT``
on SQLite (the test backend) transparently.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Declarative base for the change-package tables only — keeps
    ``Base.metadata.create_all`` from touching auth / nomaflow tables."""


class PackageStatus(str, Enum):
    """Package lifecycle. ``draft`` is the single active package per application that new changes
    attach to; ``approved`` closes it (a fresh draft opens); ``exported`` / ``promoted`` track the
    transport to another environment; ``rejected`` reopens for edits."""

    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    EXPORTED = "exported"
    PROMOTED = "promoted"
    REJECTED = "rejected"


class Operation(str, Enum):
    # Row operations — a captured SQL write, net-compacted per row on export.
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    # Invocation operations — a screen action (on_save/insert/update/delete) that calls an API or a
    # plugin and is opted into replay (``change_replay``). Not a row write: stored with the resolved
    # call params in ``new_values`` and re-issued verbatim on apply (no drift check, no net-merge).
    CALL_API = "CALL_API"
    CALL_PLUGIN = "CALL_PLUGIN"


#: Row-write operations, compacted per natural key. Invocation ops fall outside this set and are
#: carried through compaction one-for-one.
ROW_OPERATIONS = frozenset({Operation.INSERT.value, Operation.UPDATE.value, Operation.DELETE.value})


class EntryStatus(str, Enum):
    """Per-entry state. ``captured`` is the default; ``excluded`` is cherry-picked out of the
    package; ``applied`` / ``conflict`` are set during promotion (Phase 3)."""

    CAPTURED = "captured"
    EXCLUDED = "excluded"
    APPLIED = "applied"
    CONFLICT = "conflict"


_STATUS_COL = String(16)


class ChangePackage(Base):
    __tablename__ = "ly_change_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    # Scope key — one active DRAFT per application (a JDE app / environment). New tracked changes
    # made while working in that application attach to its draft.
    application: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(_STATUS_COL, nullable=False, default=PackageStatus.DRAFT.value)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Union of the ``Screen.post_apply`` step-ids of the screens that contributed to this package —
    # accumulated as changes are captured. On export these ids resolve to ``[changesets] post_apply``
    # steps appended to the bundle (run once on the target). Keeps a UDC change from pulling in a
    # security screen's remerge just because they share a connector.
    post_apply_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    submitted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Promotion chain — the package this one was applied from (set on the prod side at apply time).
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    entries: Mapped[list["ChangeEntry"]] = relationship(
        back_populates="package", cascade="all, delete-orphan", order_by="ChangeEntry.seq",
    )

    __table_args__ = (
        # At most one DRAFT per application — the "current package" invariant. Partial unique index
        # works on Postgres and SQLite; the store also enforces it at the application layer.
        Index("ux_ly_change_pkg_active", "application", unique=True,
              postgresql_where=text("status = 'draft'"), sqlite_where=text("status = 'draft'")),
        Index("ix_ly_change_pkg_app_status", "application", "status"),
    )


class ChangeEntry(Base):
    __tablename__ = "ly_change_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    package_id: Mapped[str] = mapped_column(String(36), ForeignKey("ly_change_packages.id", ondelete="CASCADE"), nullable=False)
    # Capture order within the package — replay must preserve it (a role must exist before it's
    # assigned). Monotonic per package; assigned by the store at capture time.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)

    connector: Mapped[str] = mapped_column(String(128), nullable=False)
    query: Mapped[str] = mapped_column(String(256), nullable=False)
    # The screen's read query for the written table — lets the apply side fetch the current prod row
    # by its key and compare to ``old_values`` for full pre-image drift detection. Nullable: a
    # capture without a known read query falls back to existence-only drift on apply.
    read_query: Mapped[str | None] = mapped_column(String(256), nullable=True)
    operation: Mapped[str] = mapped_column(String(8), nullable=False)
    # Logical classification for the UI (user / role / relationship / security) — from the screen's
    # ``change_entity``. Nullable so a tracked write without a declared entity still records.
    entity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Natural key (JSON) identifying the affected row — for grouping/compaction + the prod WHERE.
    entity_key: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The bound write values (new state) and the ``_ORIGINAL`` pre-image (old state, for UPDATE /
    # DELETE) — drift detection on promotion compares the pre-image to prod's current row.
    new_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    old_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(_STATUS_COL, nullable=False, default=EntryStatus.CAPTURED.value)
    captured_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    package: Mapped["ChangePackage"] = relationship(back_populates="entries")

    __table_args__ = (
        Index("ix_ly_change_ent_pkg", "package_id"),
    )


class AppliedBundle(Base):
    """A promotion bundle imported + applied to THIS environment — the target-side import log.

    Lets the operator see what's been promoted *here* (what / when / by whom / with what result) and
    warns on a duplicate apply: a bundle's ``checksum`` is its identity, so the apply path can flag
    "already applied on …". Separate from :class:`ChangePackage` (which is the SOURCE-side authoring
    record) — the target usually doesn't hold the source packages, only this log of what arrived."""

    __tablename__ = "ly_applied_bundles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    # The source package's id / name / application (connector), copied from the bundle metadata.
    source_package_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    application: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # sha256 over the bundle's ops — the bundle's identity, used to detect a re-apply.
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    op_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # ``applied`` = clean; ``partial`` = some ops hit a conflict / error.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="applied")
    # The apply report's per-status counts ({applied: n, conflict: n, error: n, …}).
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Per-op results (op summary + status + detail) — the "what was applied" detail the import-log
    # entry expands to show. No row values, just op identity + outcome, so it stays compact.
    details: Mapped[list | None] = mapped_column(JSON, nullable=True)
    applied_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
