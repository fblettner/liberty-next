from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from liberty.connectors.base import detect_statement_type, find_bind_params
from liberty.connectors.config import (
    ApiConnectorConfig,
    PoolConfig,
    QueryDef,
    SqlConnectorConfig,
    load_connectors_file,
    parse_connectors,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_querydef_plain_sql() -> None:
    q = QueryDef(name="q", sql="SELECT 1")
    assert q.sql_for("oracle") == "SELECT 1" and q.default_sql == "SELECT 1" and q.dialects == ["default"]


def test_querydef_dialect_map() -> None:
    q = QueryDef(name="q", sql={"default": "SELECT 1 LIMIT 10", "oracle": "SELECT 1 FETCH FIRST 10 ROWS ONLY"})
    assert q.dialects == ["default", "oracle"]
    assert q.sql_for("oracle").endswith("ROWS ONLY")
    assert q.sql_for("postgresql") == "SELECT 1 LIMIT 10"  # no postgres variant → default
    assert q.sql_for(None) == "SELECT 1 LIMIT 10"
    assert q.default_sql == "SELECT 1 LIMIT 10"


def test_querydef_dialect_map_requires_default() -> None:
    with pytest.raises(Exception):
        QueryDef(name="q", sql={"oracle": "SELECT 1"})  # no 'default'
    with pytest.raises(Exception):
        QueryDef(name="q", sql={"default": "  ", "oracle": "SELECT 1"})  # empty default


def test_pool_config_dialect_override_and_derivation() -> None:
    from liberty.connectors.db import PoolRegistry

    reg = PoolRegistry({
        "pg": PoolConfig(url="postgresql+asyncpg://u:p@h/db"),
        "ora": PoolConfig(url="x", dialect="oracle"),  # explicit override (URL not even valid)
        "lite": PoolConfig(url="sqlite+aiosqlite:///:memory:"),
    })
    assert reg.dialect("pg") == "postgresql"
    assert reg.dialect("ora") == "oracle"
    assert reg.dialect("lite") == "sqlite"


def test_shipped_config_parses() -> None:
    cfg = load_connectors_file(REPO_ROOT / "config" / "connectors.toml")
    assert "default" in cfg.pools  # the framework pool
    # whatever connectors the deployment config defines must be valid SQL/API connectors
    for conn in cfg.connectors.values():
        assert isinstance(conn, (SqlConnectorConfig, ApiConnectorConfig))


def test_missing_file_is_empty() -> None:
    cfg = load_connectors_file(REPO_ROOT / "does" / "not" / "exist.toml")
    assert cfg.pools == {}
    assert cfg.connectors == {}


def test_env_substitution() -> None:
    raw = tomllib.loads(
        """
        [pools.default]
        url = "postgresql+asyncpg://${DB_USER}:${DB_PASS}@db/liberty"

        [connectors.api]
        type = "api"
        base_url = "https://example.test"
        auth_type = "bearer"
        auth_token = "${API_TOKEN}"
        """
    )
    cfg = parse_connectors(raw, env={"DB_USER": "liberty", "DB_PASS": "s3cret", "API_TOKEN": "tok"})
    assert cfg.pools["default"].url == "postgresql+asyncpg://liberty:s3cret@db/liberty"
    assert cfg.connectors["api"].auth_token == "tok"

    # Unset variables collapse to empty string (fail loud later, not silently literal).
    cfg2 = parse_connectors(raw, env={})
    assert cfg2.connectors["api"].auth_token == ""


def test_env_substitution_default_value() -> None:
    raw = tomllib.loads(
        """
        [pools.default]
        url = "${LIBERTY_DB_URL:-sqlite+aiosqlite:///./liberty.db}"

        [pools.other]
        url = "${OTHER_URL:-}"
        """
    )
    # unset → default
    assert parse_connectors(raw, env={}).pools["default"].url == "sqlite+aiosqlite:///./liberty.db"
    # empty → default (shell `:-` semantics)
    assert parse_connectors(raw, env={"LIBERTY_DB_URL": ""}).pools["default"].url == "sqlite+aiosqlite:///./liberty.db"
    # set → the value wins
    assert parse_connectors(raw, env={"LIBERTY_DB_URL": "postgresql+asyncpg://x/y"}).pools["default"].url == "postgresql+asyncpg://x/y"
    # empty default
    assert parse_connectors(raw, env={}).pools["other"].url == ""


def test_unknown_type_rejected() -> None:
    with pytest.raises(Exception):
        parse_connectors({"connectors": {"x": {"type": "ftp"}}})


def test_extra_keys_rejected() -> None:
    with pytest.raises(Exception):
        parse_connectors(
            {"connectors": {"x": {"type": "sql", "pool": "default", "bogus": 1}}}
        )


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("  SELECT 1", "SELECT"),
        ("-- comment\n  insert into t values (1)", "INSERT"),
        ("/* block */ Update t set a=1", "UPDATE"),
        ("\n\ndelete from t", "DELETE"),
        ("merge into t ...", "MERGE"),
        ("drop table t", "DROP"),
        ("", ""),
        # CTE queries → the *main* statement keyword
        ("WITH a AS (SELECT 1) SELECT * FROM a", "SELECT"),
        ("WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t WHERE n<5) SELECT * FROM t", "SELECT"),
        ("WITH a AS (SELECT id FROM x), b AS (SELECT id FROM y) SELECT * FROM a JOIN b USING(id)", "SELECT"),
        ("WITH d AS (DELETE FROM old RETURNING *) INSERT INTO arch SELECT * FROM d", "INSERT"),
        ("WITH x AS MATERIALIZED (SELECT 1) DELETE FROM t WHERE c IN (SELECT 1 FROM x)", "DELETE"),
        ("WITH a AS NOT MATERIALIZED (SELECT 1) UPDATE t SET v = 1", "UPDATE"),
        ("  -- c\n WITH a AS ( select 1 ) update t set v=1", "UPDATE"),
        ("WITH a AS (SELECT '(' || ')' AS p) SELECT * FROM a", "SELECT"),  # parens inside a string literal
        ('WITH "my cte" AS (SELECT 1) SELECT * FROM "my cte"', "SELECT"),  # quoted CTE name
        ("WITH broken AS (SELECT 1", "WITH"),  # unbalanced parens → safe-reject (not allowed)
    ],
)
def test_detect_statement_type(sql: str, expected: str) -> None:
    assert detect_statement_type(sql) == expected


def test_find_bind_params_skips_literals_and_casts() -> None:
    sql = (
        "SELECT * FROM t "
        "WHERE a = :alpha "
        "AND b = 'literal :nope' "
        "AND c = \"col :nope2\" "
        "AND d = :beta::text "
        "-- :nope3\n"
        "AND e = :alpha"
    )
    assert find_bind_params(sql) == ["alpha", "beta"]
