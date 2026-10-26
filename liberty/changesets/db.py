"""Async session plumbing for the change-package tables — mirrors :mod:`liberty.jobs.db`.

The ``ly_change_*`` tables share the same :class:`~liberty.connectors.db.PoolRegistry` the SQL
connectors use; ``[changesets] pool`` (default ``"default"``) picks which one. They live on a
*control* pool (Postgres in practice), NOT on the connector being tracked — a JDE write is mirrored
into the control DB, not back into JDE. The engine + sessionmaker are built lazily on first use.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from liberty.changesets.models import Base
from liberty.schema_sync import SchemaChange, sync_tables
from liberty.connectors.db import PoolRegistry


class ChangeSetDatabase:
    """Owns the change-package session factory for one named pool."""

    def __init__(self, pools: PoolRegistry, pool_name: str = "default") -> None:
        self._pools = pools
        self._pool_name = pool_name
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    @property
    def pool_name(self) -> str:
        return self._pool_name

    def rebind(self, pools: PoolRegistry) -> None:
        """Point at a rebuilt :class:`PoolRegistry` (config reload). Drops the cached
        sessionmaker so the next session opens on the new registry's engine instead of
        the disposed one — see :mod:`liberty.runtime`."""
        self._pools = pools
        self._sessionmaker = None

    def _maker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            engine = self._pools.engine(self._pool_name)
            self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        return self._sessionmaker

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session; commits on clean exit, rolls back on exception."""
        async with self._maker()() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def create_schema(self) -> list[SchemaChange]:
        """Create the ``ly_change_*`` tables on the configured pool if absent and forward-add the
        columns an older install lacks (idempotent). Returns what was added."""
        engine = self._pools.engine(self._pool_name)
        async with engine.begin() as conn:
            changes = await conn.run_sync(sync_tables, Base.metadata)
            if any(c.table == "ly_change_packages" and c.column == "kind" for c in changes):
                await conn.run_sync(self._rebuild_active_draft_index)
        return changes

    @staticmethod
    def _rebuild_active_draft_index(sync_conn: Any) -> None:
        """When ``kind`` was just added, the active-draft unique index still carries the old
        ``WHERE status='draft'`` predicate (create_all won't ALTER it). Recreate it scoped to
        ``kind='auto'`` so custom drafts can coexist. ``CREATE UNIQUE INDEX … WHERE`` + ``DROP
        INDEX IF EXISTS`` are accepted by both Postgres and SQLite."""
        sync_conn.exec_driver_sql("DROP INDEX IF EXISTS ux_ly_change_pkg_active")
        sync_conn.exec_driver_sql(
            "CREATE UNIQUE INDEX ux_ly_change_pkg_active ON ly_change_packages (application) "
            "WHERE status = 'draft' AND kind = 'auto'"
        )

    async def drop_schema(self) -> None:  # pragma: no cover - destructive helper
        engine = self._pools.engine(self._pool_name)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
