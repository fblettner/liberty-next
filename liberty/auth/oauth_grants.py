"""The clients a user connected through the OAuth sign-in — the one thing of that sign-in that
is stored (``ly2_oauth_grant``), so that a user can see them and end one.

The sign-in itself stays without storage (:mod:`liberty.auth.oauth`: the client's registration,
the code and the tokens are signed). But a signed token cannot be taken back: ending the access
of one client meant changing one's password, which ends them all. A grant is the consent of one
user to one client; its id rides in the refresh token and in the access tokens made from it, and
both are refused once the grant is revoked — the refresh at its next use, an access token at
the next call (``/mcp`` re-reads the account at every call anyway).

Where the store is not available (no usable pool), the sign-in works as before, without the
list: tokens then carry no grant id.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import BigInteger, String, delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from liberty.connectors.db import PoolRegistry
from liberty.schema_sync import SchemaChange, sync_tables

KEEP_ENDED_SECONDS = 30 * 86400      # an ended grant (expired or revoked) is dropped after this


class Base(DeclarativeBase):
    pass


class OAuthGrant(Base):
    __tablename__ = "ly2_oauth_grant"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    username: Mapped[str] = mapped_column(String(255))
    client_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[int] = mapped_column(BigInteger)                 # epoch seconds, as the tokens count time
    last_used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[int] = mapped_column(BigInteger)                 # the day the consent ends
    revoked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username, "client_name": self.client_name, "created_at": self.created_at,
                "last_used_at": self.last_used_at, "expires_at": self.expires_at}


class OAuthGrantStore:
    """The ``ly2_oauth_grant`` table on the auth pool."""

    def __init__(self, pools: PoolRegistry, pool_name: str = "default") -> None:
        self._pools = pools
        self._pool_name = pool_name
        self._maker: async_sessionmaker[AsyncSession] | None = None

    def rebind(self, pools: PoolRegistry) -> None:
        """Point at a rebuilt :class:`PoolRegistry` (config reload) — see :mod:`liberty.runtime`."""
        self._pools = pools
        self._maker = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._maker is None:
            self._maker = async_sessionmaker(self._pools.engine(self._pool_name), expire_on_commit=False)
        async with self._maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def create_schema(self) -> list[SchemaChange]:
        async with self._pools.engine(self._pool_name).begin() as conn:
            return await conn.run_sync(sync_tables, Base.metadata)

    async def create(self, *, user_id: str, username: str, client_name: str, expires_at: int) -> str:
        """A new grant; the ended ones older than :data:`KEEP_ENDED_SECONDS` are dropped on the way."""
        now = int(time.time())
        grant = OAuthGrant(id=uuid.uuid4().hex, user_id=str(user_id), username=username, client_name=client_name[:120],
                           created_at=now, last_used_at=now, expires_at=int(expires_at))
        async with self.session() as s:
            old = now - KEEP_ENDED_SECONDS
            await s.execute(sa_delete(OAuthGrant).where((OAuthGrant.expires_at < old) | (OAuthGrant.revoked_at < old)))
            s.add(grant)
        return grant.id

    async def active(self, grant_id: str, *, touch: bool = False) -> bool:
        """Whether the grant still stands (known, not revoked, not past its day). *touch* notes
        that it was just used — on a refresh, not on every call."""
        now = int(time.time())
        async with self.session() as s:
            grant = (await s.execute(select(OAuthGrant).where(OAuthGrant.id == str(grant_id)))).scalar_one_or_none()
            if grant is None or grant.revoked_at is not None or grant.expires_at <= now:
                return False
            if touch:
                grant.last_used_at = now
            return True

    async def list_for(self, user_id: str | None = None) -> list[dict[str, Any]]:
        """The standing grants of *user_id* (of everyone when None), the latest used first."""
        now = int(time.time())
        stmt = select(OAuthGrant).where(OAuthGrant.revoked_at.is_(None), OAuthGrant.expires_at > now)
        if user_id is not None:
            stmt = stmt.where(OAuthGrant.user_id == str(user_id))
        async with self.session() as s:
            rows = (await s.execute(stmt.order_by(OAuthGrant.last_used_at.desc()))).scalars().all()
        return [r.to_dict() for r in rows]

    async def revoke(self, grant_id: str, *, user_id: str | None) -> bool:
        """End the grant — its owner's (*user_id*), or anyone's when None (an administrator).
        False when there is no such standing grant of that user."""
        async with self.session() as s:
            grant = (await s.execute(select(OAuthGrant).where(OAuthGrant.id == str(grant_id)))).scalar_one_or_none()
            if grant is None or grant.revoked_at is not None or (user_id is not None and grant.user_id != str(user_id)):
                return False
            grant.revoked_at = int(time.time())
            return True
