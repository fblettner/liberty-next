"""SQLAlchemy ORM models for internal users / roles.

These are *application data*, not v1-style ``ly_*`` metadata tables — the
"no metadata tables" rule is about form/column schema, which is still discovered
at query time. The auth tables live on a configured pool (``[auth] pool``,
default ``default``) and are created with ``liberty-admin init-db``.

Permissions are kept simple: a :class:`Role` carries a JSON list of permission
strings such as ``"sql:liberty:read"`` or the wildcard ``"*"``; a superuser
bypasses the check entirely. A richer ``Permission`` table can replace the JSON
column later without touching call sites — they go through :class:`Principal`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for the auth tables only — ``Base.metadata`` scopes
    ``create_all`` to users/roles and nothing else."""


user_roles = Table(
    "ly2_user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("ly2_users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("ly2_roles.id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "ly2_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    # List[str] of permission strings; "*" is the wildcard.
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    users: Mapped[list[User]] = relationship(secondary=user_roles, back_populates="roles")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Role(name={self.name!r}, permissions={self.permissions!r})"


class User(Base):
    __tablename__ = "ly2_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    # Argon2 hash, or NULL for OIDC-only accounts (no local password).
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    # Identity provider that owns this account: "local" or "oidc".
    provider: Mapped[str] = mapped_column(String(32), default="local")
    # The provider's subject identifier (the OIDC ``sub`` claim); NULL for local.
    provider_subject: Mapped[str | None] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    roles: Mapped[list[Role]] = relationship(
        secondary=user_roles, back_populates="users", lazy="selectin"
    )

    # -- derived ----------------------------------------------------------- #

    @property
    def role_names(self) -> list[str]:
        return sorted(r.name for r in self.roles)

    @property
    def permissions(self) -> list[str]:
        perms: set[str] = set()
        for role in self.roles:
            perms.update(role.permissions or [])
        return sorted(perms)

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "is_active": self.is_active,
            "is_superuser": self.is_superuser,
            "provider": self.provider,
            "roles": self.role_names,
            "permissions": self.permissions,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"User(id={self.id}, username={self.username!r}, provider={self.provider!r})"
