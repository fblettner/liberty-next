"""``/auth`` routes — internal login, token refresh, current user, OIDC flow.

Both login paths (internal credentials and OIDC) end the same way: the server
mints its *own* access + refresh JWTs (the IdP's tokens are never propagated).
``GET /auth/me`` reads the access token's claims — no database hit.
"""

from __future__ import annotations

from typing import Annotated

from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from liberty.auth.authstore import AuthBackend, UserRecord
from liberty.auth.dependencies import (
    CurrentPrincipal,
    get_auth_backend,
    get_oidc,
    get_token_service,
)
from liberty.auth.oidc import OIDCClient
from liberty.auth.tokens import REFRESH, TokenError, TokenService

router = APIRouter(prefix="/auth", tags=["auth"])


# -- schemas ---------------------------------------------------------------- #


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access-token lifetime, seconds


# -- helpers ---------------------------------------------------------------- #


def _issue_pair(tokens: TokenService, user: UserRecord) -> TokenPair:
    access = tokens.access_token(
        subject=user.id,
        username=user.username,
        email=user.email,
        roles=user.role_names,
        permissions=user.permissions,
        is_superuser=user.is_superuser,
        provider=user.provider,
    )
    refresh = tokens.refresh_token(subject=user.id)
    return TokenPair(
        access_token=access.token,
        refresh_token=refresh.token,
        token_type="bearer",
        expires_in=access.expires_in,
    )


_BAD_CREDENTIALS = HTTPException(
    status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect username or password",
    headers={"WWW-Authenticate": "Bearer"},
)


# -- internal login --------------------------------------------------------- #


@router.post("/login", response_model=TokenPair)
async def login(
    body: LoginRequest,
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> TokenPair:
    user = await backend.authenticate(body.username, body.password)
    if user is None:
        raise _BAD_CREDENTIALS
    return _issue_pair(tokens, user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    body: RefreshRequest,
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> TokenPair:
    try:
        claims = tokens.decode(body.refresh_token, expected_type=REFRESH)
    except TokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid refresh token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    subject = claims.get("sub")
    if not subject:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    user = await backend.get_by_id(str(subject))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="User is inactive or unknown")
    return _issue_pair(tokens, user)


@router.get("/me")
async def me(principal: CurrentPrincipal) -> dict:
    return principal.to_dict()


# -- OIDC ------------------------------------------------------------------- #


@router.get("/oidc/login")
async def oidc_login(request: Request, oidc: Annotated[OIDCClient, Depends(get_oidc)]):
    return await oidc.authorize_redirect(request, oidc.redirect_uri(request))


@router.get("/oidc/callback", name="oidc_callback", response_model=TokenPair)
async def oidc_callback(
    request: Request,
    oidc: Annotated[OIDCClient, Depends(get_oidc)],
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
):
    try:
        token = await oidc.authorize_access_token(request)
    except OAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"OIDC error: {exc.error}") from exc
    userinfo = dict(token.get("userinfo") or {})
    if not userinfo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="OIDC response carried no ID-token claims")
    s = oidc.settings
    user = await backend.provision_oidc_user(
        userinfo,
        username_claim=s.username_claim,
        email_claim=s.email_claim,
        name_claim=s.name_claim,
    )
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    pair = _issue_pair(tokens, user)
    if s.frontend_redirect:
        # SPA flow: hand the tokens back via the URL fragment (never sent to the server).
        fragment = urlencode({
            "access_token": pair.access_token,
            "refresh_token": pair.refresh_token,
            "token_type": pair.token_type,
            "expires_in": pair.expires_in,
        })
        return RedirectResponse(f"{s.frontend_redirect}#{fragment}", status_code=status.HTTP_303_SEE_OTHER)
    return pair
