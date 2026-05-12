"""Auth store — users / roles, backed by either a TOML file or the database.

Two interchangeable backends behind one async interface (:class:`AuthBackend`), both returning
:class:`UserRecord` values so the ``/auth`` routes and ``liberty-admin`` don't care which is in use:

* **toml** (the default) — ``config/auth.toml`` holds the roles and users. No database is needed to
  start the framework; for "lots of users" point customers at OIDC/LDAP instead. Hot-reloadable like
  the other config files; ``liberty-admin`` edits it; mutations write atomically and ``chmod 600``.
* **db** — the ``ly2_users`` / ``ly2_roles`` / ``ly2_user_roles`` tables on the ``[auth] pool``
  (created by ``liberty-admin init-db``). Wraps the existing ORM/:class:`AuthService`.

Picked via ``[auth] backend = "toml" | "db"``. Login passwords are Argon2id either way (one-way —
a stolen file/DB can't yield plaintext); the toml file's `auth.toml` is a deployment secret (mode 600).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import tomli_w
from pydantic import BaseModel, ConfigDict, Field

from liberty.auth.db import AuthDatabase
from liberty.auth.models import User
from liberty.auth.password import hash_password, needs_rehash, verify_password
from liberty.auth.service import AuthError, AuthService

ADMIN_ROLE = "admin"


# --------------------------------------------------------------------------- #
# config/auth.toml schema
# --------------------------------------------------------------------------- #


class AuthRole(BaseModel):
    """A role: a list of permission strings (``"sql:liberty:read"``, ``"*"`` = everything)."""

    model_config = ConfigDict(extra="forbid")

    permissions: list[str] = Field(default_factory=list)
    description: str | None = None


class AuthUser(BaseModel):
    """A user. ``password_hash`` is an Argon2 hash (``None`` for OIDC-only accounts — they have no
    local password). ``superuser`` bypasses permission checks (a role with ``"*"`` does the same)."""

    model_config = ConfigDict(extra="forbid")

    password_hash: str | None = None
    roles: list[str] = Field(default_factory=list)
    active: bool = True
    superuser: bool = False
    provider: str = "local"          # "local" | "oidc"
    sub: str | None = None           # the OIDC subject, for provider == "oidc"
    email: str | None = None
    full_name: str | None = None


class AuthFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: dict[str, AuthRole] = Field(default_factory=dict)
    users: dict[str, AuthUser] = Field(default_factory=dict)


def load_auth(path: Path | str) -> AuthFile:
    """Load ``auth.toml``; a missing file yields an empty store."""
    path = Path(path)
    if not path.exists():
        return AuthFile()
    with path.open("rb") as fh:
        return AuthFile.model_validate(tomllib.load(fh))


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value


def save_auth(path: Path | str, file: AuthFile) -> None:
    """Write *file* to *path* atomically (temp file + replace) and ``chmod 600`` it (TOML has no
    null, so ``None`` fields are dropped)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _strip_none(file.model_dump())
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        tomli_w.dump(data, fh)
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover — best effort (e.g. Windows)
        pass


# --------------------------------------------------------------------------- #
# the common record + backend interface
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class UserRecord:
    """Backend-agnostic user view. ``id`` is the JWT subject — ``str(db id)`` for the DB backend,
    the username for the TOML backend."""

    id: str
    username: str
    email: str | None
    full_name: str | None
    password_hash: str | None
    is_active: bool
    is_superuser: bool
    provider: str
    provider_subject: str | None
    roles: list[str]
    permissions: list[str]

    @property
    def role_names(self) -> list[str]:
        return self.roles

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "username": self.username, "email": self.email, "full_name": self.full_name,
            "is_active": self.is_active, "is_superuser": self.is_superuser, "provider": self.provider,
            "roles": self.roles, "permissions": self.permissions,
        }


