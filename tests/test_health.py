from __future__ import annotations

from fastapi.testclient import TestClient

from liberty.main import app


def test_health() -> None:
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_info() -> None:
    with TestClient(app) as client:
        r = client.get("/info")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Liberty v2"
        assert body["connectors_loaded"] == 0


def test_config_load() -> None:
    from liberty.config import load_settings

    settings = load_settings("config/app.toml")
    assert settings.app.name == "Liberty v2"
    assert settings.app.port == 8000
