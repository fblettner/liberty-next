"""The assistant's audit and usage record (PLAN G1.3): one row per turn — who, model, tokens,
tools — never the question, the answer or a row; recorded by the chat route, read by
/admin/ai-usage, bounded by [ai] usage_retention_days."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import update

from liberty.ai.assistant import AiAssistant
from liberty.ai.tools import ToolRegistry, tool
from liberty.ai.usage import AiCall, AiUsageStore, TurnRecorder, _utcnow, app_of
from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app


def test_app_of_the_page_context() -> None:
    assert app_of("app: nomademo\nscreen: sales — Sales") == "nomademo"
    assert app_of("page: x\n  app:  shop  ") == "shop"
    assert app_of(None) is None and app_of("screen: sales") is None


def _turn(rec: TurnRecorder, *, tools: list[tuple[str, bool]] = (), usage: dict | None = None, error: str | None = None) -> TurnRecorder:
    for name, ok in tools:
        rec.observe({"type": "tool_call", "name": name, "summary": f"connector=shop query={name}"})
        rec.observe({"type": "tool_result", "name": name, "ok": ok, "summary": "" if ok else '{"error": "permission denied"}', "size": 1234})
    rec.observe({"type": "token", "text": "the answer must not be kept"})
    if error:
        rec.observe({"type": "error", "message": error, "usage": usage or {}})
    else:
        rec.observe({"type": "done", "stop_reason": "end_turn", "usage": usage or {}})
    return rec


def test_the_recorder_folds_a_turn() -> None:
    rec = _turn(TurnRecorder(username="bob", model="m1", app="shop"), tools=[("sql_query", False), ("sql_query", True)],
                usage={"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 90, "cache_creation_input_tokens": 7})
    row = rec.row()
    assert (row.username, row.model, row.app, row.channel, row.stop_reason, row.error) == ("bob", "m1", "shop", "chat", "end_turn", None)
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_creation_tokens) == (10, 5, 90, 7)
    assert (row.tool_calls, row.tool_errors) == (2, 1)
    first, second = row.tools
    assert first["name"] == "sql_query" and first["input"] == "connector=shop query=sql_query" and first["ok"] is False
    assert first["error"] == '{"error": "permission denied"}' and first["chars"] == 1234 and first["ms"] >= 0
    assert second["ok"] is True and "error" not in second
    assert "answer" not in json.dumps(row.to_dict())                       # no text of the conversation


def test_the_recorder_on_an_error_and_on_an_abandoned_turn() -> None:
    failed = _turn(TurnRecorder(username="bob", model="m1"), error="Anthropic request failed", usage={"input_tokens": 3}).row()
    assert failed.stop_reason == "error" and failed.error == "Anthropic request failed" and failed.input_tokens == 3
    gone = TurnRecorder(username="bob", model="m1")
    gone.observe({"type": "tool_call", "name": "sql_query", "summary": "x"})
    row = gone.row()                                                       # the client left before the result
    assert row.stop_reason == "abandoned" and row.tool_calls == 1 and row.tools[0]["ok"] is None


@pytest_asyncio.fixture
async def store(tmp_path):
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}")})
    st = AiUsageStore(pools, "default")
    await st.create_schema()
    assert await st.create_schema() == []                                  # idempotent
    yield st
    await pools.dispose()


@pytest.mark.asyncio
async def test_store_summary_recent_and_retention(store) -> None:
    await store.record(_turn(TurnRecorder(username="bob", model="haiku", app="shop"), tools=[("sql_query", True), ("explorer_query", False)],
                             usage={"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 900}))
    await store.record(_turn(TurnRecorder(username="bob", model="opus"), usage={"input_tokens": 50, "output_tokens": 5}))
    await store.record(_turn(TurnRecorder(username="alice", model="haiku"), error="boom"))
    assert await store.count() == 3

    s = await store.summary(days=30)
    assert s["totals"]["turns"] == 3 and s["totals"]["input_tokens"] == 150 and s["totals"]["cache_read_tokens"] == 900
    assert s["totals"]["tool_calls"] == 2 and s["totals"]["tool_errors"] == 1 and s["totals"]["errors"] == 1
    assert [(u["username"], u["turns"], u["input_tokens"]) for u in s["by_user"]] == [("bob", 2, 150), ("alice", 1, 0)]
    assert len(s["by_day"]) == 1 and s["by_day"][0]["turns"] == 3
    assert {m["model"]: m["turns"] for m in s["by_model"]} == {"haiku": 2, "opus": 1}
    assert {t["name"]: (t["calls"], t["errors"]) for t in s["by_tool"]} == {"sql_query": (1, 0), "explorer_query": (1, 1)}

    recent = await store.recent(days=30)
    assert len(recent) == 3 and {r["username"] for r in recent} == {"bob", "alice"}
    assert [r["username"] for r in await store.recent(days=30, username="alice")] == ["alice"]

    async with store.session() as s2:                                      # one turn 100 days old
        await s2.execute(update(AiCall).where(AiCall.username == "alice").values(at=_utcnow() - timedelta(days=100)))
    assert (await store.summary(days=30))["totals"]["turns"] == 2
    assert await store.delete_older_than(90, dry_run=True) == 1 and await store.count() == 3
    assert await store.delete_older_than(90) == 1 and await store.count() == 2


# -- through the application ----------------------------------------------------


class _Stream:
    def __init__(self, final):
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            return
            yield  # pragma: no cover

        return gen()

    async def get_final_message(self):
        return self._final


class _Client:
    """One tool round, then the answer — each round reports its usage."""

    def __init__(self):
        usage = SimpleNamespace(input_tokens=20, output_tokens=4, cache_read_input_tokens=100, cache_creation_input_tokens=8)
        self._finals = [
            SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="t1", name="lookup", input={"key": "K1"})], stop_reason="tool_use", usage=usage),
            SimpleNamespace(content=[SimpleNamespace(type="text", text="done")], stop_reason="end_turn", usage=usage),
        ]
        self.n = 0
        self.messages = self

    def stream(self, **kwargs):
        self.n += 1
        return _Stream(self._finals[(self.n - 1) % 2])

    async def close(self):
        pass


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')

    async def seed() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("chat", permissions=["ai:chat"])
            await svc.create_user("admin", password="adminpw-123", is_superuser=True)
            await svc.create_user("bob", password="bobpw-123", roles=["chat"])
        await pools.dispose()

    asyncio.run(seed())
    return create_app(Settings(
        app=AppSettings(static_dir=""), connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret="ai-usage-test-secret-0123456789abcdef", pool="default"),
        ai=AISettings(enabled=True, api_key="", config_path=tmp_path / "ai.toml", usage_retention_days=30),
    ))


def _h(client: TestClient, user: str, password: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + client.post("/auth/login", json={"username": user, "password": password}).json()["access_token"]}


def test_a_chat_turn_is_recorded_and_read_back(app) -> None:
    def lookup(key: str) -> str:
        """Look a key up."""
        return "SECRET-ROW-VALUE"

    with TestClient(app) as client:
        assert app.state.ai_usage is not None
        app.state.ai = AiAssistant(AISettings(enabled=True, model="test-model"), client=_Client(), tools=ToolRegistry().add(tool(lookup, summary_keys=("key",))))
        bob, admin = _h(client, "bob", "bobpw-123"), _h(client, "admin", "adminpw-123")
        body = {"messages": [{"role": "user", "content": "PRIVATE QUESTION"}], "context": "app: shop\nscreen: orders"}
        assert client.post("/ai/chat", json=body, headers=bob).status_code == 200
        assert client.post("/ai/chat", json={"messages": [{"role": "user", "content": "again"}]}, headers=admin).status_code == 200

        assert client.get("/admin/ai-usage", headers=bob).status_code == 403          # the record is the administrators'
        out = client.get("/admin/ai-usage?days=7", headers=admin).json()
        assert out["retention_days"] == 30 and out["summary"]["days"] == 7
        totals = out["summary"]["totals"]
        assert totals["turns"] == 2 and totals["tool_calls"] == 2 and totals["input_tokens"] == 80 and totals["cache_read_tokens"] == 400
        assert {u["username"] for u in out["summary"]["by_user"]} == {"bob", "admin"}
        assert out["summary"]["by_tool"] == [{"name": "lookup", "calls": 2, "errors": 0, "ms": out["summary"]["by_tool"][0]["ms"], "chars": 32}]
        mine = client.get("/admin/ai-usage?username=bob", headers=admin).json()["recent"]
        assert len(mine) == 1
        turn = mine[0]
        assert (turn["username"], turn["app"], turn["model"], turn["channel"], turn["stop_reason"]) == ("bob", "shop", "test-model", "chat", "end_turn")
        assert turn["tools"] == [{"name": "lookup", "input": "key=K1", "ok": True, "ms": turn["tools"][0]["ms"], "chars": 16}]
        kept = json.dumps(out) + json.dumps(mine)
        assert "PRIVATE QUESTION" not in kept and "SECRET-ROW-VALUE" not in kept      # neither the question nor a row

        # Monitoring → Retention knows the store
        stores = {s["id"]: s for s in client.get("/admin/retention", headers=admin).json()["stores"]}
        assert stores["ai_usage"]["policy"] == {"days": 30} and stores["ai_usage"]["size"] == {"turns": 2} and stores["ai_usage"]["can_run"] is True
        assert client.post("/admin/retention/ai_usage/run?dry_run=true", headers=admin).json()["deleted"] == 0


def test_a_store_that_fails_never_breaks_the_chat(app) -> None:
    with TestClient(app) as client:
        app.state.ai = AiAssistant(AISettings(enabled=True), client=_Client(), tools=ToolRegistry().add(tool(lambda key: "x", name="lookup")))

        class Broken:
            async def record(self, recorder):
                raise RuntimeError("pool is down")

        app.state.ai_usage = Broken()
        r = client.post("/ai/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=_h(client, "bob", "bobpw-123"))
        assert r.status_code == 200 and r.text.rstrip().endswith("data: [DONE]")
