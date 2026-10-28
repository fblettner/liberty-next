"""Application settings — loaded from ``config/app.toml`` (Pydantic-validated).

``${ENV_VAR}`` references anywhere in the file are expanded against the
environment at load time (an unset var becomes the empty string) so secrets —
the JWT signing key, the OIDC client secret — are referenced, never inlined.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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
    repo (see the apps repo's ``README.md`` + ``docs/DEPLOYMENT.md``), so
    ``plugins/`` is a *sibling* of that directory — we resolve to ``<apps_dir>/../
    plugins/nomaflow/jobs.toml``. If the operator points ``LIBERTY_APPS_DIR``
    elsewhere (e.g. at the repo root itself), set ``[jobs] config_path`` explicitly.

    Falls back to ``./config/jobs.toml`` when the env var is unset (dev / API-only).
    See the apps repo's ``docs/NOMAFLOW.md``."""
    apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
    if apps:
        return Path(apps).parent / "plugins" / "nomaflow" / "jobs.toml"
    return Path("config/jobs.toml")

# ${NAME}  or  ${NAME:-default}  (shell semantics: the default is used when NAME is
# unset *or* empty). The default may contain anything except '}'.
_log = logging.getLogger(__name__)

_ENV_REF = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _env_or_file(src: Mapping[str, str], name: str) -> str | None:
    """``NAME`` from *src*; else the content of the file ``NAME_FILE`` points at (Docker / Swarm /
    Kubernetes secrets are mounted as files). Trailing newlines are stripped; an unreadable file
    counts as unset (logged once)."""
    v = src.get(name)
    if v:
        return v
    path = src.get(f"{name}_FILE")
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        _log.warning("%s_FILE=%s is not readable (%s) — treated as unset", name, path, exc)
        return None


def substitute_env(value: Any, *, env: dict[str, str] | None = None) -> Any:
    """Recursively replace ``${NAME}`` / ``${NAME:-default}`` references with env values — or
    with the content of the file ``NAME_FILE`` names, when ``NAME`` itself is unset."""
    src = os.environ if env is None else env

    def _repl(m: re.Match[str]) -> str:
        v = _env_or_file(src, m.group(1))
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
    # "text" (human-readable line, UTC timestamp, request id) or "json" (one object per line for a
    # log shipper). Every line carries the X-Request-ID of the request it was logged in.
    log_format: Literal["text", "json"] = "text"
    # The server log on disk (liberty.logfile): a size-rotated JSON-lines file the Server log page
    # searches. None = <config dir>/.logs/liberty.log; "" = no file.
    log_file: str | None = None
    log_file_max_mb: int = Field(default=10, ge=1, le=1024)
    log_file_backups: int = Field(default=5, ge=0, le=100)
    # Levels per logger, applied at startup and changeable from the page: {"liberty.jobs" = "DEBUG"}.
    log_levels: dict[str, str] = Field(default_factory=dict)
    # Built frontend (Vite `dist/`) served as static; empty or missing dir → not mounted.
    static_dir: str = "frontend/dist"
    # The URL prefix the app is served under behind a reverse proxy that strips it (``/liberty``
    # for https://host/liberty/); blank = the site root. Written into index.html's <base href>
    # (the SPA resolves its assets, API calls, socket and routes against it) and used as the
    # app's root_path (the URLs the server builds, e.g. the OIDC callback). Restart to apply.
    base_path: str = ""

    @field_validator("base_path")
    @classmethod
    def _norm_base_path(cls, v: str) -> str:
        v = (v or "").strip().strip("/")
        return f"/{v}" if v else ""
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
    # Use python-oracledb THICK mode (the bundled Instant Client / OCI) for every Oracle pool,
    # instead of the default thin driver. Required for Oracle Native Network Encryption (NNE) +
    # data-integrity checksumming, which thin mode can't do (fails with DPY-3001). This is an
    # APP-WIDE flag, not per-pool: ``init_oracle_client()`` is process-global, so thick vs thin is
    # an all-or-nothing choice for the whole process — every Oracle pool runs thick when this is on
    # (a sync engine driven from a thread pool). Postgres / other pools are unaffected either way.
    # Needs the Instant Client on the box — ``LIBERTY_ORACLE_CLIENT_LIB`` (set in the Docker image).
    oracle_thick: bool = False
    # UI defaults the SPA reads from GET /api/theme (public). A dashboard's own ``period.default``
    # wins over ``default_period``; a pick lasts while the dashboard tab is open. ``default_page_size`` is the grid's initial page size (the SELECT row cap stays on
    # the pool / connector / screen). ``dashboard_cache_seconds``: a widget's result is reused for
    # that long when the dashboard is reopened; Refresh bypasses it; 0 disables the cache.
    default_period: str = "thisMonth"
    default_page_size: int = 50
    dashboard_cache_seconds: int = 60
    # The workspace tabs: several open at once (``tabs_multi``; off = one page at a time, opening
    # another replaces it), closed when the operator switches application, and how many at most
    # (the oldest closes when the limit is reached).
    tabs_multi: bool = True
    tabs_close_on_app_switch: bool = False
    tabs_max: int = Field(default=5, ge=1, le=50)
    # How a row-menu / toolbar ``navigate`` action opens its target when the action does not say
    # (``inline`` blank): a new tab (default), or a panel inside the current tab with a breadcrumb.
    drill_inline: bool = False


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


