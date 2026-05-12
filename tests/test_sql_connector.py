from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.connectors.base import (
    QueryNotFoundError,
    StatementNotAllowedError,
    UnknownPoolError,
    WriteNotAllowedError,
)
from liberty.connectors.config import ColumnHint, ParamDef, PoolConfig, QueryDef, SqlConnectorConfig
from liberty.connectors.db import PoolRegistry
from liberty.connectors.dictionary import DictionaryEntry, DictionaryFile
from liberty.connectors.sql import SQLConnector


def test_pool_registry_unknown_and_empty_url() -> None:
    pools = PoolRegistry({"blank": PoolConfig(url="   ")})
    with pytest.raises(UnknownPoolError, match="empty url"):
        pools.engine("blank")
    with pytest.raises(UnknownPoolError, match="Unknown pool"):
        pools.engine("nope")


def test_pool_registry_resolves_password() -> None:
    from liberty.crypto import encrypt
    mk = "pool-pw-test-key"
    cfgs = {
        # a separate `password` field — ENC: gets decrypted; URL-special chars are escaped, not mangled
        "enc": PoolConfig(url="postgresql+asyncpg://u@h:5432/db", password=encrypt("p@ss/w:rd", mk)),
        "plain": PoolConfig(url="postgresql+asyncpg://u@h:5432/db", password="p@ss/w:rd"),
        # an ENC: password embedded in the URL is still decrypted (legacy form)
        "url_enc": PoolConfig(url=f"oracle+oracledb://system:{encrypt('hunter2', mk)}@h:1521/?service_name=X"),
        # no password to touch → URL passed through unchanged
        "asis": PoolConfig(url="postgresql+asyncpg://u:plainpw@h:5432/db"),
    }
    reg = PoolRegistry(cfgs, master_key=mk)
    assert reg._resolved_url("enc", cfgs["enc"]).password == "p@ss/w:rd"
    assert reg._resolved_url("plain", cfgs["plain"]).password == "p@ss/w:rd"
    assert reg._resolved_url("url_enc", cfgs["url_enc"]).password == "hunter2"
    u = reg._resolved_url("asis", cfgs["asis"])
    assert u.password == "plainpw" and u.username == "u"
    # a wrong/missing key leaves the ENC: value as-is (logged warning, not a crash)
    bad = PoolRegistry(cfgs, master_key="wrong")
    assert bad._resolved_url("enc", cfgs["enc"]).password == cfgs["enc"].password  # still the ENC:… string


def _connector(pools: PoolRegistry, *queries: QueryDef, max_rows: int = 1000) -> SQLConnector:
    cfg = SqlConnectorConfig(type="sql", pool="test", max_rows=max_rows, queries=list(queries))
    return SQLConnector("db", cfg, pools)


@pytest_asyncio.fixture
async def pools(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT, status TEXT)"))
        await conn.execute(
            text("INSERT INTO item (id, name, status) VALUES (1,'a','on'),(2,'b','on'),(3,'c','off')")
        )
    registry = PoolRegistry()
    registry.register_engine("test", engine)
    yield registry
    await engine.dispose()


@pytest.mark.asyncio
async def test_select_returns_rows_and_columns(pools: PoolRegistry) -> None:
    conn = _connector(pools, QueryDef(name="all", sql="SELECT id, name FROM item ORDER BY id"))
    result = await conn.execute("all")
    assert result.statement_type == "SELECT"
    assert [c.name for c in result.columns] == ["id", "name"]
    assert result.rows == [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
        {"id": 3, "name": "c"},
    ]
    assert result.rowcount == -1
    assert result.row_count == 3
    assert result.truncated is False
    assert "columns" in result.to_dict()


@pytest.mark.asyncio
async def test_column_hints_reorder_label_and_hide(pools: PoolRegistry) -> None:
    conn = _connector(
        pools,
        QueryDef(
            name="all",
            sql="SELECT id, name, status FROM item ORDER BY id",
            columns=[
                ColumnHint(name="name", label="Item Name", align="left"),
                ColumnHint(name="status", hidden=True),
                ColumnHint(name="zzz", label="ignored — not a result column"),
            ],
        ),
    )
    result = await conn.execute("all")
    # hinted columns first (in hint order), then un-hinted ones in discovery order; the
    # stale `zzz` hint is dropped (a hint never fabricates a column)
    assert [c.name for c in result.columns] == ["name", "status", "id"]
    by_name = {c.name: c for c in result.columns}
    assert by_name["name"].label == "Item Name" and by_name["name"].align == "left"
    assert by_name["status"].hidden is True
    assert by_name["id"].label is None and by_name["id"].hidden is False
    # the hints surface in to_dict() (only when non-default)
    cols = {c["name"]: c for c in result.to_dict()["columns"]}
    assert cols["name"]["label"] == "Item Name" and cols["name"]["align"] == "left"
    assert cols["status"]["hidden"] is True
    assert "label" not in cols["id"] and "hidden" not in cols["id"]
    # rows are unaffected — every column's data is still present
    assert result.rows[0] == {"id": 1, "name": "a", "status": "on"}
    # describe() exposes the configured hints (defaults excluded)
    qd = next(q for q in conn.describe()["queries"] if q["name"] == "all")
    assert {h["name"] for h in qd["columns"]} == {"name", "status", "zzz"}
    assert next(h for h in qd["columns"] if h["name"] == "status") == {"name": "status", "hidden": True}


