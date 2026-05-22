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
        assert body["name"] == "Liberty Next"
        # Reflects whatever connector config the lifespan loaded — could be empty if
        # there's no local config/connectors.toml AND no LIBERTY_APPS_DIR pointing at
        # one (the fresh-checkout case). Verify the structural invariants, not a
        # specific pool set.
        assert body["connectors_loaded"] == len(body["connectors"])
        assert isinstance(body["pools"], list)
        assert body["auth"]["backend"] in ("toml", "db")  # config/app.toml ships "toml"


def test_config_load() -> None:
    from liberty.config import load_settings

    settings = load_settings("config/app.toml")
    assert settings.app.name == "Liberty Next"
    assert settings.app.port == 8000
