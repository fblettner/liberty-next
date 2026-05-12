"""Application settings — loaded from ``config/app.toml`` (Pydantic-validated).

``${ENV_VAR}`` references anywhere in the file are expanded against the
environment at load time (an unset var becomes the empty string) so secrets —
the JWT signing key, the OIDC client secret — are referenced, never inlined.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_APP_CONFIG = Path("config/app.toml")

# ${NAME}  or  ${NAME:-default}  (shell semantics: the default is used when NAME is
# unset *or* empty). The default may contain anything except '}'.
_ENV_REF = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def substitute_env(value: Any, *, env: dict[str, str] | None = None) -> Any:
    """Recursively replace ``${NAME}`` / ``${NAME:-default}`` references with env values."""
    src = os.environ if env is None else env

    def _repl(m: re.Match[str]) -> str:
        v = src.get(m.group(1))
        if v:
            return v
        return m.group(2) if m.group(2) is not None else ""

    if isinstance(value, str):
        return _ENV_REF.sub(_repl, value)
    if isinstance(value, dict):
        return {k: substitute_env(v, env=env) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_env(v, env=env) for v in value]
    return value


class AppSettings(BaseModel):
    name: str = "Liberty v2"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    # Built frontend (Vite `dist/`) served as static; empty or missing dir → not mounted.
    static_dir: str = "frontend/dist"


class ConnectorSettings(BaseModel):
    config_path: Path = Path("config/connectors.toml")
    # Shared field dictionary (labels/types referenced by query column hints). Empty → use
    # `dictionary.toml` next to `config_path`. A missing file is fine (no shared entries).
    dictionary_path: Path | None = None


class MenuSettings(BaseModel):
    """App navigation menus (the v2 form of v1's ``ly_menus``)."""

    # `[menus.<app>]` per app; a missing file is fine (no menus → the UI falls back to
    # the flat connector list).
    config_path: Path = Path("config/menus.toml")


class AuthSettings(BaseModel):
    """Internal-user auth: where users/roles live (a TOML file or the DB), JWT signing."""

    # "toml" → users/roles in `toml_path` (no DB needed to start — the default); "db" → the ly2_*
    # tables on `pool` (created by `liberty-admin init-db`; for big user bases).
    backend: Literal["toml", "db"] = "toml"
    toml_path: Path = Path("config/auth.toml")  # used when backend == "toml"
    pool: str = "default"  # used when backend == "db" — which connector pool the ly2_* tables live on
    jwt_secret: str = ""  # ${LIBERTY_JWT_SECRET}; empty → ephemeral key generated at startup
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "liberty-v2"
    access_token_ttl: int = 3600  # seconds
    refresh_token_ttl: int = 1_209_600  # seconds (14 days)


class OIDCSettings(BaseModel):
    """OpenID Connect — any OIDC-compliant provider via its discovery URL (Keycloak, OneLogin,
    Auth0, Okta, Azure AD, Google, …; no broker/Keycloak-in-front needed). Disabled unless
    ``enabled`` + a ``discovery_url``."""

    enabled: bool = False
    # the provider's .well-known/openid-configuration URL — e.g. OneLogin:
    # https://<subdomain>.onelogin.com/oidc/2/.well-known/openid-configuration
    discovery_url: str = ""
    client_id: str = ""
    client_secret: str = ""  # ${OIDC_CLIENT_SECRET}
    scopes: str = "openid email profile"
    username_claim: str = "preferred_username"
    email_claim: str = "email"
    name_claim: str = "name"
    redirect_url: str = ""  # override the auto-derived /auth/oidc/callback URL (proxies)
    # If set, /auth/oidc/callback redirects here with the JWTs in the URL fragment
    # (#access_token=…&refresh_token=…) — for SPAs. Empty → the callback returns JSON.
    frontend_redirect: str = ""
    # Cookie session used by the OAuth state/nonce dance; falls back to jwt_secret.
    session_secret: str = ""


class AISettings(BaseModel):
    """Anthropic-backed assistant: model, tool exposure, server-side web_fetch."""

    enabled: bool = True
    api_key: str = ""  # ${ANTHROPIC_API_KEY}; empty → chat endpoint reports it unconfigured
    model: str = "claude-opus-4-7"
    max_tokens: int = 8192
    max_iterations: int = 8  # safety cap on tool-use round trips
    system_prompt: str = ""  # empty → built-in default (see liberty.ai.assistant)
    thinking: bool = False  # enable adaptive thinking
    effort: str = ""  # "" | low | medium | high | xhigh | max — only sent when set
    request_timeout: float = 120.0
    # Tool exposure
    connector_tools: bool = True  # list_connectors + sql_query (read-only queries only)
    api_tool: bool = False  # api_call — off by default (API endpoints may have side effects)
    allowed_connectors: list[str] = Field(default_factory=list)  # empty → all
    # Server-side web_fetch (Anthropic-hosted); disabled unless domains are listed
    web_fetch_domains: list[str] = Field(default_factory=list)
    web_fetch_max_uses: int = 5


class CryptoSettings(BaseModel):
    """Field-level encryption — must match v1's ``MASTER_KEY`` so v1-encrypted DB values
    (e.g. ``SETTINGS_APPLICATIONS.password``, ``ly_api_conn.conn_password``) round-trip."""

    # ${LIBERTY_MASTER_KEY} — the value in v1's secrets.json; provide it via the env var,
    # never hard-code it. Empty → crypto ops fail loudly.
    master_key: str = ""


class LicenseSettings(BaseModel):
    """The license key that unlocks ``licensed = true`` connectors (an RS256 JWT — see
    :mod:`liberty.licensing`). Empty → restricted (the open framework; licensed connectors
    aren't loaded). Supply it via the env var, not hard-coded."""

    key: str = ""   # ${LIBERTY_LICENSE_KEY}


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    connectors: ConnectorSettings = Field(default_factory=ConnectorSettings)
    menus: MenuSettings = Field(default_factory=MenuSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    oidc: OIDCSettings = Field(default_factory=OIDCSettings)
    ai: AISettings = Field(default_factory=AISettings)
    crypto: CryptoSettings = Field(default_factory=CryptoSettings)
    license: LicenseSettings = Field(default_factory=LicenseSettings)


def load_settings(
    path: Path | str = DEFAULT_APP_CONFIG, *, env: dict[str, str] | None = None
) -> Settings:
    path = Path(path)
    if not path.exists():
        return Settings()
    with path.open("rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    return Settings.model_validate(substitute_env(data, env=env))
