"""``/auth`` routes — internal login, token refresh, current user, OIDC flow.

Both login paths (internal credentials and OIDC) end the same way: the server
mints its *own* access + refresh JWTs (the IdP's tokens are never propagated).
``GET /auth/me`` reads the access token's claims — no database hit.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Annotated

from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from liberty.auth.authstore import AuthBackend, UserRecord
from liberty.auth.dependencies import (
    get_login_throttle,
    CurrentPrincipal,
    get_auth_backend,
    get_oidc,
    get_token_service,
)
from liberty.auth.oidc import OIDCClient
from liberty.auth.throttle import LoginThrottle, client_ip
from liberty.auth.tokens import REFRESH, RESET, RESET_TTL, TokenError, TokenService

router = APIRouter(prefix="/auth", tags=["auth"])
_log = logging.getLogger("liberty.auth")


# -- schemas ---------------------------------------------------------------- #


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    # 8-char floor matches the usual minimum; the Argon2 hash doesn't care about length, this is
    # just a sanity gate so a fat-fingered single char doesn't become someone's password.
    new_password: str = Field(min_length=8)


class ForgotPasswordRequest(BaseModel):
    # Both, as in nomaubl: an e-mail can be shared by several accounts, and asking for the two
    # raises the bar for someone who knows only one.
    username: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=3, max_length=255)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class ProfileUpdate(BaseModel):
    # Self-service display name + email. Blank → cleared (normalised to None below). max_length
    # keeps a pathological paste from bloating the store.
    full_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)


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
        scope=user.scope,
        attributes=user.attributes,
        # a temporary password concerns local accounts only — an SSO session is never asked
        must_change_password=user.must_change_password and user.provider == "local",
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


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Log in",
    responses={
        401: {"description": "Invalid username, password, or the account is inactive."},
    },
)
async def login(
    body: LoginRequest,
    request: Request,
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
    throttle: Annotated[LoginThrottle, Depends(get_login_throttle)],
) -> TokenPair:
    """Exchange username + password for a JWT pair. The access token (default 1 h TTL) goes
    on every subsequent ``Authorization: Bearer <token>`` header; the refresh token (default
    14 d TTL) extends the session via ``POST /auth/refresh``.

    Routes through whichever auth backend the install configured (``[auth] backend =
    "toml"`` or ``"db"`` in app.toml). For OIDC-authenticated users, use
    ``GET /auth/oidc/login`` instead — this endpoint only accepts local (password-backed)
    accounts.

    Repeated failures are throttled per username **and** per client address (``[auth]
    login_max_failures`` inside ``login_window_seconds``, default 10 in 5 min) — a 429 with
    ``Retry-After`` instead of another password check."""
    user_key = f"user:{body.username.strip().lower()}"
    ip_key = f"ip:{client_ip(dict(request.headers), request.client.host if request.client else None)}"
    wait = throttle.retry_after(user_key, ip_key)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed logins — try again in {wait} s",
            headers={"Retry-After": str(wait)},
        )
    user = await backend.authenticate(body.username, body.password)
    if user is None:
        throttle.record_failure(user_key, ip_key)
        raise _BAD_CREDENTIALS
    throttle.reset(user_key)
    return _issue_pair(tokens, user)


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Refresh access token",
    responses={
        401: {"description": "Refresh token is missing, malformed, expired, or the account has been deactivated."},
    },
)
async def refresh(
    body: RefreshRequest,
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> TokenPair:
    """Exchange a still-valid refresh token for a fresh access + refresh pair. The
    presented refresh token is **consumed**: presenting it again more than ``[auth]
    refresh_reuse_grace`` seconds later (default 60 s — enough for two browser tabs racing
    the same stored token) is refused with 401, so a leaked refresh token dies the moment
    its owner refreshes. Callers must store the *new* refresh token from each reply.

    Call this when the access token's exp claim is close — the SPA does so silently from
    a setInterval; backend integrations should refresh ~30 s before the access token
    expires to absorb clock skew."""
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
    try:
        tokens.consume_refresh(claims)
    except TokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail=f"Invalid refresh token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user = await backend.get_by_id(str(subject))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="User is inactive or unknown")
    return _issue_pair(tokens, user)


@router.get(
    "/me",
    summary="Current user claims",
    responses={
        401: {"description": "Missing / invalid / expired access token."},
    },
)
async def me(principal: CurrentPrincipal) -> dict:
    """Decoded JWT claims for the request's access token — username, roles, provider
    (``local`` / ``oidc``), and a few derived fields. Useful for the SPA to populate the
    top-right user pill without parsing the JWT client-side. For the LIVE auth-store
    record (name / email / etc. as the operator last edited them), use ``GET /auth/profile``."""
    return principal.to_dict()


@router.get(
    "/profile",
    summary="Get profile",
    responses={
        401: {"description": "Missing / invalid access token."},
        404: {"description": "The token's subject doesn't resolve to a user (account was deleted while the token was still valid)."},
    },
)
async def get_profile(
    principal: CurrentPrincipal,
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
) -> dict:
    """The current user's *live* record (full_name / email / roles / settings-access) — read from
    the store, not the access-token claims, so the Profile panel shows the latest values even
    right after an edit (the JWT's email/name are only refreshed on the next login)."""
    user = await backend.get_by_id(principal.id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    d = user.public_dict()
    # ``settings_access`` — does this user reach the superuser-only Settings area? (Mirrors the
    # require_superuser gate.) Surfaced so the Profile panel can show it without the frontend
    # re-deriving the rule.
    d["settings_access"] = bool(user.is_superuser)
    d["provider"] = principal.provider
    return d


@router.patch(
    "/profile",
    summary="Update profile",
    responses={
        400: {"description": "Caller is an OIDC user — profile fields are owned by the identity provider."},
        401: {"description": "Missing / invalid access token."},
    },
)
async def update_profile(
    body: ProfileUpdate,
    principal: CurrentPrincipal,
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
) -> dict:
    """Update the current user's display name + email. Local accounts only — an OIDC user's
    profile is owned by the identity provider (and would be overwritten on their next login), so
    we reject those rather than let an edit silently disappear."""
    if principal.provider != "local":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Your profile is managed by your identity provider; change it there.",
        )
    full_name = (body.full_name or "").strip() or None
    email = (body.email or "").strip() or None
    user = await backend.update_profile(principal.username, full_name=full_name, email=email)
    d = user.public_dict()
    d["settings_access"] = bool(user.is_superuser)
    d["provider"] = principal.provider
    return d


@router.post(
    "/change-password",
    summary="Change password",
    responses={
        400: {"description": "OIDC user (no local password) / wrong current password / new password matches current."},
        401: {"description": "Missing / invalid access token."},
    },
)
async def change_password(
    body: ChangePasswordRequest,
    principal: CurrentPrincipal,
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> dict:
    """Self-service password change for the *current* user. Verifies the current password before
    setting the new one (so a stolen, still-valid access token can't silently re-key the account).

    Only for local (password-backed) accounts — OIDC users authenticate at the IdP and have no
    local password to change here; we reject those with a clear 400 rather than create one."""
    if principal.provider != "local":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Password is managed by your identity provider; change it there.",
        )
    # Re-authenticate with the supplied current password (also confirms the account is still active).
    user = await backend.authenticate(principal.username, body.current_password)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="New password must differ from the current one")
    user = await backend.set_password(user.username, body.new_password)   # clears "change at next sign-in"
    # A fresh pair: a session signed in with a temporary password carries ``pwc`` until it's
    # replaced — the SPA swaps these in and goes on without signing in again.
    return {"ok": True, **_issue_pair(tokens, user).model_dump()}


