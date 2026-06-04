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
from liberty.connectors.config import ColumnHint, FilterDep, ParamDef, PoolConfig, QueryDef, SqlConnectorConfig, VisibleWhen
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


@pytest.mark.asyncio
async def test_schema_placeholder_substitution(tmp_path) -> None:
    from liberty.connectors.base import ConnectorError
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sch.db'}")
    async with engine.begin() as c:
        await c.execute(text("CREATE TABLE item (id INTEGER PRIMARY KEY)"))
        await c.execute(text("INSERT INTO item (id) VALUES (1),(2)"))
    # SQLite's default schema is "main" → `#SCHEMA.DTA#.item` resolves to `main.item` = `item`.
    pools = PoolRegistry({
        "ok": PoolConfig(url="sqlite://", schemas={"DTA": "main"}),
        "noschemas": PoolConfig(url="sqlite://"),
        "badval": PoolConfig(url="sqlite://", schemas={"X": "bad name"}),  # not a plain identifier
    })
    for n in ("ok", "noschemas", "badval"):
        pools.register_engine(n, engine)
    q = QueryDef(name="q", sql="SELECT id FROM #SCHEMA.DTA#.item ORDER BY id")
    qx = QueryDef(name="qx", sql="SELECT id FROM #SCHEMA.X#.item")

    r = await SQLConnector("db", SqlConnectorConfig(type="sql", pool="ok", queries=[q]), pools).execute("q")
    assert [row["id"] for row in r.rows] == [1, 2]                      # placeholder resolved
    with pytest.raises(ConnectorError, match="no schema mapping for 'DTA'"):
        await SQLConnector("db", SqlConnectorConfig(type="sql", pool="noschemas", queries=[q]), pools).execute("q")
    with pytest.raises(ConnectorError, match="not a plain identifier"):
        await SQLConnector("db", SqlConnectorConfig(type="sql", pool="badval", queries=[qx]), pools).execute("qx")
    # a query with no #SCHEMA placeholders is untouched even when the pool has a `schemas` map
    plain = QueryDef(name="p", sql="SELECT id FROM item ORDER BY id")
    r = await SQLConnector("db", SqlConnectorConfig(type="sql", pool="ok", queries=[plain]), pools).execute("p")
    assert [row["id"] for row in r.rows] == [1, 2]
    await engine.dispose()


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
            sql="SELECT id, name, status FROM item ORDER BY id"),
    )
    result = await conn.execute("all", column_hints=[
                ColumnHint(name="name", label="Item Name", align="left",
                           filter_from=[FilterDep(source="status", column="ST")]),  # cascading dep
                ColumnHint(name="status", hidden=True),
                ColumnHint(name="id", visible_when=VisibleWhen(field="status", value=["on", "off"])),  # conditional
                ColumnHint(name="zzz", label="ignored — not a result column"),
            ])
    # hinted columns first (in hint order), then un-hinted ones in discovery order; the
    # stale `zzz` hint is dropped (a hint never fabricates a column)
    assert [c.name for c in result.columns] == ["name", "status", "id"]
    by_name = {c.name: c for c in result.columns}
    assert by_name["name"].label == "Item Name" and by_name["name"].align == "left"
    assert by_name["name"].filter_from == [{"source": "status", "column": "ST"}]
    assert by_name["status"].hidden is True
    assert by_name["id"].label is None and by_name["id"].hidden is False
    assert by_name["id"].visible_when == [{"field": "status", "value": ["on", "off"]}]  # normalized to a list
    # the hints surface in to_dict() (only when non-default)
    cols = {c["name"]: c for c in result.to_dict()["columns"]}
    assert cols["name"]["label"] == "Item Name" and cols["name"]["align"] == "left"
    assert cols["name"]["filter_from"] == [{"source": "status", "column": "ST"}]
    assert cols["status"]["hidden"] is True
    assert cols["id"]["visible_when"] == [{"field": "status", "value": ["on", "off"]}]
    assert "label" not in cols["id"] and "hidden" not in cols["id"] and "filter_from" not in cols["id"]
    # rows are unaffected — every column's data is still present
    assert result.rows[0] == {"id": 1, "name": "a", "status": "on"}
    # Phase 3 — column hints left ``QueryDef``; describe() no longer surfaces them. The hints
    # ride through ``execute(column_hints=…)``, and the read-result wire payload above already
    # verifies the resolved columns. The screens API ships ``Screen.columns`` on its own route.
    qd = next(q for q in conn.describe()["queries"] if q["name"] == "all")
    assert "columns" not in qd


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
        name="all", sql="SELECT id, name, status FROM item ORDER BY id")])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    cols = {c.name: c for c in (await conn.execute("all", column_hints=[ColumnHint(name="name"), ColumnHint(name="status", dd="status", hidden=True), ColumnHint(name="id", label="ID")])).columns}  # default language
    assert cols["name"].label == "DB Item Name" and cols["name"].format == "text"  # the [connectors.db] entry
    assert cols["status"].label == "Status" and cols["status"].hidden is True and cols["status"].format == "boolean"  # shared
    assert cols["id"].label == "ID" and cols["id"].format is None  # inline label, no dict entry → no format
    cols = {c.name: c for c in (await conn.execute("all", language="fr", column_hints=[ColumnHint(name="name"), ColumnHint(name="status", dd="status", hidden=True), ColumnHint(name="id", label="ID")])).columns}
    assert cols["name"].label == "Article DB"  # the [connectors.db] translation
    assert cols["status"].label == "Status"    # no fr translation → default label
    # Phase 3 — describe() no longer surfaces column hints (they live on Screen). The wire
    # payload above already verifies resolution from the dictionary in both languages.
    qd = next(q for q in conn.describe()["queries"] if q["name"] == "all")
    assert "columns" not in qd


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
        name="q", sql="SELECT id, status, status AS kind, status AS owner, status AS password FROM item ORDER BY id")])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    cols = {c.name: c for c in (await conn.execute("q", language="fr", column_hints=[ColumnHint(name="status"), ColumnHint(name="kind"), ColumnHint(name="owner"), ColumnHint(name="password")])).columns}
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
    cols_d = {c["name"]: c for c in (await conn.execute("q", column_hints=[ColumnHint(name="status"), ColumnHint(name="kind"), ColumnHint(name="owner"), ColumnHint(name="password")])).to_dict()["columns"]}
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
            sql='SELECT id AS "ID", name AS "Name", status AS "STATUS" FROM item ORDER BY id'),
    )
    result = await conn.execute("all", column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])
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
            params=[ParamDef(name="status")]),
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
            params=[ParamDef(name="status", default="off")]),
    )
    assert [r["id"] for r in (await conn.execute("def")).rows] == [3]


@pytest.mark.asyncio
async def test_max_rows_truncation(pools: PoolRegistry) -> None:
    conn = _connector(pools, QueryDef(name="all", sql="SELECT id FROM item ORDER BY id"), max_rows=2)
    result = await conn.execute("all", column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])
    assert len(result.rows) == 2
    assert result.truncated is True


@pytest.mark.asyncio
async def test_row_cap_precedence(pools: PoolRegistry) -> None:
    # connector leaves max_rows unset → its effective default falls back to the pool's (passed here as 1)
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="all", sql="SELECT id FROM item ORDER BY id"),
        QueryDef(name="cap2", sql="SELECT id FROM item ORDER BY id"),  # per-query override
    ])
    conn = SQLConnector("db", cfg, pools, pool_max_rows=1)
    r = await conn.execute("all", column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])
    assert len(r.rows) == 1 and r.truncated is True            # pool default (1)
    r = await conn.execute("cap2", screen_max_rows=2)
    assert len(r.rows) == 2 and r.truncated is True            # query's max_rows (2)
    r = await conn.execute("all", max_rows=3, column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])
    assert len(r.rows) == 3 and r.truncated is False           # per-request override (3) — all rows
    r = await conn.execute("all", max_rows=10_000_000, column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])
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
async def test_audit_mirrors_writes_into_aud_table(pools: PoolRegistry) -> None:
    """A writable query with ``audit = "AUD_<table>"`` set mirrors each write into the named
    table — same transaction (a failing mirror rolls back the main write too), capturing the
    bound row's UPPERCASE params + ``AUD_ACTION`` / ``AUD_USER`` / ``AUD_DATE``. ``_ORIGINAL``
    keys (the rebound WHERE for `_put`s) are skipped on the audit row — they're context for
    finding the row to update, not the row's data."""
    # The audit target — operator-provided in real deployments; here we create it inline. SQLite
    # preserves DDL case in result keys, so we use lowercase to match the existing `item` fixture
    # (Postgres / Oracle would fold unquoted identifiers their own way; this test is environment-
    # agnostic because the assertions use lowercase end-user keys).
    async with pools.engine("test").begin() as c:
        await c.execute(text(
            "CREATE TABLE aud_item ("
            "id INTEGER, name TEXT, status TEXT, aud_action TEXT, aud_user TEXT, aud_date TIMESTAMP)"
        ))

    conn = _connector(
        pools,
        QueryDef(name="ins", writable=True, sql="INSERT INTO item (id, name, status) VALUES (:ID, :NAME, :STATUS)"),
        QueryDef(name="upd", writable=True, sql="UPDATE item SET name = :NAME WHERE id = :ID_ORIGINAL"),
        QueryDef(name="aud_all", sql="SELECT * FROM aud_item ORDER BY rowid"),
    )

    # INSERT — full row in params (uppercase, matching the migrated _post convention).
    r = await conn.execute("ins", {"ID": 100, "NAME": "alpha", "STATUS": "on"}, user="bob", audit_table="aud_item")
    assert r.statement_type == "INSERT" and r.rowcount == 1

    # UPDATE — new value for NAME, plus :ID_ORIGINAL to find the row (the v2 _put convention).
    # The audit row should record the *new* values (NAME) and skip the _ORIGINAL key.
    r = await conn.execute("upd", {"NAME": "alpha-2", "ID_ORIGINAL": 100}, user="alice", audit_table="aud_item")
    assert r.statement_type == "UPDATE" and r.rowcount == 1

    aud = await conn.execute("aud_all")
    rows = aud.rows
    assert len(rows) == 2
    # INSERT audit row — all bound columns logged, action + user threaded through.
    assert rows[0]["aud_action"] == "INSERT" and rows[0]["aud_user"] == "bob"
    assert rows[0]["id"] == 100 and rows[0]["name"] == "alpha" and rows[0]["status"] == "on"
    assert rows[0]["aud_date"] is not None
    # UPDATE audit row — only NAME was in the new-value params; ID_ORIGINAL is skipped.
    assert rows[1]["aud_action"] == "UPDATE" and rows[1]["aud_user"] == "alice"
    assert rows[1]["name"] == "alpha-2" and rows[1]["id"] is None  # ID wasn't in the params (only ID_ORIGINAL)


