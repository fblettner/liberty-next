"""``/api/theme`` (public) + ``/admin/config/theme/parsed`` (superuser) route tests."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AppSettings, AuthSettings, ConnectorSettings, Settings, ThemeSettings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-theme-test-secret"


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("user", password="userpw")
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(f'[pools.default]\nurl = "{db_url}"\n')
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        theme=ThemeSettings(config_path=Path(tmp_path / "theme.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
    )
    return create_app(settings)


def _h(client: TestClient, username: str) -> dict[str, str]:
    tok = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_get_theme_is_public_and_default(app) -> None:
    with TestClient(app) as client:
        r = client.get("/api/theme")  # no auth header
        assert r.status_code == 200
        body = r.json()
        assert body["preset"] == "default"
        assert body["app_name"] is None
        assert body["vars"]["blue-main"] == "#007AFF"
        assert any(p["id"] == "ocean" for p in body["presets"])
        # font choices are exposed for the editor dropdown; default emits no font override
        assert any(f["id"] == "dm-sans" for f in body["fonts"])
        assert "font-sans" not in body["vars"] and "font-scale" not in body["vars"]


def test_admin_put_font_then_public_get_reflects_it(app) -> None:
    with TestClient(app) as client:
        r = client.put(
            "/admin/config/theme/parsed",
            json={"theme": {"preset": "default", "font_family": "inter", "font_scale": 1.1}},
            headers=_h(client, "admin"),
        )
        assert r.status_code == 200 and r.json()["saved"] is True
        body = client.get("/api/theme").json()
        assert body["vars"]["font-sans"].startswith("'Inter'")
        assert body["vars"]["font-scale"] == "1.1"


def test_admin_get_requires_superuser(app) -> None:
    with TestClient(app) as client:
        assert client.get("/admin/config/theme/parsed").status_code == 401
        assert client.get("/admin/config/theme/parsed", headers=_h(client, "user")).status_code == 403
        assert client.get("/admin/config/theme/parsed", headers=_h(client, "admin")).status_code == 200


def test_admin_put_then_public_get_reflects_it(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.put(
            "/admin/config/theme/parsed",
            json={"theme": {"preset": "ocean", "app_name": "Acme", "primary_color": "#ff8800"}},
            headers=h,
        )
        assert r.status_code == 200 and r.json()["saved"] is True
        # The saved theme is live on the public endpoint (no reload step).
        body = client.get("/api/theme").json()
        assert body["preset"] == "ocean"
        assert body["app_name"] == "Acme"
        assert body["vars"]["blue-main"] == "#ff8800"
        assert body["vars"]["blue-bg"] == "rgba(255,136,0,0.15)"


def test_admin_put_rejects_unknown_keys(app) -> None:
    with TestClient(app) as client:
        r = client.put(
            "/admin/config/theme/parsed",
            json={"theme": {"preset": "default", "bogus": True}},
            headers=_h(client, "admin"),
        )
        assert r.status_code == 422


def test_admin_put_requires_superuser(app) -> None:
    with TestClient(app) as client:
        r = client.put(
            "/admin/config/theme/parsed",
            json={"theme": {"preset": "ocean"}},
            headers=_h(client, "user"),
        )
        assert r.status_code == 403
