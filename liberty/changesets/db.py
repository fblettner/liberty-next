"""Async session plumbing for the change-package tables — mirrors :mod:`liberty.jobs.db`.

The ``ly_change_*`` tables share the same :class:`~liberty.connectors.db.PoolRegistry` the SQL
connectors use; ``[changesets] pool`` (default ``"default"``) picks which one. They live on a
*control* pool (Postgres in practice), NOT on the connector being tracked — a JDE write is mirrored
into the control DB, not back into JDE. The engine + sessionmaker are built lazily on first use.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from liberty.changesets.models import Base
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

    async def create_schema(self) -> None:
        """Create the ``ly_change_*`` tables on the configured pool if absent (idempotent)."""
        engine = self._pools.engine(self._pool_name)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_schema(self) -> None:  # pragma: no cover - destructive helper
        engine = self._pools.engine(self._pool_name)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