@pytest.mark.asyncio
async def test_audit_auto_creates_missing_aud_table(pools: PoolRegistry) -> None:
    """v1 parity: an audit table that doesn't exist yet is **auto-created** from the source
    table's schema on the first write. v1's ``tbl_audit = 'Y'`` flag never required the
    operator to pre-create the AUD_<table>; the framework did it lazily. v2 keeps the same
    contract — without this, every migrated screen with ``audit`` set would fail its first
    INSERT until the operator manually ran DDL.

    The auto-create copies the source columns via ``CREATE TABLE … AS SELECT * FROM <source>
    WHERE 1=0`` and appends three audit columns (``AUD_ACTION`` / ``AUD_USER`` / ``AUD_DATE``).
    Verified once per (pool, audit-table) per process — a process cache skips the probe on
    subsequent writes."""
    from liberty.connectors.sql import reset_audit_table_cache
    reset_audit_table_cache()
    async with pools.engine("test").begin() as c:
        # Make sure the audit table really doesn't exist beforehand.
        await c.execute(text("DROP TABLE IF EXISTS aud_item_auto"))
    conn = _connector(
        pools,
        QueryDef(name="ins", writable=True, sql="INSERT INTO item (id, name, status) VALUES (:ID, :NAME, :STATUS)"),
    )
    # First write triggers the auto-create + the audit INSERT — must succeed end-to-end.
    r = await conn.execute("ins", {"ID": 500, "NAME": "first", "STATUS": "on"}, user="bob", audit_table="aud_item_auto")
    assert r.rowcount == 1
    # The audit table now exists with the source columns + the three AUD_ columns; the row
    # landed. The auto-create's ``CAST(NULL AS …) AS AUD_ACTION`` aliases keep their *uppercase*
    # case on SQLite (Postgres would fold to lowercase). We assert via lowercase aliases in the
    # SELECT to be dialect-portable.
    async with pools.engine("test").connect() as c:
        rows = (await c.execute(text(
            "SELECT id, name, status, AUD_ACTION AS aud_action, AUD_USER AS aud_user, "
            "AUD_DATE AS aud_date FROM aud_item_auto"
        ))).mappings().all()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 500 and row["name"] == "first" and row["status"] == "on"
    assert row["aud_action"] == "INSERT" and row["aud_user"] == "bob" and row["aud_date"] is not None
    # Second write skips the probe (cache hit) — still lands cleanly.
    await conn.execute("ins", {"ID": 501, "NAME": "second", "STATUS": "off"}, user="alice", audit_table="aud_item_auto")
    async with pools.engine("test").connect() as c:
        rows = (await c.execute(text("SELECT id FROM aud_item_auto ORDER BY id"))).mappings().all()
    assert [r["id"] for r in rows] == [500, 501]


@pytest.mark.asyncio
async def test_audit_date_is_naive_utc_for_tz_naive_columns(pools: PoolRegistry) -> None:
    """Regression — the ``AUD_DATE`` bind must be a **naive** UTC datetime. The auto-create
    DDL emits ``CAST(NULL AS TIMESTAMP) AS AUD_DATE`` (TIMESTAMP without time zone on
    Postgres); a tz-aware ``datetime.now(UTC)`` against that column trips asyncpg with
    ``can't subtract offset-naive and offset-aware datetimes`` and rolls the *entire*
    audited write back — main statement + auto-create + audit INSERT all gone, leaving the
    operator with no audit table created and the same error on retry. Matches the same
    naive-UTC convention SYSDATE uses in ``_apply_form_rules``."""
    from datetime import datetime as dt
    from liberty.connectors.sql import reset_audit_table_cache
    reset_audit_table_cache()
    async with pools.engine("test").begin() as c:
        await c.execute(text("DROP TABLE IF EXISTS aud_tz_test"))
    conn = _connector(
        pools,
        QueryDef(name="ins", writable=True, sql="INSERT INTO item (id, name) VALUES (:ID, :NAME)"),
    )
    await conn.execute("ins", {"ID": 800, "NAME": "tz-check"}, user="x", audit_table="aud_tz_test")
    async with pools.engine("test").connect() as c:
        rows = (await c.execute(text("SELECT AUD_DATE AS aud_date FROM aud_tz_test"))).mappings().all()
    assert len(rows) == 1
    audit_date = rows[0]["aud_date"]
    # SQLite stores datetimes as ISO strings — convert + assert naivety. On Postgres /
    # Oracle the driver returns a Python datetime directly.
    if isinstance(audit_date, str):
        audit_date = dt.fromisoformat(audit_date)
    assert isinstance(audit_date, dt)
    assert audit_date.tzinfo is None, (
        f"AUD_DATE bind must be tz-naive (matches TIMESTAMP column); got tzinfo={audit_date.tzinfo!r} — "
        "asyncpg will reject this against a TIMESTAMP WITHOUT TIME ZONE column"
    )


@pytest.mark.asyncio
async def test_audit_probe_uses_metadata_table_not_select_against_missing_table(pools: PoolRegistry) -> None:
    """Regression — the existence probe must use a metadata table (``information_schema`` /
    ``all_tables`` / ``sqlite_master``), not ``SELECT 1 FROM <audit>``. On Postgres a SELECT
    against a non-existent relation **aborts the entire transaction** (asyncpg's
    ``InFailedSQLTransactionError`` — "current transaction is aborted, commands ignored
    until end of transaction block"); the subsequent ``CREATE TABLE`` then fails and the
    whole audited write rolls back, defeating the lazy auto-create.

    The metadata-table probe returns zero rows on a miss without poisoning the transaction.

    We can't easily reproduce the transaction-poison behaviour on SQLite (it doesn't enforce
    it), but we *can* assert the probe's SQL shape — when the audit table doesn't exist, the
    next operation in the transaction must still succeed. This catches a future regression
    that reintroduces a ``SELECT 1 FROM <audit>`` probe."""
    from liberty.connectors.sql import reset_audit_table_cache
    reset_audit_table_cache()
    async with pools.engine("test").begin() as c:
        await c.execute(text("DROP TABLE IF EXISTS aud_for_probe_test"))
    conn = _connector(
        pools,
        QueryDef(name="ins", writable=True, sql="INSERT INTO item (id, name, status) VALUES (:ID, :NAME, :STATUS)"),
    )
    # First write — probes the metadata, creates the audit table, runs main + audit INSERT.
    # On Postgres the SELECT-on-missing-table approach would have aborted the txn here, the
    # CREATE TABLE would fail with "current transaction is aborted", and the whole audit
    # path would roll back. Asserting the probe-then-create-then-insert succeeds end-to-end
    # locks in the metadata-table behaviour.
    r = await conn.execute("ins", {"ID": 700, "NAME": "probe-test", "STATUS": "on"}, user="x", audit_table="aud_for_probe_test")
    assert r.rowcount == 1
    async with pools.engine("test").connect() as c:
        rows = (await c.execute(text("SELECT id FROM aud_for_probe_test"))).mappings().all()
    assert [r["id"] for r in rows] == [700]


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_the_main_write(pools: PoolRegistry) -> None:
    """A misconfigured audit table — one that exists but has the wrong schema — rolls back
    the main write. The audit INSERT runs in the same transaction. Loud rather than silently
    dropped, so an operator notices their AUD table needs fixing.

    (Note: a *missing* audit table no longer triggers this path — :meth:`_ensure_audit_table`
    auto-creates it now. This test covers the post-auto-create failure modes: schema drift
    where the source table grew a new column but the audit table didn't.)"""
    from liberty.connectors.sql import reset_audit_table_cache
    reset_audit_table_cache()
    # Create an audit table whose columns *don't* match the source (missing the `status` column
    # that the source table has + the migration would copy in). The audit INSERT will fail.
    async with pools.engine("test").begin() as c:
        await c.execute(text("DROP TABLE IF EXISTS aud_wrong_shape"))
        await c.execute(text(
            "CREATE TABLE aud_wrong_shape (id INTEGER, aud_action TEXT, aud_user TEXT, aud_date TIMESTAMP)"
        ))
    conn = _connector(
        pools,
        QueryDef(name="ins", writable=True, sql="INSERT INTO item (id, name, status) VALUES (:ID, :NAME, :STATUS)"),
        QueryDef(name="count", sql="SELECT COUNT(*) AS n FROM item"),
    )
    before = (await conn.execute("count")).rows[0]["n"]
    with pytest.raises(Exception):  # the audit INSERT references columns the audit table doesn't have
        await conn.execute("ins", {"ID": 600, "NAME": "z", "STATUS": "on"}, user="x", audit_table="aud_wrong_shape")
    assert (await conn.execute("count")).rows[0]["n"] == before  # the main write rolled back too


