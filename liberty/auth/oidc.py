"""OpenID Connect via Authlib's Starlette client — provider-agnostic.

Configured purely from the provider's discovery document (``…/.well-known/openid-configuration``),
so it works with **any** OIDC-compliant IdP — Keycloak, OneLogin, Auth0, Okta, Azure AD, Google,
… — directly, with no broker (e.g. a Keycloak in front) needed. Thin wrapper: build an
:class:`~authlib.integrations.starlette_client.OAuth` registry with one provider (``"oidc"``)
configured from that discovery doc, and expose the two half-steps of the auth-code flow. ID-token
signature/claims validation (against the JWKS in the discovery doc) is done by Authlib inside
:meth:`authorize_access_token`.

When OIDC is disabled (:attr:`OIDCSettings.enabled` false or no discovery URL),
:func:`build_oidc` returns ``None`` and the routes 404.
"""

from __future__ import annotations

from typing import Any

from authlib.integrations.starlette_client import OAuth

from liberty.config import OIDCSettings

PROVIDER_NAME = "oidc"


class OIDCClient:
    """Holds the configured Authlib provider plus the settings it was built from."""

    def __init__(self, oauth: OAuth, settings: OIDCSettings) -> None:
        self._oauth = oauth
        self.settings = settings

    @property
    def provider(self):
        return getattr(self._oauth, PROVIDER_NAME)

    async def authorize_redirect(self, request, redirect_uri: str):
        """Step 1: bounce the browser to the IdP's authorization endpoint."""
        return await self.provider.authorize_redirect(request, redirect_uri)

    async def authorize_access_token(self, request) -> dict[str, Any]:
        """Step 2: exchange the returned code for tokens; validates the ID token.

        The returned dict includes ``"userinfo"`` (the parsed ID-token claims)
        when the ``openid`` scope was requested.
        """
        return await self.provider.authorize_access_token(request)

    def redirect_uri(self, request) -> str:
        """Where the IdP sends the browser back — explicit config wins, else the
        named route on this app (override behind a reverse proxy)."""
        if self.settings.redirect_url:
            return self.settings.redirect_url
        return str(request.url_for("oidc_callback"))


def build_oidc(settings: OIDCSettings, *, master_key: str = "") -> OIDCClient | None:
    if not settings.enabled or not settings.discovery_url:
        return None
    # client_secret is stored encrypted (ENC:) when set via Settings UI; decrypt before
    # handing it to authlib. Plaintext values pass through unchanged for backward compat.
    client_secret = settings.client_secret
    if client_secret.startswith("ENC:"):
        if not master_key:
            raise ValueError("OIDC client_secret is ENC:-encrypted but no master_key configured")
        from liberty.crypto import decrypt
        client_secret = decrypt(client_secret, master_key)
    oauth = OAuth()
    oauth.register(
        name=PROVIDER_NAME,
        server_metadata_url=settings.discovery_url,
        client_id=settings.client_id,
        client_secret=client_secret,
        client_kwargs={"scope": settings.scopes},
    )
    return OIDCClient(oauth, settings)