@pytest.mark.asyncio
async def test_column_hints_resolve_from_dictionary(pools: PoolRegistry) -> None:
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(default_language="en", entries={
        "name": DictionaryEntry(label="Item Name", format="text", l={"fr": "Nom"}),  # shared / common
        "status": DictionaryEntry(label="Status", format="boolean"),
    }, connectors={
        # the "db" connector has its own `name` entry (entry-level — wins outright, not a field merge);
        # it has no `status` entry → that one falls back to the shared one
        "db": DictionarySection(entries={"name": DictionaryEntry(label="DB Item Name", format="text", l={"fr": "Article DB"})}),
    })
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[QueryDef(
        name="all", sql="SELECT id, name, status FROM item ORDER BY id",
        # bare hint → look up under the column name; `dd` ref; inline `label` overrides everything
        columns=[ColumnHint(name="name"), ColumnHint(name="status", dd="status", hidden=True), ColumnHint(name="id", label="ID")],
    )])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    cols = {c.name: c for c in (await conn.execute("all")).columns}  # default language
    assert cols["name"].label == "DB Item Name" and cols["name"].format == "text"  # the [connectors.db] entry
    assert cols["status"].label == "Status" and cols["status"].hidden is True and cols["status"].format == "boolean"  # shared
    assert cols["id"].label == "ID" and cols["id"].format is None  # inline label, no dict entry → no format
    cols = {c.name: c for c in (await conn.execute("all", language="fr")).columns}
    assert cols["name"].label == "Article DB"  # the [connectors.db] translation
    assert cols["status"].label == "Status"    # no fr translation → default label
    # describe() resolves the *default* language; the `dd` ref shows through
    by = {h["name"]: h for h in next(q for q in conn.describe()["queries"] if q["name"] == "all")["columns"]}
    assert by["name"] == {"name": "name", "label": "DB Item Name", "format": "text"}
    assert by["status"] == {"name": "status", "dd": "status", "label": "Status", "hidden": True, "format": "boolean"}
    assert by["id"] == {"name": "id", "label": "ID"}


@pytest.mark.asyncio
async def test_column_hints_attach_display_rule(pools: PoolRegistry) -> None:
    # The dictionary's entries can carry a display rule (BOOLEAN / ENUM / LOOKUP — v1's dd_rules).
    # The SQL connector resolves it at result time and emits it as Column.rule for the frontend.
    from liberty.connectors.dictionary import (
        DictionaryEntry, DictionaryFile, DictionarySection, EnumDef, EnumValue, LookupDef,
    )
    d = DictionaryFile(connectors={"db": DictionarySection(
        entries={
            "status": DictionaryEntry(label="Status", format="boolean", rules="BOOLEAN", rules_values="on"),
            "kind": DictionaryEntry(label="Kind", rules="ENUM", rules_values="1"),
            "owner": DictionaryEntry(label="Owner", rules="LOOKUP", rules_values="users"),
            "password": DictionaryEntry(label="Password", rules="PASSWORD"),  # form-layer → no display rule
        },
        enums={"1": EnumDef(values=[EnumValue(value="A", label="Active", l={"fr": "Actif"}),
                                    EnumValue(value="I", label="Inactive")])},
        lookups={"users": LookupDef(query="users_get", value="USR_ID", label="USR_NAME")},
    )})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[QueryDef(
        name="q", sql="SELECT id, status, status AS kind, status AS owner, status AS password FROM item ORDER BY id",
        columns=[ColumnHint(name="status"), ColumnHint(name="kind"), ColumnHint(name="owner"), ColumnHint(name="password")],
    )])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    cols = {c.name: c for c in (await conn.execute("q", language="fr")).columns}
    assert cols["status"].rule == {"kind": "boolean", "true_value": "on"}
    assert cols["kind"].rule == {
        "kind": "enum",
        "values": [{"value": "A", "label": "Actif"}, {"value": "I", "label": "Inactive"}],  # fr falls back per-value
    }
    assert cols["owner"].rule == {
        "kind": "lookup", "connector": "db", "query": "users_get", "value": "USR_ID", "label": "USR_NAME",
    }
    assert cols["password"].rule is None  # PASSWORD is a form-layer rule — not a display transform
    # the resolved rule rides along in to_dict() (so the frontend gets it via /api/sql/...)
    cols_d = {c["name"]: c for c in (await conn.execute("q")).to_dict()["columns"]}
    assert cols_d["status"]["rule"]["kind"] == "boolean"
    assert "rule" not in cols_d["password"]  # no rule → no key emitted


