"""The assistant's tools answer for the caller: what a user may not open through the screens
they may not read through the chat either (PLAN G1.2). Outside a request — no caller — nothing
is restricted, like the data scope."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from liberty.ai.assistant import AiAssistant
from liberty.ai.connector_tools import build_connector_tools
from liberty.ai.explorer_tools import build_explorer_tools
from liberty.ai.instance_tools import build_instance_tools
from liberty.ai.ops_tools import build_ops_tools
from liberty.ai.tools import ToolRegistry, tool
from liberty.auth.db import AuthDatabase
from liberty.auth.principal import Principal, current_principal
from liberty.auth.scope import current_scope, current_username
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import ConnectorsFile, PoolConfig, QueryDef, SqlConnectorConfig
from liberty.connectors.db import PoolRegistry
from liberty.connectors.registry import ConnectorRegistry
from liberty.main import create_app

from tests import test_ai_instance_tools as inst


def _as(*permissions: str, superuser: bool = False, username: str = "bob") -> Principal:
    return Principal(id="1", username=username, permissions=tuple(permissions), is_superuser=superuser)


@pytest.fixture(autouse=True)
def _no_caller_left_behind():
    token = current_principal.set(None)
    yield
    current_principal.reset(token)


@pytest_asyncio.fixture
async def registry():
    cfg = ConnectorsFile(
        pools={"default": PoolConfig(url="sqlite+aiosqlite:///:memory:")},
        connectors={
            "sales": SqlConnectorConfig(type="sql", pool="default", queries=[
                QueryDef(name="orders", sql="SELECT 1 AS n", label="Orders"),
                QueryDef(name="margins", sql="SELECT 2 AS n", label="Margins"),
            ]),
            "hr": SqlConnectorConfig(type="sql", pool="default", queries=[QueryDef(name="salaries", sql="SELECT 3 AS n", label="Salaries")]),
        },
    )
    reg = ConnectorRegistry(cfg)
    yield reg
    await reg.aclose()


def _named(tools):
    return {t.name: t for t in tools}


# -- connectors ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_connectors_shows_only_what_the_caller_may_run(registry) -> None:
    lc = _named(build_connector_tools(registry))["list_connectors"]
    assert [c["name"] for c in (await lc.run({}))["connectors"]] == ["sales", "hr"]          # no caller: everything
    current_principal.set(_as("sql:sales:orders"))
    assert (await lc.run({}))["connectors"] == [{"name": "sales", "type": "sql", "queries": [{"name": "orders", "label": "Orders"}]}]
    assert (await lc.run({"search": "salaries"}))["matches"] == 0                             # not even by keyword
    with pytest.raises(Exception, match="no connector 'hr'"):                                  # nor by name
        await lc.run({"connector": "hr"})
    current_principal.set(_as("sql:*", "!sql:hr:*"))                                           # a deny wins over the glob
    assert [c["name"] for c in (await lc.run({}))["connectors"]] == ["sales"]
    current_principal.set(_as(superuser=True))
    assert [c["name"] for c in (await lc.run({}))["connectors"]] == ["sales", "hr"]


@pytest.mark.asyncio
async def test_sql_query_checks_the_permission_before_the_lookup(registry) -> None:
    sql_query = _named(build_connector_tools(registry))["sql_query"]
    current_principal.set(_as("sql:sales:orders"))
    assert (await sql_query.run({"connector": "sales", "query": "orders"}))["rows"] == [{"n": 1}]
    for connector, query in (("sales", "margins"), ("hr", "salaries"), ("hr", "ghost"), ("ghost", "x")):
        with pytest.raises(PermissionError, match=f"missing sql:{connector}:{query}"):          # the same answer, real or not
            await sql_query.run({"connector": connector, "query": query})


@pytest.mark.asyncio
async def test_a_refusal_reaches_the_model_as_an_error_result(registry) -> None:
    reg = ToolRegistry().add(*build_connector_tools(registry))
    current_principal.set(_as("sql:sales:orders"))
    content, is_error = await reg.execute("sql_query", {"connector": "hr", "query": "salaries"})
    assert is_error and "permission denied" in content and "do not try another" in content


# -- superuser-only tools -----------------------------------------------------


@pytest.mark.asyncio
async def test_superuser_only_tools_are_neither_offered_nor_run() -> None:
    st = SimpleNamespace(jobs=None, connectors=None)
    reg = ToolRegistry().add(tool(lambda: "hi", name="hello"), *build_ops_tools(lambda: st))
    everything = ["hello", "jobs_status", "server_log", "integrity_checks"]
    assert [t["name"] for t in reg.definitions()] == everything
    assert [t["name"] for t in reg.definitions(_as(superuser=True))] == everything
    assert [t["name"] for t in reg.definitions(_as("*"))] == ["hello"]             # every permission is still not an administrator
    current_principal.set(_as("*"))
    content, is_error = await reg.execute("server_log", {})
    assert is_error and "administrators" in content
    assert (await reg.execute("hello", {})) == ("hi", False)


def test_the_request_offers_the_callers_tools() -> None:
    reg = ToolRegistry().add(tool(lambda: "hi", name="hello"), tool(lambda: "x", name="admin_thing", superuser_only=True))
    a = AiAssistant(AISettings(enabled=True), client=None, tools=reg)
    assert [t["name"] for t in a._request_kwargs(model=None, principal=_as("ai:chat"))["tools"]] == ["hello"]
    assert [t["name"] for t in a._request_kwargs(model=None, principal=_as(superuser=True))["tools"]] == ["hello", "admin_thing"]
    assert [t["name"] for t in a.tool_catalog(_as("ai:chat"))] == ["hello"]


# -- explorer -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_explorer_models_follow_the_explorer_permission() -> None:
    models = {("shop", "sales"): SimpleNamespace(label="Sales", description=None, hub="s", entities={}, analyses={}),
              ("shop", "margins"): SimpleNamespace(label="Margins", description=None, hub="m", entities={}, analyses={})}
    explorer = SimpleNamespace(items=lambda: [(a, m, v) for (a, m), v in models.items()], get=lambda a, m: models.get((a, m)))
    st = SimpleNamespace(explorer=explorer)
    tools = _named(build_explorer_tools(lambda: st))
    current_principal.set(_as("explorer:shop:sales"))
    assert [m["model"] for m in (await tools["explorer_models"].run({}))["models"]] == ["sales"]
    for model in ("margins", "ghost"):                                               # the same answer, real or not
        with pytest.raises(ValueError, match=f"no explorer model shop/{model} available"):
            await tools["explorer_fields"].run({"app": "shop", "model": model})
    current_principal.set(_as("explorer:shop:*"))
    assert [m["model"] for m in (await tools["explorer_models"].run({}))["models"]] == ["sales", "margins"]


# -- the instance catalogue ---------------------------------------------------


@pytest.mark.asyncio
async def test_describe_instance_is_pruned_to_the_caller() -> None:
    st = inst._state()
    tools = _named(build_instance_tools(lambda: st))
    whole = await tools["describe_instance"].run({})
    assert [a["id"] for a in whole["apps"]] == ["nomajde", "nomasx1"] and whole["jobs"]

    current_principal.set(_as("sql:nomajde:f0092_get"))
    mine = await tools["describe_instance"].run({})
    assert [a["id"] for a in mine["apps"]] == ["nomajde"]                            # nomasx1: nothing of it → not even its name
    assert mine["apps"][0]["screens"] == [{"id": "f0092", "label": "Users", "read_query": "f0092_get"}]
    assert [m["id"] for m in mine["apps"][0]["menu"]] == ["users"]                   # the empty Security folder is gone
    assert mine["jobs"] == [] and mine["dashboards"] == []
    assert [p["id"] for p in mine["playbooks"]] == ["create_user", "dup_role"]       # the playbooks of the apps they see
    with pytest.raises(KeyError, match="no application 'nomasx1'"):
        await tools["describe_instance"].run({"app": "nomasx1"})

    assert [p["id"] for p in await tools["list_playbooks"].run({})] == ["create_user", "dup_role"]
    with pytest.raises(KeyError):
        await tools["load_playbook"].run({"app": "nomasx1", "id": "sox"})
    assert (await tools["search_catalog"].run({"query": "sod audit"}))["matches"] == 0
    assert (await tools["search_catalog"].run({"query": "nightly"}))["matches"] == 0

    current_principal.set(_as("sql:nomajde:f0092_get", "job:nightly"))               # the job's own permission lists it
    assert [j["id"] for j in (await tools["describe_instance"].run({}))["jobs"]] == ["nightly"]

    current_principal.set(_as("ai:chat"))                                            # nothing at all
    assert (await tools["describe_instance"].run({}))["apps"] == []
    assert await tools["list_playbooks"].run({}) == []

    current_principal.set(_as(superuser=True))
    assert await tools["describe_instance"].run({}) == whole


# -- the chat route: the caller reaches the tools inside the stream -------------


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
    """First round: a tool call; second round: the answer."""

    def __init__(self):
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        self._finals = [
            SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="t1", name="whoami", input={})], stop_reason="tool_use", usage=usage),
            SimpleNamespace(content=[SimpleNamespace(type="text", text="done")], stop_reason="end_turn", usage=usage),
        ]
        self.calls: list[dict] = []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _Stream(self._finals[len(self.calls) - 1])

    async def close(self):
        pass


def test_chat_runs_the_tools_as_the_caller(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')

    async def seed() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("reader", permissions=["ai:chat", "sql:sales:orders"])
            await svc.create_user("bob", password="bobpw-123", roles=["reader"])
        await pools.dispose()

    asyncio.run(seed())
    app = create_app(Settings(
        app=AppSettings(static_dir=""), connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret="ai-permissions-test-secret-0123456789", pool="default"),
        ai=AISettings(enabled=True, api_key="", config_path=tmp_path / "ai.toml"),
    ))
    seen: dict = {}

    def whoami() -> str:
        """Who the tool runs for."""
        who = current_principal.get()
        seen.update(username=who.username if who else None, perms=who.permissions if who else None,
                    scope_user=current_username.get(), scope=current_scope.get())
        return "ok"

    with TestClient(app) as client:
        fake = _Client()
        app.state.ai = AiAssistant(AISettings(enabled=True), client=fake, tools=ToolRegistry().add(
            tool(whoami), tool(lambda: "x", name="admin_thing", superuser_only=True)))
        token = client.post("/auth/login", json={"username": "bob", "password": "bobpw-123"}).json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        r = client.post("/ai/chat", json={"messages": [{"role": "user", "content": "who am I?"}]}, headers=h)
        events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
        assert [e["type"] for e in events] == ["tool_call", "tool_result", "done"] and events[1]["ok"] is True
        assert seen["username"] == "bob" and seen["scope_user"] == "bob"
        assert "sql:sales:orders" in seen["perms"]
        assert [t["name"] for t in fake.calls[0]["tools"]] == ["whoami"]               # the administrators' tool is not offered
        assert [t["name"] for t in client.get("/ai/tools", headers=h).json()["tools"]] == ["whoami"]
