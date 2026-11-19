"""OAuth sign-in for the MCP endpoint — Liberty as its own authorization server.

A client (Claude) given only the address of ``/mcp`` finds the rest by itself, as the MCP
authorization specification lays it out:

1. ``/mcp`` answers 401 with the address of the **protected resource metadata** (RFC 9728),
   which names this server as the authorization server;
2. the **authorization server metadata** (RFC 8414; also served as OpenID discovery, the only
   address a client can reach when the server sits under a ``base_path``) lists the endpoints;
3. the client **registers** itself (RFC 7591, ``POST /oauth/register``);
4. it opens the browser on ``/oauth/authorize`` — a page of the application, so the user signs
   in the usual way (password or SSO) and allows the client: ``POST /oauth/approve`` answers
   the redirect carrying a one-minute, single-use **authorization code** bound to the PKCE
   challenge (S256, mandatory);
5. ``POST /oauth/token`` exchanges the code for an access token and a rotating refresh token.

Nothing is stored. A registered client IS its signed ``client_id`` (it carries its redirect
addresses); the code and the refresh token are signed tokens too. The access token is a
**personal access token** of one hour — the kind ``/mcp`` already reads — so the account is
re-read at every call and a password change, a deactivation or the loss of ``mcp:use`` ends the
access, exactly as for a token the user pasted. The consent lasts ``[ai] mcp_token_max_days``:
after that day the refresh is refused and the user allows the client again.

Everything answers 404 unless ``[ai] mcp_enabled``. The addresses published are built on
``[app] public_url`` (else the request's own base URL): set it when the server is behind a proxy.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from liberty.auth.authstore import AuthBackend, UserRecord
from liberty.auth.dependencies import CurrentPrincipal, get_auth_backend, get_token_service, require_permission
from liberty.auth.principal import Principal
from liberty.auth.routes import PERSONAL_TOKEN_PERMISSION, _password_fingerprint, account_for_personal_claims
from liberty.auth.tokens import OAUTH_CLIENT, OAUTH_CODE, OAUTH_REFRESH, TokenError, TokenService

router = APIRouter(tags=["oauth"])
_log = logging.getLogger("liberty.auth")

ACCESS_TTL = 3600                 # the access token: a personal access token of one hour
CODE_TTL = 60                     # the authorization code
CLIENT_TTL = 10 * 366 * 86400     # a registration (the client registers again when it ends)
SCOPE = "mcp"                     # the one scope: the MCP endpoint as the signed-in user
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_LOOPBACK = {"localhost", "127.0.0.1", "[::1]", "::1"}


# -- addresses ---------------------------------------------------------------- #


def public_base(request: Request) -> str:
    """The address clients reach this server at: ``[app] public_url``, else the request's own
    base URL (scheme, host and ``base_path``)."""
    url = (request.app.state.settings.app.public_url or "").rstrip("/")
    return url or str(request.base_url).rstrip("/")


def challenge(request: Request, error: str = "") -> str:
    """The ``WWW-Authenticate`` value of a 401 from ``/mcp``: where the protected resource
    metadata is — how a client finds the sign-in."""
    parts = [f'resource_metadata="{public_base(request)}/.well-known/oauth-protected-resource"', f'scope="{SCOPE}"']
    if error:
        parts.insert(0, f'error="{error}"')
    return "Bearer " + ", ".join(parts)


def _require_enabled(request: Request) -> None:
    settings = getattr(request.app.state.settings, "ai", None)
    if settings is None or not settings.enabled or not settings.mcp_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The MCP endpoint is disabled ([ai] mcp_enabled = false)")


Enabled = Depends(_require_enabled)


# -- discovery ---------------------------------------------------------------- #


@router.get("/.well-known/oauth-protected-resource", include_in_schema=False, dependencies=[Enabled])
@router.get("/.well-known/oauth-protected-resource/{rest:path}", include_in_schema=False, dependencies=[Enabled])
async def protected_resource(request: Request) -> dict:
    base = public_base(request)
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "scopes_supported": [SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Liberty Next",
    }


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False, dependencies=[Enabled])
@router.get("/.well-known/oauth-authorization-server/{rest:path}", include_in_schema=False, dependencies=[Enabled])
@router.get("/.well-known/openid-configuration", include_in_schema=False, dependencies=[Enabled])
@router.get("/.well-known/openid-configuration/{rest:path}", include_in_schema=False, dependencies=[Enabled])
async def authorization_server(request: Request) -> dict:
    base = public_base(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [SCOPE],
    }


# -- clients ------------------------------------------------------------------ #


def _oauth_error(code: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": code, "error_description": description}, status_code=status_code, headers=_NO_STORE)


def _redirect_problem(uri: Any) -> str | None:
    """Why *uri* cannot be a redirect address — https, or http on the machine itself (a desktop
    client's local listener); never a fragment."""
    if not isinstance(uri, str) or not uri or len(uri) > 2000:
        return "a redirect address is a string"
    parts = urlsplit(uri)
    if parts.fragment:
        return "a redirect address carries no fragment"
    if parts.scheme == "https" and parts.netloc:
        return None
    if parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK:
        return None
    return "a redirect address is https, or http on localhost"


def _client_tag(client_id: str) -> str:
    """What a code / refresh token keeps of the client it was issued to."""
    return hashlib.sha256(client_id.encode()).hexdigest()[:24]


def _client(tokens: TokenService, client_id: str) -> dict:
    """The registration behind *client_id* (``name``, ``redirect_uris``). Raises :class:`TokenError`."""
    claims = tokens.decode(client_id, expected_type=OAUTH_CLIENT)
    uris = claims.get("redirect_uris")
    if not isinstance(uris, list) or not uris:
        raise TokenError("the registration carries no redirect address")
    return {"name": str(claims.get("name") or "MCP client"), "redirect_uris": [str(u) for u in uris]}


@router.post("/oauth/register", include_in_schema=False, dependencies=[Enabled])
async def register(request: Request, tokens: Annotated[TokenService, Depends(get_token_service)]) -> JSONResponse:
    """Dynamic client registration (RFC 7591), open as the specification wants it: a client is
    public (no secret, PKCE instead) and its registration is the signed ``client_id`` itself."""
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return _oauth_error("invalid_client_metadata", "a JSON object is expected")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > 10:
        return _oauth_error("invalid_redirect_uri", "redirect_uris: one to ten addresses")
    for uri in uris:
        problem = _redirect_problem(uri)
        if problem:
            return _oauth_error("invalid_redirect_uri", problem)
    name = str(body.get("client_name") or "MCP client").strip()[:80] or "MCP client"
    issued = tokens.mint(OAUTH_CLIENT, {"sub": "client", "name": name, "redirect_uris": uris}, CLIENT_TTL)
    return JSONResponse({
        "client_id": issued.token,
        "client_id_issued_at": int(time.time()),
        "client_name": name,
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPE,
    }, status_code=201, headers=_NO_STORE)


# -- consent (the application's /oauth/authorize page calls these) ------------------ #


class ClientInfo(BaseModel):
    client_name: str
    redirect_host: str          # where the user is sent back — shown on the consent page
    local: bool                 # a listener on the user's own machine (a desktop client)
    allowed: bool               # the caller holds `mcp:use`
    changes: bool               # the server lets an external client make changes ([ai] mcp_changes)
    days: int                   # how long the consent lasts ([ai] mcp_token_max_days)


class ApproveRequest(BaseModel):
    client_id: str = Field(min_length=1)
    redirect_uri: str = Field(min_length=1)
    code_challenge: str = Field(min_length=43, max_length=128)
    code_challenge_method: str = "S256"
    state: str | None = Field(default=None, max_length=2000)


class Approved(BaseModel):
    redirect_to: str


def _checked_client(tokens: TokenService, client_id: str, redirect_uri: str) -> dict:
    """The registration, once *redirect_uri* is one of its addresses — exactly. A failure here is
    shown to the user and never redirected: the address cannot be trusted."""
    try:
        client = _client(tokens, client_id)
    except TokenError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown client: {exc}") from exc
    if redirect_uri not in client["redirect_uris"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="The redirect address is not one the client registered")
    return client


@router.get("/oauth/client", response_model=ClientInfo, summary="Describe an OAuth client", dependencies=[Enabled])
async def client_info(
    client_id: str, redirect_uri: str, request: Request, principal: CurrentPrincipal,
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> ClientInfo:
    """What the consent page shows about the client asking to act as the signed-in user."""
    client = _checked_client(tokens, client_id, redirect_uri)
    host = urlsplit(redirect_uri).hostname or ""
    ai = request.app.state.settings.ai
    return ClientInfo(client_name=client["name"], redirect_host=host, local=host in _LOOPBACK,
                      allowed=principal.has_permission(PERSONAL_TOKEN_PERMISSION),
                      changes=bool(getattr(ai, "mcp_changes", False)),
                      days=max(1, int(getattr(ai, "mcp_token_max_days", 366) or 366)))


@router.post(
    "/oauth/approve", response_model=Approved, summary="Allow an OAuth client", dependencies=[Enabled],
    responses={403: {"description": "The caller lacks `mcp:use`."}},
)
async def approve(
    body: ApproveRequest,
    principal: Annotated[Principal, Depends(require_permission(PERSONAL_TOKEN_PERMISSION))],
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> Approved:
    """The signed-in user allows the client to act as them through the MCP endpoint: the answer
    is the client's redirect address carrying a one-minute authorization code."""
    client = _checked_client(tokens, body.client_id, body.redirect_uri)
    if body.code_challenge_method != "S256":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="code_challenge_method must be S256")
    user = await backend.get_by_id(principal.id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    code = tokens.mint(OAUTH_CODE, {
        "sub": user.id, "pwf": _password_fingerprint(user.password_hash), "cid": _client_tag(body.client_id),
        "ru": body.redirect_uri, "cc": body.code_challenge,
    }, CODE_TTL)
    params = {"code": code.token}
    if body.state is not None:
        params["state"] = body.state
    _log.info("OAuth: %s allowed the client %r (MCP endpoint)", user.username, client["name"])
    sep = "&" if urlsplit(body.redirect_uri).query else "?"
    return Approved(redirect_to=f"{body.redirect_uri}{sep}{urlencode(params)}")


# -- tokens ------------------------------------------------------------------- #


def _pkce_matches(verifier: str, challenge_: str) -> bool:
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii", "ignore")).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(digest, challenge_)


def _grant(request: Request, tokens: TokenService, user: UserRecord, client_tag: str, deadline: int) -> JSONResponse:
    """The token answer: a personal access token of at most one hour and the next refresh
    token, neither living past *deadline* — the day the consent ends."""
    left = deadline - int(time.time())
    if left <= 0:
        return _oauth_error("invalid_grant", "the authorization has ended: sign in again")
    fingerprint = _password_fingerprint(user.password_hash)
    access = tokens.personal_token(subject=user.id, password_fingerprint=fingerprint, ttl=min(ACCESS_TTL, left))
    refresh = tokens.mint(OAUTH_REFRESH, {"sub": user.id, "pwf": fingerprint, "cid": client_tag, "dl": deadline}, left)
    return JSONResponse({
        "access_token": access.token, "token_type": "Bearer", "expires_in": access.expires_in,
        "refresh_token": refresh.token, "scope": SCOPE,
    }, headers=_NO_STORE)


@router.post("/oauth/token", include_in_schema=False, dependencies=[Enabled])
async def token(
    request: Request,
    backend: Annotated[AuthBackend, Depends(get_auth_backend)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> JSONResponse:
    """The token endpoint: ``authorization_code`` (with the PKCE verifier) and ``refresh_token``
    (rotated: the one presented is used up). The account is re-read each time."""
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}
    client_id = form.get("client_id", "")
    try:
        _client(tokens, client_id)
    except TokenError as exc:
        return _oauth_error("invalid_client", f"unknown client: {exc}", 401)
    tag = _client_tag(client_id)
    grant = form.get("grant_type", "")
    try:
        if grant == "authorization_code":
            claims = tokens.decode(form.get("code", ""), expected_type=OAUTH_CODE)
            if claims.get("cid") != tag or claims.get("ru") != form.get("redirect_uri"):
                raise TokenError("the code was issued to another client or redirect address")
            if not _pkce_matches(form.get("code_verifier", ""), str(claims.get("cc") or "")):
                raise TokenError("the PKCE verifier does not match")
            tokens.consume_once(claims)
            user = await account_for_personal_claims(claims, backend)
            days = max(1, int(getattr(request.app.state.settings.ai, "mcp_token_max_days", 366) or 366))
            deadline = int(time.time()) + days * 86400
            _log.info("OAuth: access issued to %s through the sign-in (%d days)", user.username, days)
        elif grant == "refresh_token":
            claims = tokens.decode(form.get("refresh_token", ""), expected_type=OAUTH_REFRESH)
            if claims.get("cid") != tag:
                raise TokenError("the refresh token was issued to another client")
            user = await account_for_personal_claims(claims, backend)
            tokens.consume_refresh(claims)
            deadline = int(claims.get("dl") or 0)
        else:
            return _oauth_error("unsupported_grant_type", "authorization_code or refresh_token")
    except TokenError as exc:
        return _oauth_error("invalid_grant", str(exc))
    return _grant(request, tokens, user, tag, deadline)
