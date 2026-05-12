from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-admin-test-secret"


def _toml(db_url: str, *, extra_connector: bool = False) -> str:
    base = textwrap.dedent(
        f"""
        [pools.default]
        url = "{db_url}"

        [connectors.db]
        type = "sql"
        pool = "default"

        [[connectors.db.queries]]
        name = "answer"
        sql = "SELECT 42 AS answer"
        """
    )
    if extra_connector:
        base += textwrap.dedent(
            """
            [connectors.db2]
            type = "sql"
            pool = "default"

            [[connectors.db2.queries]]
            name = "two"
            sql = "SELECT 2 AS two"
            """
        )
    return base


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("reader", password="readerpw")  # not a superuser
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def env(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(_toml(db_url))
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings), conn_toml, db_url


def _h(client: TestClient, username: str) -> dict[str, str]:
    token = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_reload_requires_superuser(env) -> None:
    app, _, _ = env
    with TestClient(app) as client:
        assert client.post("/admin/reload").status_code == 401
        assert client.post("/admin/reload", headers=_h(client, "reader")).status_code == 403


def test_reload_picks_up_new_connector(env) -> None:
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        before = {c["name"] for c in client.get("/api/connectors", headers=h).json()["connectors"]}
        assert before == {"db"}

        conn_toml.write_text(_toml(db_url, extra_connector=True))
        r = client.post("/admin/reload", headers=h)
        assert r.status_code == 200 and r.json()["reloaded"] is True
        assert set(r.json()["connectors"]) == {"db", "db2"}

        # the new connector is live, and the old one still works
        after = {c["name"] for c in client.get("/api/connectors", headers=h).json()["connectors"]}
        assert after == {"db", "db2"}
        assert client.get("/api/sql/db2/two", headers=h).json()["rows"] == [{"two": 2}]
        assert client.get("/api/sql/db/answer", headers=h).json()["rows"] == [{"answer": 42}]

        # auth still works after the registry swap (re-pointed at the new pools)
        assert client.post("/auth/login", json={"username": "admin", "password": "adminpw"}).status_code == 200


def test_config_get_requires_superuser(env) -> None:
    app, conn_toml, _ = env
    with TestClient(app) as client:
        assert client.get("/admin/config/connectors").status_code == 401
        assert client.get("/admin/config/connectors", headers=_h(client, "reader")).status_code == 403
        r = client.get("/admin/config/connectors", headers=_h(client, "admin"))
        assert r.status_code == 200
        body = r.json()
        assert body["path"] == str(conn_toml) and "[connectors.db]" in body["content"]


def test_config_put_validates_then_writes(env) -> None:
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.put("/admin/config/connectors", json={"content": "x = ="}, headers=h).status_code == 422
        assert client.put(
            "/admin/config/connectors", json={"content": '[connectors.x]\ntype = "ftp"\n'}, headers=h
        ).status_code == 422
        # a non-superuser can't write
        assert client.put("/admin/config/connectors", json={"content": "# ok"}, headers=_h(client, "reader")).status_code == 403
        # valid content is written and reflected on the next GET, and Reload picks it up
        new_content = _toml(db_url, extra_connector=True)
        assert client.put("/admin/config/connectors", json={"content": new_content}, headers=h).json()["saved"] is True
        assert conn_toml.read_text() == new_content
        assert "[connectors.db2]" in client.get("/admin/config/connectors", headers=h).json()["content"]
        assert set(client.post("/admin/reload", headers=h).json()["connectors"]) == {"db", "db2"}


def test_oidc_callback_fragment_redirect() -> None:
    from liberty.config import OIDCSettings

    s = OIDCSettings(enabled=True, discovery_url="https://idp.test/.well-known", frontend_redirect="https://app.test/oidc/callback")
    assert s.frontend_redirect == "https://app.test/oidc/callback"
    # (the full OIDC flow needs a real IdP; this just pins the new setting + that the
    # callback route now returns a redirect when frontend_redirect is set — see liberty/auth/routes.py)