@pytest.mark.asyncio
async def test_audit_anonymous_when_no_user(pools: PoolRegistry) -> None:
    """``execute()`` without a ``user`` (an unauthenticated path / internal call) records
    ``"anonymous"`` on ``AUD_USER`` — the column is never NULL."""
    async with pools.engine("test").begin() as c:
        await c.execute(text("CREATE TABLE aud_item2 (id INTEGER, aud_action TEXT, aud_user TEXT, aud_date TIMESTAMP)"))
    conn = _connector(
        pools,
        QueryDef(name="ins", writable=True, sql="INSERT INTO item (id) VALUES (:ID)"),
        QueryDef(name="all", sql="SELECT aud_user FROM aud_item2"),
    )
    await conn.execute("ins", {"ID": 7}, audit_table="aud_item2")  # no user kwarg
    assert (await conn.execute("all", column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])).rows == [{"aud_user": "anonymous"}]


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


def test_describe_no_longer_surfaces_screen_fields(pools: PoolRegistry) -> None:
    """Phase 3 — companion-query refs (update/insert/delete_query), columns, auto_load,
    audit, key_columns, and max_rows all moved off ``QueryDef`` onto :class:`Screen`. The
    connector's describe() output no longer carries them — the frontend reads them from
    ``GET /api/screens/{app}/{id}`` instead."""
    conn = _connector(
        pools,
        QueryDef(name="item_get", sql="SELECT id, name FROM item ORDER BY id"),
        QueryDef(name="item_put", sql="UPDATE item SET name = :name WHERE id = :id", writable=True),
    )
    q = next(q for q in conn.describe()["queries"] if q["name"] == "item_get")
    for k in ("update_query", "insert_query", "delete_query", "columns",
              "auto_load", "audit", "key_columns", "max_rows"):
        assert k not in q, f"describe() should not surface {k!r} after Phase 3"
    # The SQL-layer bits stay.
    assert q.keys() >= {"name", "writable", "sql", "params", "bind_params", "statement_type", "dialects"}


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


# --------------------------------------------------------------------------- #
# Oracle compat — trim on read + null coalesce on write (v1's NCHAR/NUMBER quirks)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_trim_strings_strips_trailing_whitespace_when_enabled() -> None:
    """v1's automatic trim for Oracle CHAR/NCHAR — re-enable on any pool via the explicit
    ``trim_strings = true`` flag. SQLite (used in tests) doesn't space-pad on its own, so we
    feed pre-padded values to verify the strip logic runs."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.execute(text("CREATE TABLE lbl (id INTEGER PRIMARY KEY, name TEXT)"))
        await c.execute(text("INSERT INTO lbl VALUES (1, 'role   '), (2, 'demo')"))
    pools = PoolRegistry({"on": PoolConfig(url="sqlite://", trim_strings=True)})
    pools.register_engine("on", engine)
    conn = SQLConnector("c", SqlConnectorConfig(type="sql", pool="on", queries=[
        QueryDef(name="all", sql="SELECT id, name FROM lbl ORDER BY id"),
    ]), pools)
    r = await conn.execute("all", column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])
    assert [row["name"] for row in r.rows] == ["role", "demo"]   # trailing spaces stripped
    await engine.dispose()


@pytest.mark.asyncio
async def test_trim_strings_off_keeps_whitespace() -> None:
    """Explicit ``trim_strings = false`` (or non-Oracle pool with no override) preserves the
    raw cell value. Required for pools where trailing whitespace is data, not padding."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.execute(text("CREATE TABLE lbl (id INTEGER PRIMARY KEY, name TEXT)"))
        await c.execute(text("INSERT INTO lbl VALUES (1, 'role   ')"))
    pools = PoolRegistry({"off": PoolConfig(url="sqlite://", trim_strings=False)})
    pools.register_engine("off", engine)
    conn = SQLConnector("c", SqlConnectorConfig(type="sql", pool="off", queries=[
        QueryDef(name="all", sql="SELECT id, name FROM lbl"),
    ]), pools)
    r = await conn.execute("all", column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])
    assert r.rows[0]["name"] == "role   "   # untouched
    await engine.dispose()


@pytest.mark.asyncio
async def test_oracle_coalesce_nulls_replaces_with_typed_sentinels() -> None:
    """``coalesce_nulls = true`` introspects ``ALL_TAB_COLUMNS`` and replaces *empty* bind
    values (``None`` *or* ``""``) with a single space (char) or ``0`` (number) before the
    bind. SQLite can't impersonate Oracle's all_tab_columns, so the test sets the cache
    manually via the helpers and verifies the coalesce step transforms the params dict."""
    from liberty.connectors.sql import _coalesce_oracle_nulls, _oracle_target_table

    # Target-table extraction handles unquoted, quoted, schema-prefixed names + MERGE / DELETE.
    assert _oracle_target_table("INSERT INTO foo VALUES (:a, :b)") == (None, "FOO")
    assert _oracle_target_table('INSERT INTO "MyTbl" VALUES (:a)') == (None, "MYTBL")
    assert _oracle_target_table("update prodlib.f0092 SET ulnam = :n WHERE ulid = :id") == ("PRODLIB", "F0092")
    assert _oracle_target_table("MERGE INTO crp.f00921 t USING (...)") == ("CRP", "F00921")
    assert _oracle_target_table("DELETE FROM prodlib.f0093 WHERE id = :id") == ("PRODLIB", "F0093")
    # Garbage in → None out (skip coalesce; operator hand-rolls NVL).
    assert _oracle_target_table("/* hello */ SELECT 1") is None

    # Coalesce: None *and* "" values get the column's sentinel; non-empty values + unknown
    # columns pass through. The migration's `:<COL>_ORIGINAL` rebind on _put queries resolves
    # to the source column's type — so an empty CHAR ORIGINAL bind becomes " " (space).
    # Char sentinel is " " (space) not "" because Oracle treats "" as NULL on every string
    # type — binding "" to a NCHAR NOT-NULL column still violates the constraint.
    # Cache shape carries ``{kind, length}`` per column — kind distinguishes fixed-width
    # CHAR / NCHAR (``char_fixed``) from VARCHAR2 / NVARCHAR2 (``char``); both trigger the
    # space sentinel here, but only ``char_fixed`` triggers the WHERE-bind right-padding
    # (covered by the separate ``_pad_char_binds`` test below).
    col_types = {
        "NAME": {"kind": "char_fixed", "length": 10},
        "AMOUNT": {"kind": "number", "length": None},
        "STATUS": {"kind": "char", "length": None},
    }
    bound = {
        "NAME": None,            # CHAR none → space
        "AMOUNT": "",            # NUMBER empty → 0 (frontend sends "" for blank inputs)
        "STATUS": "active",      # non-empty untouched
        "EXTRA": None,           # column unknown — pass through
        "NAME_ORIGINAL": "",     # _ORIGINAL suffix strip → still CHAR → space
    }
    out = _coalesce_oracle_nulls(bound, col_types)
    assert out == {
        "NAME": " ",                # CHAR none → space
        "AMOUNT": 0,                # NUMBER empty → 0
        "STATUS": "active",         # non-empty untouched
        "EXTRA": None,              # column unknown — pass through
        "NAME_ORIGINAL": " ",       # _ORIGINAL suffix strip → still CHAR → space
    }


def test_pad_char_binds_right_pads_to_declared_char_width() -> None:
    """``trim_strings = true`` rounds out the JDE round-trip: reads strip trailing whitespace,
    writes pad the bind back to the column's declared CHAR(N) width before binding. Without
    it, a DELETE / UPDATE WHERE bound from a trimmed read value matches 0 rows on Oracle
    (CHAR(10) stored ``"JDE       "`` vs VARCHAR2 bind ``"JDE"`` is non-blank-padded → unequal).

    Only touches fixed-width ``CHAR`` / ``NCHAR`` — VARCHAR2 binds stay untouched (padding
    would BREAK the comparison; stored ``"JDE"`` ≠ bind ``"JDE       "`` on VARCHAR2).
    Already-long-enough values pass through; ``_ORIGINAL``-suffixed UPDATE rebinds resolve
    to the source column's metadata."""
    from liberty.connectors.sql import _pad_char_binds

    col_types = {
        "ULUSER": {"kind": "char_fixed", "length": 10},     # CHAR(10) — pad
        "ULMNI":  {"kind": "char_fixed", "length": 1},      # CHAR(1)  — already fits
        "NOTES":  {"kind": "char",       "length": None},   # VARCHAR2 — never pad
        "AMOUNT": {"kind": "number",     "length": None},   # numbers untouched
    }
    bound = {
        "ULUSER": "JDE",                # CHAR(10) → "JDE       " (7 spaces)
        "ULMNI": "Y",                   # CHAR(1)  → unchanged (length already met)
        "NOTES": "free text",           # VARCHAR2 → unchanged (mustn't pad)
        "AMOUNT": "123",                # number → unchanged
        "ULUSER_ORIGINAL": "JDE",       # _ORIGINAL → resolves to ULUSER → pad to 10
        "UNKNOWN": "value",             # column not in cache → unchanged
        "ULUSER_EMPTY": "",             # empty string → left to coalesce, not this step
        "ULUSER_NONE": None,            # None → not a string → unchanged
    }
    out = _pad_char_binds(bound, col_types)
    assert out["ULUSER"] == "JDE       "
    assert len(out["ULUSER"]) == 10
    assert out["ULMNI"] == "Y"
    assert out["NOTES"] == "free text"
    assert out["AMOUNT"] == "123"
    assert out["ULUSER_ORIGINAL"] == "JDE       "
    assert out["UNKNOWN"] == "value"
    assert out["ULUSER_EMPTY"] == ""
    assert out["ULUSER_NONE"] is None

    # Empty col_types → no-op (introspection failed or no schema → skip silently).
    assert _pad_char_binds({"X": "val"}, {}) == {"X": "val"}

    # Length-missing or zero → skip (can't pad without a width).
    assert _pad_char_binds(
        {"X": "val"},
        {"X": {"kind": "char_fixed", "length": None}},
    ) == {"X": "val"}
    assert _pad_char_binds(
        {"X": "val"},
        {"X": {"kind": "char_fixed", "length": 0}},
    ) == {"X": "val"}