@runtime_checkable
class AuthBackend(Protocol):
    async def authenticate(self, username: str, password: str) -> UserRecord | None: ...
    async def get_by_id(self, subject_id: str) -> UserRecord | None: ...
    async def provision_oidc_user(
        self, claims: dict, *, username_claim: str = "preferred_username",
        email_claim: str = "email", name_claim: str = "name", default_roles: list[str] | None = None,
    ) -> UserRecord: ...
    async def list_users(self) -> list[UserRecord]: ...
    async def list_roles(self) -> list[tuple[str, list[str], str | None]]: ...  # (name, permissions, description)
    async def count_users(self) -> int: ...
    async def create_user(
        self, username: str, *, password: str | None = None, email: str | None = None,
        full_name: str | None = None, is_superuser: bool = False, is_active: bool = True,
        roles: list[str] | None = None, provider: str = "local", provider_subject: str | None = None,
    ) -> UserRecord: ...
    async def set_password(self, username: str, password: str) -> UserRecord: ...
    async def set_active(self, username: str, active: bool) -> UserRecord: ...
    async def assign_roles(self, username: str, roles: list[str], *, replace: bool = False) -> UserRecord: ...
    async def get_or_create_role(
        self, name: str, *, permissions: list[str] | None = None, description: str | None = None,
    ) -> tuple[str, list[str], str | None]: ...
    async def ready(self) -> None: ...  # backend-specific "make sure it can be used" (init-db / touch the file)


# --------------------------------------------------------------------------- #
# TOML backend
# --------------------------------------------------------------------------- #


