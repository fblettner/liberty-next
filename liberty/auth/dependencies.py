"""FastAPI dependencies for authentication and authorisation.

* :func:`get_current_principal` — require a valid ``access`` JWT, return a
  :class:`Principal` built from its claims (no DB hit);
* :func:`optional_principal` — same, but ``None`` instead of 401 when absent;
* :func:`require_permission` / :func:`require_role` — dependency factories that
  401 if unauthenticated, 403 if the principal lacks the permission/role;
* :func:`get_auth_service` — yields an :class:`AuthService` on a fresh session;
* :func:`get_oidc` — the configured :class:`OIDCClient`, or 404 if OIDC is off.

Wiring is read from ``request.app.state`` (set in the app lifespan), so these
work with whatever ``Settings`` the app was started with.
"""

from __future__ import annotations

from typing import Annotated, AsyncIterator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from liberty.auth.db import AuthDatabase
from liberty.auth.oidc import OIDCClient
from liberty.auth.principal import Principal
from liberty.auth.service import AuthService
from liberty.auth.tokens import ACCESS, TokenError, TokenService

_bearer = HTTPBearer(auto_error=False, description="Liberty access token")
_BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


# -- app.state accessors ---------------------------------------------------- #


def get_token_service(request: Request) -> TokenService:
    return request.app.state.token_service


def get_auth_db(request: Request) -> AuthDatabase:
    return request.app.state.auth_db


async def get_auth_service(request: Request) -> AsyncIterator[AuthService]:
    async with get_auth_db(request).session() as session:
        yield AuthService(session)


def get_oidc(request: Request) -> OIDCClient:
    client: OIDCClient | None = getattr(request.app.state, "oidc", None)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="OIDC is not configured")
    return client


# -- principal -------------------------------------------------------------- #

_UNAUTHENTICATED = HTTPException(
    status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def _principal_from_credentials(
    creds: HTTPAuthorizationCredentials | None, tokens: TokenService
) -> Principal | None:
    if creds is None or (creds.scheme or "").lower() != "bearer":
        return None
    try:
        claims = tokens.decode(creds.credentials, expected_type=ACCESS)
    except TokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return Principal.from_claims(claims)


def get_current_principal(
    creds: _BearerDep, tokens: Annotated[TokenService, Depends(get_token_service)]
) -> Principal:
    principal = _principal_from_credentials(creds, tokens)
    if principal is None:
        raise _UNAUTHENTICATED
    return principal


def optional_principal(
    creds: _BearerDep, tokens: Annotated[TokenService, Depends(get_token_service)]
) -> Principal | None:
    return _principal_from_credentials(creds, tokens)


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]


# -- authorisation guards --------------------------------------------------- #


def require_permission(permission: str):
    """Dependency: 401 if unauthenticated, 403 if the principal lacks *permission*."""

    def _guard(principal: CurrentPrincipal) -> Principal:
        if not principal.has_permission(permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {permission}"
            )
        return principal

    return _guard


def require_role(role: str):
    """Dependency: 401 if unauthenticated, 403 if the principal lacks *role*."""

    def _guard(principal: CurrentPrincipal) -> Principal:
        if not principal.has_role(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Missing role: {role}")
        return principal

    return _guard


def require_superuser(principal: CurrentPrincipal) -> Principal:
    if not principal.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Superuser required")
    return principal
