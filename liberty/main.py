from __future__ import annotations

import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from liberty import __version__
from liberty.ai.assistant import build_assistant
from liberty.ai.routes import router as ai_router
from liberty.auth.authstore import build_auth_backend
from liberty.auth.oidc import build_oidc
from liberty.auth.routes import router as auth_router
from liberty.auth.tokens import TokenConfig, TokenService
from liberty.config import AuthSettings, Settings, load_settings
from liberty.connectors import ConnectorRegistry, load_connectors
from liberty.connectors.base import ConnectorError
from liberty.licensing import verify_license
from liberty.actions import load_actions
from liberty.charts import load_charts
from liberty.dashboards import load_dashboards
from liberty.menus import load_menus
from liberty.screens import load_screens
from liberty.web import (
    access_router,
    actions_router,
    admin_router,
    changesets_router,
    charts_router,
    dictgen_router,
    connectors_router,
    dashboards_router,
    export_router,
    jobs_router,
    license_router,
    menus_router,
    plugins_router,
    reports_router,
    screens_router,
    theme_router,
)

_log = logging.getLogger("liberty")


class SPAStaticFiles(StaticFiles):
    """Serve a Vite build, falling back to ``index.html`` for client-side routes
    (so a hard refresh on ``/connectors`` doesn't 404). Mounted at ``/`` *after*
    the API routers, so it never shadows ``/api``, ``/auth``, ``/ai``, ``/admin``,
    ``/health``, ``/info`` or ``/docs``."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def _build_token_service(cfg: AuthSettings) -> TokenService:
    secret = cfg.jwt_secret
    if not secret:
        secret = secrets.token_urlsafe(48)
        _log.warning(
            "auth.jwt_secret is empty — using an ephemeral signing key; issued tokens "
            "won't survive a restart. Set LIBERTY_JWT_SECRET in the environment."
        )
    return TokenService(
        TokenConfig(
            secret=secret,
            algorithm=cfg.jwt_algorithm,
            issuer=cfg.jwt_issuer,
            access_ttl=cfg.access_token_ttl,
            refresh_ttl=cfg.refresh_token_ttl,
        )
    )


def ensure_plugins_on_sys_path() -> None:
    """Make ``${LIBERTY_APPS_DIR}/../plugins/`` importable as a Python source root —
    so a ``python`` step's ``callable = "nomasx1.security:j_x"`` resolves to
    ``<apps-repo>/plugins/nomasx1/security.py`` without the operator wiring sys.path
    by hand (PHASE13 §5.3).

    Customer-specific code (proprietary SQL templates, business orchestration) lives
    in the apps repo, never in the open framework. The framework only provides the
    import hook and the generic primitives (see :mod:`liberty.etl`); the apps repo
    composes them with its private logic.

    Resolution: ``LIBERTY_APPS_DIR`` is the apps repo's ``config/`` subdir by
    convention, so ``plugins/`` is its sibling. When the env var isn't set we fall
    back to ``./plugins/`` relative to cwd (dev shell). The directory is only
    prepended when it exists; idempotent on repeat calls (re-imports / test
    rebuilds of the app)."""
    apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
    plugins = Path(apps).parent / "plugins" if apps else Path("plugins")
    if not plugins.is_dir():
        return
    resolved = str(plugins.resolve())
    if resolved in sys.path:
        return
    # Insert at index 0 so the apps repo's plugin packages win over any same-named
    # site-packages module (an audit trail for "why did my callable resolve to X?").
    sys.path.insert(0, resolved)
    _log.info("liberty.plugins importable from %s", resolved)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    ensure_plugins_on_sys_path()

    # ── Socket.IO ───────────────────────────────────────────────────────────
    # The Socket.IO server is created up-front (outside the lifespan) so we can
    # wrap the FastAPI app in ``socketio.ASGIApp`` below — uvicorn needs the
    # wrapped ASGI app as its entry point, and that's a sync operation that runs
    # before lifespan. The TokenService is also built early because the SIO
    # connect handler needs it for JWT verification.
    #
    # ``LibertySio`` registers its event handlers in __init__ and reads
    # ``self.app.state`` lazily inside the dashboard / log paths, so it's safe to
    # construct here even though ``app.state.connectors`` / .screens / .license
    # don't exist yet — they're populated by the lifespan before any client can
    # actually subscribe.
    token_service = _build_token_service(settings.auth)
    from liberty.sio import LibertySio
    # ``app`` itself doesn't exist yet — we'll set ``sio_layer.app`` once FastAPI
    # is constructed (a couple of lines down).
    sio_layer = LibertySio.__new__(LibertySio)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.license = verify_license(settings.license.key, master_key=settings.crypto.master_key)
        if app.state.license.error and settings.license.key.strip():
            logging.getLogger("liberty.licensing").warning("license: %s", app.state.license.error)
        app.state.connectors = load_connectors(
            settings.connectors.config_path,
            dictionary_path=settings.connectors.dictionary_path,
            master_key=settings.crypto.master_key,
            license=app.state.license,
            default_language=settings.app.default_language,
        )
        app.state.menus = load_menus(settings.menus.config_path)
        app.state.screens = load_screens(settings.screens.config_path)
        app.state.charts = load_charts(settings.charts.config_path)
        app.state.actions = load_actions(settings.actions.config_path)
        app.state.dashboards = load_dashboards(settings.dashboards.config_path)
        app.state.auth_backend = build_auth_backend(settings, app.state.connectors.pools)
        # Change packages — control tables on the configured pool. None when disabled so the route
        # capture hook short-circuits. Schema is created lazily here (idempotent), like nomaflow.
        app.state.changesets_db = None
        if settings.changesets.enabled:
            from liberty.changesets.db import ChangeSetDatabase
            cs_db = ChangeSetDatabase(app.state.connectors.pools, settings.changesets.pool)
            try:
                # Self-bootstrap the control tables (idempotent). Guarded like nomaflow: an
                # unreachable pool skips change-package capture rather than failing app boot.
                await cs_db.create_schema()
                app.state.changesets_db = cs_db
            except Exception as exc:  # noqa: BLE001 — never let a bad control pool take down boot
                _log.warning(
                    "changesets pool %r unusable (%s) — change-package capture disabled this run",
                    settings.changesets.pool, exc,
                )
        app.state.token_service = token_service
        app.state.oidc = build_oidc(settings.oidc, master_key=settings.crypto.master_key)
        app.state.ai = build_assistant(settings.ai, app.state.connectors, master_key=settings.crypto.master_key)
        from liberty.reports.wiring import build_reports
        app.state.reports = build_reports(settings, app.state.license)
        # Log-handler attach against the running loop. The SIO server itself is
        # already initialised + registered with ASGIApp (below) — handlers fire
        # the moment a client connects.
        import asyncio
        sio_layer.attach_log_handler(loop=asyncio.get_running_loop())
        app.state.sio = sio_layer
        # nomaflow (Phase 13a chunk 3): build the registry + runner + scheduler,
        # start the scheduler (recovery sweep runs here, then APScheduler kicks
        # in for cron). app.state.jobs = NomaflowComponents (registry/runner/
        # scheduler/db). Lifespan shutdown stops the scheduler cleanly.
        from liberty.jobs.wiring import build_nomaflow, shutdown_nomaflow
        app.state.jobs = await build_nomaflow(
            settings, app.state.connectors, sio_layer=sio_layer,
            changesets=app.state.changesets_db,
        )
        # Optional filesystem watcher — when ``[app] hot_reload = true``, edits to the
        # config TOMLs reload the matching subsystem without an explicit Settings →
        # Reload click. Started AFTER nomaflow + everything else is in place so handler
        # imports see the live app.state. No-op task when the setting is off.
        from liberty.web.hot_reload import start_watcher
        app.state.hot_reload_task = await start_watcher(app)
        try:
            yield
        finally:
            app.state.hot_reload_task.cancel()
            try:
                await app.state.hot_reload_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — best-effort
                pass
            await shutdown_nomaflow(app.state.jobs)
            await sio_layer.stop()
            if app.state.ai is not None:
                await app.state.ai.aclose()
            await app.state.connectors.aclose()

    # OpenAPI tag metadata — drives the per-section grouping + descriptions ReDoc shows at
    # /redoc. Tags align with the FastAPI router prefixes so every endpoint lands under a
    # named section instead of dumping in "default". Order matters: ReDoc renders sections
    # in the order they appear here, so the operational ones (auth / connectors / screens)
    # surface first and the admin endpoints sit lower.
    #
    # Descriptions are full Markdown — ReDoc renders them as the section intro above each
    # tag's endpoint list. Treat them as the SDK-grade narrative an integrator needs to
    # use the section: auth pattern, common errors, headers, the URL shape, links to
    # related sections. Keep them current — they're load-bearing for /redoc readers who
    # never look at the source.
    openapi_tags = [
        {
            "name": "auth",
            "description": (
                "**Authentication + token issuance.** Liberty issues short-lived JWT access "
                "tokens (default 1 hour) + long-lived refresh tokens (default 14 days). "
                "Every other endpoint that needs a logged-in caller reads the access token "
                "from the ``Authorization: Bearer <token>`` header.\n\n"
                "### Two backends\n"
                "- **TOML** (default) — users in ``config/auth.toml`` (Argon2-hashed). "
                "  Suitable for development + small installs. Bootstrap with "
                "  ``liberty-admin init-db`` (prints the first ``admin`` password).\n"
                "- **DB** — users in the ``ly2_users`` / ``ly2_roles`` tables on the configured "
                "  pool. Pick when you need self-service registration / shared user pool / "
                "  thousands of users.\n\n"
                "### OIDC\n"
                "When ``[oidc] enabled = true`` in app.toml, ``/auth/oidc/login`` redirects to "
                "the configured provider's authorize endpoint; the provider POSTs back to "
                "``/auth/oidc/callback`` which exchanges the code for tokens, upserts the user "
                "in the auth store (provider = ``oidc``), and issues a Liberty access + refresh "
                "pair. Frontend-friendly: set ``[oidc] frontend_redirect`` to redirect the "
                "browser to a SPA route with the tokens in the URL fragment.\n\n"
                "### Common errors\n"
                "- **401 Invalid credentials** — bad username / password.\n"
                "- **401 Invalid token** — access token expired (refresh it) or signature "
                "  invalid (signing key changed; re-login).\n"
                "- **400 Your profile is managed by your identity provider** — OIDC users "
                "  can't change name / email / password here (the provider owns those)."
            ),
        },
        {
            "name": "connectors",
            "description": (
                "**Run SQL queries + call HTTP endpoints.** Both kinds of connector are "
                "configured in ``connectors.toml`` (or shipped via the apps repo); this section "
                "is the runtime surface.\n\n"
                "### URL shape\n"
                "- ``GET /api/sql/<connector>/<query>`` — run a read query.\n"
                "  Query params bind ``:name`` placeholders in the query's SQL: "
                "  ``?USR_APPS_ID=NOMASX1&_limit=100&_sort=USR_ID&_dir=asc``.\n"
                "- ``POST /api/sql/<connector>/<query>`` — run a writable query "
                "  (``writable = true`` in connectors.toml).\n"
                "- ``GET/POST/PUT/DELETE /api/http/<connector>/<endpoint>`` — call an API "
                "  endpoint with the body forwarded verbatim (auth, headers, base URL applied "
                "  by the connector).\n\n"
                "### Reserved query params (SQL)\n"
                "| Param | Meaning |\n"
                "|---|---|\n"
                "| ``_limit`` | Row cap. Falls back to the connector / pool default (1000) when omitted. |\n"
                "| ``_offset`` | Pagination offset. |\n"
                "| ``_sort`` / ``_dir`` | Server-side sort by a result column (``asc`` / ``desc``). |\n"
                "| ``_count`` | Returns just the total row count, not the data. |\n"
                "| ``_stream`` | Server-Sent-Events; each row arrives as a ``data:`` frame. |\n"
                "| ``_filter`` | JSON-encoded filter tree applied server-side. |\n\n"
                "### Permissions\n"
                "- Read query: ``sql:<connector>:<query>`` (or ``*``).\n"
                "- Writable query: ``sql:<connector>:<query>:write`` (or ``*``).\n"
                "- API endpoint: ``api:<connector>:<endpoint>``.\n\n"
                "A user without the permission gets **403 Forbidden** with the missing perm in "
                "the body; the AI assistant's ``list_connectors`` tool returns the same view "
                "the operator sees."
            ),
        },
        {
            "name": "screens",
            "description": (
                "**Screen rendering — the React TableView entry point.** Returns a merged "
                "object combining the screen definition (``screens.toml`` entry), the read "
                "query's result columns + rows, and dictionary metadata resolved for the "
                "request's language (``Accept-Language`` / ``X-Liberty-Lang`` header).\n\n"
                "### Lifecycle\n"
                "1. ``GET /api/screens`` — list every screen the current user can see (the "
                "   app switcher + the workspace menu read from here).\n"
                "2. ``GET /api/screens/<app>`` — narrow to one app's screens.\n"
                "3. ``GET /api/screens/<app>/<screen_id>`` — the full screen object: query "
                "   result, resolved column hints, dialog (form) field list, prompt fields, "
                "   action / row-menu chains. The frontend stays on this single GET as long "
                "   as the screen is open; subsequent loads of the screen's own data hit "
                "   ``/api/sql/<connector>/<query>`` directly.\n\n"
                "### Permission gating\n"
                "The user must hold ``sql:<connector>:<query>`` (or ``*``) where ``query`` is "
                "the screen's ``read_query``. Without it the screen is hidden from "
                "``GET /api/screens`` AND ``GET /api/screens/<app>/<screen_id>`` returns 403."
            ),
        },
        {
            "name": "menus",
            "description": (
                "**App switcher + per-app menu trees.** Each app's menu lives in "
                "``[menus.<app>]`` in menus.toml — a flat list of menu items linked by "
                "``parent`` ids. The runtime resolves the tree + filters out items the "
                "user can't see (each leaf menu item gates on the permission of its target "
                "query / screen / dashboard).\n\n"
                "### Endpoints\n"
                "- ``GET /api/menus`` — every app menu the user can see, keyed by app name. "
                "  Each entry carries the resolved tree + the connector's ``home_path`` (the "
                "  default landing page for that app) + ``show_in_switcher`` (whether the app "
                "  appears in the workspace selector).\n"
                "- ``GET /api/menus/<app>`` — narrow to a single app's menu.\n\n"
                "### Visibility rules\n"
                "An item is **hidden** when:\n"
                "1. Its target query / screen / dashboard isn't loadable.\n"
                "2. The user lacks the corresponding permission.\n"
                "3. The item carries explicit ``roles = [...]`` and the user isn't in any of them.\n\n"
                "Folders (no ``type``) are auto-hidden when every child is hidden."
            ),
        },
        {
            "name": "charts",
            "description": (
                "**Saved chart specs.** Operators save chart configurations from the TableView's "
                "Chart toggle (the floppy icon). Saved charts can be referenced from screens "
                "(``Screen.chart_id``) and dashboard widgets (chart-ref widget mode).\n\n"
                "### Endpoint\n"
                "- ``GET /api/charts/<scope>/<id>`` — one chart's metadata: connector, query, "
                "  spec (axes / series / colours / aggregation).\n\n"
                "Charts are scoped under their owning connector — ``[charts.<connector>.<id>]`` — "
                "and inherit the connector's permission gate. A user who can't read the chart's "
                "underlying query can't load the chart either."
            ),
        },
        {
            "name": "dashboards",
            "description": (
                "**Dashboards — widget grids with shared filters.** Each dashboard is a list "
                "of widgets (chart / kpi / table) plus optional dashboard-level filters whose "
                "picked value re-fetches every applicable widget with the bind applied.\n\n"
                "### URL\n"
                "``/dashboard/<id>`` in the SPA resolves to ``GET /api/dashboards/<id>`` which "
                "returns the dashboard's widget list. Each widget then issues its own "
                "``/api/sql/<connector>/<query>`` to populate.\n\n"
                "### Widget kinds\n"
                "- **chart** — chart-ref (id of a saved chart) OR inline (connector + query + spec)\n"
                "- **kpi** — connector + query + aggregation column (sum / avg / count / min / max)\n"
                "- **table** — connector + query + optional column subset"
            ),
        },
        {
            "name": "license",
            "description": (
                "**License status.** Liberty's open framework is free; licensed connectors "
                "(apps repos like ``nomasx1`` / ``nomajde``) require a valid RS256 JWT license "
                "key set via ``LIBERTY_LICENSE_KEY``. Without a key those connectors aren't "
                "loaded (the framework runs in **restricted** mode).\n\n"
                "### Endpoint\n"
                "- ``GET /api/license`` — public (no auth). Returns ``{mode, customer, plan, "
                "  apps, expires_at, error}``. ``mode`` is one of:\n"
                "  - ``full`` — key is valid, licensed connectors are loaded.\n"
                "  - ``restricted`` — no key OR key is invalid (``error`` carries the reason: "
                "    expired / signature mismatch / decode error).\n\n"
                "Public on purpose — license info isn't secret + the SPA needs to know "
                "which licensed connectors to expect before showing the sign-in page."
            ),
        },
        {
            "name": "theme",
            "description": (
                "**Per-deployment branding.** Colours, primary font, app name. Loaded from "
                "``config/theme.toml`` and resolved on every ``GET /api/theme`` so the Sign-In "
                "page renders branded **before** the user logs in.\n\n"
                "### Endpoint\n"
                "- ``GET /api/theme`` — public (no auth). Returns the resolved CSS custom "
                "  properties + the app name. The SPA applies them at boot.\n\n"
                "Edit the theme in **Settings → Theme** (the structured editor). Changes are "
                "live for every new request — no reload needed."
            ),
        },
        {
            "name": "ai",
            "description": (
                "**Anthropic-backed chat assistant.** Streaming chat over Anthropic's Messages "
                "API with a tool-use loop. The assistant can:\n\n"
                "1. **List connectors** + their queries / endpoints (``list_connectors``).\n"
                "2. **Run** read-only SQL queries (``sql_query``) or call API endpoints "
                "   (``api_call`` — opt-in, ``[ai] api_tool = true``).\n"
                "3. **Scaffold** new config (``[ai] scaffold_tools = true``): introspect a "
                "   table, propose CRUD queries, propose dictionary entries, propose screens, "
                "   propose menu items. The tools return **proposals** that the chat UI surfaces "
                "   as Apply cards — they never write directly.\n\n"
                "### Endpoints\n"
                "- ``GET /ai/tools`` — debug surface listing the tools currently exposed. "
                "  Includes the model + whether the assistant is available (key wired in).\n"
                "- ``POST /ai/chat`` — Server-Sent-Events stream. Body: standard Messages-API "
                "  shape (``{messages: [{role, content}, ...]}``). Each ``data:`` frame is one "
                "  ``ChatEvent``:\n\n"
                "  | Type | Meaning |\n"
                "  |---|---|\n"
                "  | ``token`` | One streamed text delta from the assistant. |\n"
                "  | ``thinking`` | Adaptive-thinking delta (only when ``thinking = true``). |\n"
                "  | ``tool_call`` | Assistant is about to call ``name(input)`` — UI shows a pending row. |\n"
                "  | ``tool_result`` | Tool returned ``ok / summary`` — UI flips the row icon. |\n"
                "  | ``proposal`` | Scaffold tool returned a Proposal envelope ready for Apply. |\n"
                "  | ``error`` | Anthropic / tool error — terminates the stream. |\n"
                "  | ``done`` | Stream finished cleanly — ``stop_reason`` + ``usage`` totals. |"
            ),
        },
        {
            "name": "nomaflow",
            "description": (
                "**Nomaflow — config-driven ETL + scheduler.** Jobs are defined in "
                "``jobs.toml`` (each ``[[jobs]]`` block: id + cron schedule + step list). Steps "
                "run in order; results from each step are available to later steps via the "
                "chain context. Step kinds: ``sql_copy`` / ``sql_query`` / ``python`` / "
                "``ldap_sync`` / ``http`` / ``call_job``.\n\n"
                "### Endpoints\n"
                "- ``GET /admin/jobs`` — job catalogue with last-run + next-fire annotations.\n"
                "- ``POST /admin/jobs/<id>/run`` — fire a single job manually (with optional "
                "  per-fire overrides via the Run-with-parameters modal).\n"
                "- ``GET /admin/jobs/runs`` — per-run history (the ``nomaflow_job_runs`` table).\n"
                "- ``GET /admin/jobs/runs/<run_id>`` — one run's detail (per-step rows + log).\n"
                "- ``POST /admin/jobs/runs/<run_id>/cancel`` — request cancellation.\n\n"
                "### Operational surface\n"
                "The catalogue, schedule, retention policy and per-job presets all edit through "
                "the **/nomaflow** tab in the SPA (a dedicated workspace under Settings)."
            ),
        },
        {
            "name": "meta",
            "description": (
                "**Liveness + runtime info.** Public — used by Docker `HEALTHCHECK`, load "
                "balancers, and operators checking install state at a glance. `/health` is the "
                "fast probe (returns immediately); `/info` includes connector / screen / pool "
                "counts + AI assistant status + license mode."
            ),
        },
        {
            "name": "admin",
            "description": (
                "**Operator-only — superuser permission required.** Everything in this section "
                "needs the caller to be a superuser. Surfaces the structured-config CRUD, "
                "deployment packaging, cross-file rename, dependency walks, AI assistant "
                "proposal apply, and runtime reload.\n\n"
                "### Major flows\n"
                "- **Config CRUD** — ``GET /admin/config/<kind>/parsed`` + "
                "  ``PUT /admin/config/<kind>/parsed`` for every TOML section "
                "  (pools / connectors / dictionary / menus / screens / charts / dashboards / "
                "  theme / jobs / app). Validated against the matching Pydantic model on write.\n"
                "- **Find usages** — ``POST /admin/find-usages`` walks the live registries for "
                "  every reference to a named entity. Drives the per-editor 'Find usages' button.\n"
                "- **Dependencies** — ``POST /admin/find-dependencies`` computes the transitive "
                "  closure of a seed (screen / dashboard / chart / menu item). "
                "  ``POST /admin/build-package`` serializes the closure into a ZIP for "
                "  cross-install deployment. ``POST /admin/import-package`` applies it with a "
                "  ``merge`` / ``overwrite`` / ``replace_all`` strategy — ``overwrite`` "
                "  respects per-entity ``override = true`` flags so customer customisations "
                "  survive vendor upgrades.\n"
                "- **Clone with deps** — ``POST /admin/clone-with-deps`` duplicates a screen / "
                "  chart / dashboard and optionally the queries it references, rewriting refs "
                "  on the cloned entity to point at the new query names.\n"
                "- **Delete with deps** — ``POST /admin/delete-with-deps`` (+ ``/preview``) "
                "  removes a screen / chart / dashboard and optionally the queries it "
                "  exclusively owns (the planner preserves any query referenced from elsewhere).\n"
                "- **AI assistant proposals** — ``POST /admin/config/apply-proposal`` applies a "
                "  Proposal returned by a scaffold tool through the same parsed-config write "
                "  paths the structured editors use.\n"
                "- **Rename** — ``POST /admin/config/rename`` performs cross-file id renames "
                "  (connector / query / table / lookup / sequence / dictionary entry / screen "
                "  app), rewriting every reference in screens.toml / dictionary.toml / etc.\n"
                "- **Reload** — ``POST /admin/reload`` rebuilds the live registries from disk "
                "  (connectors / menus / screens / charts / dashboards / dictionary), swaps "
                "  them into ``app.state``, disposes the old ones. Hot-reload (``[app] "
                "  hot_reload = true``) watches files + reloads automatically on changes.\n\n"
                "### Auth\n"
                "Every endpoint here requires ``Authorization: Bearer <token>`` + the caller's "
                "``is_superuser`` flag. Non-superusers get 403."
            ),
        },
    ]
    app = FastAPI(
        title="Liberty Next",
        version=__version__,
        lifespan=lifespan,
        description=(
            "**Connector-driven low-code framework.** Configure SQL queries + HTTP endpoints in "
            "TOML, the framework derives schemas at query time, serves a React admin UI on the "
            "same port, surfaces an Anthropic tool-use assistant for natural-language access, and "
            "wraps everything in a structured-config builder + dependency-aware deployment "
            "packager (`Settings → Package`). See the README for install and the Liberty Apps "
            "repo for the customer-facing nomasx1 / nomajde / nomaflow packages."
        ),
        contact={"name": "Liberty Next", "url": "https://github.com/fblettner/liberty-next"},
        license_info={"name": "Open framework (licensed connectors sold separately)"},
        openapi_tags=openapi_tags,
        docs_url="/docs",      # Swagger UI — interactive
        redoc_url="/redoc",    # ReDoc — print-friendly + better description rendering
    )
    # Finish wiring the Socket.IO layer now that the FastAPI app exists. This
    # runs at create_app time (before the lifespan), so `LibertySio.__init__`
    # registers the event handlers on a real ``AsyncServer`` that's about to be
    # mounted onto the ASGI wrapper below. ``self.app.state`` reads inside the
    # dashboard / log handlers happen lazily (only when a client subscribes), so
    # missing state at construction time is fine — the lifespan populates it
    # before any client can actually connect.
    LibertySio.__init__(sio_layer, app, token_service)

    if settings.oidc.enabled:
        # Authlib's Starlette client stashes the OAuth state/nonce in the session.
        session_secret = (
            settings.oidc.session_secret or settings.auth.jwt_secret or secrets.token_urlsafe(32)
        )
        app.add_middleware(
            SessionMiddleware, secret_key=session_secret, same_site="lax", https_only=False
        )

    @app.exception_handler(ConnectorError)
    async def _connector_error_handler(_request: Request, exc: ConnectorError) -> JSONResponse:
        # Safety net for connector problems that aren't caught per-route (e.g. an
        # unconfigured DB pool surfacing on /auth/login) — a clean 503, not a stack trace.
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    app.include_router(auth_router)
    app.include_router(connectors_router)
    app.include_router(menus_router)
    app.include_router(screens_router)
    app.include_router(export_router)
    app.include_router(charts_router)
    app.include_router(actions_router)
    app.include_router(dashboards_router)
    app.include_router(license_router)
    app.include_router(theme_router)
    app.include_router(ai_router)
    app.include_router(admin_router)
    app.include_router(access_router)
    app.include_router(dictgen_router)
    app.include_router(jobs_router)
    app.include_router(plugins_router)
    app.include_router(reports_router)
    app.include_router(changesets_router)

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/info", tags=["meta"], summary="Runtime info")
    async def info() -> dict[str, object]:
        s: Settings = app.state.settings
        connectors: ConnectorRegistry = app.state.connectors
        ai = app.state.ai
        return {
            "name": s.app.name,
            "version": __version__,
            "connectors_loaded": len(connectors),
            "connectors": connectors.names(),
            "pools": connectors.pools.names(),
            "dictionary": {
                "entries": len(connectors.dictionary.entries),
                "default_language": s.app.default_language,
            },
            "menus": {"apps": list(app.state.menus.menus)},
            "screens": {
                "apps": list(app.state.screens.screens),
                "total": sum(len(scr) for scr in app.state.screens.screens.values()),
            },
            "charts": {"total": sum(len(by_id) for by_id in app.state.charts.charts.values())},
            "dashboards": {"total": sum(len(by_id) for by_id in app.state.dashboards.dashboards.values())},
            "auth": {
                "backend": s.auth.backend,
                **({"pool": s.auth.pool} if s.auth.backend == "db" else {"toml": str(s.auth.toml_path)}),
                "oidc_enabled": app.state.oidc is not None,
            },
            "ai": {
                "enabled": ai is not None,
                "available": ai.available if ai is not None else False,
                "model": ai.settings.model if ai is not None else None,
            },
            "crypto": {"configured": bool(s.crypto.master_key)},
            "license": {"mode": app.state.license.mode},
            "frontend": getattr(app.state, "frontend_dir", None),
        }

    # Serve the built frontend last, so API routes always win.
    app.state.frontend_dir = None
    if settings.app.static_dir:
        dist = Path(settings.app.static_dir)
        if (dist / "index.html").is_file():
            app.state.frontend_dir = str(dist)
            app.mount("/", SPAStaticFiles(directory=dist, html=True), name="spa")

    # ── Socket.IO ASGI wrapping ─────────────────────────────────────────────
    # Wrap the FastAPI app with python-socketio's ASGI shim — every request whose
    # path starts with ``/socket.io/`` routes to the SIO engine; everything else
    # falls through to FastAPI. The lifespan + dependency-injection machinery
    # *stays on the FastAPI app*; the wrapper is transparent to those.
    #
    # We expose the wrapped app on ``app.asgi_app`` (a regular attribute) so
    # uvicorn's ``liberty.main:asgi_app`` entry point picks it up. Tests that
    # already call ``create_app(...)`` and use the returned FastAPI app keep
    # working unchanged — they hit the FastAPI surface directly (no SIO).
    from liberty.sio import make_asgi_app
    app.asgi_app = make_asgi_app(sio_layer.sio, app)   # type: ignore[attr-defined]

    return app


app = create_app()
# ``asgi_app`` is the wrapped Socket.IO + FastAPI composition. uvicorn / start.sh
# point at this so the ``/socket.io/*`` path routes through python-socketio's
# Engine.IO server, and everything else falls through to FastAPI.
asgi_app = app.asgi_app   # type: ignore[attr-defined]


def _setup_app_logging(level: str) -> None:
    """Give the ``liberty`` logger tree its own stdout handler.

    uvicorn's logging config only wires up the ``uvicorn`` loggers — it never
    adds a handler to the root logger. So application logs (``liberty.*``,
    including ``liberty.jobs.*``) propagate to a handler-less root and Python's
    last-resort handler drops everything below WARNING. Result: a running job's
    ``_log.info`` progress lines are invisible.

    The *logger* is pinned at INFO so INFO records always reach the attached
    handlers — the nomaflow run-log capture (:mod:`liberty.jobs.runlog`) depends
    on that. The *stdout handler* carries the operator's configured ``log_level``,
    so a deployment that sets ``log_level = "warning"`` still gets a quiet
    console while the run-log buffer keeps capturing INFO. ``propagate = False``
    avoids a double emit via root."""
    lg = logging.getLogger("liberty")
    if lg.level == logging.NOTSET or lg.level > logging.INFO:
        lg.setLevel(logging.INFO)
    lg.propagate = False
    # Skip if a stdout handler is already attached (the run-log capture handler
    # is a plain logging.Handler, not a StreamHandler, so it doesn't count).
    if any(isinstance(h, logging.StreamHandler) for h in lg.handlers):
        return
    handler = logging.StreamHandler()
    handler.setLevel(level.upper())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s — %(message)s", datefmt="%H:%M:%S",
    ))
    lg.addHandler(handler)


def run() -> None:
    import uvicorn

    settings = load_settings()
    _setup_app_logging(settings.app.log_level)
    uvicorn.run(
        "liberty.main:asgi_app",
        host=settings.app.host,
        port=settings.app.port,
        log_level=settings.app.log_level,
    )