class ActionSettings(BaseModel):
    """Shared actions — reusable named action chains (see :mod:`liberty.actions`). ``[actions.<id>]``
    per action; a missing file is fine (no shared actions → screens just use inline chains)."""

    config_path: Path = Field(default_factory=lambda: _default_config_path("actions"))


class ExplorerSettings(BaseModel):
    """The Data Explorer models (see :mod:`liberty.explorer`). ``[explorer.<app>]`` per app; a
    missing file is fine (no explorer)."""

    config_path: Path = Field(default_factory=lambda: _default_config_path("explorer"))


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
    # Refresh-token rotation: a refresh token already exchanged may be presented again for
    # this many seconds (two browser tabs racing the same stored token); after that it's
    # refused. 0 = strict single use.
    refresh_reuse_grace: int = 60
    # Login throttle: after `login_max_failures` failed logins for a username inside
    # `login_window_seconds` (or 5× that for one client address — password spraying),
    # POST /auth/login answers 429 + Retry-After. 0 failures = throttle off.
    login_max_failures: int = 10
    login_window_seconds: int = 300


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
    # ``{attribute: claim}`` — user attributes a role's data scope reads (``#USER.company#``),
    # refreshed from the ID token at every login; a mapped claim that is absent removes the attribute.
    attribute_claims: dict[str, str] = Field(default_factory=dict)
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
    # Operations tools — jobs_status / server_log / integrity_checks (liberty.ai.ops_tools).
    # Off by default: the log tail can carry what the server logged; enable for an install
    # whose chat users are administrators.
    ops_tools: bool = False
    explorer_tools: bool = True  # explorer_models / explorer_fields / explorer_query — aggregates on the Data Explorer models
    allowed_connectors: list[str] = Field(default_factory=list)  # empty → all
    # Server-side web_fetch (Anthropic-hosted); disabled unless domains are listed
    web_fetch_domains: list[str] = Field(default_factory=list)
    web_fetch_max_uses: int = 5
    # The chat's model picker: the models a user may choose besides ``model`` (empty → no
    # picker, ``model`` only). ``POST /ai/chat`` refuses any other id.
    allowed_models: list[str] = Field(default_factory=list)
    # Starter questions shown on an empty conversation, keyed by ``*`` (everywhere), an app
    # (``nomajde``) or an app's screen (``nomajde/f0092``); the most specific key that matches
    # the page the user is on wins.
    starters: dict[str, list[str]] = Field(default_factory=dict)
    # ai.toml — the assistant material the APPS ship (starters per app / screen, playbooks
    # next), bundled and merged like the other per-app files; ``starters`` above are the
    # operator's and win over it. See liberty.ai.appconfig.
    config_path: Path = Field(default_factory=lambda: _default_config_path("ai"))


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
    """Reports framework settings — install-wide defaults applied to every
    report rendered through :func:`liberty.reports.render.render_content`.

    * ``branding`` (Phase 3b) — PDF cover / header / footer defaults edited
      via Settings → Reports → Branding.
    * ``templates_config_path`` (Phase 4a) — where ``reports.toml`` lives.
      Resolves through :func:`_default_config_path` so ``LIBERTY_APPS_DIR``
      (when set) redirects the default to ``${LIBERTY_APPS_DIR}/reports.toml``
      — operator-authored custom reports live alongside the other per-app
      configs (menus / screens / dashboards / …).
    """

    branding: ReportsBrandingSettings = Field(default_factory=ReportsBrandingSettings)
    templates_config_path: Path = Field(
        default_factory=lambda: _default_config_path("reports"),
        description=(
            "Path to reports.toml, which carries operator-authored custom "
            "reports (one [reports.<id>] table per report). A missing file "
            "is fine — no custom reports are loaded."
        ),
    )


