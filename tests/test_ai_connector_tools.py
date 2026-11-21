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
    # the read queries only (sql_query refuses a writable one), without their empty fields
    assert db["queries"] == [{"name": "answer", "label": "The answer"}]


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


@pytest_asyncio.fixture
async def big_registry(tmp_path):
    """A table of 50 rows behind a pool sized for the grid (max_rows far above it)."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    url = f"sqlite+aiosqlite:///{tmp_path / 'big.db'}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(sa.text("CREATE TABLE lines (n INTEGER)"))
        await conn.execute(sa.text("INSERT INTO lines (n) VALUES (:n)"), [{"n": i} for i in range(50)])
    await engine.dispose()
    cfg = ConnectorsFile(
        pools={"default": PoolConfig(url=url, max_rows=500_000)},
        connectors={"db": SqlConnectorConfig(type="sql", pool="default", queries=[QueryDef(name="lines", sql="SELECT n FROM lines ORDER BY n")])},
    )
    reg = ConnectorRegistry(cfg)
    yield reg
    await reg.aclose()


@pytest.mark.asyncio
async def test_sql_query_has_its_own_row_cap(big_registry) -> None:
    """The pool's max_rows is the grid's (it streams); a tool result is read in one piece."""
    sql_query = _tool(build_connector_tools(big_registry, max_rows=10), "sql_query")
    out = await sql_query.run({"connector": "db", "query": "lines"})
    assert out["row_count"] == 10 and out["truncated"] is True
    assert "first 10 rows" in out["note"] and "Data Explorer" in out["note"]
    fewer = await sql_query.run({"connector": "db", "query": "lines", "limit": 3})
    assert fewer["row_count"] == 3 and fewer["truncated"] is True
    capped = await sql_query.run({"connector": "db", "query": "lines", "limit": 5000})   # never above the cap
    assert capped["row_count"] == 10


@pytest.mark.asyncio
async def test_sql_query_under_the_cap_is_whole(big_registry) -> None:
    sql_query = _tool(build_connector_tools(big_registry), "sql_query")       # the default cap: 200
    out = await sql_query.run({"connector": "db", "query": "lines"})
    assert out["row_count"] == 50 and out["truncated"] is False and "note" not in out


def test_the_setting_reaches_the_tool(big_registry) -> None:
    from liberty.ai.assistant import build_assistant
    from liberty.config import AISettings

    assert AISettings().sql_max_rows == 200
    a = build_assistant(AISettings(enabled=True, api_key="", sql_max_rows=7), big_registry)
    assert "limit" in a.tools.get("sql_query").input_schema["properties"]
    import asyncio
    out = asyncio.run(a.tools.get("sql_query").run({"connector": "db", "query": "lines"}))
    assert out["row_count"] == 7


@pytest_asyncio.fixture
async def large_registry():
    """Two connectors with enough queries to be above the full-listing size."""
    def queries(prefix: str, n: int) -> list[QueryDef]:
        return [
            QueryDef(name=f"{prefix}{i:04d}_get", sql="SELECT 1", label=f"{prefix.upper()}{i:04d}",
                     description=f"Table number {i} of the {prefix} module, a description long enough to weigh")
            for i in range(n)
        ] + [QueryDef(name=f"{prefix}_put", sql="DELETE FROM x", writable=True)]

    cfg = ConnectorsFile(
        pools={"default": PoolConfig(url="sqlite+aiosqlite:///:memory:")},
        connectors={
            "jde": SqlConnectorConfig(type="sql", pool="default", queries=[
                *queries("f", 400), QueryDef(name="address_book", sql="SELECT 1", label="Address Book", description="Who's who")]),
            "audit": SqlConnectorConfig(type="sql", pool="default", queries=queries("sod", 30)),
        },
    )
    reg = ConnectorRegistry(cfg)
    yield reg
    await reg.aclose()


@pytest.mark.asyncio
async def test_list_connectors_overview_on_a_large_install(large_registry) -> None:
    from liberty.ai.connector_tools import LIST_MAX_CHARS

    lc = _tool(build_connector_tools(large_registry), "list_connectors")
    out = await lc.run({})
    assert out["connectors"] == [{"name": "jde", "type": "sql", "count": 401}, {"name": "audit", "type": "sql", "count": 30}]
    assert "search" in out["note"] and len(json.dumps(out)) < 1000
    # one connector that fits: its queries in full, the writable one left out
    audit = await lc.run({"connector": "audit"})
    assert len(audit["connectors"][0]["queries"]) == 30 and "note" not in audit
    assert audit["connectors"][0]["queries"][0] == {
        "name": "sod0000_get", "label": "SOD0000", "description": "Table number 0 of the sod module, a description long enough to weigh"}
    # one connector that does not: names and labels only
    jde = await lc.run({"connector": "jde"})
    assert len(jde["connectors"][0]["queries"]) == 401 and "description" not in jde["connectors"][0]["queries"][0]
    assert "names and labels only" in jde["note"]
    assert len(json.dumps(audit)) <= LIST_MAX_CHARS
    with pytest.raises(Exception, match="no connector 'ghost'"):
        await lc.run({"connector": "ghost"})


@pytest.mark.asyncio
async def test_list_connectors_search(large_registry) -> None:
    lc = _tool(build_connector_tools(large_registry), "list_connectors")
    out = await lc.run({"search": "Address BOOK"})                      # every word, any case, label or description
    assert out == {"connectors": [{"name": "jde", "type": "sql", "queries": [
        {"name": "address_book", "label": "Address Book", "description": "Who's who"}]}], "matches": 1}
    one = await lc.run({"search": "sod0007"})
    assert [q["name"] for q in one["connectors"][0]["queries"]] == ["sod0007_get"] and one["connectors"][0]["name"] == "audit"
    scoped = await lc.run({"search": "table sod0007", "connector": "audit"})   # words in any order, one connector
    assert scoped["matches"] == 1
    many = await lc.run({"search": "table number"})                      # capped, with the real count
    assert many["matches"] == 430 and sum(len(c["queries"]) for c in many["connectors"]) == 40 and "first 40" in many["note"]
    none = await lc.run({"search": "payroll"})
    assert none["connectors"] == [] and none["matches"] == 0 and "No match" in none["note"]
    assert (await lc.run({"search": "_put"}))["matches"] == 0             # a writable query is never listed


# -- sql_query runs a SELECT the way its screen does (web/connectors.select_events) ------------


@pytest_asyncio.fixture
async def udc(tmp_path):
    """A UDC-types table on a JDE-style connector (2-character column prefix), read by a screen."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    from liberty.screens.config import parse_screens

    url = f"sqlite+aiosqlite:///{tmp_path / 'udc.db'}"
    engine = create_async_engine(url)
    rows = [{"sy": "00", "rt": f"A{i:03d}", "dl": f"Type {i}", "seq": i} for i in range(300)] + [
        {"sy": "01", "rt": "ST", "dl": "Search Type", "seq": 500}, {"sy": "01", "rt": "PH", "dl": "Phone Type", "seq": 501},
        {"sy": "01", "rt": "LP", "dl": "Language", "seq": 502}]
    async with engine.begin() as conn:
        await conn.execute(sa.text("CREATE TABLE f0004 (dtsy TEXT, dtrt TEXT, dtdl01 TEXT, dtuseq INTEGER)"))
        await conn.execute(sa.text("INSERT INTO f0004 VALUES (:sy, :rt, :dl, :seq)"), rows)
    await engine.dispose()
    select = "SELECT dtsy AS DTSY, dtrt AS DTRT, dtdl01 AS DTDL01, dtuseq AS DTUSEQ FROM f0004 ORDER BY dtsy, dtrt"
    cfg = ConnectorsFile(
        pools={"default": PoolConfig(url=url)},
        connectors={"jde": SqlConnectorConfig(type="sql", pool="default", dd_prefix=2, queries=[
            QueryDef(name="f0004_get", sql=select, label="UDC types"),
            QueryDef(name="no_screen_get", sql=select),
        ])},
    )
    screens = parse_screens({"screens": {"admin": {"f0004": {
        "connector": "jde", "read_query": "f0004_get", "label": "UDC Types",
        "columns": [{"name": "DTSY", "label": "System"}, {"name": "DTRT", "label": "Type", "dd": "RT"}, {"name": "DTDL01", "label": "Description"},
                    {"name": "DTUSEQ", "label": "Sequence", "format": "integer"}],
    }}}})
    reg = ConnectorRegistry(cfg)
    yield {t.name: t for t in build_connector_tools(reg, max_rows=200, screens=lambda: screens)}
    await reg.aclose()


