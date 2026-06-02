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


def resolve_app_config_path() -> Path:
    """Where the master settings file (``app.toml``) actually lives.

    ``LIBERTY_APP_CONFIG`` (env) wins — useful for a gitignored ``config/app-dev.toml``
    in dev so encrypted license/api-key values don't get committed. Falls back to the
    historical default ``config/app.toml``. Used by both :func:`load_settings` and the
    Settings → App editor so reads + writes always target the same file."""
    override = os.environ.get("LIBERTY_APP_CONFIG", "").strip()
    if override:
        return Path(override)
    return DEFAULT_APP_CONFIG


def _default_config_path(name: str) -> Path:
    """Default location for a per-section TOML (``connectors`` / ``screens`` / etc.).

    When ``LIBERTY_APPS_DIR`` is set in the environment, every default per-section path
    resolves to ``${LIBERTY_APPS_DIR}/<name>.toml`` — the convention for the **liberty-apps**
    private repo where customer-specific configuration lives separately from the framework
    (see ``../liberty-apps/README.md``). Unset → falls back to ``./config/<name>.toml``,
    the historical layout where everything sat under the framework checkout's ``config/``.

    An explicit ``config_path`` set in ``app.toml`` still wins over both — it's a Pydantic
    field default, so any value the operator provides overrides the factory.

    Note: ``auth.toml`` is **not** routed through here. It carries Argon2 password hashes
    and stays per-installation under the framework's own ``config/`` regardless of the
    apps dir (gitignored in both repos)."""
    apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
    if apps:
        return Path(apps) / f"{name}.toml"
    return Path(f"config/{name}.toml")


def _default_jobs_path() -> Path:
    """Default location for ``jobs.toml`` — special-cased from :func:`_default_config_path`
    because nomaflow lives under ``plugins/`` in the apps repo, not at the top of ``config/``.

    ``LIBERTY_APPS_DIR`` is set by convention to the ``config/`` subdir of the apps
    repo (see :file:`liberty-apps/README.md` + :file:`docs/DEPLOYMENT.md`), so
    ``plugins/`` is a *sibling* of that directory — we resolve to ``<apps_dir>/../
    plugins/nomaflow/jobs.toml``. If the operator points ``LIBERTY_APPS_DIR``
    elsewhere (e.g. at the repo root itself), set ``[jobs] config_path`` explicitly.

    Falls back to ``./config/jobs.toml`` when the env var is unset (dev / API-only).
    See ``docs/PHASE13.md`` §3."""
    apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
    if apps:
        return Path(apps).parent / "plugins" / "nomaflow" / "jobs.toml"
    return Path("config/jobs.toml")

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
    name: str = "Liberty Next"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    # Built frontend (Vite `dist/`) served as static; empty or missing dir → not mounted.
    static_dir: str = "frontend/dist"
    # When true, the server watches the config TOML files (menus / screens / dictionary /
    # charts / dashboards / jobs / the [connectors.*] half of connectors.toml) and reloads
    # the matching subsystem on change. Off by default — operators get the explicit
    # POST /admin/reload by default. Flip on in app.toml ``[app] hot_reload = true`` when
    # actively editing config from disk (vim / scp / git pull) and you want changes live
    # without clicking Reload. ``[pools.*]`` changes are intentionally NOT auto-reloaded —
    # swapping a live SQL engine mid-query is too risky; use the Pools page's manual reload
    # for those. See liberty.web.hot_reload for the per-file dispatch.
    hot_reload: bool = False
    # Fallback language for dictionary translations when a request doesn't supply Accept-Language.
    # Each dictionary entry's ``l`` map carries per-language overrides; when none match, the base
    # ``label`` is used. App-wide setting (one value covers every connector).
    default_language: str = "en"


class ConnectorSettings(BaseModel):
    # Path resolves through ``_default_config_path`` so ``LIBERTY_APPS_DIR`` (when set)
    # redirects the default to ``${LIBERTY_APPS_DIR}/connectors.toml``; explicit
    # ``config_path`` in app.toml still wins. Same convention for every section below.
    config_path: Path = Field(default_factory=lambda: _default_config_path("connectors"))
    # Shared field dictionary (labels/types referenced by query column hints). Empty → use
    # `dictionary.toml` next to `config_path`. A missing file is fine (no shared entries).
    dictionary_path: Path | None = None


class MenuSettings(BaseModel):
    """App navigation menus (the v2 form of v1's ``ly_menus``)."""

    # `[menus.<app>]` per app; a missing file is fine (no menus → the UI falls back to
    # the flat connector list).
    config_path: Path = Field(default_factory=lambda: _default_config_path("menus"))


class ScreenSettings(BaseModel):
    """Screen definitions per app (the v2 collapse of v1's ``ly_tables`` + dialog stack —
    see :mod:`liberty.screens`)."""

    # `[screens.<app>.<id>]` per screen; a missing file is fine (no screens → only the raw
    # connector/queries flow is available in the UI).
    config_path: Path = Field(default_factory=lambda: _default_config_path("screens"))


class ChartSettings(BaseModel):
    """Saved chart definitions — Phase 8 slice 2 (see :mod:`liberty.charts`). Per-session
    chart specs from the TableView's chart toggle persist client-side in localStorage; this
    file holds the ones an operator has saved for sharing."""

    # `[charts.<id>]` per chart; a missing file is fine (no shared charts → the runtime still
    # works via localStorage specs).
    config_path: Path = Field(default_factory=lambda: _default_config_path("charts"))


