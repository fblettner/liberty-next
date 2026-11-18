"""Personal access tokens (PLAN G4.1): what a user gives an external client — the MCP endpoint —
to act as them. A fourth kind of the existing signed tokens; the account is re-read at every
use, so nothing a token can do outlives the account's own rights."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.routes import principal_from_personal_token
from liberty.auth.service import AuthService
from liberty.auth.tokens import TokenError
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')

    async def seed() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("mcp", permissions=["mcp:use", "sql:shop:orders"])
            await svc.get_or_create_role("plain", permissions=["sql:shop:orders"])
            await svc.create_user("bob", password="bobpw-123", roles=["mcp"])
            await svc.create_user("carol", password="carolpw-123", roles=["plain"])
        await pools.dispose()

    asyncio.run(seed())
    return create_app(Settings(
        app=AppSettings(static_dir=""), connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret="personal-token-test-secret-0123456789abc", pool="default"),
        ai=AISettings(enabled=False, mcp_token_max_days=30),
    ))


def _login(client: TestClient, user: str, password: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + client.post("/auth/login", json={"username": user, "password": password}).json()["access_token"]}


def _who(app, token: str):
    return asyncio.run(principal_from_personal_token(token, app.state.token_service, app.state.auth_backend))


def test_a_token_is_the_user_as_they_are_now(app) -> None:
    with TestClient(app) as client:
        bob = _login(client, "bob", "bobpw-123")
        r = client.post("/auth/tokens", json={"days": 7}, headers=bob)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["days"] == 7 and out["expires_at"] > 0
        who = _who(app, out["token"])
        assert who.username == "bob" and set(who.permissions) == {"mcp:use", "sql:shop:orders"} and not who.is_superuser

        # the cap of [ai] mcp_token_max_days
        assert client.post("/auth/tokens", json={"days": 400}, headers=bob).json()["days"] == 30
        assert client.post("/auth/tokens", json={"days": 0}, headers=bob).status_code == 422

        # mcp:use is what creates one
        assert client.post("/auth/tokens", json={}, headers=_login(client, "carol", "carolpw-123")).status_code == 403


def test_a_personal_token_opens_nothing_but_what_reads_it(app) -> None:
    """Not an access token: the API refuses it, and an access token is not a personal one."""
    with TestClient(app) as client:
        bob = _login(client, "bob", "bobpw-123")
        token = client.post("/auth/tokens", json={}, headers=bob).json()["token"]
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        with pytest.raises(TokenError, match="expected a personal token"):
            _who(app, bob["Authorization"].split()[1])
        with pytest.raises(TokenError):
            _who(app, "not-a-token")


def test_what_ends_a_token(app) -> None:
    with TestClient(app) as client:
        bob = _login(client, "bob", "bobpw-123")
        token = client.post("/auth/tokens", json={}, headers=bob).json()["token"]
        backend = app.state.auth_backend

        async def go(coro):
            return await coro

        # the role changes: the token follows at once …
        asyncio.run(go(backend.assign_roles("bob", ["plain"], replace=True)))
        with pytest.raises(TokenError, match="no longer holds mcp:use"):
            _who(app, token)
        asyncio.run(go(backend.assign_roles("bob", ["mcp"], replace=True)))
        assert _who(app, token).username == "bob"
        # … the account is deactivated …
        asyncio.run(go(backend.set_active("bob", False)))
        with pytest.raises(TokenError, match="inactive"):
            _who(app, token)
        asyncio.run(go(backend.set_active("bob", True)))
        # … a temporary password must be changed first …
        asyncio.run(go(backend.set_must_change("bob", True)))
        with pytest.raises(TokenError, match="change its password first"):
            _who(app, token)
        asyncio.run(go(backend.set_must_change("bob", False)))
        assert _who(app, token).username == "bob"
        # … and a password change ends every token of the account (how a leaked one is ended)
        asyncio.run(go(backend.set_password("bob", "another-pw-456")))
        with pytest.raises(TokenError, match="ended by a password change"):
            _who(app, token)


def test_an_expired_token(app) -> None:
    with TestClient(app):
        tokens = app.state.token_service
        user = asyncio.run(app.state.auth_backend.get_by_username("bob"))
        from liberty.auth.routes import _password_fingerprint

        old = tokens.personal_token(subject=user.id, password_fingerprint=_password_fingerprint(user.password_hash), ttl=-10).token
        with pytest.raises(TokenError, match="expired"):
            _who(app, old)
