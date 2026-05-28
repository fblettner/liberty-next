"""AuthService — user/role operations over an :class:`AsyncSession`.

Holds one session for its lifetime; the caller controls the transaction
boundary (the FastAPI dependency / the CLI both use
:meth:`AuthDatabase.session`, which commits on clean exit). Roles are eager-
loaded (``lazy="selectin"`` on the model), so the returned :class:`User`
objects stay usable after the session closes.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from liberty.auth.models import Role, User
from liberty.auth.password import hash_password, needs_rehash, verify_password


class AuthError(Exception):
    """Raised for auth-operation problems (duplicate username, unknown user, …)."""


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- lookups ----------------------------------------------------------- #

    async def get_user(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_user_by_username(self, username: str) -> User | None:
        result = await self.session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_user_by_subject(self, provider: str, subject: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.provider == provider, User.provider_subject == subject)
        )
        return result.scalar_one_or_none()

    async def list_users(self) -> list[User]:
        result = await self.session.execute(select(User).order_by(User.username))
        return list(result.scalars().all())

    async def count_users(self) -> int:
        return int((await self.session.execute(select(func.count()).select_from(User))).scalar_one())

    async def get_role(self, name: str) -> Role | None:
        result = await self.session.execute(select(Role).where(Role.name == name))
        return result.scalar_one_or_none()

    # -- authentication ---------------------------------------------------- #

    async def authenticate(self, username: str, password: str) -> User | None:
        """Return the user iff active, local-or-password-bearing, and the password matches."""
        user = await self.get_user_by_username(username)
        if user is None or not user.is_active or not user.password_hash:
            return None
        if not verify_password(user.password_hash, password):
            return None
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            await self.session.flush()
        return user

    # -- mutations --------------------------------------------------------- #

    async def create_user(
        self,
        username: str,
        *,
        password: str | None = None,
        email: str | None = None,
        full_name: str | None = None,
        is_superuser: bool = False,
        is_active: bool = True,
        roles: list[str] | None = None,
        provider: str = "local",
        provider_subject: str | None = None,
    ) -> User:
        if await self.get_user_by_username(username) is not None:
            raise AuthError(f"user {username!r} already exists")
        user = User(
            username=username,
            email=email,
            full_name=full_name,
            password_hash=hash_password(password) if password else None,
            is_superuser=is_superuser,
            is_active=is_active,
            provider=provider,
            provider_subject=provider_subject,
        )
        # Assign the collection explicitly (even if empty) so it counts as loaded —
        # a freshly-flushed object's relationship would otherwise lazy-load on
        # access, which is forbidden under async.
        user.roles = await self._resolve_roles(roles) if roles else []
        self.session.add(user)
        await self.session.flush()
        return user

    async def set_password(self, user: User, password: str) -> None:
        user.password_hash = hash_password(password)
        await self.session.flush()

    async def set_active(self, user: User, active: bool) -> None:
        user.is_active = active
        await self.session.flush()

    async def update_profile(self, user: User, *, full_name: str | None, email: str | None) -> None:
        """Self-service profile edit — display name + email. ``None`` clears the field (an empty
        string from the form is normalised to ``None`` by the caller)."""
        user.full_name = full_name
        user.email = email
        await self.session.flush()

    async def assign_roles(self, user: User, role_names: list[str], *, replace: bool = False) -> None:
        roles = await self._resolve_roles(role_names)
        if replace:
            user.roles = roles
        else:
            existing = {r.id for r in user.roles}
            user.roles = user.roles + [r for r in roles if r.id not in existing]
        await self.session.flush()

    async def get_or_create_role(
        self, name: str, *, permissions: list[str] | None = None, description: str | None = None
    ) -> Role:
        role = await self.get_role(name)
        if role is None:
            role = Role(name=name, permissions=list(permissions or []), description=description)
            self.session.add(role)
            await self.session.flush()
        elif permissions is not None or description is not None:
            if permissions is not None:
                role.permissions = list(permissions)
            if description is not None:
                role.description = description
            await self.session.flush()
        return role

    # -- OIDC provisioning ------------------------------------------------- #

    async def provision_oidc_user(
        self,
        claims: dict,
        *,
        username_claim: str = "preferred_username",
        email_claim: str = "email",
        name_claim: str = "name",
        default_roles: list[str] | None = None,
    ) -> User:
        """Find the local account for an OIDC ``sub`` (creating it on first login),
        refresh its profile fields, and return it."""
        subject = claims.get("sub")
        if not subject:
            raise AuthError("OIDC claims missing 'sub'")
        subject = str(subject)
        email = claims.get(email_claim)
        full_name = claims.get(name_claim)
        username = claims.get(username_claim) or email or f"oidc:{subject}"

        user = await self.get_user_by_subject("oidc", subject)
        if user is None:
            # Avoid colliding with an existing local username.
            base, candidate, n = username, username, 1
            while await self.get_user_by_username(candidate) is not None:
                n += 1
                candidate = f"{base}-{n}"
            user = User(
                username=candidate,
                email=email,
                full_name=full_name,
                password_hash=None,
                provider="oidc",
                provider_subject=subject,
            )
            user.roles = await self._resolve_roles(default_roles) if default_roles else []
            self.session.add(user)
        else:
            if email is not None:
                user.email = email
            if full_name is not None:
                user.full_name = full_name
        await self.session.flush()
        return user

    # -- internals --------------------------------------------------------- #

    async def _resolve_roles(self, names: list[str]) -> list[Role]:
        roles: list[Role] = []
        for name in names:
            role = await self.get_role(name)
            if role is None:
                raise AuthError(f"unknown role {name!r}")
            roles.append(role)
        return roles
