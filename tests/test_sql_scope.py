"""Row-level data scope applied by SQLConnector: SELECT wrap (hints path and probe path), modes,
streaming, the request contextvar fallback, and the write check."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.auth.scope import DataScope, ScopeDeniedError, current_scope
from liberty.connectors.config import ColumnHint, PoolConfig, QueryDef, SqlConnectorConfig
from liberty.connectors.db import PoolRegistry
from liberty.connectors.sql import SQLConnector, StreamRows


@pytest_asyncio.fixture
async def conn(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}"
    eng = create_async_engine(url)
    async with eng.begin() as c:
        await c.execute(text("CREATE TABLE sales (id INTEGER PRIMARY KEY, company TEXT, pole TEXT, amount INTEGER)"))
        for row in [(1, "A", "X", 10), (2, "A", "Y", 20), (3, "B", "X", 30), (4, "C", "Z", 40)]:
            await c.execute(text("INSERT INTO sales VALUES (:i, :c, :p, :a)"), {"i": row[0], "c": row[1], "p": row[2], "a": row[3]})
    await eng.dispose()
    pools = PoolRegistry({"default": PoolConfig(url=url)})
    cfg = SqlConnectorConfig(type="sql", pool="default", queries=[
        QueryDef(name="sales_get", sql="SELECT id, company, pole, amount FROM sales ORDER BY id"),
        QueryDef(name="sales_put", sql="UPDATE sales SET amount = :AMOUNT WHERE id = :ID_ORIGINAL", writable=True),
        QueryDef(name="sales_delete", sql="DELETE FROM sales WHERE id = :ID", writable=True),
    ])
    c = SQLConnector("db", cfg, pools)
    try:
        yield c
    finally:
        await pools.dispose()


def _scope(groups):
    return DataScope.from_claims(groups)


HINTS = [ColumnHint(name="company", dd="COMPANY_ID"), ColumnHint(name="pole"), ColumnHint(name="amount")]


@pytest.mark.asyncio
async def test_select_is_wrapped_hints_path(conn) -> None:
    sc = _scope([[{"column": "company", "values": ["A"]}, {"column": "pole", "values": ["X"]}], [{"column": "company", "values": ["C"]}]])
    res = await conn.execute("sales_get", column_hints=HINTS, scope=sc)
    assert [r["id"] for r in res.rows] == [1, 4]              # (A and X) or C — ORDER BY kept
    assert not conn._scope_columns                              # hints named every scoped column: no probe
    assert len((await conn.execute("sales_get", column_hints=HINTS)).rows) == 4   # no scope → everything


@pytest.mark.asyncio
async def test_select_is_wrapped_probe_path_and_dd(conn) -> None:
    # no hints at all → the connector probes the query once for its columns
    sc = _scope([[{"column": "COMPANY", "values": ["B"]}]])
    res = await conn.execute("sales_get", scope=sc)
    assert [r["id"] for r in res.rows] == [3] and "sales_get" in conn._scope_columns
    res = await conn.execute("sales_get", scope=sc)             # cached probe, same result
    assert [r["id"] for r in res.rows] == [3]
    # a dd rule resolves through the hint that carries the dd
    res = await conn.execute("sales_get", column_hints=HINTS, scope=_scope([[{"dd": "COMPANY_ID", "values": ["C"]}]]))
    assert [r["id"] for r in res.rows] == [4]


@pytest_asyncio.fixture
async def jde(tmp_path):
    """A JDE-shaped connector: ``dd_prefix = 2``, two tables sharing the data item CO, codes stored
    blank-padded the way Oracle CHAR(N) hands them back, and the pool's ``trim_strings`` on."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'jde.db'}"
    eng = create_async_engine(url)
    async with eng.begin() as c:
        await c.execute(text("CREATE TABLE f0101 (aban8 INTEGER PRIMARY KEY, abalph TEXT, abco TEXT)"))
        await c.execute(text("CREATE TABLE f0006 (mcmcu TEXT PRIMARY KEY, mcco TEXT, mcan8 INTEGER)"))
        for an8, alph, co in [(1, "Ann", "00001     "), (2, "Bob", "00050     "), (3, "Cid", "00070     ")]:
            await c.execute(text("INSERT INTO f0101 VALUES (:a, :n, :c)"), {"a": an8, "n": alph, "c": co})
        for mcu, co, an8 in [("M1", "00001     ", 1), ("M2", "00050     ", 2), ("M3", "00001     ", 2)]:
            await c.execute(text("INSERT INTO f0006 VALUES (:m, :c, :a)"), {"m": mcu, "c": co, "a": an8})
    await eng.dispose()
    pools = PoolRegistry({"default": PoolConfig(url=url, trim_strings=True)})
    cfg = SqlConnectorConfig(type="sql", pool="default", dd_prefix=2, queries=[
        QueryDef(name="ab_get", sql="SELECT ABAN8, ABALPH, ABCO FROM f0101 ORDER BY ABAN8"),
        QueryDef(name="bu_get", sql="SELECT MCMCU, MCCO, MCAN8, ABALPH FROM f0006 JOIN f0101 ON ABAN8 = MCAN8 ORDER BY MCMCU"),
        QueryDef(name="ab_put", sql="UPDATE f0101 SET ABALPH = :ABALPH WHERE ABAN8 = :ABAN8_ORIGINAL", writable=True),
    ])
    c = SQLConnector("nomajde", cfg, pools)
    try:
        yield c
    finally:
        await pools.dispose()


