from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from liberty import __version__
from liberty.config import Settings, load_settings
from liberty.connectors import ConnectorRegistry, load_connectors


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.connectors = load_connectors(settings.connectors.config_path)
    try:
        yield
    finally:
        await app.state.connectors.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Liberty v2", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/info")
    async def info() -> dict[str, object]:
        settings: Settings = app.state.settings
        connectors: ConnectorRegistry = app.state.connectors
        return {
            "name": settings.app.name,
            "version": __version__,
            "connectors_loaded": len(connectors),
            "connectors": connectors.names(),
            "pools": connectors.pools.names(),
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
