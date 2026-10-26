"""Liveness endpoints — built from an explicit test ``Settings``, never from the
module-level ``liberty.main.app``.

The module-level app's lifespan reads whatever ``config/connectors.toml`` /
``LIBERTY_APPS_DIR`` the machine has — on a developer box that is the real
deployment config (an ``ENC:`` pool password with no master key in the shell), so
the test used to fail there and only passed in CI by *absence* of a config. Every
config path here points at a temp dir and the framework pool is a throw-away
SQLite file, so the suite is green on any machine for the right reason.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.config import (
    ActionSettings,
    AISettings,
    AppSettings,
    AuthSettings,
    ChartSettings,
    ConnectorSettings,
    DashboardSettings,
    JobsSettings,
    MenuSettings,
    ScreenSettings,
    Settings,
    ThemeSettings,
)
from liberty.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    (tmp_path / "connectors.toml").write_text(
        textwrap.dedent(
            f"""
            [pools.default]
            url = "sqlite+aiosqlite:///{tmp_path / 'framework.db'}"
            """
        )
    )
    return Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=tmp_path / "connectors.toml"),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        actions=ActionSettings(config_path=tmp_path / "actions.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        theme=ThemeSettings(config_path=tmp_path / "theme.toml"),
        auth=AuthSettings(backend="toml", toml_path=tmp_path / "auth.toml", jwt_secret="test-secret"),
        ai=AISettings(enabled=False),
        jobs=JobsSettings(config_path=tmp_path / "jobs.toml"),
    )


def test_health(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_info(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        r = client.get("/info")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Liberty Next"
        assert body["connectors_loaded"] == len(body["connectors"])
        assert body["pools"] == ["default"]
        assert body["auth"]["backend"] == "toml"


def test_config_load() -> None:
    """The committed ``config/app.toml`` still parses into the defaults we ship."""
    from liberty.config import load_settings

    loaded = load_settings("config/app.toml")
    assert loaded.app.name == "Liberty Next"
    assert loaded.app.port == 8000