class DashboardSettings(BaseModel):
    """Dashboard layouts — Phase 8 slice 3 (see :mod:`liberty.dashboards`). A dashboard is a
    grid of widgets (chart, kpi, …) surfaced through the menu system like any other screen."""

    config_path: Path = Field(default_factory=lambda: _default_config_path("dashboards"))


class ThemeSettings(BaseModel):
    """Per-deployment branding — the operator's colours + app name (see :mod:`liberty.theme`).
    A missing file is fine (the built-in Liberty default)."""

    config_path: Path = Field(default_factory=lambda: _default_config_path("theme"))


class AuthSettings(BaseModel):
    """Internal-user auth: where users/roles live (a TOML file or the DB), JWT signing."""

    # "toml" → users/roles in `toml_path` (no DB needed to start — the default); "db" → the ly2_*
    # tables on `pool` (created by `liberty-admin init-db`; for big user bases).
    backend: Literal["toml", "db"] = "toml"
    toml_path: Path = Path("config/auth.toml")  # used when backend == "toml"
    pool: str = "default"  # used when backend == "db" — which connector pool the ly2_* tables live on
    jwt_secret: str = ""  # ${LIBERTY_JWT_SECRET}; empty → ephemeral key generated at startup
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "liberty-next"
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
    model: str = "claude-opus-4-8"
    max_tokens: int = 8192
    max_iterations: int = 8  # safety cap on tool-use round trips
    system_prompt: str = ""  # empty → built-in default (see liberty.ai.assistant)
    thinking: bool = False  # enable adaptive thinking
    effort: str = ""  # "" | low | medium | high | xhigh | max — only sent when set
    request_timeout: float = 120.0
    # Tool exposure
    connector_tools: bool = True  # list_connectors + sql_query (read-only queries only)
    api_tool: bool = False  # api_call — off by default (API endpoints may have side effects)
    # Tier 1 scaffolding tools — introspect_table + scaffold_crud_queries / dict_entries /
    # screen / menu_item. Each *write-capable* tool returns a Proposal envelope rather than
    # mutating files; the chat UI surfaces it as an Apply card that POSTs to
    # /admin/config/apply-proposal. Default OFF — operators opt in deliberately.
    scaffold_tools: bool = False
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


class ReportsBrandingSettings(BaseModel):
    """Install-wide PDF cover / header / footer defaults applied to every
    report rendered through :func:`liberty.reports.render.render_content`.

    Plugin-supplied ``pdf_options`` on the :class:`~liberty.reports.ReportContent`
    return value still override these per-report (so a report can pick its
    own ``subtitle`` or ``primary_color`` if it wants). The operator-curated
    branding here just fills the gaps — typically: company name as ``author``,
    customer brand color, a neutral eyebrow like "Rapport d'audit".

    Edited via Settings → Reports (frontend ``ReportsBuilder``); persisted in
    ``[reports.branding]`` inside ``app.toml`` (see
    :func:`liberty.web.admin.put_reports_branding`)."""

    author: str = ""
    primary_color: str = "#0b3a82"
    primary_color_light: str = "#2563eb"
    cover_eyebrow: str = "Rapport"
    cover_ref: str = "Document confidentiel"
    footer_left: str = "Confidentiel"


class ReportsSettings(BaseModel):
    """Reports framework settings (Phase 3b). Today only carries branding
    defaults; ``[reports.scheduling]`` etc. would land here later as further
    sub-sections without touching the rest of the config tree."""

    branding: ReportsBrandingSettings = Field(default_factory=ReportsBrandingSettings)


class JobsSettings(BaseModel):
    """nomaflow — config-driven ETL + scheduler (see ``docs/PHASE13.md``).

    ``config_path`` resolves through :func:`_default_jobs_path` so
    ``LIBERTY_APPS_DIR`` (when set) redirects the default to
    ``${LIBERTY_APPS_DIR}/plugins/nomaflow/jobs.toml`` — nomaflow is an *app*, so
    its catalogue lives in the apps repo under ``plugins/`` (not at the
    config-root level like connectors/screens/etc). ``pool`` defaults to
    ``"default"`` so the ``nomaflow_*`` tables land on the same pool the auth
    tables use; override only when an operator wants them on a separate DB."""

    config_path: Path = Field(default_factory=_default_jobs_path)
    pool: str = "default"
    # Master toggle. False → the registry still loads (so /admin views work) but the
    # scheduler + runner won't start at app boot. Useful for one-off maintenance windows.
    enabled: bool = True


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    connectors: ConnectorSettings = Field(default_factory=ConnectorSettings)
    menus: MenuSettings = Field(default_factory=MenuSettings)
    screens: ScreenSettings = Field(default_factory=ScreenSettings)
    charts: ChartSettings = Field(default_factory=ChartSettings)
    dashboards: DashboardSettings = Field(default_factory=DashboardSettings)
    theme: ThemeSettings = Field(default_factory=ThemeSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    oidc: OIDCSettings = Field(default_factory=OIDCSettings)
    ai: AISettings = Field(default_factory=AISettings)
    crypto: CryptoSettings = Field(default_factory=CryptoSettings)
    license: LicenseSettings = Field(default_factory=LicenseSettings)
    jobs: JobsSettings = Field(default_factory=JobsSettings)
    reports: ReportsSettings = Field(default_factory=ReportsSettings)


def load_settings(
    path: Path | str | None = None, *, env: dict[str, str] | None = None
) -> Settings:
    """Load ``app.toml``. ``path=None`` → :func:`resolve_app_config_path`
    (``LIBERTY_APP_CONFIG`` env override, else ``config/app.toml``)."""
    p = Path(path) if path is not None else resolve_app_config_path()
    if not p.exists():
        return Settings()
    with p.open("rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    return Settings.model_validate(substitute_env(data, env=env))
