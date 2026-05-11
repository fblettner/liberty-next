from __future__ import annotations

import logging
import secrets
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
from liberty.auth.db import AuthDatabase
from liberty.auth.oidc import build_oidc
from liberty.auth.routes import router as auth_router
from liberty.auth.tokens import TokenConfig, TokenService
from liberty.config import AuthSettings, Settings, load_settings
from liberty.connectors import ConnectorRegistry, load_connectors
from liberty.connectors.base import ConnectorError
from liberty.menus import load_menus
from liberty.web import admin_router, connectors_router, menus_router

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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.connectors = load_connectors(
            settings.connectors.config_path,
            dictionary_path=settings.connectors.dictionary_path,
            master_key=settings.crypto.master_key,
        )
        app.state.menus = load_menus(settings.menus.config_path)
        app.state.auth_db = AuthDatabase(app.state.connectors.pools, settings.auth.pool)
        app.state.token_service = _build_token_service(settings.auth)
        app.state.oidc = build_oidc(settings.oidc)
        app.state.ai = build_assistant(settings.ai, app.state.connectors)
        try:
            yield
        finally:
            if app.state.ai is not None:
                await app.state.ai.aclose()
            await app.state.connectors.aclose()

    app = FastAPI(title="Liberty v2", version=__version__, lifespan=lifespan)

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
    app.include_router(ai_router)
    app.include_router(admin_router)

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
            "auth": {"pool": s.auth.pool, "oidc_enabled": app.state.oidc is not None},
            "ai": {
                "enabled": ai is not None,
                "available": ai.available if ai is not None else False,
                "model": ai.settings.model if ai is not None else None,
            },
            "crypto": {"configured": bool(s.crypto.master_key)},
            "frontend": getattr(app.state, "frontend_dir", None),
        }

    # Serve the built frontend last, so API routes always win.
    app.state.frontend_dir = None
    if settings.app.static_dir:
        dist = Path(settings.app.static_dir)
        if (dist / "index.html").is_file():
            app.state.frontend_dir = str(dist)
            app.mount("/", SPAStaticFiles(directory=dist, html=True), name="spa")

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run(
        "liberty.main:app",
        host=settings.app.host,
        port=settings.app.port,
        log_level=settings.app.log_level,
    )