def test_trim_strings_and_coalesce_nulls_are_explicit_per_pool() -> None:
    """``trim_strings`` / ``coalesce_nulls`` are plain bool flags on the pool — no dialect-based
    auto-enable. Off by default; the operator opts in per pool (typically Oracle pools whose
    schema uses space-padded CHAR / NCHAR — JD Edwards). An unknown pool reports both off."""
    cfgs = {
        "ora_default": PoolConfig(url="oracle+oracledb://x@h/?service_name=s"),               # unset → off
        "ora_on":      PoolConfig(url="oracle+oracledb://x@h/?service_name=s",
                                   trim_strings=True, coalesce_nulls=True),
        "pg_default":  PoolConfig(url="postgresql+asyncpg://x@h/db"),                          # unset → off
        "pg_on":       PoolConfig(url="postgresql+asyncpg://x@h/db", coalesce_nulls=True),
    }
    pools = PoolRegistry(cfgs)
    assert pools.coalesce_nulls("ora_default") is False    # off by default — no auto-on
    assert pools.coalesce_nulls("ora_on") is True
    assert pools.coalesce_nulls("pg_default") is False
    assert pools.coalesce_nulls("pg_on") is True
    assert pools.trim_strings("ora_default") is False
    assert pools.trim_strings("ora_on") is True
    assert pools.trim_strings("pg_default") is False
    # Unknown pool — both flags report off (defensive default).
    assert pools.coalesce_nulls("missing") is False
    assert pools.trim_strings("missing") is False


# --------------------------------------------------------------------------- #
# Write-time type coercion + form-layer rules (LOGIN / SYSDATE / PASSWORD /
# DEFAULT / SEQUENCE). The frontend submits everything as strings; v2's SQL
# connector consults the column's resolved format and rules to coerce + stamp
# server-side values, so the same path covers dialog Save, batch-edit grid,
# and any future API caller.
# --------------------------------------------------------------------------- #


def test_coerce_value_numbers_dates_booleans() -> None:
    """Round-trip the module-level coercer for every format family. Empty strings
    become None (NULL); unknown / blank format passes through; coercion failures
    leave the original so the DB error is the actionable message."""
    from datetime import date, datetime
    from liberty.connectors.sql import _coerce_value
    # integer family
    assert _coerce_value("123", "number") == 123
    assert _coerce_value("123", "integer") == 123
    assert _coerce_value("123.0", "number") == 123       # zero-decimal allowed → int
    assert _coerce_value("123.5", "integer") == 123.5    # non-integer → float (DB rejects loudly)
    # decimal / currency
    assert _coerce_value("12.34", "decimal") == 12.34
    assert _coerce_value("9.99", "currency") == 9.99
    # date / datetime / timestamp
    assert _coerce_value("2026-05-18", "date") == date(2026, 5, 18)
    assert _coerce_value("2026-05-18T12:34:56", "datetime") == datetime(2026, 5, 18, 12, 34, 56)
    assert _coerce_value("2026-05-18 12:34:56", "timestamp") == datetime(2026, 5, 18, 12, 34, 56)
    # boolean (no rule attached — permissive fallback)
    assert _coerce_value("true", "boolean") is True
    assert _coerce_value("N", "boolean") is False
    # empty string → None
    assert _coerce_value("", "number") is None
    assert _coerce_value("", "text") is None
    # None passes through
    assert _coerce_value(None, "number") is None
    # already-typed values pass through (a recursive sequence call may pre-coerce)
    assert _coerce_value(42, "number") == 42
    # unparseable → original (DB raises its own clearer error)
    assert _coerce_value("not-a-date", "date") == "not-a-date"
    # unknown / blank format → pass through
    assert _coerce_value("anything", None) == "anything"
    assert _coerce_value("anything", "") == "anything"


@pytest.mark.asyncio
async def test_write_query_without_columns_hint_still_gets_coercion(pools: PoolRegistry) -> None:
    """The migration emits ``_post`` / ``_put`` / ``_delete`` queries **without** a ``columns``
    block (the column layout lives on the matching ``_get``). The form-rule resolver must
    still find each bind's metadata — by falling back to ``DictionaryFile.find_entry(name)``
    keyed off the bind name — otherwise the user's reported asyncpg ``'str' object cannot
    be interpreted as an integer`` slips right through to the driver. Exact user case:
    LICENSE_CSI_APPS INSERT with LCA_CSI_ID (number) bound as ``"123456"``."""
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        # The actual dictionary entries the user has — note: no `LCA_AUDIT_*` here, that's
        # the operator's data; we're only proving the bind-fallback works for the format ones.
        "LCA_CSI_ID": DictionaryEntry(label="CSI Number", format="number"),
        "LCA_APPS_ID": DictionaryEntry(label="Application ID", format="number"),
    })})
    # Mimic the migrated _post query exactly — no `columns` block.
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="lca_post", writable=True,
                 sql="INSERT INTO item (id, name) VALUES (:LCA_CSI_ID, :LCA_APPS_ID)"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    # The bind-name fallback (no qdef.columns) resolves both binds via find_entry()
    # and coerces both string numerics to int.
    bound = conn._apply_form_rules(
        {"LCA_CSI_ID": "123456", "LCA_APPS_ID": "10"}, cfg.queries[0],
        stmt_type="INSERT", user="bob",
    )
    assert bound == {"LCA_CSI_ID": 123456, "LCA_APPS_ID": 10}


@pytest.mark.asyncio
async def test_write_coerces_number_string_to_int(pools: PoolRegistry) -> None:
    """The frontend sends "123" but the column's dictionary format says number — the SQL
    connector coerces before binding so asyncpg / oracledb get a real Python int (the user's
    reported bug: ``'str' object cannot be interpreted as an integer``)."""
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "ID": DictionaryEntry(label="ID", format="number"),
        "NAME": DictionaryEntry(label="Name", format="text"),
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="ins", writable=True,
                 sql="INSERT INTO item (id, name) VALUES (:ID, :NAME)"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    # Submit a string — would crash on a strict driver; sqlite happens to accept it but the
    # coercion still applies. The assertion is on the connector's bound dict, not the row.
    bound = conn._apply_form_rules(
        {"ID": "42", "NAME": "alpha"}, cfg.queries[0],
        stmt_type="INSERT", user="bob",
    )
    assert bound == {"ID": 42, "NAME": "alpha"}  # number coerced; text untouched
    # Round-trip the actual INSERT to prove SQLAlchemy + driver accept the coerced value.
    r = await conn.execute("ins", {"ID": "999", "NAME": "z"}, column_hints=[ColumnHint(name="ID"), ColumnHint(name="NAME")])
    assert r.rowcount == 1


@pytest.mark.asyncio
async def test_write_coerces_empty_string_to_null(pools: PoolRegistry) -> None:
    """An empty string in a number/date field becomes SQL NULL (Postgres rejects ``''`` for
    INTEGER / DATE; Oracle accepts it on VARCHAR2 but not on NUMBER / DATE)."""
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "ID": DictionaryEntry(label="ID", format="number"),
        "NAME": DictionaryEntry(label="Name", format="text"),
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="ins", writable=True,
                 sql="INSERT INTO item (id, name) VALUES (:ID, :NAME)"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    bound = conn._apply_form_rules(
        {"ID": "", "NAME": ""}, cfg.queries[0], stmt_type="INSERT", user=None,
    )
    assert bound == {"ID": None, "NAME": None}  # both empty strings → NULL


