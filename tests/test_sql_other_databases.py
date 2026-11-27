"""What the SQL connector says differently to SQL Server and Db2 — and that Oracle is told what it always was."""
from __future__ import annotations

from liberty.connectors.sql import _column_types_sql
from liberty.connectors.sqltext import _build_filter_predicate, _oracle_target_table


def test_the_column_types_are_asked_of_each_database_own_catalogue() -> None:
    sql, owner = _column_types_sql("oracle", "SY920")
    assert sql == "SELECT column_name, data_type, char_length FROM all_tab_columns WHERE owner = :o AND table_name = :t" and owner == "SY920"
    sql, owner = _column_types_sql("mssql", "JDE_PRODUCTION.DBO")
    assert "FROM JDE_PRODUCTION.INFORMATION_SCHEMA.COLUMNS" in sql and owner == "DBO"          # a table of another database: its own catalogue
    sql, owner = _column_types_sql("mssql", "DBO")
    assert "FROM INFORMATION_SCHEMA.COLUMNS" in sql and owner == "DBO"
    assert "SYSCAT.COLUMNS" in _column_types_sql("db2", "PRODDTA")[0] and "QSYS2.SYSCOLUMNS" in _column_types_sql("ibmi", "PRODDTA")[0]
    assert _column_types_sql("postgresql", "public") is None                                    # not known here: no bind hygiene, as before


def test_the_table_a_write_targets_may_be_named_with_its_database() -> None:
    assert _oracle_target_table("INSERT INTO SY920.F00950 (A) VALUES (:A)") == ("SY920", "F00950")
    assert _oracle_target_table("UPDATE JDE_PRODUCTION.dbo.F0101 SET ABALPH = :ABALPH") == ("JDE_PRODUCTION.DBO", "F0101")
    assert _oracle_target_table("DELETE FROM F0101 WHERE ABAN8 = :ABAN8") == (None, "F0101")


def test_a_text_filter_is_written_for_sql_server_its_own_way_only() -> None:
    for dialect in ("oracle", "postgresql", "db2"):
        assert "'%' ||" in _build_filter_predicate("NAME", None, "contains", dialect)
    said = _build_filter_predicate("NAME", None, "contains", "mssql")
    assert "||" not in said and "CONCAT('%', CAST(:NAME AS VARCHAR(4000)), '%')" in said
    assert "||" not in _build_filter_predicate("NAME", None, "startsWith", "mssql") and "||" not in _build_filter_predicate("NAME", None, "endsWith", "mssql")
    assert _build_filter_predicate("NAME", None, "equals", "mssql") == _build_filter_predicate("NAME", None, "equals", "db2")