class TomlAuthBackend:
    """Users / roles in ``config/auth.toml`` — no database. The file is reloaded on every call (it's
    small) so a hand edit / hot-reload takes effect immediately."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    # -- helpers ----------------------------------------------------------- #

    def _resolve(self, f: AuthFile, u: AuthUser) -> tuple[list[str], bool]:
        perms: set[str] = set(p for rn in u.roles for p in (f.roles.get(rn).permissions if f.roles.get(rn) else []))
        return sorted(perms), bool(u.superuser)

    def _record(self, f: AuthFile, username: str, u: AuthUser) -> UserRecord:
        perms, sup = self._resolve(f, u)
        return UserRecord(
            id=username, username=username, email=u.email, full_name=u.full_name,
            password_hash=u.password_hash, is_active=u.active, is_superuser=sup,
            provider=u.provider, provider_subject=u.sub, roles=sorted(u.roles), permissions=perms,
        )

    def _get(self, f: AuthFile, username: str) -> AuthUser:
        u = f.users.get(username)
        if u is None:
            raise AuthError(f"unknown user {username!r}")
        return u

    # -- AuthBackend ------------------------------------------------------- #

    async def ready(self) -> None:
        """Create an empty auth.toml if it doesn't exist (so liberty-admin can write to it)."""
        if not self.path.exists():
            save_auth(self.path, AuthFile())

    async def authenticate(self, username: str, password: str) -> UserRecord | None:
        f = load_auth(self.path)
        u = f.users.get(username)
        if u is None or not u.active or not u.password_hash or not verify_password(u.password_hash, password):
            return None
        if needs_rehash(u.password_hash):
            u.password_hash = hash_password(password)
            save_auth(self.path, f)
        return self._record(f, username, u)

    async def get_by_id(self, subject_id: str) -> UserRecord | None:
        f = load_auth(self.path)
        u = f.users.get(subject_id)
        return self._record(f, subject_id, u) if u else None

    async def provision_oidc_user(
        self, claims: dict, *, username_claim: str = "preferred_username",
        email_claim: str = "email", name_claim: str = "name", default_roles: list[str] | None = None,
    ) -> UserRecord:
        sub = claims.get("sub")
        if not sub:
            raise AuthError("OIDC claims missing 'sub'")
        sub, email, full_name = str(sub), claims.get(email_claim), claims.get(name_claim)
        f = load_auth(self.path)
        for un, u in f.users.items():
            if u.provider == "oidc" and u.sub == sub:
                if email is not None:
                    u.email = email
                if full_name is not None:
                    u.full_name = full_name
                save_auth(self.path, f)
                return self._record(f, un, u)
        base = str(claims.get(username_claim) or email or f"oidc:{sub}")
        cand, n = base, 1
        while cand in f.users:
            n += 1
            cand = f"{base}-{n}"
        u = AuthUser(roles=list(default_roles or []), provider="oidc", sub=sub, email=email, full_name=full_name)
        f.users[cand] = u
        save_auth(self.path, f)
        return self._record(f, cand, u)

    async def list_users(self) -> list[UserRecord]:
        f = load_auth(self.path)
        return [self._record(f, un, u) for un, u in sorted(f.users.items())]

    async def list_roles(self) -> list[tuple[str, list[str], str | None]]:
        f = load_auth(self.path)
        return [(n, list(r.permissions), r.description) for n, r in sorted(f.roles.items())]

    async def count_users(self) -> int:
        return len(load_auth(self.path).users)

    async def create_user(
        self, username: str, *, password: str | None = None, email: str | None = None,
        full_name: str | None = None, is_superuser: bool = False, is_active: bool = True,
        roles: list[str] | None = None, provider: str = "local", provider_subject: str | None = None,
    ) -> UserRecord:
        f = load_auth(self.path)
        if username in f.users:
            raise AuthError(f"user {username!r} already exists")
        for rn in roles or []:
            if rn not in f.roles:
                raise AuthError(f"unknown role {rn!r}")
        u = AuthUser(
            password_hash=hash_password(password) if password else None, roles=list(roles or []),
            active=is_active, superuser=is_superuser, provider=provider, sub=provider_subject,
            email=email, full_name=full_name,
        )
        f.users[username] = u
        save_auth(self.path, f)
        return self._record(f, username, u)

    async def set_password(self, username: str, password: str) -> UserRecord:
        f = load_auth(self.path)
        u = self._get(f, username)
        u.password_hash = hash_password(password)
        save_auth(self.path, f)
        return self._record(f, username, u)

    async def set_active(self, username: str, active: bool) -> UserRecord:
        f = load_auth(self.path)
        u = self._get(f, username)
        u.active = active
        save_auth(self.path, f)
        return self._record(f, username, u)

    async def assign_roles(self, username: str, roles: list[str], *, replace: bool = False) -> UserRecord:
        f = load_auth(self.path)
        u = self._get(f, username)
        for rn in roles:
            if rn not in f.roles:
                raise AuthError(f"unknown role {rn!r}")
        u.roles = sorted(set(roles)) if replace else sorted(set(u.roles) | set(roles))
        save_auth(self.path, f)
        return self._record(f, username, u)

    async def get_or_create_role(
        self, name: str, *, permissions: list[str] | None = None, description: str | None = None,
    ) -> tuple[str, list[str], str | None]:
        f = load_auth(self.path)
        r = f.roles.get(name)
        if r is None:
            r = AuthRole(permissions=list(permissions or []), description=description)
            f.roles[name] = r
        else:
            if permissions is not None:
                r.permissions = list(permissions)
            if description is not None:
                r.description = description
        save_auth(self.path, f)
        return (name, list(r.permissions), r.description)


# --------------------------------------------------------------------------- #
# DB backend (thin adapter over AuthDatabase + AuthService)
# --------------------------------------------------------------------------- #


