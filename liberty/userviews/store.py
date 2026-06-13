"""Per-user saved views — durable per-(user, kind, key) store on the auth pool.

Replaces browser localStorage for things a user wants to persist across devices:
a grid's saved format and (later) a chart spec. Generic by design:

* ``kind``     — ``"grid"`` (a ResultTable's format) or ``"chart"`` (a ChartView
  spec); extensible without a schema change.
* ``view_key`` — the **app-scoped** identity the frontend builds, e.g.
  ``"<app>::<screen>::<grid>"`` for a grid or ``"<connector>/<query>"`` for a
  chart, so the same table name in two apps never collides.
* ``payload``  — opaque JSON owned by the frontend (the grid format / chart spec).

Self-bootstrapping like :class:`~liberty.changesets.db.ChangeSetDatabase` /
``JobDatabase``: :meth:`create_schema` is called (idempotent) from the app
lifespan, so the table appears on existing installs at upgrade without
re-running ``liberty-admin init-db``. Keyed by ``username`` (a string) rather
than a FK to ``ly2_users`` so OIDC principals without a local row work too.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from sqlalchemy import DateTime, String, delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

from liberty.connectors.db import PoolRegistry


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Own metadata so ``create_all`` touches only the user-view table."""


class UserView(Base):
    __tablename__ = "ly2_user_view"

    username: Mapped[str] = mapped_column(String(255), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    view_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow,
    )


class UserViewStore:
    """CRUD over :class:`UserView` on the configured pool (the auth pool)."""

    def __init__(self, pools: PoolRegistry, pool_name: str = "default") -> None:
        self._pools = pools
        self._pool_name = pool_name
        self._maker: async_sessionmaker[AsyncSession] | None = None

    @property
    def pool_name(self) -> str:
        return self._pool_name

    def _sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._maker is None:
            self._maker = async_sessionmaker(
                self._pools.engine(self._pool_name), expire_on_commit=False,
            )
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

    async def create_schema(self) -> None:
        """Create ``ly2_user_view`` on the pool if absent (idempotent)."""
        engine = self._pools.engine(self._pool_name)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def list_views(self, username: str, kind: str, view_key: str) -> list[dict[str, Any]]:
        """The user's named views for one (kind, view_key) — e.g. all the grid
        views they saved for a given screen, ``[{name, payload, updated_at}]``."""
        stmt = (
            select(UserView)
            .where(UserView.username == username, UserView.kind == kind, UserView.view_key == view_key)
            .order_by(UserView.name)
        )
        async with self.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [
            {"name": r.name, "payload": dict(r.payload),
             "updated_at": r.updated_at.isoformat() if r.updated_at else None}
            for r in rows
        ]

    async def save_view(self, username: str, kind: str, view_key: str, name: str, payload: dict) -> None:
        """Upsert one named view for this (user, kind, key)."""
        async with self.session() as s:
            row = await s.get(UserView, (username, kind, view_key, name))
            if row is None:
                s.add(UserView(username=username, kind=kind, view_key=view_key, name=name, payload=payload))
            else:
                row.payload = payload
                row.updated_at = _utcnow()

    async def delete_view(self, username: str, kind: str, view_key: str, name: str) -> bool:
        """Delete one named view. True if a row was removed."""
        async with self.session() as s:
            res = await s.execute(
                sa_delete(UserView).where(
                    UserView.username == username,
                    UserView.kind == kind,
                    UserView.view_key == view_key,
                    UserView.name == name,
                )
            )
            return (res.rowcount or 0) > 0
