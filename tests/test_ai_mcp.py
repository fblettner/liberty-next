"""The MCP endpoint (PLAN G4.2): the assistant's tools for an external client acting as a user,
through a personal access token — the same registry, the same checks, recorded as `mcp`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.ai.assistant import AiAssistant
from liberty.ai.tools import ToolRegistry, tool
from liberty.auth.db import AuthDatabase
from liberty.auth.principal import current_principal
from liberty.auth.scope import current_username
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

pytest.importorskip("mcp")

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _app(tmp_path, *, mcp_enabled: bool = True):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')

    async def seed() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("mcp", permissions=["mcp:use", "ai:chat", "plugin:shop:*"])
            await svc.create_user("admin", password="adminpw-123", is_superuser=True)
            await svc.create_user("bob", password="bobpw-123", roles=["mcp"])
        await pools.dispose()

    asyncio.run(seed())
    return create_app(Settings(
        app=AppSettings(static_dir=""), connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret="mcp-endpoint-test-secret-0123456789abcdef", pool="default"),
        ai=AISettings(enabled=True, api_key="", config_path=tmp_path / "ai.toml", mcp_enabled=mcp_enabled),
    ))


def _with(t, permission: str):
    """A tool that needs a permission, as an application's tool does."""
    t.permission = permission
    return t


def _tools(seen: dict) -> ToolRegistry:
    def lookup(key: str) -> dict:
        """Look a key up.

        Args:
            key: the key.
        """
        who = current_principal.get()
        seen.update(user=who.username if who else None, scope_user=current_username.get())
        return {"key": key, "value": "V-" + key}

    return ToolRegistry().add(
        tool(lookup, summary_keys=("key",)),
        tool(lambda: "saved", name="save_it", read_only=False),
        _with(tool(lambda: "x", name="shop_stock"), "plugin:shop:stock"),
        _with(tool(lambda: "x", name="other_app"), "plugin:other:x"),
        tool(lambda: "x", name="admin_thing", superuser_only=True),
        tool(lambda: {"proposal": {}}, name="scaffold_thing", superuser_only=True, chat_only=True),
    )


def _token(client: TestClient, user: str, password: str) -> str:
    access = client.post("/auth/login", json={"username": user, "password": password}).json()["access_token"]
    return client.post("/auth/tokens", json={"days": 1}, headers={"Authorization": f"Bearer {access}"}).json()["token"]


def _rpc(client: TestClient, token: str, method: str, params: dict | None = None, rid: int = 1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method, **({"params": params} if params is not None else {})}
    return client.post("/mcp", json=body, headers={**HEADERS, "Authorization": f"Bearer {token}"})


INIT = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}}


def test_the_endpoint_serves_the_callers_tools(tmp_path) -> None:
    app = _app(tmp_path)
    seen: dict = {}
    with TestClient(app) as client:
        app.state.ai = AiAssistant(AISettings(enabled=True), client=None, tools=_tools(seen))
        bob, admin = _token(client, "bob", "bobpw-123"), _token(client, "admin", "adminpw-123")

        init = _rpc(client, bob, "initialize", INIT)
        assert init.status_code == 200, init.text
        info = init.json()["result"]
        assert info["serverInfo"]["name"] == "liberty-next" and "Data Explorer" in info["instructions"]

        listed = _rpc(client, bob, "tools/list").json()["result"]["tools"]
        by = {t["name"]: t for t in listed}
        assert list(by) == ["lookup", "save_it", "shop_stock"]              # not another app's, not the administrators', not a chat card
        assert by["lookup"]["inputSchema"] == {"type": "object", "properties": {"key": {"type": "string", "description": "the key."}}, "required": ["key"]}
        assert by["lookup"]["annotations"]["readOnlyHint"] is True and by["save_it"]["annotations"]["readOnlyHint"] is False
        assert [t["name"] for t in _rpc(client, admin, "tools/list").json()["result"]["tools"]] == ["lookup", "save_it", "shop_stock", "other_app", "admin_thing"]

        called = _rpc(client, bob, "tools/call", {"name": "lookup", "arguments": {"key": "K1"}}).json()["result"]
        assert called["isError"] is False and json.loads(called["content"][0]["text"]) == {"key": "K1", "value": "V-K1"}
        assert seen == {"user": "bob", "scope_user": "bob"}                 # the tool ran as the token's user

        for name in ("other_app", "admin_thing", "scaffold_thing", "ghost"):   # the same answer, whatever the reason
            refused = _rpc(client, bob, "tools/call", {"name": name, "arguments": {}}).json()["result"]
            assert refused["isError"] is True and "unknown tool" in refused["content"][0]["text"]

        # every call is in the usage record, under the mcp channel
        access = client.post("/auth/login", json={"username": "admin", "password": "adminpw-123"}).json()["access_token"]
        usage = client.get("/admin/ai-usage?username=bob", headers={"Authorization": f"Bearer {access}"}).json()["recent"]
        assert {u["channel"] for u in usage} == {"mcp"} and len(usage) == 5
        ok = next(u for u in usage if u["tools"][0]["name"] == "lookup")
        assert ok["tools"][0]["input"] == "key=K1" and ok["tools"][0]["ok"] is True and ok["tool_errors"] == 0


def test_the_endpoint_needs_a_personal_token(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        app.state.ai = AiAssistant(AISettings(enabled=True), client=None, tools=_tools({}))
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        none = client.post("/mcp", json=body, headers=HEADERS)
        assert none.status_code == 401 and none.headers["www-authenticate"] == "Bearer"
        access = client.post("/auth/login", json={"username": "bob", "password": "bobpw-123"}).json()["access_token"]
        assert client.post("/mcp", json=body, headers={**HEADERS, "Authorization": f"Bearer {access}"}).status_code == 401   # a session token is not one
        token = _token(client, "bob", "bobpw-123")
        assert _rpc(client, token, "tools/list").status_code == 200
        asyncio.run(app.state.auth_backend.set_password("bob", "changed-pw-456"))        # how a leaked token is ended
        assert _rpc(client, token, "tools/list").status_code == 401


def test_the_endpoint_is_off_unless_enabled(tmp_path) -> None:
    app = _app(tmp_path, mcp_enabled=False)
    with TestClient(app) as client:
        token = _token(client, "bob", "bobpw-123")
        r = _rpc(client, token, "tools/list")
        assert r.status_code == 404 and "mcp_enabled" in r.json()["detail"]
        app.state.settings.ai.mcp_enabled = True                                           # read per request: no restart
        app.state.ai = AiAssistant(AISettings(enabled=True), client=None, tools=_tools({}))
        assert _rpc(client, token, "tools/list").status_code == 200