@pytest.mark.asyncio
async def test_dd_rule_reaches_every_prefixed_column(jde) -> None:
    sc = _scope([[{"dd": "CO", "values": ["00001"]}]])
    # ABCO carries CO through the prefix, no screen hint needed; the stored value is blank-padded
    # and the pool trims, so the predicate is TRIM(lib_rls.ABCO) IN (CAST(:… AS VARCHAR(4000)))
    res = await jde.execute("ab_get", scope=sc)
    assert [r["ABAN8"] for r in res.rows] == [1] and res.rows[0]["ABCO"] == "00001"
    # a right-justified / padded rule value is stripped too
    assert [r["ABAN8"] for r in (await jde.execute("ab_get", scope=_scope([[{"dd": "CO", "values": ["  00001 "]}]]))).rows] == [1]
    # the join: MCCO is restricted too; M3 is company 00001 but its address (Bob) is 00050 — hidden,
    # because the query exposes the business unit's company only (MCCO), ABCO isn't selected
    res = await jde.execute("bu_get", scope=sc)
    assert [r["MCMCU"] for r in res.rows] == ["M1", "M3"]
    # exclusion: everything but company 00001
    res = await jde.execute("ab_get", scope=_scope([[{"dd": "CO", "values": ["00001"], "exclude": True}]]))
    assert [r["ABAN8"] for r in res.rows] == [2, 3]
    # a rule for another connector is silent here, even in deny mode
    other = _scope([[{"dd": "CO", "values": ["00050"], "mode": "deny", "connector": "nomasx1"}]])
    assert len((await jde.execute("ab_get", scope=other)).rows) == 3
    # writes: the row's ABCO (under its _ORIGINAL key, the way the grid sends it) must be in scope
    await jde.execute("ab_put", {"ABALPH": "Ann B.", "ABAN8_ORIGINAL": 1, "ABCO_ORIGINAL": "00001     "}, scope=sc)
    with pytest.raises(ScopeDeniedError):
        await jde.execute("ab_put", {"ABALPH": "Bob B.", "ABAN8_ORIGINAL": 2, "ABCO_ORIGINAL": "00050"}, scope=sc)


@pytest.mark.asyncio
async def test_modes(conn) -> None:
    # the scoped column isn't in the query: ignore → everything, deny → nothing
    assert len((await conn.execute("sales_get", scope=_scope([[{"column": "region", "values": ["EU"]}]]))).rows) == 4
    assert (await conn.execute("sales_get", scope=_scope([[{"column": "region", "values": ["EU"], "mode": "deny"}]]))).rows == []
    assert (await conn.execute("sales_get", scope=_scope([[{"column": "company", "values": []}]]))).rows == []


@pytest.mark.asyncio
async def test_stream_and_contextvar_fallback(conn) -> None:
    sc = _scope([[{"column": "company", "values": ["A"]}]])
    ids = []
    async for ev in conn.execute_stream("sales_get", scope=sc):
        if isinstance(ev, StreamRows):
            ids += [r["id"] for r in ev.rows]
    assert ids == [1, 2]
    token = current_scope.set(sc)   # what the auth dependency does per request
    try:
        assert [r["id"] for r in (await conn.execute("sales_get")).rows] == [1, 2]
    finally:
        current_scope.reset(token)
    assert len((await conn.execute("sales_get")).rows) == 4


