"""``liberty.runtime.rebuild_runtime`` — the single reload path (A3.1).

The regression these pin: a reload used to swap ``app.state.connectors`` but leave the
change-package DB, the saved-view store, the nomaflow job DB and (on the watcher path)
the auth backend holding the **old** ``PoolRegistry`` — whose ``dispose()`` only cleared
its engine cache, so the next use silently rebuilt an engine from the superseded config.

Coroutines run through ``client.portal`` (the TestClient's own event loop) because the
app's aiosqlite engines are bound to that loop.
"""

from __future__ import annotations

import asyncio
import textwrap
from functools import partial
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
from liberty.connectors.db import PoolRegistry, PoolRegistryClosedError
from liberty.main import create_app
from liberty.runtime import ALL_SECTIONS, rebuild_runtime


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
    (tmp_path / "jobs.toml").write_text("")
    return Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=tmp_path / "connectors.toml"),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        actions=ActionSettings(config_path=tmp_path / "actions.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        theme=ThemeSettings(config_path=tmp_path / "theme.toml"),
        auth=AuthSettings(backend="db", toml_path=tmp_path / "auth.toml", jwt_secret="test-secret"),
        ai=AISettings(enabled=False),
        jobs=JobsSettings(config_path=tmp_path / "jobs.toml", enabled=False),
    )


# ── PoolRegistry is terminal after dispose ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_disposed_pool_registry_refuses_to_resurrect_engines(tmp_path: Path) -> None:
    from liberty.connectors.config import PoolConfig

    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")})
    assert pools.engine("default") is not None and not pools.closed
    await pools.dispose()
    assert pools.closed
    with pytest.raises(PoolRegistryClosedError):
        pools.engine("default")  # used to quietly build a fresh engine from the old config


# ── a full reload re-points every pool holder ────────────────────────────────────────


def test_full_rebuild_rebinds_every_pool_holder(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        state = app.state
        old = state.connectors
        old_pools = old.pools
        assert state.jobs is not None, "nomaflow should start on the sqlite default pool"
        assert state.jobs.db._pools is old_pools  # noqa: SLF001 — precondition
        assert state.user_views is not None and state.user_views._pools is old_pools  # noqa: SLF001
        assert state.auth_backend.db._pools is old_pools  # noqa: SLF001 — db backend

        result = client.portal.call(rebuild_runtime, app)

        new = state.connectors
        assert new is not old and result.connectors is new
        assert result.sections == set(ALL_SECTIONS)
        # Every holder now points at the NEW registry's pools …
        assert state.jobs.db._pools is new.pools  # noqa: SLF001
        assert state.user_views._pools is new.pools  # noqa: SLF001
        assert state.auth_backend.db._pools is new.pools  # noqa: SLF001
        if state.changesets_db is not None:
            assert state.changesets_db._pools is new.pools  # noqa: SLF001
        # … and the old registry is terminal, not resurrectable.
        assert old_pools.closed
        with pytest.raises(PoolRegistryClosedError):
            old_pools.engine("default")
        # The rebound holders actually work on the new registry (sessionmaker cache dropped).
        client.portal.call(state.user_views.create_schema)
        client.portal.call(state.jobs.db.create_schema)


def test_partial_rebuild_leaves_connectors_alone(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        state = app.state
        old = state.connectors
        result = client.portal.call(partial(rebuild_runtime, app, sections={"menus", "screens"}))
        assert state.connectors is old and not old.pools.closed
        assert result.connectors is None and result.sections == {"menus", "screens"}


def test_dispose_old_false_keeps_the_previous_registry_open(settings: Settings) -> None:
    """The connectors.toml watcher's pool-change branch: in-flight queries may still be
    using the old engines, so the old registry must stay usable — but every holder on
    app.state moves to the new one regardless."""
    app = create_app(settings)
    with TestClient(app) as client:
        state = app.state
        old = state.connectors
        client.portal.call(partial(rebuild_runtime, app, sections={"connectors"}, dispose_old=False))
        assert state.connectors is not old
        assert not old.pools.closed
        assert old.pools.engine("default") is not None
        assert state.jobs.db._pools is state.connectors.pools  # noqa: SLF001
        client.portal.call(old.aclose)


def test_unknown_section_is_rejected(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        with pytest.raises(ValueError):
            client.portal.call(partial(rebuild_runtime, app, sections={"nope"}))


def test_concurrent_rebuilds_serialise_under_the_lock(settings: Settings) -> None:
    """Two reloads racing (a Settings save + the file watcher) must not dispose each
    other's registry: whichever runs second sees — and replaces — the first's result,
    and the registry left on app.state is open with every holder bound to it."""
    app = create_app(settings)
    with TestClient(app) as client:
        state = app.state

        async def race() -> None:
            await asyncio.gather(rebuild_runtime(app), rebuild_runtime(app, sections={"connectors"}))

        client.portal.call(race)
        assert not state.connectors.pools.closed
        assert state.jobs.db._pools is state.connectors.pools  # noqa: SLF001
        assert state.auth_backend.db._pools is state.connectors.pools  # noqa: SLF001


def test_admin_reload_route_goes_through_rebuild(settings: Settings) -> None:
    """``POST /admin/reload`` keeps its reply shape and leaves the state fully rebound."""
    app = create_app(settings)
    with TestClient(app) as client:
        state = app.state
        old_pools = state.connectors.pools
        client.portal.call(state.auth_backend.ready)
        client.portal.call(partial(state.auth_backend.create_user, "admin", password="adminpw", is_superuser=True))
        tok = client.post("/auth/login", json={"username": "admin", "password": "adminpw"}).json()["access_token"]
        r = client.post("/admin/reload", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["reloaded"] is True and body["pools"] == ["default"]
        assert {"connectors", "nomaflow", "reports", "license_mode", "menu_apps"} <= set(body)
        assert old_pools.closed
        assert state.jobs.db._pools is state.connectors.pools  # noqa: SLF001
        # The rebound auth backend still serves logins on the new registry.
        assert client.post("/auth/login", json={"username": "admin", "password": "adminpw"}).status_code == 200
