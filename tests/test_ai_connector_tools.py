from __future__ import annotations

import json

import pytest
import pytest_asyncio

from liberty.ai.connector_tools import build_connector_tools
from liberty.connectors.config import (
    ConnectorsFile,
    PoolConfig,
    QueryDef,
    SqlConnectorConfig,
)
from liberty.connectors.registry import ConnectorRegistry


@pytest_asyncio.fixture
async def registry():
    cfg = ConnectorsFile(
        pools={"default": PoolConfig(url="sqlite+aiosqlite:///:memory:")},
        connectors={
            "db": SqlConnectorConfig(
                type="sql",
                pool="default",
                queries=[
                    QueryDef(name="answer", sql="SELECT 42 AS answer", label="The answer"),
                    QueryDef(name="wipe", sql="DELETE FROM whatever", writable=True),
                ],
            )
        },
    )
    reg = ConnectorRegistry(cfg)
    yield reg
    await reg.aclose()


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


@pytest.mark.asyncio
async def test_list_connectors(registry) -> None:
    tools = build_connector_tools(registry)
    assert [t.name for t in tools] == ["list_connectors", "sql_query"]  # no api_call by default
    listing = await _tool(tools, "list_connectors").run({})
    db = listing["connectors"][0]
    assert db["name"] == "db" and db["type"] == "sql"
    by_name = {q["name"]: q for q in db["queries"]}
    assert by_name["answer"]["read_only"] is True
    assert by_name["wipe"]["read_only"] is False


@pytest.mark.asyncio
async def test_sql_query_runs_read_only(registry) -> None:
    sql_query = _tool(build_connector_tools(registry), "sql_query")
    result = await sql_query.run({"connector": "db", "query": "answer"})
    assert result["rows"] == [{"answer": 42}]


@pytest.mark.asyncio
async def test_sql_query_refuses_writable(registry) -> None:
    sql_query = _tool(build_connector_tools(registry), "sql_query")
    with pytest.raises(Exception) as exc:
        await sql_query.run({"connector": "db", "query": "wipe"})
    assert "writable" in str(exc.value)


@pytest.mark.asyncio
async def test_sql_query_unknown_query(registry) -> None:
    sql_query = _tool(build_connector_tools(registry), "sql_query")
    with pytest.raises(Exception):
        await sql_query.run({"connector": "db", "query": "ghost"})


@pytest.mark.asyncio
async def test_allowed_filter(registry) -> None:
    tools = build_connector_tools(registry, allowed=["something-else"])
    listing = await _tool(tools, "list_connectors").run({})
    assert listing["connectors"] == []
    with pytest.raises(Exception) as exc:
        await _tool(tools, "sql_query").run({"connector": "db", "query": "answer"})
    assert "not available" in str(exc.value)


@pytest.mark.asyncio
async def test_api_tool_opt_in(registry) -> None:
    assert [t.name for t in build_connector_tools(registry, include_api=True)] == [
        "list_connectors",
        "sql_query",
        "api_call",
    ]


@pytest.mark.asyncio
async def test_through_tool_registry(registry) -> None:
    from liberty.ai.tools import ToolRegistry

    reg = ToolRegistry().add(*build_connector_tools(registry))
    content, is_error = await reg.execute("sql_query", {"connector": "db", "query": "answer"})
    assert not is_error and json.loads(content)["rows"] == [{"answer": 42}]
    content, is_error = await reg.execute("sql_query", {"connector": "db", "query": "wipe"})
    assert is_error and "writable" in content
