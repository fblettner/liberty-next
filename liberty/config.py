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
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_APP_CONFIG = Path("config/app.toml")

_ENV_REF = re.compile(r"\$\{(\w+)\}")


def substitute_env(value: Any, *, env: dict[str, str] | None = None) -> Any:
    """Recursively replace ``${NAME}`` references with environment values."""
    src = os.environ if env is None else env
    if isinstance(value, str):
        return _ENV_REF.sub(lambda m: src.get(m.group(1), ""), value)
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


class AuthSettings(BaseModel):
    """Internal-user auth: which pool the auth tables live on, JWT signing."""

    pool: str = "default"
    jwt_secret: str = ""  # ${LIBERTY_JWT_SECRET}; empty → ephemeral key generated at startup
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "liberty-v2"
    access_token_ttl: int = 3600  # seconds
    refresh_token_ttl: int = 1_209_600  # seconds (14 days)


class OIDCSettings(BaseModel):
    """OpenID Connect (Keycloak-ready). Disabled unless ``enabled`` + a discovery URL."""

    enabled: bool = False
    discovery_url: str = ""  # the .well-known/openid-configuration URL
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


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    connectors: ConnectorSettings = Field(default_factory=ConnectorSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    oidc: OIDCSettings = Field(default_factory=OIDCSettings)
    ai: AISettings = Field(default_factory=AISettings)


def load_settings(
    path: Path | str = DEFAULT_APP_CONFIG, *, env: dict[str, str] | None = None
) -> Settings:
    path = Path(path)
    if not path.exists():
        return Settings()
    with path.open("rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    return Settings.model_validate(substitute_env(data, env=env))
