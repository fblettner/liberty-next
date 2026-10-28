"""The notifications inbox — one row per (recipient, message) on the framework pool.

``ly2_notification`` is self-bootstrapping like the other framework tables: :meth:`create_schema`
goes through :func:`liberty.schema_sync.sync_tables` from the lifespan, so the table appears on an
existing install at upgrade. Keyed by ``username`` (a string), so OIDC principals without a local
row work too. The bell reads :meth:`unread_count`, the dropdown and the inbox page read
:meth:`list_for`, a click calls :meth:`mark_read`.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from sqlalchemy import DateTime, String, Text, delete as sa_delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

from liberty.connectors.db import PoolRegistry
from liberty.schema_sync import SchemaChange, sync_tables


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Own metadata so ``create_all`` touches only the notification table."""


class Notification(Base):
    __tablename__ = "ly2_notification"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    username: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    rule: Mapped[str | None] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    # Where the row leads when clicked — a frontend route (``/sql/nomajde/period_status``).
    link: Mapped[str | None] = mapped_column(String(1024))
    # The event's values (the row, the job …) for the inbox to show or the link to use.
    payload: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "username": self.username, "rule": self.rule, "subject": self.subject,
            "body": self.body, "link": self.link, "payload": dict(self.payload or {}),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "read": self.read_at is not None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
        }


class NotificationStore:
    """CRUD over :class:`Notification` on the configured pool."""

    def __init__(self, pools: PoolRegistry, pool_name: str = "default") -> None:
        self._pools = pools
        self._pool_name = pool_name
        self._maker: async_sessionmaker[AsyncSession] | None = None

    @property
    def pool_name(self) -> str:
        return self._pool_name

    def rebind(self, pools: PoolRegistry) -> None:
        """Point at a rebuilt :class:`PoolRegistry` (config reload) — see :mod:`liberty.runtime`."""
        self._pools = pools
        self._maker = None

    def _sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._maker is None:
            self._maker = async_sessionmaker(self._pools.engine(self._pool_name), expire_on_commit=False)
        return self._maker

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker()() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def create_schema(self) -> list[SchemaChange]:
        engine = self._pools.engine(self._pool_name)
        async with engine.begin() as conn:
            return await conn.run_sync(sync_tables, Base.metadata)

    async def add(
        self, usernames: list[str], *, subject: str, body: str | None = None, link: str | None = None,
        rule: str | None = None, payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """One row per recipient (duplicates in *usernames* collapse to one)."""
        rows = [
            Notification(username=u, rule=rule, subject=subject[:512], body=body, link=link, payload=dict(payload or {}))
            for u in dict.fromkeys(usernames)
        ]
        if not rows:
            return []
        async with self.session() as s:
            s.add_all(rows)
            await s.flush()
            return [r.to_dict() for r in rows]

    async def list_for(self, username: str, *, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        stmt = select(Notification).where(Notification.username == username)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(max(1, min(limit, 500)))
        async with self.session() as s:
            return [r.to_dict() for r in (await s.execute(stmt)).scalars().all()]

    async def unread_count(self, username: str) -> int:
        stmt = select(func.count()).select_from(Notification).where(Notification.username == username, Notification.read_at.is_(None))
        async with self.session() as s:
            return int((await s.execute(stmt)).scalar_one())

    async def mark_read(self, username: str, ids: list[str] | None = None) -> int:
        """Mark the user's notifications read — *ids* or every unread one. Only the user's own
        rows (a foreign id is ignored). Returns how many changed."""
        stmt = update(Notification).where(Notification.username == username, Notification.read_at.is_(None))
        if ids is not None:
            stmt = stmt.where(Notification.id.in_(ids))
        stmt = stmt.values(read_at=_utcnow())
        async with self.session() as s:
            return int((await s.execute(stmt)).rowcount or 0)

    async def delete_older_than(self, days: int, *, dry_run: bool = False) -> int:
        """Retention: drop read notifications older than *days* (*dry_run*: count them only)."""
        cutoff = _utcnow() - timedelta(days=days)
        stmt = sa_delete(Notification).where(Notification.read_at.is_not(None), Notification.created_at < cutoff)
        async with self.session() as s:
            n = int((await s.execute(stmt)).rowcount or 0)
            if dry_run:
                await s.rollback()
            return n

    async def counts(self) -> dict[str, int]:
        """``{total, read}`` — the Retention page's size of the inbox."""
        from sqlalchemy import func, select
        async with self.session() as s:
            total = (await s.execute(select(func.count()).select_from(Notification))).scalar() or 0
            read = (await s.execute(select(func.count()).select_from(Notification).where(Notification.read_at.is_not(None)))).scalar() or 0
        return {"total": int(total), "read": int(read)}
