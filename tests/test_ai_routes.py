from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from liberty.ai.assistant import AiAssistant
from liberty.ai.tools import ToolRegistry, tool
from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "ai-route-test-secret"


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("alice", password="alicepw")  # no ai:chat permission
        await pools.dispose()

    asyncio.run(go())


def _make_app(tmp_path, *, ai_enabled: bool = True):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=ai_enabled, api_key=""),  # no API key → "unconfigured"
    )
    return create_app(settings)


@pytest.fixture
def app(tmp_path):
    return _make_app(tmp_path)


def _login(client: TestClient, username: str, password: str) -> str:
    return client.post("/auth/login", json={"username": username, "password": password}).json()["access_token"]


def _sse_events(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            payload = line[6:]
            if payload != "[DONE]":
                out.append(json.loads(payload))
    return out


# --- a fake-client assistant for the happy path ---------------------------- #


def _text_delta(t):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=t))


def _final(text):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=3, output_tokens=2, cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )


class _Stream:
    def __init__(self, text):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            yield _text_delta(self._text)

        return gen()

    async def get_final_message(self):
        return _final(self._text)


class _Messages:
    def stream(self, **kwargs):
        return _Stream("pong")


class _FakeClient:
    def __init__(self):
        self.messages = _Messages()

    async def close(self):
        pass


def _install_fake_assistant(app) -> None:
    @tool
    def noop() -> str:
        return "noop"

    app.state.ai = AiAssistant(AISettings(api_key="x"), client=_FakeClient(), tools=ToolRegistry().add(noop))


# --- tests ----------------------------------------------------------------- #


def test_chat_requires_auth(app) -> None:
    with TestClient(app) as client:
        assert client.post("/ai/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401


def test_chat_requires_permission(app) -> None:
    with TestClient(app) as client:
        token = _login(client, "alice", "alicepw")
        r = client.post(
            "/ai/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


def test_chat_first_message_must_be_user(app) -> None:
    with TestClient(app) as client:
        token = _login(client, "admin", "adminpw")
        r = client.post(
            "/ai/chat",
            json={"messages": [{"role": "assistant", "content": "hi"}]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400


def test_chat_unconfigured_streams_error(app) -> None:
    # No fake assistant installed → the real build_assistant ran with api_key="" → client is None.
    with TestClient(app) as client:
        token = _login(client, "admin", "adminpw")
        r = client.post(
            "/ai/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        events = _sse_events(r.text)
        assert events[0]["type"] == "error" and "not configured" in events[0]["message"].lower()


def test_chat_streams_tokens(app) -> None:
    with TestClient(app) as client:
        token = _login(client, "admin", "adminpw")
        _install_fake_assistant(app)
        r = client.post(
            "/ai/chat",
            json={"messages": [{"role": "user", "content": "ping"}]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        events = _sse_events(r.text)
        assert [e["type"] for e in events] == ["token", "done"]
        assert events[0]["text"] == "pong"
        assert events[-1]["stop_reason"] == "end_turn"
        assert "[DONE]" in r.text


def test_tools_endpoint(app) -> None:
    with TestClient(app) as client:
        token = _login(client, "admin", "adminpw")
        r = client.get("/ai/tools", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False  # api_key="" in the fixture
        assert body["model"] == "claude-opus-4-8"
        names = {t["name"] for t in body["tools"]}
        assert {"list_connectors", "sql_query"} <= names  # connector tools are on by default


def test_tools_requires_permission(app) -> None:
    with TestClient(app) as client:
        token = _login(client, "alice", "alicepw")
        assert client.get("/ai/tools", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_disabled_ai_is_404(tmp_path) -> None:
    app = _make_app(tmp_path, ai_enabled=False)
    with TestClient(app) as client:
        token = _login(client, "admin", "adminpw")
        h = {"Authorization": f"Bearer {token}"}
        assert client.get("/ai/tools", headers=h).status_code == 404
        assert client.post("/ai/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=h).status_code == 404


def test_info_reports_ai(app) -> None:
    with TestClient(app) as client:
        body = client.get("/info").json()
        assert body["ai"]["enabled"] is True
        assert body["ai"]["available"] is False  # no api key
        assert body["ai"]["model"] == "claude-opus-4-8"
