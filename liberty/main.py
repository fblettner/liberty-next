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
from liberty.charts import load_charts
from liberty.dashboards import load_dashboards
from liberty.menus import load_menus
from liberty.screens import load_screens
from liberty.web import (
    admin_router,
    charts_router,
    connectors_router,
    dashboards_router,
    export_router,
    jobs_router,
    license_router,
    menus_router,
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


def _ensure_plugins_on_sys_path() -> None:
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
    _ensure_plugins_on_sys_path()

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
        app.state.license = verify_license(settings.license.key)
        if app.state.license.error and settings.license.key.strip():
            logging.getLogger("liberty.licensing").warning("license: %s", app.state.license.error)
        app.state.connectors = load_connectors(
            settings.connectors.config_path,
            dictionary_path=settings.connectors.dictionary_path,
            master_key=settings.crypto.master_key,
            license=app.state.license,
        )
        app.state.menus = load_menus(settings.menus.config_path)
        app.state.screens = load_screens(settings.screens.config_path)
        app.state.charts = load_charts(settings.charts.config_path)
        app.state.dashboards = load_dashboards(settings.dashboards.config_path)
        app.state.auth_backend = build_auth_backend(settings, app.state.connectors.pools)
        app.state.token_service = token_service
        app.state.oidc = build_oidc(settings.oidc)
        app.state.ai = build_assistant(settings.ai, app.state.connectors)
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

    app = FastAPI(title="Liberty Next", version=__version__, lifespan=lifespan)
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
    app.include_router(dashboards_router)
    app.include_router(license_router)
    app.include_router(theme_router)
    app.include_router(ai_router)
    app.include_router(admin_router)
    app.include_router(jobs_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/info")
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
                "default_language": connectors.dictionary.default_language,
            },
            "menus": {"apps": list(app.state.menus.menus)},
            "screens": {
                "apps": list(app.state.screens.screens),
                "total": sum(len(scr) for scr in app.state.screens.screens.values()),
            },
            "charts": {"total": len(app.state.charts.charts)},
            "dashboards": {"total": len(app.state.dashboards.dashboards)},
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
