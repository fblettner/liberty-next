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
        ai=AISettings(enabled=ai_enabled, api_key="", config_path=tmp_path / "ai.toml"),  # no API key → "unconfigured"
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
    def __init__(self):
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _Stream("pong")


class _FakeClient:
    def __init__(self):
        self.messages = _Messages()

    async def close(self):
        pass


def _install_fake_assistant(app, **settings_kw) -> None:
    @tool
    def noop() -> str:
        return "noop"

    app.state.ai = AiAssistant(AISettings(api_key="x", **settings_kw), client=_FakeClient(), tools=ToolRegistry().add(noop))


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
        anon = client.get("/info").json()
        assert anon["ai"]["enabled"] is True
        assert anon["ai"]["available"] is False  # no api key
        assert "model" not in anon["ai"]  # the model name is only shown to authenticated callers
        token = _login(client, "admin", "adminpw")
        body = client.get("/info", headers={"Authorization": f"Bearer {token}"}).json()
        assert body["ai"]["model"] == "claude-opus-4-8"


def test_chat_forwards_the_page_context(app) -> None:
    with TestClient(app) as client:
        token = _login(client, "admin", "adminpw")
        _install_fake_assistant(app)
        r = client.post(
            "/ai/chat",
            json={"messages": [{"role": "user", "content": "what is this row?"}], "context": "screen: f0092\ncurrent row: {\"ULUSER\": \"JDE\"}"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200 and [e["type"] for e in _sse_events(r.text)] == ["token", "done"]
        system = app.state.ai._client.messages.calls[0]["system"]
        assert len(system) == 2 and "ULUSER" in system[1]["text"] and "cache_control" not in system[1]


def test_model_picker_and_starters(app) -> None:
    with TestClient(app) as client:
        token = _login(client, "admin", "adminpw")
        h = {"Authorization": f"Bearer {token}"}
        # the default: no picker (one model), no starters
        body = client.get("/ai/tools", headers=h).json()
        assert [m["id"] for m in body["models"]] == ["claude-opus-4-8"] and body["starters"] == {}
        _install_fake_assistant(app, model="claude-opus-4-8", allowed_models=["claude-haiku-5-5", "claude-opus-4-8", ""],
                                starters={"*": ["What can you do?"], "nomajde/f0092": ["Who has no role?"]})
        body = client.get("/ai/tools", headers=h).json()
        assert body["models"] == [{"id": "claude-opus-4-8", "label": "Opus 4.8 (recommended)"}, {"id": "claude-haiku-5-5", "label": "Haiku 5.5 (fast, cheap)"}]
        assert body["starters"]["nomajde/f0092"] == ["Who has no role?"]
        # the chat refuses a model that is not allowed, accepts an allowed one
        msgs = [{"role": "user", "content": "ping"}]
        r = client.post("/ai/chat", json={"messages": msgs, "model": "claude-fable-5-1"}, headers=h)
        assert r.status_code == 400 and "allowed_models" in r.json()["detail"]
        r = client.post("/ai/chat", json={"messages": msgs, "model": "claude-haiku-5-5"}, headers=h)
        assert r.status_code == 200 and app.state.ai._client.messages.calls[-1]["model"] == "claude-haiku-5-5"


def test_bundled_starters_are_edited_and_merged_under_the_operators(app, tmp_path) -> None:
    with TestClient(app) as client:
        token = _login(client, "admin", "adminpw")
        h = {"Authorization": f"Bearer {token}"}
        assert client.get("/admin/config/ai/parsed", headers=h).json() == {"path": str(tmp_path / "ai.toml"), "ai": {}}
        body = {"ai": {"nomajde": {"starters": ["Who has no role?"], "screens": {"f0092": {"starters": ["Users never logged in"]}, "f0093": {"starters": []}}}}}
        r = client.put("/admin/config/ai/parsed", json=body, headers=h)
        assert r.status_code == 200, r.text
        text = (tmp_path / "ai.toml").read_text()
        assert "f0093" not in text and 'starters = [\n    "Who has no role?",' in text
        assert client.get("/admin/config/ai/parsed", headers=h).json()["ai"] == {"nomajde": {"starters": ["Who has no role?"], "screens": {"f0092": {"starters": ["Users never logged in"]}}}}
        assert client.put("/admin/config/ai/parsed", json={"ai": {"nomajde": {"playbook": 1}}}, headers=h).status_code == 422
        # the running server sees it after a reload, with the operator's [ai] starters on top
        assert client.post("/admin/reload", headers=h).status_code == 200
        _install_fake_assistant(app, starters={"*": ["What can you do?"], "nomajde": ["The operator's question"]})
        starters = client.get("/ai/tools", headers=h).json()["starters"]
        assert starters == {"nomajde": ["The operator's question"], "nomajde/f0092": ["Users never logged in"], "*": ["What can you do?"]}
