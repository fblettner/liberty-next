from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.dependencies import require_permission, require_role
from liberty.auth.service import AuthService
from liberty.auth.tokens import TokenConfig, TokenService
from liberty.config import AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "route-test-secret-key"
ISSUER = "liberty-next"


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.get_or_create_role("reader", permissions=["sql:liberty:read"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("alice", password="alicepw", roles=["reader"])
            await svc.create_user("bob", password="bobpw", is_active=False)
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),  # no SPA mount — these tests add routes after create_app
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, jwt_issuer=ISSUER, pool="default"),
    )
    application = create_app(settings)

    # Two routes guarded by the auth dependencies, just for these tests.
    @application.get("/_t/read")
    def _read(p=Depends(require_permission("sql:liberty:read"))):
        return {"user": p.username}

    @application.get("/_t/admin")
    def _admin(p=Depends(require_role("admin"))):
        return {"user": p.username}

    return application


def _login(client: TestClient, username: str, password: str):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_login_returns_token_pair(app) -> None:
    with TestClient(app) as client:
        r = _login(client, "alice", "alicepw")
        assert r.status_code == 200
        body = r.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 3600
        assert body["access_token"] and body["refresh_token"]


def test_login_bad_password(app) -> None:
    with TestClient(app) as client:
        assert _login(client, "alice", "nope").status_code == 401


def test_login_unknown_user(app) -> None:
    with TestClient(app) as client:
        assert _login(client, "ghost", "x").status_code == 401


def test_login_inactive_user(app) -> None:
    with TestClient(app) as client:
        assert _login(client, "bob", "bobpw").status_code == 401


def test_me_requires_token(app) -> None:
    with TestClient(app) as client:
        assert client.get("/auth/me").status_code == 401


def test_me_with_token(app) -> None:
    with TestClient(app) as client:
        token = _login(client, "alice", "alicepw").json()["access_token"]
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == "alice"
        assert body["roles"] == ["reader"]
        assert body["permissions"] == ["sql:liberty:read"]
        assert body["is_superuser"] is False


def test_me_with_garbage_token(app) -> None:
    with TestClient(app) as client:
        r = client.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401


def test_me_with_expired_token(app) -> None:
    expired = TokenService(
        TokenConfig(secret=JWT_SECRET, issuer=ISSUER, access_ttl=-10)
    ).access_token(subject="2", username="alice")
    with TestClient(app) as client:
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {expired.token}"})
        assert r.status_code == 401


def test_refresh_flow(app) -> None:
    with TestClient(app) as client:
        pair = _login(client, "alice", "alicepw").json()
        r = client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
        assert r.status_code == 200
        new_access = r.json()["access_token"]
        # the freshly-minted access token works
        me = client.get("/auth/me", headers={"Authorization": f"Bearer {new_access}"})
        assert me.status_code == 200 and me.json()["username"] == "alice"


def test_refresh_rejects_access_token(app) -> None:
    with TestClient(app) as client:
        access = _login(client, "alice", "alicepw").json()["access_token"]
        assert client.post("/auth/refresh", json={"refresh_token": access}).status_code == 401


def test_refresh_rejects_garbage(app) -> None:
    with TestClient(app) as client:
        assert client.post("/auth/refresh", json={"refresh_token": "x.y.z"}).status_code == 401


def test_permission_guard(app) -> None:
    with TestClient(app) as client:
        alice = _login(client, "alice", "alicepw").json()["access_token"]
        admin = _login(client, "admin", "adminpw").json()["access_token"]
        assert client.get("/_t/read", headers={"Authorization": f"Bearer {alice}"}).status_code == 200
        assert client.get("/_t/read", headers={"Authorization": f"Bearer {admin}"}).status_code == 200
        assert client.get("/_t/read").status_code == 401


def test_role_guard(app) -> None:
    with TestClient(app) as client:
        alice = _login(client, "alice", "alicepw").json()["access_token"]
        admin = _login(client, "admin", "adminpw").json()["access_token"]
        assert client.get("/_t/admin", headers={"Authorization": f"Bearer {admin}"}).status_code == 200
        r = client.get("/_t/admin", headers={"Authorization": f"Bearer {alice}"})
        assert r.status_code == 403


def test_oidc_routes_404_when_disabled(app) -> None:
    with TestClient(app) as client:
        assert client.get("/auth/oidc/login", follow_redirects=False).status_code == 404
        assert client.get("/auth/oidc/callback", follow_redirects=False).status_code == 404


def test_info_reports_auth(app) -> None:
    with TestClient(app) as client:
        body = client.get("/info").json()
        assert body["auth"]["pool"] == "default"
        assert body["auth"]["oidc_enabled"] is False
