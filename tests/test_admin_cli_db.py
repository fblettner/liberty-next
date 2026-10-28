"""`liberty-admin set-default-pool` / `retarget-pools`: the framework database moves to another server
(`./liberty db external`) — or comes back."""

from __future__ import annotations

import json
import tomllib

import pytest

from liberty.admin_cli import _retarget_url, main
from liberty.crypto import decrypt

KEY = "cli-db-tests-master-key"


@pytest.fixture
def env(tmp_path, monkeypatch):
    conn = tmp_path / "connectors.toml"
    conn.write_text(
        "# operator comment\n"
        '[pools.default]\nurl = "postgresql+asyncpg://liberty@pg/liberty"\npassword = "old"\n'
        '[pools.nomasx1]\nurl = "postgresql+asyncpg://nomasx1@pg:5432/nomasx1"\n'
        '[pools.jde]\nurl = "oracle+oracledb://system@10.0.0.9:1521/?service_name=JDE"\n'
        '[pools.demo]\nurl = "postgresql+asyncpg://liberty@${POSTGRES_HOST:-pg}:5432/demo"\n'
    )
    app = tmp_path / "app.toml"
    app.write_text(f'[connectors]\nconfig_path = "{conn}"\n[crypto]\nmaster_key = "{KEY}"\n')
    for k in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    return ["--config-app", str(app), "--config-connectors", str(conn)], conn


def _run(base, *args, capsys):
    rc = main([*base, *args])
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else None)


def test_set_default_pool_points_at_the_environment(env, capsys, monkeypatch) -> None:
    base, conn = env
    monkeypatch.setenv("POSTGRES_HOST", "db.example.com")
    monkeypatch.setenv("POSTGRES_PORT", "6432")
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    rc, out = _run(base, "set-default-pool", capsys=capsys)
    assert rc == 0 and out["host"] == "db.example.com"
    pool = tomllib.loads(conn.read_text())["pools"]["default"]
    assert pool["url"] == "postgresql+asyncpg://liberty@db.example.com:6432/liberty"
    assert decrypt(pool["password"], KEY) == "s3cret"
    assert "# operator comment" in conn.read_text()


def test_set_default_pool_needs_a_password(env, monkeypatch) -> None:
    base, _ = env
    monkeypatch.delenv("LIBERTY_DB_URL", raising=False)
    with pytest.raises(SystemExit, match="POSTGRES_PASSWORD"):
        main([*base, "set-default-pool"])


def test_retarget_pools_moves_only_the_pools_on_that_server(env, capsys) -> None:
    base, conn = env
    rc, out = _run(base, "retarget-pools", "--from", "pg:5432", "--to", "db.example.com", "--dry-run", capsys=capsys)
    assert rc == 0 and out["dry_run"] is True and {c["pool"] for c in out["changed"]} == {"default", "nomasx1"}
    assert "@pg" in conn.read_text()                                   # a dry run writes nothing
    rc, out = _run(base, "retarget-pools", "--from", "pg", "--to", "db.example.com:5432", capsys=capsys)
    pools = tomllib.loads(conn.read_text())["pools"]
    assert pools["default"]["url"] == "postgresql+asyncpg://liberty@db.example.com:5432/liberty"
    assert pools["nomasx1"]["url"] == "postgresql+asyncpg://nomasx1@db.example.com:5432/nomasx1"
    assert pools["jde"]["url"].startswith("oracle+oracledb://system@10.0.0.9:1521/")   # another server
    assert "${POSTGRES_HOST:-pg}" in pools["demo"]["url"]                             # follows the env already


def test_retarget_url_matches_host_and_port() -> None:
    assert _retarget_url("postgresql://u@pg/db", "pg", "5432", "h", "5432") == "postgresql://u@h:5432/db"
    assert _retarget_url("postgresql://u@pg:6543/db", "pg", "5432", "h", "5432") is None
    assert _retarget_url("postgresql://u@pgsql:5432/db", "pg", "5432", "h", "5432") is None
    assert _retarget_url("postgresql://u@pg:5432/db?ssl=require", "pg", "5432", "h", "6000") == "postgresql://u@h:6000/db?ssl=require"


def test_set_default_pool_back_to_the_light_sqlite(env, capsys, monkeypatch) -> None:
    base, conn = env
    monkeypatch.setenv("LIBERTY_DB_URL", "sqlite+aiosqlite:////data/liberty.db")
    rc, out = _run(base, "set-default-pool", capsys=capsys)
    assert rc == 0 and tomllib.loads(conn.read_text())["pools"]["default"] == {"url": "${LIBERTY_DB_URL}", "pool_pre_ping": True}