# -- password reset by e-mail ------------------------------------------------- #


def _password_fingerprint(password_hash: str | None) -> str:
    """What a reset link is bound to: the hash it was issued for (changes with any new password)."""
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:24]


def _public_base(request: Request) -> str:
    """The address the reset link points at: ``[app] public_url``, else the browser's origin plus
    ``[app] base_path``, else the request's own base URL."""
    s = request.app.state.settings
    url = (s.app.public_url or "").rstrip("/")
    if url:
        return url
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin:
        return origin + (s.app.base_path or "")
    return str(request.base_url).rstrip("/")


def _reset_mail(lang: str, username: str, link: str) -> tuple[str, str]:
    minutes = RESET_TTL // 60
    if lang.startswith("fr"):
        return ("Réinitialisation du mot de passe", (
            f"Bonjour,\n\nUne réinitialisation du mot de passe a été demandée pour le compte {username}.\n"
            f"Ouvrez le lien ci-dessous pour choisir un nouveau mot de passe. Il expire dans {minutes} minutes "
            f"et ne sert qu'une fois.\n\n{link}\n\nSi vous n'avez rien demandé, ignorez ce message : votre mot "
            "de passe actuel reste valable."))
    return ("Password reset", (
        f"Hello,\n\nA password reset was requested for the account {username}.\n"
        f"Open the link below to choose a new password. It expires in {minutes} minutes and works once.\n\n"
        f"{link}\n\nIf you didn't ask for a reset, ignore this message: your current password stays valid."))