class DbAuthBackend:
    """The ``ly2_*`` tables — wraps :class:`AuthService` (one session per call) and maps its ORM
    objects to :class:`UserRecord`s."""

    def __init__(self, db: AuthDatabase) -> None:
        self.db = db

    @staticmethod
    def _record(u: User) -> UserRecord:
        return UserRecord(
            id=str(u.id), username=u.username, email=u.email, full_name=u.full_name,
            password_hash=u.password_hash, is_active=u.is_active, is_superuser=u.is_superuser,
            provider=u.provider, provider_subject=u.provider_subject, roles=u.role_names, permissions=u.permissions,
        )

    async def ready(self) -> None:
        await self.db.create_schema()

    async def authenticate(self, username: str, password: str) -> UserRecord | None:
        async with self.db.session() as s:
            u = await AuthService(s).authenticate(username, password)
            return self._record(u) if u else None

    async def get_by_id(self, subject_id: str) -> UserRecord | None:
        try:
            uid = int(subject_id)
        except (TypeError, ValueError):
            return None
        async with self.db.session() as s:
            u = await AuthService(s).get_user(uid)
            return self._record(u) if u else None

    async def provision_oidc_user(
        self, claims: dict, *, username_claim: str = "preferred_username",
        email_claim: str = "email", name_claim: str = "name", default_roles: list[str] | None = None,
    ) -> UserRecord:
        async with self.db.session() as s:
            u = await AuthService(s).provision_oidc_user(
                claims, username_claim=username_claim, email_claim=email_claim, name_claim=name_claim,
                default_roles=default_roles,
            )
            return self._record(u)

    async def list_users(self) -> list[UserRecord]:
        async with self.db.session() as s:
            return [self._record(u) for u in await AuthService(s).list_users()]

    async def list_roles(self) -> list[tuple[str, list[str], str | None]]:
        from sqlalchemy import select
        from liberty.auth.models import Role
        async with self.db.session() as s:
            rows = (await s.execute(select(Role).order_by(Role.name))).scalars().all()
            return [(r.name, list(r.permissions or []), r.description) for r in rows]

    async def count_users(self) -> int:
        async with self.db.session() as s:
            return await AuthService(s).count_users()

    async def create_user(
        self, username: str, *, password: str | None = None, email: str | None = None,
        full_name: str | None = None, is_superuser: bool = False, is_active: bool = True,
        roles: list[str] | None = None, provider: str = "local", provider_subject: str | None = None,
    ) -> UserRecord:
        async with self.db.session() as s:
            u = await AuthService(s).create_user(
                username, password=password, email=email, full_name=full_name, is_superuser=is_superuser,
                is_active=is_active, roles=roles, provider=provider, provider_subject=provider_subject,
            )
            return self._record(u)

    async def set_password(self, username: str, password: str) -> UserRecord:
        async with self.db.session() as s:
            svc = AuthService(s)
            u = await svc.get_user_by_username(username)
            if u is None:
                raise AuthError(f"unknown user {username!r}")
            await svc.set_password(u, password)
            return self._record(u)

    async def set_active(self, username: str, active: bool) -> UserRecord:
        async with self.db.session() as s:
            svc = AuthService(s)
            u = await svc.get_user_by_username(username)
            if u is None:
                raise AuthError(f"unknown user {username!r}")
            await svc.set_active(u, active)
            return self._record(u)

    async def assign_roles(self, username: str, roles: list[str], *, replace: bool = False) -> UserRecord:
        async with self.db.session() as s:
            svc = AuthService(s)
            u = await svc.get_user_by_username(username)
            if u is None:
                raise AuthError(f"unknown user {username!r}")
            await svc.assign_roles(u, roles, replace=replace)
            return self._record(u)

    async def get_or_create_role(
        self, name: str, *, permissions: list[str] | None = None, description: str | None = None,
    ) -> tuple[str, list[str], str | None]:
        async with self.db.session() as s:
            r = await AuthService(s).get_or_create_role(name, permissions=permissions, description=description)
            return (r.name, list(r.permissions or []), r.description)


def build_auth_backend(settings, pools) -> AuthBackend:  # settings: liberty.config.Settings
    """Build the configured auth backend. ``[auth] backend == "db"`` → :class:`DbAuthBackend` over
    ``[auth] pool``; else (the default) :class:`TomlAuthBackend` over ``[auth] toml_path``."""
    if settings.auth.backend == "db":
        return DbAuthBackend(AuthDatabase(pools, settings.auth.pool))
    return TomlAuthBackend(settings.auth.toml_path)