@pytest.mark.asyncio
async def test_column_hints_match_case_insensitively(pools: PoolRegistry) -> None:
    # The database may report column names in a different case than the hints use
    # (Postgres folds unquoted identifiers to lowercase; v1's migrated col_target/dd are
    # uppercase) — hints still match, and the emitted column keeps the *discovered* case
    # so it lines up with the row dict's keys.
    conn = _connector(
        pools,
        QueryDef(
            name="all",
            sql='SELECT id AS "ID", name AS "Name", status AS "STATUS" FROM item ORDER BY id',
            columns=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ],
        ),
    )
    result = await conn.execute("all")
    assert [c.name for c in result.columns] == ["Name", "ID", "STATUS"]   # discovered case, hint order
    by = {c.name: c for c in result.columns}
    assert by["Name"].label == "Item Name"
    assert by["ID"].label == "Identifier"
    assert by["STATUS"].hidden is True
    assert result.rows[0] == {"ID": 1, "Name": "a", "STATUS": "on"}        # row keys = discovered case


@pytest.mark.asyncio
async def test_named_params_and_missing_param_binds_null(pools: PoolRegistry) -> None:
    conn = _connector(
        pools,
        QueryDef(
            name="filtered",
            sql="SELECT id FROM item WHERE (:status IS NULL OR status = :status) ORDER BY id",
            params=[ParamDef(name="status")],
        ),
    )
    assert [r["id"] for r in (await conn.execute("filtered", {"status": "on"})).rows] == [1, 2]
    # No param supplied → :status bound to SQL NULL → filter is a no-op.
    assert [r["id"] for r in (await conn.execute("filtered")).rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_param_default_applied(pools: PoolRegistry) -> None:
    conn = _connector(
        pools,
        QueryDef(
            name="def",
            sql="SELECT id FROM item WHERE status = :status ORDER BY id",
            params=[ParamDef(name="status", default="off")],
        ),
    )
    assert [r["id"] for r in (await conn.execute("def")).rows] == [3]


@pytest.mark.asyncio
async def test_max_rows_truncation(pools: PoolRegistry) -> None:
    conn = _connector(pools, QueryDef(name="all", sql="SELECT id FROM item ORDER BY id"), max_rows=2)
    result = await conn.execute("all")
    assert len(result.rows) == 2
    assert result.truncated is True


@pytest.mark.asyncio
async def test_row_cap_precedence(pools: PoolRegistry) -> None:
    # connector leaves max_rows unset → its effective default falls back to the pool's (passed here as 1)
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="all", sql="SELECT id FROM item ORDER BY id"),
        QueryDef(name="cap2", sql="SELECT id FROM item ORDER BY id", max_rows=2),  # per-query override
    ])
    conn = SQLConnector("db", cfg, pools, pool_max_rows=1)
    r = await conn.execute("all")
    assert len(r.rows) == 1 and r.truncated is True            # pool default (1)
    r = await conn.execute("cap2")
    assert len(r.rows) == 2 and r.truncated is True            # query's max_rows (2)
    r = await conn.execute("all", max_rows=3)
    assert len(r.rows) == 3 and r.truncated is False           # per-request override (3) — all rows
    r = await conn.execute("all", max_rows=10_000_000)
    assert len(r.rows) == 3                                     # clamped to HARD_MAX_ROWS, but only 3 rows exist


@pytest.mark.asyncio
async def test_write_requires_writable(pools: PoolRegistry) -> None:
    conn = _connector(pools, QueryDef(name="ins", sql="INSERT INTO item (id, name) VALUES (9, 'z')"))
    with pytest.raises(WriteNotAllowedError):
        await conn.execute("ins")


@pytest.mark.asyncio
async def test_write_when_writable(pools: PoolRegistry) -> None:
    conn = _connector(
        pools,
        QueryDef(name="ins", sql="INSERT INTO item (id, name) VALUES (:id, :name)", writable=True),
        QueryDef(name="count", sql="SELECT COUNT(*) AS n FROM item"),
    )
    result = await conn.execute("ins", {"id": 9, "name": "z"})
    assert result.statement_type == "INSERT"
    assert result.rowcount == 1
    assert (await conn.execute("count")).rows == [{"n": 4}]


@pytest.mark.asyncio
async def test_disallowed_statement_rejected_before_connecting(pools: PoolRegistry) -> None:
    conn = _connector(pools, QueryDef(name="bad", sql="DROP TABLE item", writable=True))
    with pytest.raises(StatementNotAllowedError):
        await conn.execute("bad")
    # The table is still there.
    ok = _connector(pools, QueryDef(name="c", sql="SELECT COUNT(*) AS n FROM item"))
    assert (await ok.execute("c")).rows == [{"n": 3}]