@router.post(
    "/forgot-password",
    summary="Ask for a password reset link",
    responses={429: {"description": "Too many requests from this address."}},
)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
    throttle: Annotated[LoginThrottle, Depends(get_login_throttle)],
) -> dict:
    """Mail a reset link (valid one hour, once) to a **local** account whose username **and**
    e-mail both match. The answer is the same whether or not they match, whether or not the mail
    could be sent — it never tells who has an account. Needs SMTP (``[notify] smtp_host``); the
    link points at ``[app] public_url`` (else the browser's origin). Throttled per address."""
    ip_key = f"reset:{client_ip(dict(request.headers), request.client.host if request.client else None)}"
    wait = throttle.retry_after(ip_key)
    if wait:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=f"Too many requests — try again in {wait} s",
                            headers={"Retry-After": str(wait)})
    throttle.record_failure(ip_key)   # every request counts: the limit is on asking, not on failing
    ok = {"ok": True}
    user = await backend.get_by_username(body.username.strip())
    if (user is None or not user.is_active or user.provider != "local" or not user.password_hash
            or not user.email or user.email.strip().casefold() != body.email.strip().casefold()):
        return ok
    dispatcher = getattr(request.app.state, "notify", None)
    if dispatcher is None or not request.app.state.settings.notify.smtp_host:
        _log.warning("password reset asked for %s but SMTP is not configured ([notify] smtp_host)", user.username)
        return ok
    token = tokens.reset_token(subject=user.id, password_fingerprint=_password_fingerprint(user.password_hash)).token
    link = f"{_public_base(request)}/reset-password?{urlencode({'token': token})}"
    subject, text = _reset_mail(request.headers.get("x-liberty-lang", "en"), user.username, link)
    try:
        await asyncio.to_thread(dispatcher._send_email, [user.email], subject, text)
        _log.info("password reset link sent to %s", user.username)
    except Exception as exc:  # noqa: BLE001 — never tell the caller; the operator reads the log
        _log.error("password reset mail for %s failed: %s", user.username, exc)
    return ok


@router.post(
    "/reset-password",
    summary="Set a new password from a reset link",
    responses={400: {"description": "The link is invalid, expired or already used."}},
)
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
    throttle: Annotated[LoginThrottle, Depends(get_login_throttle)],
) -> dict:
    """Set the new password of the account the link was mailed for — once: the link is bound to the
    password it was issued for, so it stops working as soon as the password changes."""
    ip_key = f"reset:{client_ip(dict(request.headers), request.client.host if request.client else None)}"
    wait = throttle.retry_after(ip_key)
    if wait:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=f"Too many requests — try again in {wait} s",
                            headers={"Retry-After": str(wait)})
    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, detail="This link is invalid, expired or already used.")
    try:
        claims = tokens.decode(body.token, expected_type=RESET)
    except TokenError as exc:
        throttle.record_failure(ip_key)
        raise invalid from exc
    user = await backend.get_by_id(str(claims.get("sub")))
    if (user is None or not user.is_active or user.provider != "local"
            or claims.get("pwf") != _password_fingerprint(user.password_hash)):
        throttle.record_failure(ip_key)
        raise invalid
    await backend.set_password(user.username, body.new_password)   # clears "change at next sign-in"
    _log.info("password reset through a mailed link for %s", user.username)
    return {"ok": True}


# -- OIDC ------------------------------------------------------------------- #


@router.get(
    "/oidc/login",
    summary="Start OIDC login",
    responses={
        307: {"description": "Redirect to the provider's authorize endpoint with state + PKCE."},
        404: {"description": "OIDC is not configured (``[oidc] enabled = false`` in app.toml)."},
    },
)
async def oidc_login(request: Request, oidc: Annotated[OIDCClient, Depends(get_oidc)]):
    """Kick off an OIDC authorization-code flow. The browser is redirected to the configured
    provider's authorize endpoint with state + nonce + PKCE set; the provider eventually
    POSTs back to ``/auth/oidc/callback`` which exchanges the code for tokens.

    Configure via ``[oidc]`` in app.toml — ``provider_url``, ``client_id``,
    ``client_secret``, ``scopes``, and the claim-mapping fields
    (``username_claim`` / ``email_claim`` / ``name_claim``)."""
    return await oidc.authorize_redirect(request, oidc.redirect_uri(request))


@router.get(
    "/oidc/callback",
    name="oidc_callback",
    response_model=TokenPair,
    summary="OIDC callback",
    responses={
        303: {"description": "When ``[oidc] frontend_redirect`` is set, redirects there with tokens in the URL fragment."},
        400: {"description": "OAuth error from the provider, or no userinfo claims in the response."},
        403: {"description": "Account exists locally but is disabled."},
    },
)
async def oidc_callback(
    request: Request,
    oidc: Annotated[OIDCClient, Depends(get_oidc)],
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
):
    """OIDC redirect target. Exchanges the code for tokens, extracts the configured claims,
    upserts (or creates) a local user with ``provider = "oidc"``, and issues a Liberty
    access + refresh pair.

    **Two return modes:**

    - **Default** — returns the ``TokenPair`` as JSON. Used when this endpoint is hit by
      a backend integration that handles redirects itself.
    - **Frontend redirect** — when ``[oidc] frontend_redirect`` is set, the browser is
      redirected there with the tokens in the URL fragment (``#access_token=…&refresh_token=…``).
      The fragment never reaches the server; the SPA's bootstrap reads it on load and
      drops it into the auth store."""
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
        attribute_claims=s.attribute_claims or None,
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