@pytest.mark.asyncio
async def test_sql_query_runs_like_the_screen(udc) -> None:
    whole = await udc["sql_query"].run({"connector": "jde", "query": "f0004_get"})
    assert whole["row_count"] == 200 and whole["truncated"] is True           # 303 rows: system 01 is beyond the cap
    assert "narrow with `filters`" in whole["note"]
    assert [c["label"] for c in whole["columns"]] == ["System", "Type", "Description", "Sequence"]   # the screen's labels


@pytest.mark.asyncio
async def test_sql_query_filters_are_the_dictionary_binds(udc) -> None:
    q = udc["sql_query"]
    by_column = await q.run({"connector": "jde", "query": "f0004_get", "filters": {"DTSY": "01"}})
    assert [r["DTRT"] for r in by_column["rows"]] == ["LP", "PH", "ST"] and by_column["truncated"] is False
    by_item = await q.run({"connector": "jde", "query": "f0004_get", "filters": {"DTSY": "01", "RT": "ST"}})   # the dictionary item the screen gives the column
    assert [r["DTDL01"] for r in by_item["rows"]] == ["Search Type"]
    by_name = await q.run({"connector": "jde", "query": "f0004_get", "filters": {"DTSY": "01", "DTRT": "ST"}})   # a column name is sent as its item
    assert [r["DTDL01"] for r in by_name["rows"]] == ["Search Type"]
    with pytest.raises(Exception, match="filter SY matches no column"):            # on a screen column the item is the hint's, not a guess
        await q.run({"connector": "jde", "query": "f0004_get", "filters": {"SY": "01"}})
    no_screen = await q.run({"connector": "jde", "query": "no_screen_get", "filters": {"SY": "01", "RT": "ST"}})   # no screen: the name minus the table prefix
    assert [r["DTDL01"] for r in no_screen["rows"]] == ["Search Type"]
    typed = await q.run({"connector": "jde", "query": "f0004_get", "filters": {"DTUSEQ": "501"}})            # typed by the column's format
    assert [r["DTRT"] for r in typed["rows"]] == ["PH"]
    ranged = await q.run({"connector": "jde", "query": "f0004_get", "filters": {"DTUSEQ_from": 500, "DTUSEQ_to": 501}})
    assert sorted(r["DTRT"] for r in ranged["rows"]) == ["PH", "ST"]
    bare = await q.run({"connector": "jde", "query": "no_screen_get", "filters": {"dtsy": "01"}})           # no screen needed
    assert bare["row_count"] == 3