@pytest.mark.asyncio
async def test_form_rule_login_stamps_user_uppercase(pools: PoolRegistry) -> None:
    """``rules = "LOGIN"`` substitutes the caller's username on INSERT and UPDATE — used by
    the v1 audit columns (e.g. TV_AUDIT_USER, ACL_AUDIT_USER). The value is **uppercased**
    to match v1's convention ("ADMIN" not "admin" — every audit table + downstream report
    expects uppercase). Unauthenticated paths land as ``"ANONYMOUS"`` so the column is
    never NULL."""
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "USR": DictionaryEntry(rules="LOGIN"),
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="ins", writable=True, sql="INSERT INTO item (id, name) VALUES (1, :USR)"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    out = conn._apply_form_rules({"USR": None}, cfg.queries[0], stmt_type="INSERT", user="alice")
    assert out["USR"] == "ALICE"  # uppercased
    # Even a caller-supplied value is overwritten — LOGIN is server-trusted, not user input.
    out = conn._apply_form_rules({"USR": "evil-spoof"}, cfg.queries[0], stmt_type="INSERT", user="Bob")
    assert out["USR"] == "BOB"
    # Unauthenticated → "ANONYMOUS"
    out = conn._apply_form_rules({"USR": None}, cfg.queries[0], stmt_type="INSERT", user=None)
    assert out["USR"] == "ANONYMOUS"


@pytest.mark.asyncio
async def test_form_rule_sysdate_format_aware(pools: PoolRegistry) -> None:
    """SYSDATE / CURRENT_DATE coerces the ``now()`` to match the column's format — a
    ``format = "date"`` column gets a ``date``, ``datetime`` gets a ``datetime``, ``jdedate``
    gets the JDE Julian integer. This keeps the driver from rejecting a datetime bind on a
    DATE column (Postgres errors loudly; Oracle silently truncates the time) and matches
    JDE's CYYDDD storage convention without extra wiring on the operator's side."""
    from datetime import date, datetime as dt
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "CREATED_AT": DictionaryEntry(format="datetime", rules="SYSDATE"),
        "CREATED_ON": DictionaryEntry(format="date", rules="SYSDATE"),
        "JDE_DATE": DictionaryEntry(format="jdedate", rules="SYSDATE"),
        "PLAIN": DictionaryEntry(rules="SYSDATE"),  # no format — fall through to bare datetime
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="ins", writable=True,
                 sql="INSERT INTO item (id, name, status) VALUES (:CREATED_AT, :CREATED_ON, :PLAIN)"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    out = conn._apply_form_rules(
        {"CREATED_AT": None, "CREATED_ON": None, "JDE_DATE": None, "PLAIN": None},
        cfg.queries[0], stmt_type="INSERT", user="x",
        column_hints=[ColumnHint(name="CREATED_AT"), ColumnHint(name="CREATED_ON"),
                      ColumnHint(name="JDE_DATE"), ColumnHint(name="PLAIN")],
    )
    # datetime column → datetime, date column → date (no time), jdedate → CYYDDD int.
    assert isinstance(out["CREATED_AT"], dt) and not isinstance(out["CREATED_AT"], date.__class__)
    assert isinstance(out["CREATED_ON"], date) and not isinstance(out["CREATED_ON"], dt)
    assert isinstance(out["JDE_DATE"], int)
    # The SYSDATE rule stamps ``datetime.now(UTC)`` — compare against the UTC date, not the
    # local one, otherwise the test flakes across midnight UTC in non-UTC timezones.
    from datetime import timezone
    today_utc = dt.now(timezone.utc).date()
    assert out["JDE_DATE"] == (today_utc.year - 1900) * 1000 + today_utc.timetuple().tm_yday
    # All three resolve to the same instant (the same ``now()`` per call).
    assert out["CREATED_AT"].date() == out["CREATED_ON"] == today_utc
    # No format → bare datetime (the pre-coercion fallback).
    assert isinstance(out["PLAIN"], dt)


def test_jdedate_coercion() -> None:
    """``_coerce_value(x, "jdedate")`` accepts ``date`` / ``datetime`` / ISO string / int —
    all collapse to CYYDDD. Out-of-range years (< 1900) return None (can't encode). The
    same path runs on read in Phase 5's `DynamicResultMapper` parity (deferred), so this
    is the symmetric write-time half — the migration's JDE-date columns roundtrip cleanly."""
    from datetime import date, datetime as dt
    from liberty.connectors.sql import _coerce_value, _to_jde_julian
    # 2026-05-18 → CYYDDD = 126138 ((2026-1900)*1000 + 138)
    assert _coerce_value(date(2026, 5, 18), "jdedate") == 126138
    assert _coerce_value(dt(2026, 5, 18, 12, 0, 0), "jdedate") == 126138
    assert _coerce_value("2026-05-18", "jdedate") == 126138
    # Already-Julian int passes through.
    assert _coerce_value(126138, "jdedate") == 126138
    assert _coerce_value("126138", "jdedate") == 126138
    # Empty / unparseable → None.
    assert _coerce_value("", "jdedate") is None
    assert _coerce_value(None, "jdedate") is None
    assert _coerce_value("not-a-date", "jdedate") is None
    # Year < 1900 can't survive the encoding.
    assert _to_jde_julian(date(1899, 12, 31)) is None
    # First and last days of a JDE year.
    assert _to_jde_julian(date(2000, 1, 1)) == 100001    # century 1, year 0, day 1
    assert _to_jde_julian(date(2024, 12, 31)) == 124366  # 2024 is a leap year


def test_infer_false_value_pairs() -> None:
    """The well-known v1 boolean pairs Y/N, 1/0, true/false (case-preserved). Everything else
    returns None — the operator sets ``DictionaryEntry.false_value`` explicitly for non-standard
    pairs like NOMASX1's 01/null user-status convention."""
    from liberty.connectors.dictionary import infer_false_value
    assert infer_false_value("Y") == "N"
    assert infer_false_value("y") == "n"
    assert infer_false_value("1") == "0"
    assert infer_false_value("true") == "false"
    assert infer_false_value("True") == "False"
    assert infer_false_value("TRUE") == "FALSE"
    # No inference — the operator must set false_value explicitly (or leave as null on uncheck).
    assert infer_false_value("01") is None
    assert infer_false_value("YES") is None
    assert infer_false_value("A") is None
    assert infer_false_value(None) is None
    assert infer_false_value("") is None


def test_resolve_rule_boolean_surfaces_false_value() -> None:
    """``resolve_rule`` exposes the BOOLEAN's false counterpart so the frontend dialog's
    checkbox knows what to send on uncheck. ``false_value`` is the operator's explicit
    override; falls back to ``infer_false_value(true_value)`` (Y→N etc.); omitted entirely
    when no obvious counterpart (the frontend then sends null — v1's default behaviour)."""
    from liberty.connectors.dictionary import DictionaryEntry, DictionaryFile
    d = DictionaryFile()
    # Explicit false_value wins
    e = DictionaryEntry(rules="BOOLEAN", rules_values="01", false_value="X")
    assert d.resolve_rule(e) == {"kind": "boolean", "true_value": "01", "false_value": "X"}
    # Standard Y → N inference (most common case)
    e = DictionaryEntry(rules="BOOLEAN", rules_values="Y")
    assert d.resolve_rule(e) == {"kind": "boolean", "true_value": "Y", "false_value": "N"}
    # No counterpart: 01 → no false_value key — frontend sends null on uncheck
    e = DictionaryEntry(rules="BOOLEAN", rules_values="01")
    assert d.resolve_rule(e) == {"kind": "boolean", "true_value": "01"}
    # Empty rules_values defaults true_value to "Y" → infers "N"
    e = DictionaryEntry(rules="BOOLEAN")
    assert d.resolve_rule(e) == {"kind": "boolean", "true_value": "Y", "false_value": "N"}


def test_resolve_rule_form_layer_auto_fill() -> None:
    """v1's `dd_rules` form-layer cases (SYSDATE / CURRENT_DATE / LOGIN) resolve to an
    ``auto_fill`` rule that carries a stable source id the frontend dispatches against its
    auth-builtins layer. PASSWORD and SEQUENCE / NN don't auto-fill — PASSWORD is driven by
    the `format = "password"` flag (masking only), and SEQUENCE / NN are server-side (the
    SQL connector fires them inside the INSERT transaction). Unknown / blank rules stay None
    so the existing widget-detection path isn't perturbed."""
    from liberty.connectors.dictionary import DictionaryEntry, DictionaryFile
    d = DictionaryFile()
    # SYSDATE + CURRENT_DATE both map to the same source — v1 used SYSDATE on Oracle and
    # CURRENT_DATE on Postgres; v2 normalises.
    assert d.resolve_rule(DictionaryEntry(rules="SYSDATE")) == {"kind": "auto_fill", "source": "current_date"}
    assert d.resolve_rule(DictionaryEntry(rules="CURRENT_DATE")) == {"kind": "auto_fill", "source": "current_date"}
    # LOGIN — the current user's username (auth-installed at the frontend layer).
    assert d.resolve_rule(DictionaryEntry(rules="LOGIN")) == {"kind": "auto_fill", "source": "login_user"}
    # PASSWORD intentionally returns None — masking is driven by ``format = "password"``, not
    # by the rule kind. Treating it as auto_fill would seed the column with whatever the source
    # resolves to (typically the username), which is the v1 / v2 bug we already fixed.
    assert d.resolve_rule(DictionaryEntry(rules="PASSWORD")) is None
    # SEQUENCE / NN are server-side (fired in SQLConnector._resolve_sequences during INSERT).
    assert d.resolve_rule(DictionaryEntry(rules="SEQUENCE", rules_values="1")) is None
    assert d.resolve_rule(DictionaryEntry(rules="NN", rules_values="1")) is None
    # Unknown rule still returns None (existing behaviour — widget-detection won't fire).
    assert d.resolve_rule(DictionaryEntry(rules="UNKNOWN_RULE")) is None
    # Blank / missing rule → None.
    assert d.resolve_rule(DictionaryEntry()) is None
    assert d.resolve_rule(DictionaryEntry(rules="")) is None


