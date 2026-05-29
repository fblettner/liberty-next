"""``/admin/access`` RBAC management routes — users + roles CRUD, superuser gating, self-lockout."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-access-test-secret"


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("alice", password="alicepw")
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    (tmp_path / "connectors.toml").write_text(f'[pools.default]\nurl = "{db_url}"\n')
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
    )
    return create_app(settings)


def _h(client: TestClient, username: str) -> dict[str, str]:
    tok = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_requires_superuser(app) -> None:
    with TestClient(app) as client:
        assert client.get("/admin/access/users").status_code == 401
        assert client.get("/admin/access/users", headers=_h(client, "alice")).status_code == 403
        assert client.get("/admin/access/users", headers=_h(client, "admin")).status_code == 200


def test_list_users(app) -> None:
    with TestClient(app) as client:
        users = client.get("/admin/access/users", headers=_h(client, "admin")).json()["users"]
        by = {u["username"]: u for u in users}
        assert by["admin"]["is_superuser"] is True
        assert by["alice"]["is_superuser"] is False and by["alice"]["is_active"] is True


def test_upsert_role_then_assign_to_new_user(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.put("/admin/access/roles/reader", json={"permissions": ["sql:app1:users_get"], "description": "Read users"}, headers=h)
        assert r.status_code == 200 and r.json()["permissions"] == ["sql:app1:users_get"]
        # update the role's permissions (upsert replaces)
        r = client.put("/admin/access/roles/reader", json={"permissions": ["sql:app1:users_get", "sql:app1:roles_get"]}, headers=h)
        assert r.json()["permissions"] == ["sql:app1:users_get", "sql:app1:roles_get"]
        # create a user with that role
        r = client.post("/admin/access/users", json={"username": "bob", "password": "bobpw123", "roles": ["reader"]}, headers=h)
        assert r.status_code == 201 and r.json()["roles"] == ["reader"]


def test_create_duplicate_user_conflict(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/access/users", json={"username": "alice", "password": "whatever1"}, headers=h)
        assert r.status_code == 409


def test_patch_user_roles_active_superuser(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        client.put("/admin/access/roles/reader", json={"permissions": ["sql:app1:users_get"]}, headers=h)
        r = client.patch("/admin/access/users/alice", json={"roles": ["reader"], "is_superuser": True, "email": "alice@x.y"}, headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["roles"] == ["reader"] and body["is_superuser"] is True and body["email"] == "alice@x.y"


def test_self_lockout_guard(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.patch("/admin/access/users/admin", json={"is_active": False}, headers=h).status_code == 400
        assert client.patch("/admin/access/users/admin", json={"is_superuser": False}, headers=h).status_code == 400
        # but editing other fields on self is fine
        assert client.patch("/admin/access/users/admin", json={"email": "admin@x.y"}, headers=h).status_code == 200


def test_set_password_then_login(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/access/users/alice/password", json={"password": "alice-new-pw"}, headers=h)
        assert r.status_code == 200
        assert client.post("/auth/login", json={"username": "alice", "password": "alicepw"}).status_code == 401
        assert client.post("/auth/login", json={"username": "alice", "password": "alice-new-pw"}).status_code == 200


def test_short_password_rejected(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.post("/admin/access/users", json={"username": "x", "password": "short"}, headers=h).status_code == 422
        assert client.post("/admin/access/users/alice/password", json={"password": "short"}, headers=h).status_code == 422
