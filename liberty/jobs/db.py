"""Async session plumbing for the nomaflow tables — mirrors :mod:`liberty.auth.db`.

The nomaflow tables share the same :class:`~liberty.connectors.db.PoolRegistry`
the SQL connectors use; ``[jobs] pool`` (default ``"default"`` — same default as
auth, so single-DB deployments work out of the box) picks which one. The engine
and :class:`async_sessionmaker` are built lazily on first use.

``liberty-admin init-db`` calls :meth:`JobDatabase.create_schema` after the auth
bootstrap.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from liberty.connectors.db import PoolRegistry
from liberty.jobs.models import Base


class JobDatabase:
    """Owns the nomaflow-table session factory for one named pool."""

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
        """Create the ``nomaflow_*`` tables on the configured pool if absent (idempotent)."""
        engine = self._pools.engine(self._pool_name)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_schema(self) -> None:  # pragma: no cover - destructive helper
        engine = self._pools.engine(self._pool_name)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