@pytest.mark.asyncio
async def test_writes_are_checked(conn) -> None:
    sc = _scope([[{"column": "company", "values": ["A"], "mode": "deny"}]])
    # update inside the scope (new values + the row's previous values)
    res = await conn.execute("sales_put", {"ID": 1, "AMOUNT": 11, "COMPANY": "A", "ID_ORIGINAL": 1, "COMPANY_ORIGINAL": "A"}, scope=sc)
    assert res.rowcount == 1
    with pytest.raises(ScopeDeniedError):   # moving a row out of the scope
        await conn.execute("sales_put", {"ID": 1, "AMOUNT": 11, "COMPANY": "B", "ID_ORIGINAL": 1, "COMPANY_ORIGINAL": "A"}, scope=sc)
    with pytest.raises(ScopeDeniedError):   # touching a row that was outside the scope
        await conn.execute("sales_put", {"ID": 3, "AMOUNT": 1, "COMPANY": "A", "ID_ORIGINAL": 3, "COMPANY_ORIGINAL": "B"}, scope=sc)
    with pytest.raises(ScopeDeniedError):   # delete by id without the scoped column: deny
        await conn.execute("sales_delete", {"ID": 3}, scope=sc)
    # ignore mode lets a delete-by-id through (the operator's choice)
    res = await conn.execute("sales_delete", {"ID": 4}, scope=_scope([[{"column": "company", "values": ["A"]}]]))
    assert res.rowcount == 1


@pytest.mark.asyncio
async def test_scope_value_typed_by_the_dictionary_whatever_the_column_case(tmp_path) -> None:
    """A result column comes back lower-cased (Postgres) while the dictionary files the item
    upper-cased: the scope value must still be bound as the item's type (an integer column
    compared with a text bind is a Postgres error)."""
    from liberty.connectors.dictionary import DictionaryEntry, DictionaryFile, DictionarySection
    url = f"sqlite+aiosqlite:///{tmp_path / 'typed.db'}"
    eng = create_async_engine(url)
    async with eng.begin() as c:
        await c.execute(text("CREATE TABLE t (company_code INTEGER, qty INTEGER)"))
        await c.execute(text("INSERT INTO t VALUES (212, 1), (25, 2)"))
    await eng.dispose()
    pools = PoolRegistry({"default": PoolConfig(url=url)})
    d = DictionaryFile(default_language="en", connectors={"nomademo": DictionarySection(entries={"COMPANY_CODE": DictionaryEntry(format="integer")})})
    cfg = SqlConnectorConfig(type="sql", pool="default", queries=[QueryDef(name="t_get", sql="SELECT company_code, qty FROM t ORDER BY company_code")])
    c = SQLConnector("nomademo", cfg, pools, dictionary=d)
    try:
        sc = _scope([[{"column": "COMPANY_CODE", "values": ["212"]}]])     # the user attribute: strings
        _sql, binds = await c.scoped_select("t_get", scope=sc, dictionary_scope="nomademo")
        assert 212 in binds.values() and "212" not in binds.values()        # bound as the integer the dictionary says
        assert [r["qty"] for r in (await c.execute("t_get", scope=sc, dictionary_scope="nomademo")).rows] == [1]
    finally:
        await pools.dispose()


@pytest.mark.asyncio
async def test_wrapped_query_keeps_an_alias_qualified_order_by(tmp_path) -> None:
    """``ORDER BY s.company`` in the query: once a scope (or a filter) wraps it, the ORDER BY is
    re-attached outside, where the alias no longer exists — the qualifier is dropped."""
    from liberty.connectors.sql import _hoisted_order_by
    assert _hoisted_order_by("s.company_name, f.period DESC, \"q\".x") == "company_name, period DESC, \"q\".x"
    assert _hoisted_order_by("company") == "company"
    url = f"sqlite+aiosqlite:///{tmp_path / 'ob.db'}"
    eng = create_async_engine(url)
    async with eng.begin() as c:
        await c.execute(text("CREATE TABLE s (company TEXT, amount INTEGER)"))
        await c.execute(text("INSERT INTO s VALUES ('B', 1), ('A', 2), ('A', 3)"))
    await eng.dispose()
    pools = PoolRegistry({"default": PoolConfig(url=url)})
    cfg = SqlConnectorConfig(type="sql", pool="default", queries=[QueryDef(name="s_get", sql="SELECT s.company, s.amount FROM s ORDER BY s.company, s.amount DESC")])
    c = SQLConnector("db", cfg, pools)
    try:
        res = await c.execute("s_get", scope=_scope([[{"column": "COMPANY", "values": ["A"]}]]))
        assert [r["amount"] for r in res.rows] == [3, 2]
    finally:
        await pools.dispose()