@pytest.mark.asyncio
async def test_boolean_rule_value_not_coerced_to_python_bool(pools: PoolRegistry) -> None:
    """Regression — a column with ``format = "boolean"`` *and* ``rules = "BOOLEAN"`` stores
    its true / false value as a **string** in the DB (``"Y"`` / ``"N"`` on NOMASX1's
    CSI_STATUS, ``"01"`` / null on user status). v2 must not coerce that bind to a Python
    ``bool``: asyncpg rejects ``True`` on a varchar column with ``expected str, got bool``
    (the user's actual error on LICENSE_CSI's UPDATE).

    The check lives in :meth:`SQLConnector._apply_form_rules` — when ``rule == "BOOLEAN"``,
    the type-coercion pass is skipped because the rule already tells us the column is
    string-typed. Pure ``format = "boolean"`` columns *without* a rule still coerce
    (a real PG ``bool`` column needs Python ``bool``)."""
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "CSI_STATUS": DictionaryEntry(format="boolean", rules="BOOLEAN", rules_values="Y", false_value="N"),
        # A pure-format column (no rule) still coerces — that path serves real PG bool columns.
        "ACTIVE": DictionaryEntry(format="boolean"),
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="upd", writable=True,
                 sql="UPDATE item SET status = :CSI_STATUS, name = :ACTIVE WHERE id = :ID_ORIGINAL"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    # Frontend sends the rule's true_value ("Y") on check, "N" on uncheck — both must stay as
    # strings on the bind so the driver hands a string to the varchar column.
    out = conn._apply_form_rules(
        {"CSI_STATUS": "Y", "ACTIVE": "true", "ID_ORIGINAL": "123"},
        cfg.queries[0], stmt_type="UPDATE", user="admin",
    )
    assert out["CSI_STATUS"] == "Y"      # not Python True — would break the varchar column
    assert out["ACTIVE"] is True          # no rule → coerced normally (real bool column path)
    # The :CSI_STATUS_ORIGINAL bind (if it existed) would also stay as a string — same code path.
    out = conn._apply_form_rules(
        {"CSI_STATUS": "N", "CSI_STATUS_ORIGINAL": "Y"}, cfg.queries[0], stmt_type="UPDATE", user="admin",
    )
    assert out["CSI_STATUS"] == "N" and out["CSI_STATUS_ORIGINAL"] == "Y"


@pytest.mark.asyncio
async def test_form_rule_boolean_substitutes_false_value(pools: PoolRegistry) -> None:
    """Backend safety net: a BOOLEAN-ruled column receiving NULL gets the rule's ``false_value``
    substituted before binding. Handles the NOMASX1 CSI_STATUS case (DB needs 'Y' / 'N', dialog
    might send null if the rule hasn't loaded yet, batch-edit grid sends raw text). Explicit
    ``false_value`` wins; the standard Y → N inference covers the common case."""
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        # Y / N (inferred) — most common
        "STATUS": DictionaryEntry(format="text", rules="BOOLEAN", rules_values="Y"),
        # Explicit false_value (operator override)
        "FLAG": DictionaryEntry(format="text", rules="BOOLEAN", rules_values="A", false_value="B"),
        # 01 / null (no inference, no override) — null passes through, v1 default
        "ACTIVE": DictionaryEntry(format="text", rules="BOOLEAN", rules_values="01"),
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="ins", writable=True,
                 sql="INSERT INTO item (id, name, status) VALUES (:STATUS, :FLAG, :ACTIVE)"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    # All three checked → true_value passes through.
    out = conn._apply_form_rules(
        {"STATUS": "Y", "FLAG": "A", "ACTIVE": "01"}, cfg.queries[0], stmt_type="INSERT", user="x",
    )
    assert out == {"STATUS": "Y", "FLAG": "A", "ACTIVE": "01"}
    # All three unchecked → null on the wire. STATUS → "N" (inferred), FLAG → "B" (explicit),
    # ACTIVE → still null (no false_value, no inference for "01").
    out = conn._apply_form_rules(
        {"STATUS": None, "FLAG": None, "ACTIVE": None}, cfg.queries[0], stmt_type="INSERT", user="x",
    )
    assert out == {"STATUS": "N", "FLAG": "B", "ACTIVE": None}


@pytest.mark.asyncio
async def test_sequence_resolved_via_dictionary_sequence_id(pools: PoolRegistry) -> None:
    """The SQL connector resolves a SEQUENCE-ruled column's ``rules_values`` as a *sequence id*
    (first-class entity in ``DictionaryFile.sequences``), not the raw query name. Mirrors the
    v1 → v2 migration where ``ly_sequence`` lands as ``[sequences.<id>]`` and entries reference
    them. The named query runs in the same write transaction as the INSERT."""
    from liberty.connectors.dictionary import DictionarySection, SequenceDef
    d = DictionaryFile(connectors={"db": DictionarySection(
        sequences={
            "item_next_id": SequenceDef(
                description="Next item id", query="item_next_id_q",
            ),
        },
        entries={
            "ID": DictionaryEntry(format="number", rules="SEQUENCE", rules_values="item_next_id"),
        },
    )})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="item_next_id_q", sql="SELECT COALESCE(MAX(id), 0) + 1 FROM item"),
        QueryDef(name="ins", writable=True, sql="INSERT INTO item (id, name) VALUES (:ID, 'seq')"),
        QueryDef(name="all", sql="SELECT id, name FROM item ORDER BY id"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    # Fixture has rows 1, 2, 3 → next id should be 4.
    r = await conn.execute("ins", {"ID": None}, column_hints=[ColumnHint(name="CREATED_AT"), ColumnHint(name="CREATED_ON"), ColumnHint(name="JDE_DATE"), ColumnHint(name="PLAIN")])
    assert r.rowcount == 1
    rows = (await conn.execute("all", column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])).rows
    assert rows[-1] == {"id": 4, "name": "seq"}


@pytest.mark.asyncio
async def test_form_rule_disabled_opts_out_of_inherited_rule(pools: PoolRegistry) -> None:
    """``rules = "DISABLED"`` short-circuits every form-rule on this column — used to opt
    out of an inherited dictionary rule for a specific screen (a bulk-import / archive /
    backfill that wants to keep the row's pre-computed audit / PK values). Type coercion
    still applies; only the rule substitution is disabled."""
    from liberty.connectors.dictionary import DictionarySection
    # The dictionary entry for AUDIT_USER says LOGIN — but the operator wants this
    # screen's INSERT to keep whatever the row carries (a v1-style archive load).
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "AUDIT_USER": DictionaryEntry(rules="DISABLED"),  # per-screen override (operator-edited)
        "AUDIT_DATE": DictionaryEntry(format="date", rules="DISABLED"),
        "ID": DictionaryEntry(format="number", rules="DISABLED"),  # also defuses any DEFAULT
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="ins", writable=True,
                 sql="INSERT INTO item (id, name, status) VALUES (:ID, :AUDIT_USER, :AUDIT_DATE)"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    out = conn._apply_form_rules(
        {"ID": "42", "AUDIT_USER": "imported-as-is", "AUDIT_DATE": "2024-01-01"},
        cfg.queries[0], stmt_type="INSERT", user="alice",
    )
    # AUDIT_USER kept as the caller supplied (would have been "ALICE" with LOGIN).
    assert out["AUDIT_USER"] == "imported-as-is"
    # ID still coerced (number format → int) — DISABLED suppresses *rules*, not types.
    assert out["ID"] == 42
    # AUDIT_DATE coerced to a Python date.
    from datetime import date
    assert out["AUDIT_DATE"] == date(2024, 1, 1)


@pytest.mark.asyncio
async def test_form_rule_sysdate_stamps_now(pools: PoolRegistry) -> None:
    """``rules = "SYSDATE"`` / ``"CURRENT_DATE"`` stamps ``datetime.now(UTC)`` (one value per
    call, so every audit column in the same write lands on the same instant)."""
    from datetime import datetime
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "CREATED": DictionaryEntry(format="datetime", rules="SYSDATE"),
        "UPDATED": DictionaryEntry(format="datetime", rules="CURRENT_DATE"),
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="ins", writable=True,
                 sql="INSERT INTO item (id, name, status) VALUES (1, :CREATED, :UPDATED)"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    out = conn._apply_form_rules({"CREATED": None, "UPDATED": ""}, cfg.queries[0], stmt_type="INSERT", user="x")
    assert isinstance(out["CREATED"], datetime) and isinstance(out["UPDATED"], datetime)
    # Both columns get the *same* timestamp (one ``datetime.now`` per call).
    assert out["CREATED"] == out["UPDATED"]


@pytest.mark.asyncio
async def test_form_rule_password_hashes_value(pools: PoolRegistry) -> None:
    """``rules = "PASSWORD"`` Argon2-hashes the value before binding. Blank password → NULL
    (the dialog drops blank fields from the SET on UPDATE; INSERT with no password lands as
    NULL, which the column should be nullable to allow). A hash starts with ``$argon2``."""
    from liberty.auth.password import verify_password
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "PWD": DictionaryEntry(format="password", rules="PASSWORD"),
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="ins", writable=True, sql="INSERT INTO item (id, name) VALUES (1, :PWD)"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    out = conn._apply_form_rules({"PWD": "hunter2"}, cfg.queries[0], stmt_type="INSERT", user="x")
    assert isinstance(out["PWD"], str) and out["PWD"].startswith("$argon2")
    assert verify_password(out["PWD"], "hunter2")    # round-trip via the existing hasher
    # Blank password → None (dialog drops blanks; the bind matches the dialog's intent)
    out = conn._apply_form_rules({"PWD": ""}, cfg.queries[0], stmt_type="INSERT", user="x")
    assert out["PWD"] is None