@pytest.mark.asyncio
async def test_sql_query_refuses_a_filter_that_was_not_applied(udc) -> None:
    """The connector ignores a dictionary bind no column carries — every row would pass for the
    filtered ones."""
    with pytest.raises(Exception, match="filter SYSTEM matches no column of jde.f0004_get — it was not applied. The columns are: DTSY, DTRT, DTDL01, DTUSEQ"):
        await udc["sql_query"].run({"connector": "jde", "query": "f0004_get", "filters": {"SYSTEM": "01", "DTSY": "01"}})


@pytest.mark.asyncio
async def test_sql_query_still_refuses_a_write_and_an_unknown_query(udc) -> None:
    with pytest.raises(Exception):
        await udc["sql_query"].run({"connector": "jde", "query": "ghost"})
    with pytest.raises(Exception, match="no connector|not a SQL connector|ghost"):
        await udc["sql_query"].run({"connector": "ghost", "query": "f0004_get"})


@pytest.mark.asyncio
async def test_sql_query_reads_an_endpoint_that_holds_rows() -> None:
    """What a screen on an API connector reads, the assistant reads the same way: the endpoint's
    parameters are its only filters."""
    import httpx

    from liberty.connectors.base import ConnectorError
    from tests.test_api_rows import ANSWER, CFG

    sent: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content or b"null"))
        return httpx.Response(200, json=ANSWER)

    reg = ConnectorRegistry(ConnectorsFile(pools={"default": PoolConfig(url="sqlite+aiosqlite:///:memory:")}, connectors={"erp": CFG}),
                            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        tools = build_connector_tools(reg, include_api=True)
        listed = (await _tool(tools, "list_connectors").run({"connector": "erp"}))["connectors"][0]["endpoints"]
        assert [e.get("rows") for e in listed] == [True, True, None]
        out = await _tool(tools, "sql_query").run({"connector": "erp", "query": "addresses", "filters": {"at1": "E"}, "limit": 2})
        assert [r["AN8"] for r in out["rows"]] == [1001, 1234] and out["truncated"] is True
        assert sent[-1] == {"type": "E", "name": "", "max": "2"}          # the filter went to the endpoint, with the limit
        with pytest.raises(ConnectorError, match="filters only on its parameters: AT1, ALPH, MAX"):
            await _tool(tools, "sql_query").run({"connector": "erp", "query": "addresses", "filters": {"AN8": 1001}})
        with pytest.raises(ConnectorError, match="not an endpoint that holds rows"):
            await _tool(tools, "sql_query").run({"connector": "erp", "query": "no_rows"})
    finally:
        await reg.aclose()
