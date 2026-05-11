from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from liberty import __version__
from liberty.config import Settings, load_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = load_settings()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Liberty v2", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/info")
    async def info() -> dict[str, object]:
        settings: Settings = app.state.settings
        return {
            "name": settings.app.name,
            "version": __version__,
            "connectors_loaded": 0,
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