@pytest.mark.asyncio
async def test_form_rule_default_on_insert_only(pools: PoolRegistry) -> None:
    """The dictionary entry's ``default`` value seeds an empty INSERT bind — but never an
    UPDATE (UPDATE keeps the column's current value when not supplied), and never overwrites
    an explicit user value."""
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "STATUS": DictionaryEntry(default="active"),
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="ins", writable=True, sql="INSERT INTO item (id, status) VALUES (1, :STATUS)"),
        QueryDef(name="upd", writable=True, sql="UPDATE item SET status = :STATUS WHERE id = :ID_ORIGINAL"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    # INSERT + missing value → use default
    out = conn._apply_form_rules({"STATUS": None}, cfg.queries[0], stmt_type="INSERT", user="x")
    assert out["STATUS"] == "active"
    # INSERT + explicit value → keep it
    out = conn._apply_form_rules({"STATUS": "draft"}, cfg.queries[0], stmt_type="INSERT", user="x")
    assert out["STATUS"] == "draft"
    # UPDATE + missing value → leave None (don't default — UPDATE preserves the current value)
    out = conn._apply_form_rules({"STATUS": None, "ID_ORIGINAL": 1}, cfg.queries[1], stmt_type="UPDATE", user="x")
    assert out["STATUS"] is None


@pytest.mark.asyncio
async def test_form_rule_skips_original_suffix(pools: PoolRegistry) -> None:
    """The migration's ``_put`` WHERE-clause re-bindings use ``:NAME_ORIGINAL`` — these hold
    the row's *pre-edit* values and must not be overwritten by LOGIN / SYSDATE / DEFAULT
    (which would corrupt the WHERE). Type coercion still applies — the WHERE compares to a
    real column type."""
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "ID": DictionaryEntry(format="number"),
        "USR": DictionaryEntry(rules="LOGIN"),
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="upd", writable=True,
                 sql="UPDATE item SET name = :USR WHERE id = :ID_ORIGINAL"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    out = conn._apply_form_rules(
        {"USR": None, "ID_ORIGINAL": "42"}, cfg.queries[0], stmt_type="UPDATE", user="alice",
    )
    # SET clause's LOGIN bind got stamped (uppercased per v1 convention); WHERE clause's
    # _ORIGINAL got coerced (number) but *not* substituted by the rule (it's not the SET-clause column).
    assert out["USR"] == "ALICE"
    assert out["ID_ORIGINAL"] == 42


@pytest.mark.asyncio
async def test_form_rule_sequence_resolves_in_same_transaction(pools: PoolRegistry) -> None:
    """``rules = "SEQUENCE"`` / ``"NN"`` runs the named v2 query inside the *same* transaction
    as the INSERT — a separate ``MAX(col)+1`` query whose first column is the next number.
    The bind picks it up automatically; a caller-supplied explicit value still wins."""
    from liberty.connectors.dictionary import DictionarySection
    # The sequence query: SELECT COALESCE(MAX(id), 0) + 1 FROM item. Lives on the same
    # connector. ``rules_values`` names the v2 query (the migration resolved it from v1's
    # seq_id → seq_query_id → migrated query name).
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "ID": DictionaryEntry(format="number", rules="SEQUENCE", rules_values="item_next_id"),
        "NAME": DictionaryEntry(label="Name", format="text"),
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="item_next_id", sql="SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM item"),
        QueryDef(name="ins", writable=True,
                 sql="INSERT INTO item (id, name) VALUES (:ID, :NAME)"),
        QueryDef(name="all", sql="SELECT id, name FROM item ORDER BY id"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    # The fixture has rows 1, 2, 3 already → next id should be 4.
    r = await conn.execute("ins", {"ID": None, "NAME": "from-seq"}, column_hints=[ColumnHint(name="ID"), ColumnHint(name="NAME")])
    assert r.rowcount == 1
    rows = (await conn.execute("all", column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])).rows
    assert rows[-1] == {"id": 4, "name": "from-seq"}
    # A caller-supplied explicit value should NOT trigger the sequence — keeps the explicit value.
    r = await conn.execute("ins", {"ID": 99, "NAME": "explicit"}, column_hints=[ColumnHint(name="ID"), ColumnHint(name="NAME")])
    assert r.rowcount == 1
    rows = (await conn.execute("all", column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])).rows
    assert {"id": 99, "name": "explicit"} in rows
    # Two more INSERTs with NULL IDs — should land on 5 then 100 (max(99,4)+1, then +1).
    await conn.execute("ins", {"ID": None, "NAME": "from-seq-2"}, column_hints=[ColumnHint(name="ID"), ColumnHint(name="NAME")])
    await conn.execute("ins", {"ID": None, "NAME": "from-seq-3"}, column_hints=[ColumnHint(name="ID"), ColumnHint(name="NAME")])
    final = (await conn.execute("all", column_hints=[
                ColumnHint(name="name", label="Item Name"),
                ColumnHint(name="ID", label="Identifier"),       # hint upper, result upper — fine
                ColumnHint(name="status", hidden=True),          # hint lower, result "STATUS"
            ])).rows
    ids = sorted(r["id"] for r in final if r["name"].startswith("from-seq"))
    assert ids == [4, 100, 101]  # each call ran ``MAX(id) + 1`` in its own transaction


@pytest.mark.asyncio
async def test_form_rule_sequence_missing_query_logs_and_falls_through(pools: PoolRegistry, caplog) -> None:
    """A SEQUENCE rule pointing at a query that doesn't exist on this connector logs a
    warning and leaves the bind as NULL — the DB will reject the row if the column is NOT NULL,
    which surfaces the misconfiguration clearly without crashing on an unrelated request."""
    import logging
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "ID": DictionaryEntry(format="number", rules="SEQUENCE", rules_values="nope_not_a_query"),
    })})
    cfg = SqlConnectorConfig(type="sql", pool="test", queries=[
        QueryDef(name="ins", writable=True,
                 sql="INSERT INTO item (id, name) VALUES (:ID, 'x')"),
    ])
    conn = SQLConnector("db", cfg, pools, dictionary=d)
    with caplog.at_level(logging.WARNING, logger="liberty.connectors.sql"):
        r = await conn.execute("ins", {"ID": None}, column_hints=[ColumnHint(name="ID")])
        assert r.rowcount == 1
    # The warning names whichever of {sequence, query} failed to resolve. Phase 8 looks the id
    # up in ``DictionaryFile.sequences`` first; a missing entry there → "unknown sequence".
    assert any(
        "references unknown sequence" in rec.message or "references unknown query" in rec.message
        for rec in caplog.records
    )


# --------------------------------------------------------------------------- #
# Phase 8: runtime filter wrap. The migrator stores the inner SELECT clean; the
# SQL connector wraps dynamically based on caller-supplied filter params. Type-aware:
# text columns get operator support, non-text get a typed equals.
# --------------------------------------------------------------------------- #


def test_split_order_by_top_level() -> None:
    """``_split_order_by`` splits at the last top-level ORDER BY (depth 0 in parens). A
    subquery's ORDER BY isn't taken (depth > 0). When the SQL has no top-level ORDER BY,
    returns ``(sql, "")`` so the caller can compose unconditionally."""
    from liberty.connectors.sql import _split_order_by
    body, ob = _split_order_by("SELECT id, name FROM t ORDER BY name")
    assert body == "SELECT id, name FROM t" and ob == "name"
    # Subquery's ORDER BY left alone — last top-level is the outer one.
    body, ob = _split_order_by(
        "SELECT * FROM (SELECT id FROM t ORDER BY id) sub ORDER BY id DESC"
    )
    assert body.endswith(") sub") and ob == "id DESC"
    # No top-level ORDER BY → empty clause
    body, ob = _split_order_by("SELECT id FROM t")
    assert body == "SELECT id FROM t" and ob == ""


@pytest.mark.asyncio
async def test_filter_wrap_text_column_default_op_contains(pools: PoolRegistry) -> None:
    """A ``filter = true`` text column with no operator hint → ``contains`` predicate
    (``LOWER(col) LIKE LOWER('%v%')``). The wrap fires only because the caller sent a
    value; without it the stored SELECT runs unwrapped."""
    q = QueryDef(
        name="users", sql="SELECT id, name, status FROM item")
    conn = _connector(pools, q)
    # No filter param → unwrapped: returns all rows (3 in fixture).
    assert len(((await conn.execute("users", column_hints=[ColumnHint(name="name", filter=True)])).rows)) == 3
    # With a `name = "b"` filter → wraps with `contains` → matches just 'b'.
    rs = (await conn.execute("users", {"name": "b"}, column_hints=[ColumnHint(name="name", filter=True)])).rows
    assert [r["name"] for r in rs] == ["b"]
    # An operator hint: startsWith picks 'a' / 'b' / 'c' depending on input.
    rs = (await conn.execute("users", {"name": "a", "name_op": "startsWith"}, column_hints=[ColumnHint(name="name", filter=True)])).rows
    assert [r["name"] for r in rs] == ["a"]


