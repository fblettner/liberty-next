"""The authenticated caller, as request handlers see it.

A :class:`Principal` is built from an access-token's claims — no per-request
database hit — so it reflects the user's roles/permissions *as of token issue*.
Short access-token TTLs keep that staleness bounded; the refresh flow re-reads
the user, so role changes propagate within a TTL.

Permission strings are colon-segmented (``"sql:liberty:read"``); a ``"*"`` in any
segment position is a wildcard, and ``"*"`` alone (or :attr:`is_superuser`) means
"everything".
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _segment_match(pattern: str, value: str) -> bool:
    """Colon-segment glob: ``*`` matches one segment; a trailing ``*`` matches
    the rest (``"sql:*"`` covers ``"sql:liberty:read"``)."""
    ps = pattern.split(":")
    vs = value.split(":")
    for i, p in enumerate(ps):
        if i >= len(vs):
            return False
        if p == "*":
            if i == len(ps) - 1:
                return True  # trailing * covers any remaining segments
            continue
        if p != vs[i]:
            return False
    return len(ps) == len(vs)


@dataclass(slots=True, frozen=True)
class Principal:
    id: str
    username: str
    email: str | None = None
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    is_superuser: bool = False
    provider: str = "local"

    # -- constructors ------------------------------------------------------ #

    @classmethod
    def from_claims(cls, claims: dict) -> Principal:
        return cls(
            id=str(claims.get("sub", "")),
            username=claims.get("username") or str(claims.get("sub", "")),
            email=claims.get("email"),
            roles=tuple(claims.get("roles") or ()),
            permissions=tuple(claims.get("perms") or ()),
            is_superuser=bool(claims.get("sup")),
            provider=claims.get("provider") or "local",
        )

    @classmethod
    def from_user(cls, user) -> Principal:  # liberty.auth.models.User
        return cls(
            id=str(user.id),
            username=user.username,
            email=user.email,
            roles=tuple(user.role_names),
            permissions=tuple(user.permissions),
            is_superuser=bool(user.is_superuser),
            provider=user.provider,
        )

    # -- authorisation ----------------------------------------------------- #

    def has_role(self, role: str) -> bool:
        return self.is_superuser or role in self.roles

    def has_permission(self, permission: str) -> bool:
        if self.is_superuser or "*" in self.permissions:
            return True
        return any(_segment_match(p, permission) for p in self.permissions)

    def require_permission(self, permission: str) -> None:
        if not self.has_permission(permission):
            raise PermissionError(permission)

    # -- serialisation ----------------------------------------------------- #

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "roles": list(self.roles),
            "permissions": list(self.permissions),
            "is_superuser": self.is_superuser,
            "provider": self.provider,
        }
