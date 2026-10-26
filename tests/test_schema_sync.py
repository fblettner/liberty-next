"""``liberty.schema_sync`` — create_all plus forward-adding the columns an older install's tables
lack, rendered per dialect, journaled in the upgrade history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, create_engine, func, inspect, text
from sqlalchemy.dialects import postgresql

from liberty.schema_sync import COMPONENT, SchemaChange, add_column_ddl, journal_schema_changes, sync_tables
from liberty.versioning import ConfigVersionStore


def _model() -> tuple[MetaData, Table]:
    md = MetaData()
    t = Table(
        "widgets", md,
        Column("id", Integer, primary_key=True),
        Column("name", String(40), nullable=False),
        Column("note", String(200)),                                        # nullable, no default
        Column("kind", String(16), nullable=False, server_default="auto"),  # NOT NULL + server default
        Column("retries", Integer, nullable=False, default=3),              # NOT NULL + scalar python default
        Column("active", Boolean, nullable=False, default=True),
        Column("created_at", DateTime(timezone=True), nullable=False, default=func.now()),  # callable → no DEFAULT possible
    )
    return md, t


def test_sync_adds_missing_columns_once(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    with sqlite3.connect(db) as c:  # the "old install": two of seven columns, one row
        c.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name VARCHAR(40) NOT NULL)")
        c.execute("INSERT INTO widgets (id, name) VALUES (1, 'w')")
    md, _ = _model()
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as conn:
        changes = sync_tables(conn, md)
    assert [c.column for c in changes] == ["note", "kind", "retries", "active", "created_at"]
    by_col = {c.column: c for c in changes}
    assert by_col["note"].note is None and by_col["kind"].note is None
    assert by_col["created_at"].note == "model says NOT NULL but has no default — added nullable"
    with eng.begin() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("widgets")}
        assert cols == {"id", "name", "note", "kind", "retries", "active", "created_at"}
        row = conn.execute(text("SELECT kind, retries, active FROM widgets WHERE id = 1")).one()
        assert tuple(row) == ("auto", 3, 1)  # existing rows got the defaults
        conn.execute(text("INSERT INTO widgets (id, name) VALUES (2, 'x')"))  # NOT NULL + DEFAULT still insertable
        assert sync_tables(conn, md) == []  # idempotent


def test_sync_creates_missing_tables_too(tmp_path: Path) -> None:
    md, _ = _model()
    eng = create_engine(f"sqlite:///{tmp_path / 'n.db'}")
    with eng.begin() as conn:
        assert sync_tables(conn, md) == []  # a brand-new table has every column: nothing to add
        assert inspect(conn).has_table("widgets")


def test_postgres_ddl_rendering() -> None:
    """The DDL is compiled for the connection's dialect — check the Postgres spellings without a
    server: quoting, types, boolean literal, NOT NULL only with a default."""
    md, t = _model()

    class _Conn:  # just what add_column_ddl reads
        dialect = postgresql.dialect()

    ddl = {c.name: add_column_ddl(t, c, _Conn())[0] for c in t.columns}
    assert ddl["kind"] == "ALTER TABLE widgets ADD COLUMN kind VARCHAR(16) DEFAULT 'auto' NOT NULL"
    assert ddl["retries"] == "ALTER TABLE widgets ADD COLUMN retries INTEGER DEFAULT 3 NOT NULL"
    assert ddl["active"] == "ALTER TABLE widgets ADD COLUMN active BOOLEAN DEFAULT true NOT NULL"
    assert ddl["note"] == "ALTER TABLE widgets ADD COLUMN note VARCHAR(200)"
    assert ddl["created_at"] == "ALTER TABLE widgets ADD COLUMN created_at TIMESTAMP WITH TIME ZONE"
    # a reserved word gets quoted
    t2 = Table("x", MetaData(), Column("id", Integer, primary_key=True), Column("order", Integer))
    assert add_column_ddl(t2, t2.c.order, _Conn())[0] == 'ALTER TABLE x ADD COLUMN "order" INTEGER'


def test_server_default_quotes_are_escaped() -> None:
    t = Table("x", MetaData(), Column("id", Integer, primary_key=True), Column("s", String(8), server_default="it's"))

    class _Conn:
        dialect = postgresql.dialect()

    assert "DEFAULT 'it''s'" in add_column_ddl(t, t.c.s, _Conn())[0]


def test_journal(tmp_path: Path) -> None:
    store = ConfigVersionStore(tmp_path)
    assert journal_schema_changes(store, []) is None
    row = journal_schema_changes(store, [SchemaChange("widgets", "kind", "ALTER …"), SchemaChange("w", "c", "…", "added nullable")])
    assert row["component"] == COMPONENT and row["from_version"] is None
    assert row["summary"] == "added widgets.kind, w.c (added nullable)"
    again = journal_schema_changes(store, [SchemaChange("w", "d", "…")])
    assert again["from_version"] == row["to_version"]  # chained like the other components
    assert store.list_upgrades()[0]["component"] == COMPONENT
    assert journal_schema_changes(None, [SchemaChange("w", "d", "…")]) is None


@pytest.mark.asyncio
async def test_changesets_old_table_is_upgraded_and_index_rebuilt(tmp_path: Path) -> None:
    """The real case that used to need a hand-written list: a pre-`kind` ly_change_packages."""
    from liberty.changesets.db import ChangeSetDatabase
    from liberty.connectors.config import PoolConfig
    from liberty.connectors.db import PoolRegistry

    db = tmp_path / "cs.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE ly_change_packages (id VARCHAR(36) PRIMARY KEY, application VARCHAR(128) NOT NULL, "
                  "status VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL)")
        c.execute("CREATE UNIQUE INDEX ux_ly_change_pkg_active ON ly_change_packages (application) WHERE status = 'draft'")
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{db}")})
    try:
        changes = await ChangeSetDatabase(pools, "default").create_schema()
        assert ("ly_change_packages", "kind") in {(c.table, c.column) for c in changes}
        with sqlite3.connect(db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(ly_change_packages)")}
            assert "kind" in cols
            idx_sql = c.execute("SELECT sql FROM sqlite_master WHERE name = 'ux_ly_change_pkg_active'").fetchone()[0]
            assert "kind = 'auto'" in idx_sql
        assert await ChangeSetDatabase(pools, "default").create_schema() == []
    finally:
        await pools.dispose()