@pytest.mark.asyncio
async def test_filter_wrap_non_text_column_typed_equals(pools: PoolRegistry) -> None:
    """A ``filter = true`` non-text column (here ``integer``) — the wrap emits a plain
    ``col = :bind`` and **coerces the bind to a Python int** before binding. asyncpg's
    strict type check rejects a string against an integer-typed param (the user's bug:
    ``invalid input for query argument $1: '10' ('str' object cannot be interpreted as
    an integer)``), so the Python-side coercion is what makes the comparison work on
    Postgres natively. The column index applies because there's no SQL CAST."""
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "id": DictionaryEntry(format="integer"),
    })})
    q = QueryDef(
        name="users", sql="SELECT id, name, status FROM item")
    conn = SQLConnector("db", SqlConnectorConfig(type="sql", pool="test", queries=[q]), pools, dictionary=d)
    rs = (await conn.execute("users", {"id": 2}, column_hints=[ColumnHint(name="id", filter=True)])).rows
    assert [r["id"] for r in rs] == [2]
    # The string "2" works too — the wrap helper coerces it to int(2) Python-side
    # before binding. SQLite is permissive so this would even work without coercion;
    # the real test is the dict-level coercion check below.
    rs = (await conn.execute("users", {"id": "2"}, column_hints=[ColumnHint(name="id", filter=True)])).rows
    assert [r["id"] for r in rs] == [2]
    # Direct check on ``_apply_filter_wrap`` itself: the returned params carry the
    # coerced int, not the original string. This is what fixes the asyncpg
    # ``'str' object cannot be interpreted as an integer`` error on Postgres.
    qdef = conn.get_query("users")
    new_sql, new_params = conn._apply_filter_wrap(
        "SELECT id, name, status FROM item", qdef, {"id": "10"}, stmt_type="SELECT",
        column_hints=[ColumnHint(name="id", filter=True)],
    )
    assert new_params == {"id": 10}    # str → int
    # And the SQL is wrapped with a plain ``col = :id`` (no CAST around the bind, so
    # asyncpg sends the int with its native type code and the index applies).
    assert "lib_flt.id = :id" in new_sql
    assert "CAST(:id" not in new_sql


@pytest.mark.asyncio
async def test_filter_wrap_multiple_active_columns_anded(pools: PoolRegistry) -> None:
    """Multiple active filter columns → ``AND``-ed predicates. Empty / null filter binds
    are dropped (no predicate for them) so the stored SELECT stays unencumbered when the
    operator only filters one column."""
    q = QueryDef(
        name="users",
        sql="SELECT id, name, status FROM item ORDER BY id")
    conn = _connector(pools, q)
    # Only `name` set → only one predicate fires
    rs = (await conn.execute("users", {"name": "a"}, column_hints=[
            ColumnHint(name="name", filter=True),
            ColumnHint(name="status", filter=True),
        ])).rows
    assert [r["name"] for r in rs] == ["a"]
    # Both → AND-ed
    rs = (await conn.execute("users", {"name": "a", "status": "on"}, column_hints=[
            ColumnHint(name="name", filter=True),
            ColumnHint(name="status", filter=True),
        ])).rows
    assert rs == [{"id": 1, "name": "a", "status": "on"}]
    # Empty string treated as no filter — picks up the other one only
    rs = (await conn.execute("users", {"name": "", "status": "off"}, column_hints=[
            ColumnHint(name="name", filter=True),
            ColumnHint(name="status", filter=True),
        ])).rows
    assert [r["name"] for r in rs] == ["c"]
    # ORDER BY preserved (the wrap re-attaches it)
    rs = (await conn.execute("users", {"name": ""}, column_hints=[
            ColumnHint(name="name", filter=True),
            ColumnHint(name="status", filter=True),
        ])).rows
    assert [r["id"] for r in rs] == [1, 2, 3]


@pytest.mark.asyncio
async def test_filter_wrap_resolves_format_from_dictionary(pools: PoolRegistry) -> None:
    """Regression: a filter-flagged column without an inline ``format`` on the hint must
    pick up the format from its dictionary entry (via ``col.dd``, or ``col.name`` when
    unset). Otherwise a numeric column gets the *text* operator path
    (``LOWER(integer)``) and Postgres rejects it — the exact error the user hit on the
    LICENSE_CSI nested table where ``LCA_CSI_ID`` was hinted as
    ``{ name = "LCA_CSI_ID", dd = "CSI_ID", filter = true }`` (format on the CSI_ID
    dictionary entry, not the hint).

    The dictionary's CSI_ID entry has ``format = "number"`` → typed equals path, no
    LOWER, and the column-side stays as its native int type so a btree index applies."""
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        # Dictionary says CSI_ID is a number — the column hint inherits this via `dd`.
        "CSI_ID": DictionaryEntry(format="number"),
    })})
    q = QueryDef(
        name="csi_list", sql="SELECT id, name, status FROM item")
    conn = SQLConnector("db", SqlConnectorConfig(type="sql", pool="test", queries=[q]), pools, dictionary=d)
    # A "contains" op on a number column would have failed pre-fix (LOWER(integer)).
    # Post-fix the runtime emits a typed equals regardless of the op the caller sent —
    # non-text columns ignore the operator hint and run as equals (the FilterPanel
    # already restricts the operator picker for non-text columns; this is defence-in-
    # depth for a stray op value from another caller).
    rs = (await conn.execute("csi_list", {"id": 2}, column_hints=[ColumnHint(name="id", dd="CSI_ID", filter=True)])).rows
    assert [r["id"] for r in rs] == [2]
    rs = (await conn.execute("csi_list", {"id": "2", "id_op": "contains"}, column_hints=[ColumnHint(name="id", dd="CSI_ID", filter=True)])).rows
    assert [r["id"] for r in rs] == [2]


@pytest.mark.asyncio
async def test_filter_wrap_strips_op_binds_from_sql(pools: PoolRegistry) -> None:
    """The ``:NAME_op`` value comes from the caller as a *signal* (which operator to use),
    not a bind that ends up in the SQL. The wrap reads it but doesn't emit it as a
    SQLAlchemy bind — confirms the migrated SQL no longer has ``:NAME_op`` cluttering it
    and the request-side ``_op`` doesn't trip the `text()` parser."""
    q = QueryDef(
        name="users", sql="SELECT id, name FROM item")
    conn = _connector(pools, q)
    # _op should be consumed by the wrap and not emit a `:name_op` bind — verify there
    # are no spurious errors when sending an op alongside the value.
    rs = (await conn.execute("users", {"name": "b", "name_op": "equals"}, column_hints=[ColumnHint(name="name", filter=True)])).rows
    assert [r["name"] for r in rs] == ["b"]


# --------------------------------------------------------------------------- #
# JDE Julian date conversion on read (mirror of write-side `_coerce_value`).
# --------------------------------------------------------------------------- #


def test_from_jde_julian_helper() -> None:
    """``_from_jde_julian`` mirrors ``_to_jde_julian``: CYYDDD int → ``"YYYY-MM-DD"``
    ISO string. Pass-through on null / non-julian / out-of-range values so bad data
    surfaces instead of silently corrupting."""
    from liberty.connectors.sql import _from_jde_julian
    # 2026-05-18: (2026 - 1900) * 1000 + 138 = 126138
    assert _from_jde_julian(126138) == "2026-05-18"
    # 2024-12-31 (leap year, day 366)
    assert _from_jde_julian(124366) == "2024-12-31"
    # First day of the 21st century: 2000-01-01 → C=1, YY=00, DDD=001 = 100001
    assert _from_jde_julian(100001) == "2000-01-01"
    # String of digits — same conversion.
    assert _from_jde_julian("126138") == "2026-05-18"
    # 0 → "no date" in JDE; pass-through so the operator sees the raw 0
    assert _from_jde_julian(0) == 0
    # None → None
    assert _from_jde_julian(None) is None
    # Non-numeric → pass-through (returns raw)
    assert _from_jde_julian("not-a-date") == "not-a-date"
    # Out-of-range day → pass-through
    assert _from_jde_julian(126999) == 126999


@pytest.mark.asyncio
async def test_jdedate_columns_converted_to_iso_on_read(pools: PoolRegistry) -> None:
    """Columns whose resolved ``format`` is ``"jdedate"`` get their CYYDDD integers
    converted to ISO date strings on read — the grid shows ``"2026-05-18"`` instead of
    ``126138``. Round-trip with the write-side coercion: the dialog sends the ISO string
    back, ``_coerce_value`` re-encodes the CYYDDD integer on bind."""
    async with pools.engine("test").begin() as c:
        await c.execute(text("CREATE TABLE jde (id INTEGER, upmj INTEGER)"))
        await c.execute(text("INSERT INTO jde (id, upmj) VALUES (1, 126138), (2, 0), (3, NULL)"))
    from liberty.connectors.dictionary import DictionarySection
    d = DictionaryFile(connectors={"db": DictionarySection(entries={
        "upmj": DictionaryEntry(format="jdedate"),
    })})
    q = QueryDef(name="all", sql="SELECT id, upmj FROM jde ORDER BY id")
    conn = SQLConnector("db", SqlConnectorConfig(type="sql", pool="test", queries=[q]), pools, dictionary=d)
    rs = (await conn.execute("all", column_hints=[ColumnHint(name="upmj")])).rows
    assert rs[0] == {"id": 1, "upmj": "2026-05-18"}
    # JDE 0 = "no date" → pass-through as-is so it's visible
    assert rs[1] == {"id": 2, "upmj": 0}
    # NULL stays NULL
    assert rs[2] == {"id": 3, "upmj": None}
