"""A pool whose driver has no asyncio (SQL Server through pymssql, Db2, IBM i through ODBC) runs in
threads behind the interface every pool has — what the thick Oracle pools already run on.

SQLite stands for them here: ``sqlite://`` is the same database through its plain (sync) driver.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from liberty.connectors.base import ConnectorError
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry, driver_is_async, ping_sql, try_pool
from liberty.connectors.thick_engine import ThickAsyncEngine


def test_which_drivers_speak_asyncio() -> None:
    assert driver_is_async(make_url("sqlite+aiosqlite:///x.db")) is True
    assert driver_is_async(make_url("postgresql+asyncpg://u@h/db")) is True
    assert driver_is_async(make_url("oracle+oracledb://u@h/?service_name=x")) is True    # one driver, both ways: the asyncio one is taken
    assert driver_is_async(make_url("sqlite:///x.db")) is False
    assert driver_is_async(make_url("mssql+pymssql://u@h/db")) is False           # the dialect ships with SQLAlchemy; the driver is asked for later


def test_a_driver_that_is_not_installed_names_its_extra() -> None:
    for url, extra in (("ibmi://u@h/db", "ibmi"), ("db2+ibm_db://u@h:50000/db", "db2")):
        try:
            make_url(url).get_dialect()
        except Exception:  # noqa: BLE001 — not installed here: the message is what is tested
            with pytest.raises(ConnectorError, match=rf"liberty-next\[{extra}\]"):
                driver_is_async(make_url(url))


def test_a_pool_whose_driver_is_missing_says_what_to_install(monkeypatch) -> None:
    from liberty.connectors import db

    def absent(url, **_kwargs):
        raise ModuleNotFoundError("No module named 'pymssql'")

    monkeypatch.setattr(db, "create_engine", absent)
    with pytest.raises(ConnectorError, match=r"pool 'jde'.*liberty-next\[mssql\].*pymssql"):
        PoolRegistry({"jde": PoolConfig(url="mssql+pymssql://u:pw@host/JDE920")}).engine("jde")


def test_the_ping_each_database_accepts() -> None:
    assert ping_sql("oracle") == "SELECT 1 FROM DUAL"
    assert ping_sql("db2") == ping_sql("ibmi") == ping_sql("ibm_db_sa") == "SELECT 1 FROM SYSIBM.SYSDUMMY1"
    assert ping_sql("postgresql") == ping_sql("mssql") == ping_sql("") == "SELECT 1"


@pytest.mark.asyncio
async def test_a_sync_driver_pool_reads_writes_and_streams(tmp_path: Path) -> None:
    pools = PoolRegistry({"plain": PoolConfig(url=f"sqlite:///{tmp_path / 'plain.db'}")})
    engine = pools.engine("plain")
    assert isinstance(engine, ThickAsyncEngine) and pools.dialect("plain") == "sqlite"
    assert pools.engine("plain") is engine                                         # built once
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE t (n INTEGER, s TEXT)"))
            await conn.execute(text("INSERT INTO t VALUES (:n, :s)"), {"n": 1, "s": "one"})
            await conn.execute(text("INSERT INTO t VALUES (:n, :s)"), {"n": 2, "s": "two"})
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT n, s FROM t WHERE n >= :n ORDER BY n"), {"n": 1})
            assert list(result.keys()) == ["n", "s"] and [dict(r) for r in result.mappings()] == [{"n": 1, "s": "one"}, {"n": 2, "s": "two"}]
            stream = await conn.stream(text("SELECT n FROM t ORDER BY n"))
            assert [[r["n"] for r in part] async for part in stream.mappings().partitions(1)] == [[1], [2]]
        # a transaction that fails leaves nothing behind
        with pytest.raises(RuntimeError):
            async with engine.begin() as conn:
                await conn.execute(text("INSERT INTO t VALUES (3, 'three')"))
                raise RuntimeError("stop")
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM t"))).scalar() == 2
    finally:
        await pools.dispose()


@pytest.mark.asyncio
async def test_the_connection_test_of_a_sync_driver_pool(tmp_path: Path) -> None:
    answer = await try_pool(PoolConfig(url=f"sqlite:///{tmp_path / 'probe.db'}"))
    assert answer["ok"] is True and answer["dialect"] == "sqlite"
