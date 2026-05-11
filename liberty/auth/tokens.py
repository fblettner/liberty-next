"""JWT issuance and validation for internal logins (HS256 by default).

Two token kinds, distinguished by the ``typ`` claim:

* ``access``  — short-lived; carries identity *and* authorisation snapshot
  (``roles``, ``perms``, ``sup``) so request handlers need no DB hit;
* ``refresh`` — long-lived; carries only ``sub``; exchanged for a fresh access
  token (which re-reads the user, so role changes take effect).

OIDC logins mint our own ``access``/``refresh`` pair too — the IdP's tokens are
not propagated downstream.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

ACCESS = "access"
REFRESH = "refresh"


class TokenError(Exception):
    """Raised when a token is malformed, expired, wrong-typed, or wrong-issuer."""


@dataclass(slots=True, frozen=True)
class TokenConfig:
    secret: str
    algorithm: str = "HS256"
    issuer: str = "liberty-v2"
    access_ttl: int = 3600
    refresh_ttl: int = 1_209_600

    @classmethod
    def ephemeral(cls, **overrides) -> TokenConfig:
        """A config with a random secret — fine for tests / single-process dev,
        but tokens won't survive a restart."""
        return cls(secret=secrets.token_urlsafe(48), **overrides)


@dataclass(slots=True)
class IssuedToken:
    token: str
    token_type: str  # "access" | "refresh"
    expires_in: int  # seconds until expiry
    expires_at: int  # unix epoch seconds


class TokenService:
    """Mints and verifies the two token kinds against one :class:`TokenConfig`."""

    def __init__(self, config: TokenConfig) -> None:
        self.config = config

    # -- issuance ---------------------------------------------------------- #

    def _encode(self, claims: dict, ttl: int, typ: str) -> IssuedToken:
        now = int(time.time())
        exp = now + ttl
        payload = {
            **claims,
            "iss": self.config.issuer,
            "iat": now,
            "nbf": now,
            "exp": exp,
            "typ": typ,
            "jti": uuid.uuid4().hex,
        }
        token = jwt.encode(payload, self.config.secret, algorithm=self.config.algorithm)
        return IssuedToken(token=token, token_type=typ, expires_in=ttl, expires_at=exp)

    def access_token(
        self,
        *,
        subject: str,
        username: str,
        email: str | None = None,
        roles: list[str] | None = None,
        permissions: list[str] | None = None,
        is_superuser: bool = False,
        provider: str = "local",
    ) -> IssuedToken:
        return self._encode(
            {
                "sub": subject,
                "username": username,
                "email": email,
                "roles": roles or [],
                "perms": permissions or [],
                "sup": bool(is_superuser),
                "provider": provider,
            },
            self.config.access_ttl,
            ACCESS,
        )

    def refresh_token(self, *, subject: str) -> IssuedToken:
        return self._encode({"sub": subject}, self.config.refresh_ttl, REFRESH)

    # -- verification ------------------------------------------------------ #

    def decode(self, token: str, *, expected_type: str | None = None) -> dict:
        """Decode + verify *token*; raise :class:`TokenError` on any problem."""
        try:
            claims = jwt.decode(
                token,
                self.config.secret,
                algorithms=[self.config.algorithm],
                issuer=self.config.issuer,
            )
        except ExpiredSignatureError as exc:
            raise TokenError("token expired") from exc
        except JWTError as exc:
            raise TokenError(f"invalid token: {exc}") from exc
        # python-jose validates claims that are present but doesn't enforce their
        # presence — make the ones we always mint mandatory.
        for required in ("sub", "exp", "iat", "typ"):
            if required not in claims:
                raise TokenError(f"token missing required claim {required!r}")
        if expected_type is not None and claims.get("typ") != expected_type:
            raise TokenError(f"expected a {expected_type} token, got {claims.get('typ')!r}")
        return claims