@pytest.mark.asyncio
async def test_dialect_variant_selected(pools: PoolRegistry) -> None:
    # The fixture pool is SQLite → a query carrying a `sqlite` variant resolves to it;
    # one with only `default` + `oracle` falls back to `default`.
    c1 = _connector(pools, QueryDef(name="v", sql={"default": "SELECT 1 AS x", "sqlite": "SELECT 2 AS x"}))
    assert (await c1.execute("v")).rows == [{"x": 2}]
    c2 = _connector(pools, QueryDef(name="v", sql={"default": "SELECT 9 AS x", "oracle": "SELECT 8 AS x"}))
    assert (await c2.execute("v")).rows == [{"x": 9}]
    # describe() surfaces the dialect list
    assert c2.describe()["queries"][0]["dialects"] == ["default", "oracle"]


@pytest.mark.asyncio
async def test_cte_select_runs(pools: PoolRegistry) -> None:
    # A WITH ... SELECT resolves to SELECT — not rejected by the allow-list, runs as a read.
    conn = _connector(pools, QueryDef(name="cte", sql="WITH on_items AS (SELECT id FROM item WHERE status = 'on') SELECT COUNT(*) AS n FROM on_items"))
    result = await conn.execute("cte")
    assert result.statement_type == "SELECT" and result.rows == [{"n": 2}]


@pytest.mark.asyncio
async def test_cte_write_requires_writable(pools: PoolRegistry) -> None:
    # A WITH ... DELETE resolves to DELETE → still gated by `writable` (the writability
    # check is the orthogonal gate, regardless of the CTE prefix).
    sql = "WITH gone AS (SELECT id FROM item WHERE status = 'off') DELETE FROM item WHERE id IN (SELECT id FROM gone)"
    with pytest.raises(WriteNotAllowedError):
        await _connector(pools, QueryDef(name="cte_del", sql=sql)).execute("cte_del")
    result = await _connector(pools, QueryDef(name="cte_del", sql=sql, writable=True)).execute("cte_del")
    assert result.statement_type == "DELETE"  # (sqlite doesn't report rowcount for WITH-DML)
    count = _connector(pools, QueryDef(name="n", sql="SELECT COUNT(*) AS n FROM item"))
    assert (await count.execute("n")).rows == [{"n": 2}]  # the 'off' row was deleted


@pytest.mark.asyncio
async def test_unknown_query(pools: PoolRegistry) -> None:
    conn = _connector(pools, QueryDef(name="x", sql="SELECT 1"))
    with pytest.raises(QueryNotFoundError):
        await conn.execute("nope")


def test_describe_resolves_companion_queries(pools: PoolRegistry) -> None:
    conn = _connector(
        pools,
        QueryDef(name="item_get", sql="SELECT id, name FROM item ORDER BY id"),
        QueryDef(name="item_put", sql="UPDATE item SET name = :name WHERE id = :id", writable=True),
        QueryDef(name="item_post", sql="INSERT INTO item (id, name) VALUES (:id, :name)", writable=True),
        QueryDef(name="readonly_get", sql="SELECT id FROM item"),                                  # no companions
        QueryDef(name="lonely_get", sql="SELECT 1 AS x", update_query="item_put", delete_query="item_post"),  # explicit
        QueryDef(name="bad_get", sql="SELECT 1 AS x", update_query="item_get"),                    # points at a non-writable query → ignored
    )
    by = {q["name"]: q for q in conn.describe()["queries"]}
    assert (by["item_get"]["update_query"], by["item_get"]["insert_query"], by["item_get"]["delete_query"]) == (
        "item_put", "item_post", None,                          # <base>_get → <base>_put / _post (no _delete defined)
    )
    assert (by["readonly_get"]["update_query"], by["readonly_get"]["insert_query"]) == (None, None)
    assert (by["lonely_get"]["update_query"], by["lonely_get"]["delete_query"]) == ("item_put", "item_post")  # explicit
    assert by["bad_get"]["update_query"] is None                # explicit target isn't writable
    assert by["item_put"]["update_query"] is None               # not a _get query


def test_describe_lists_metadata(pools: PoolRegistry) -> None:
    conn = _connector(
        pools,
        QueryDef(name="all", sql="SELECT id FROM item WHERE status = :status", params=[ParamDef(name="status")]),
    )
    desc = conn.describe()
    assert desc["type"] == "sql"
    assert desc["pool"] == "test"
    q = desc["queries"][0]
    assert q["name"] == "all"
    assert q["statement_type"] == "SELECT"
    assert q["bind_params"] == ["status"]
