"""JWT issuance and validation for internal logins (HS256 by default).

Two token kinds, distinguished by the ``typ`` claim:

* ``access``  — short-lived; carries identity *and* authorisation snapshot
  (``roles``, ``perms``, ``sup``) so request handlers need no DB hit;
* ``refresh`` — long-lived; carries only ``sub``; exchanged for a fresh access
  token (which re-reads the user, so role changes take effect). **Rotated** on use:
  the pair returned by ``/auth/refresh`` carries a new refresh token and the one just
  presented is remembered as consumed (:meth:`TokenService.consume_refresh`) — presenting
  it again after a short grace is refused, so a leaked refresh token stops working as
  soon as its owner refreshes. The consumed set is per process (restart → unknown tokens
  are accepted once and tracked from there); the grace absorbs two browser tabs racing
  the same stored token.

Signing goes through PyJWT (HS256 by default; ``python-jose`` until 7.0.57 — same
claims, same secret, so tokens minted before the switch still verify).

OIDC logins mint our own ``access``/``refresh`` pair too — the IdP's tokens are
not propagated downstream.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass

import contextlib
import warnings

import jwt
from jwt.warnings import InsecureKeyLengthWarning

MIN_SECRET_BYTES = 32  # RFC 7518 §3.2 for HS256 — `openssl rand -base64 48` gives 64


@contextlib.contextmanager
def _quiet_short_key():
    """PyJWT ≥ 2.10 warns on EVERY encode / decode when the HMAC secret is shorter than 32
    bytes. One actionable warning at startup (``liberty.main._build_token_service``) beats
    one per request in the server log, so the per-call repeat is silenced around the two
    PyJWT calls — locally, so no global filter state can override or leak it."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureKeyLengthWarning)
        yield

ACCESS = "access"
REFRESH = "refresh"
# A password-reset link (``/auth/forgot-password`` → mail → ``/auth/reset-password``): carries the
# user and a fingerprint of the password hash it was issued for, so it works once — the reset
# changes the hash — and not after any other password change.
RESET = "reset"
RESET_TTL = 3600


class TokenError(Exception):
    """Raised when a token is malformed, expired, wrong-typed, or wrong-issuer."""


@dataclass(slots=True, frozen=True)
class TokenConfig:
    secret: str
    algorithm: str = "HS256"
    issuer: str = "liberty-next"
    access_ttl: int = 3600
    refresh_ttl: int = 1_209_600
    # Seconds a consumed refresh token may still be exchanged (two tabs racing the same
    # stored token). 0 = strict single use.
    refresh_reuse_grace: int = 60

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
        # jti → (consumed_at, exp) of refresh tokens already exchanged (rotation).
        self._consumed: dict[str, tuple[float, int]] = {}

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
        with _quiet_short_key():
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
        scope: list | None = None,
        attributes: dict | None = None,
        must_change_password: bool = False,
    ) -> IssuedToken:
        claims: dict = {
            "sub": subject,
            "username": username,
            "email": email,
            "roles": roles or [],
            "perms": permissions or [],
            "sup": bool(is_superuser),
            "provider": provider,
        }
        if scope:  # the resolved row-level data scope (liberty.auth.scope) — absent = unrestricted
            claims["scope"] = scope
        if attributes:
            claims["attrs"] = attributes
        if must_change_password:  # every call but the change itself is refused (auth dependency)
            claims["pwc"] = True
        return self._encode(
            claims,
            self.config.access_ttl,
            ACCESS,
        )

    def refresh_token(self, *, subject: str) -> IssuedToken:
        return self._encode({"sub": subject}, self.config.refresh_ttl, REFRESH)

    def reset_token(self, *, subject: str, password_fingerprint: str) -> IssuedToken:
        """A password-reset link's token (see :data:`RESET`)."""
        return self._encode({"sub": subject, "pwf": password_fingerprint}, RESET_TTL, RESET)

    # -- verification ------------------------------------------------------ #

    def decode(self, token: str, *, expected_type: str | None = None) -> dict:
        """Decode + verify *token*; raise :class:`TokenError` on any problem."""
        try:
            with _quiet_short_key():
                claims = jwt.decode(
                    token,
                    self.config.secret,
                    algorithms=[self.config.algorithm],
                    issuer=self.config.issuer,
                )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("token expired") from exc
        except jwt.InvalidTokenError as exc:  # bad signature / issuer / nbf / malformed
            raise TokenError(f"invalid token: {exc}") from exc
        # PyJWT verifies the claims that are present; make the ones we always mint mandatory.
        for required in ("sub", "exp", "iat", "typ"):
            if required not in claims:
                raise TokenError(f"token missing required claim {required!r}")
        if expected_type is not None and claims.get("typ") != expected_type:
            raise TokenError(f"expected a {expected_type} token, got {claims.get('typ')!r}")
        return claims

    # -- refresh rotation --------------------------------------------------- #

    def consume_refresh(self, claims: dict) -> None:
        """Mark the refresh token behind *claims* as exchanged. Raise :class:`TokenError`
        when it was already exchanged longer than ``refresh_reuse_grace`` seconds ago —
        the reuse signature of a stolen refresh token (or a very stale tab)."""
        jti = claims.get("jti")
        if not jti:
            return  # a token minted before jti existed — nothing to track
        now = time.monotonic()
        self._gc(now)
        seen = self._consumed.get(jti)
        if seen is not None:
            consumed_at, _exp = seen
            if now - consumed_at > self.config.refresh_reuse_grace:
                raise TokenError("refresh token already used")
            return  # inside the grace — a second tab racing the same token
        self._consumed[jti] = (now, int(claims.get("exp") or 0))

    def _gc(self, now: float) -> None:
        """Forget consumed tokens whose own ``exp`` has passed (they can't be replayed)."""
        if len(self._consumed) < 1000:
            return
        wall = time.time()
        for jti in [j for j, (_at, exp) in self._consumed.items() if exp and exp < wall]:
            self._consumed.pop(jti, None)
