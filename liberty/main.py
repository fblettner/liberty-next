from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from liberty import __version__
from liberty.auth.db import AuthDatabase
from liberty.auth.oidc import build_oidc
from liberty.auth.routes import router as auth_router
from liberty.auth.tokens import TokenConfig, TokenService
from liberty.config import AuthSettings, Settings, load_settings
from liberty.connectors import ConnectorRegistry, load_connectors

_log = logging.getLogger("liberty")


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
        app.state.connectors = load_connectors(settings.connectors.config_path)
        app.state.auth_db = AuthDatabase(app.state.connectors.pools, settings.auth.pool)
        app.state.token_service = _build_token_service(settings.auth)
        app.state.oidc = build_oidc(settings.oidc)
        try:
            yield
        finally:
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

    app.include_router(auth_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/info")
    async def info() -> dict[str, object]:
        s: Settings = app.state.settings
        connectors: ConnectorRegistry = app.state.connectors
        return {
            "name": s.app.name,
            "version": __version__,
            "connectors_loaded": len(connectors),
            "connectors": connectors.names(),
            "pools": connectors.pools.names(),
            "auth": {"pool": s.auth.pool, "oidc_enabled": app.state.oidc is not None},
        }

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
