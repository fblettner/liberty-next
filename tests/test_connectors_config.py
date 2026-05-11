from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from liberty.connectors.base import detect_statement_type, find_bind_params
from liberty.connectors.config import (
    ApiConnectorConfig,
    SqlConnectorConfig,
    load_connectors_file,
    parse_connectors,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_shipped_config_parses() -> None:
    cfg = load_connectors_file(REPO_ROOT / "config" / "connectors.toml")
    assert "default" in cfg.pools
    assert isinstance(cfg.connectors["liberty"], SqlConnectorConfig)
    assert isinstance(cfg.connectors["httpbin"], ApiConnectorConfig)
    assert {q.name for q in cfg.connectors["liberty"].queries} == {"ping", "now"}


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