class JobsSettings(BaseModel):
    """nomaflow — config-driven ETL + scheduler (see the apps repo's ``docs/NOMAFLOW.md``).

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
    # Per-run log ring-buffer cap (lines beyond it drop, oldest first) shown in the UI
    # run log. Default 5000; raise it for jobs that legitimately emit tens of thousands
    # of lines (e.g. a per-table ETL over thousands of tables) so the run's head isn't lost.
    run_log_max_lines: int = 5000
    # The file work area (uploads for a run, file_load rejects reports). Default: next to the
    # config, ``<config dir>/.nomaflow/files`` — a mounted volume on Docker.
    files_dir: Path | None = None
    max_upload_mb: int = Field(200, ge=1)


class PostApplyStep(BaseModel):
    """A reusable run-ONCE step (a library entry, keyed by ``id``) that a change-tracked screen can
    reference via ``Screen.post_apply``. When a package is exported, the union of the step-ids of the
    screens that CONTRIBUTED to it is resolved here and appended to the bundle — so a UDC change and a
    security change on the same connector pull in different post-apply steps (the security screen's
    remerge, not both). Executed once on the target after every change op applies. Mirrors the
    apply-op shape so it reuses the apply engine's replay paths."""

    id: str = Field(description="Unique id, referenced by ``Screen.post_apply``.")
    type: Literal["call_plugin", "call_api", "run_query"] = Field(
        default="call_plugin", description="How to run: a server plugin, an API endpoint, or a SQL query.",
    )
    connector: str | None = Field(
        default=None, description="Target connector for call_api / run_query. Blank → the package's connector.",
    )
    target: str = Field(description="The callable (``module:function``), endpoint name, or query name to run.")
    params: dict[str, Any] = Field(default_factory=dict, description="Static params passed to the call.")
    label: str | None = Field(default=None, description="Display label in the apply report.")


class NotifySettings(BaseModel):
    """Notifications (``liberty.notify``): the inbox table's pool and the SMTP account the email
    channel sends from. ``smtp_password`` may be ``ENC:…`` (encrypted with the master key, like the
    other secrets). ``smtp_host`` empty → the email channel reports "not configured" and the
    portal / action channels still work."""

    enabled: bool = True
    pool: str = "default"
    # The rules — ``[notifications.<id>]`` (liberty.notify.rules); next to the other config files.
    config_path: Path = Field(default_factory=lambda: _default_config_path("notifications"))
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True      # STARTTLS after connecting (port 587)
    smtp_ssl: bool = False     # implicit TLS from the first byte (port 465)
    smtp_timeout: int = 20
    # Read notifications older than this are swept by the retention job (0 = keep forever).
    retention_days: int = 90


class ChangeSetsSettings(BaseModel):
    """Change packages — capture tracked writes into reviewable, promotable change-sets (see
    ``project_nomajde_change_packages``). ``pool`` defaults to ``"default"`` so the ``ly_change_*``
    *control* tables land on the same pool as auth / nomaflow; point it at the framework Postgres
    (e.g. ``nomasx1``) when the default pool is a connector you don't want control tables on.
    ``enabled`` off → no capture (the screens' ``change_tracked`` flags become no-ops).
    ``post_apply`` runs once on the target after a bundle of the matching ``application`` applies."""

    pool: str = "default"
    enabled: bool = True
    post_apply: list[PostApplyStep] = Field(default_factory=list)
    # Retention (Monitoring → Retention): exported / promoted packages and the import logs older
    # than this many days are deleted; 0 = keep them all (the default — they are audit evidence).
    retention_days: int = Field(default=0, ge=0, le=36500)


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    connectors: ConnectorSettings = Field(default_factory=ConnectorSettings)
    menus: MenuSettings = Field(default_factory=MenuSettings)
    screens: ScreenSettings = Field(default_factory=ScreenSettings)
    charts: ChartSettings = Field(default_factory=ChartSettings)
    actions: ActionSettings = Field(default_factory=ActionSettings)
    dashboards: DashboardSettings = Field(default_factory=DashboardSettings)
    explorer: ExplorerSettings = Field(default_factory=ExplorerSettings)
    theme: ThemeSettings = Field(default_factory=ThemeSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    oidc: OIDCSettings = Field(default_factory=OIDCSettings)
    ai: AISettings = Field(default_factory=AISettings)
    crypto: CryptoSettings = Field(default_factory=CryptoSettings)
    license: LicenseSettings = Field(default_factory=LicenseSettings)
    jobs: JobsSettings = Field(default_factory=JobsSettings)
    reports: ReportsSettings = Field(default_factory=ReportsSettings)
    changesets: ChangeSetsSettings = Field(default_factory=ChangeSetsSettings)
    notify: NotifySettings = Field(default_factory=NotifySettings)


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
