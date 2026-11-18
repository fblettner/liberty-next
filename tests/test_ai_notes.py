"""Conversations and notes on the per-user saved-views store (PLAN G5.2 / G5.3): two more kinds
of `/api/views`, a note proposed on a card and saved by the user, the notes of the user added
to the context of their questions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from liberty.ai.assistant import NOTES_MAX_CHARS, AiAssistant, _notes_block
from liberty.ai.instance_tools import NOTE_MAX_CHARS, build_instance_tools
from liberty.ai.tools import ToolRegistry
from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app
from tests import test_ai_instance_tools as inst


@pytest.mark.asyncio
async def test_remember_note_is_a_card_not_a_write() -> None:
    st = inst._state()
    tools = {t.name: t for t in build_instance_tools(lambda: st)}
    note = tools["remember_note"]
    assert note.chat_only and not note.read_only and not note.superuser_only      # every user's, in the Liberty chat only
    out = await note.run({"text": "  A conflict means\n a conflict line. ", "app": "nomasx1"})
    assert out["proposal"] == {"kind": "note", "summary": "A conflict means a conflict line.", "target_path": "your notes",
                               "payload": {"text": "A conflict means a conflict line.", "app": "nomasx1"}, "app": "nomasx1"}
    assert "accepts or declines" in out["message"]
    everywhere = await note.run({"text": "Amounts in EUR."})
    assert everywhere["proposal"]["payload"]["app"] == "*" and "app" not in everywhere["proposal"]
    assert (await note.run({"text": "x", "app": "ghost-app"}))["proposal"]["payload"]["app"] == "ghost-app"   # any name: it is only a key
    with pytest.raises(ValueError):
        await note.run({"text": "   "})
    with pytest.raises(ValueError, match="at most"):
        await note.run({"text": "x" * (NOTE_MAX_CHARS + 1)})


def test_notes_block_is_capped() -> None:
    block = _notes_block(["A conflict means a conflict line.", "Amounts  in\nEUR."])
    assert "<user_notes>\n- A conflict means a conflict line.\n- Amounts in EUR.\n</user_notes>" in block and "what they say now wins" in block
    many = _notes_block([f"note {i} " + "x" * 280 for i in range(20)])
    assert many.count("\n- ") + many.count(">\n- ") <= 8 and len(many) < NOTES_MAX_CHARS + 400


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
    def __init__(self):
        self.calls: list[dict] = []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return _Stream(SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn", usage=usage))

    async def close(self):
        pass


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
            await svc.get_or_create_role("chat", permissions=["ai:chat"])
            await svc.create_user("bob", password="bobpw-123", roles=["chat"])
            await svc.create_user("carol", password="carolpw-123", roles=["chat"])
        await pools.dispose()

    asyncio.run(seed())
    return create_app(Settings(
        app=AppSettings(static_dir=""), connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret="ai-notes-test-secret-0123456789abcdef0", pool="default"),
        ai=AISettings(enabled=True, api_key="", config_path=tmp_path / "ai.toml"),
    ))


def _h(client: TestClient, user: str, password: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + client.post("/auth/login", json={"username": user, "password": password}).json()["access_token"]}


def test_the_users_notes_ride_with_their_questions(app) -> None:
    with TestClient(app) as client:
        fake = _Client()
        app.state.ai = AiAssistant(AISettings(enabled=True), client=fake, tools=ToolRegistry())
        bob, carol = _h(client, "bob", "bobpw-123"), _h(client, "carol", "carolpw-123")

        def note(headers, key, name, text):
            r = client.put("/api/views/ai_note", json={"key": key, "name": name, "payload": {"text": text}}, headers=headers)
            assert r.status_code == 200, r.text

        note(bob, "*", "n1", "Amounts in EUR.")
        note(bob, "nomasx1", "n2", "A conflict means a conflict line.")
        note(bob, "nomajde", "n3", "Roles are the *GROUP profiles.")
        note(carol, "nomasx1", "n1", "Carol's own note.")

        def system_of(headers, context):
            fake.calls.clear()
            body = {"messages": [{"role": "user", "content": "hi"}], **({"context": context} if context else {})}
            assert client.post("/ai/chat", json=body, headers=headers).status_code == 200
            return fake.calls[0]["system"][1]["text"]

        on_sx1 = system_of(bob, "app: nomasx1\nscreen: sod_summary")
        assert "- Amounts in EUR.\n- A conflict means a conflict line." in on_sx1          # every application, then this one
        assert "GROUP profiles" not in on_sx1 and "Carol" not in on_sx1                    # another application's, another user's
        assert "<page_context>" in on_sx1
        nowhere = system_of(bob, None)
        assert "Amounts in EUR." in nowhere and "conflict line" not in nowhere
        assert "Carol's own note." in system_of(carol, "app: nomasx1") and "EUR" not in system_of(carol, "app: nomasx1")
        # the notes are in the uncached block: the cached prompt is the same for everyone
        assert "user_notes" not in fake.calls[0]["system"][0]["text"]

        # a deleted note is gone at the next question
        assert client.delete("/api/views/ai_note", params={"key": "*", "name": "n1"}, headers=bob).json() == {"deleted": True}
        assert "EUR" not in system_of(bob, None) and "<user_notes>" not in system_of(bob, None)


def test_conversations_are_the_users_own_documents(app) -> None:
    with TestClient(app) as client:
        bob, carol = _h(client, "bob", "bobpw-123"), _h(client, "carol", "carolpw-123")
        doc = {"title": "Top business unit", "app": "nomademo", "messages": [{"role": "user", "content": "Top BU?"}, {"role": "assistant", "content": "France."}]}
        assert client.put("/api/views/chat", json={"key": "conversations", "name": "c-1", "payload": doc}, headers=bob).status_code == 200
        mine = client.get("/api/views/chat", params={"key": "conversations"}, headers=bob).json()["views"]
        assert [(v["name"], v["payload"]["title"]) for v in mine] == [("c-1", "Top business unit")] and mine[0]["updated_at"]
        assert client.get("/api/views/chat", params={"key": "conversations"}, headers=carol).json()["views"] == []
        assert client.delete("/api/views/chat", params={"key": "conversations", "name": "c-1"}, headers=carol).json() == {"deleted": False}
        assert client.delete("/api/views/chat", params={"key": "conversations", "name": "c-1"}, headers=bob).json() == {"deleted": True}
        assert client.get("/api/views/secrets", params={"key": "x"}, headers=bob).status_code == 404
        assert json.dumps(mine)   # plain JSON
