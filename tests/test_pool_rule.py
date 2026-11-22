"""The POOL field rule: a field that names a database pool of this installation is drawn as a
dropdown of the pools — the registry says them to the dictionary, the rule answers like an enum."""
from __future__ import annotations

import pytest

from liberty.connectors.config import ConnectorsFile, PoolConfig, SqlConnectorConfig
from liberty.connectors.dictionary import DictionaryEntry, DictionaryFile
from liberty.connectors.registry import ConnectorRegistry

URL = "sqlite+aiosqlite:///:memory:"


def _file() -> ConnectorsFile:
    return ConnectorsFile(
        pools={"default": PoolConfig(url=URL), "jde_prod": PoolConfig(url=URL), "jde_test": PoolConfig(url=URL)},
        connectors={
            "app": SqlConnectorConfig(type="sql", pool="default"),
            "jdedwards": SqlConnectorConfig(type="sql", pool="jde_prod", pools=["jde_test", "jde_prod"]),
        },
    )


@pytest.mark.asyncio
async def test_the_rule_offers_every_pool_or_those_of_a_connector() -> None:
    reg = ConnectorRegistry(_file(), dictionary=DictionaryFile(entries={
        "ANY_POOL": DictionaryEntry(label="Pool", rules="POOL"),
        "JDE_POOL": DictionaryEntry(label="Pool", rules="POOL", rules_values="jdedwards"),
        "NO_SUCH": DictionaryEntry(label="Pool", rules="pool", rules_values="missing"),
    }))
    try:
        d = reg.dictionary
        every = d.resolve_rule(d.find_entry("ANY_POOL"))
        assert every == {"kind": "enum", "values": [{"value": p, "label": p} for p in ("default", "jde_prod", "jde_test")]}
        # a connector named: its own pool first, then the others it may run on — each once
        own = d.resolve_rule(d.find_entry("JDE_POOL"))
        assert [v["value"] for v in own["values"]] == ["jde_prod", "jde_test"]
        # a connector that does not exist offers nothing (the field then shows what is stored)
        assert d.resolve_rule(d.find_entry("NO_SUCH")) == {"kind": "enum", "values": []}
    finally:
        await reg.aclose()


def test_a_dictionary_read_alone_knows_no_pool() -> None:
    d = DictionaryFile(entries={"P": DictionaryEntry(rules="POOL")})
    assert d.resolve_rule(d.find_entry("P")) == {"kind": "enum", "values": []}
    # the pools are not part of the file: nothing of them is written back
    assert "pools" not in str(d.model_dump())


# ── an LDAP directory as a pool ─────────────────────────────────────────────────────────────────
def _with_directory() -> ConnectorsFile:
    f = _file()
    f.pools["directory"] = PoolConfig(url="ldaps://cn=svc,dc=example,dc=com@ad.example.com/dc=example,dc=com", password="s3cret@")
    f.pools["plain_directory"] = PoolConfig(url="ldap://svc%40corp.local@10.0.0.5:3268")
    return f


@pytest.mark.asyncio
async def test_a_directory_is_a_pool_like_another() -> None:
    reg = ConnectorRegistry(_with_directory())
    try:
        pools = reg.pools
        assert pools.is_directory("directory") and not pools.is_directory("default")
        t = pools.directory("directory")
        assert (t.host, t.port, t.use_ssl) == ("ad.example.com", 636, True)       # the usual port of ldaps
        assert (t.user, t.password, t.base) == ("cn=svc,dc=example,dc=com", "s3cret@", "dc=example,dc=com")
        assert t.url == "ldaps://ad.example.com:636"
        u = pools.directory("plain_directory")
        assert (u.user, u.port, u.use_ssl, u.base, u.password) == ("svc@corp.local", 3268, False, None, None)
        assert pools.dialect("directory") == "ldaps"
        # it is offered by a field ruled POOL, with the databases
        d = DictionaryFile(entries={"P": DictionaryEntry(rules="POOL")})
        d.know_pools(pools.names())
        assert "directory" in [v["value"] for v in d.resolve_rule(d.find_entry("P"))["values"]]
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_each_kind_of_pool_refuses_the_other_use() -> None:
    from liberty.connectors.base import ConnectorError

    reg = ConnectorRegistry(_with_directory())
    try:
        with pytest.raises(ConnectorError, match="is an LDAP directory: it runs no SQL"):
            reg.pools.engine("directory")
        with pytest.raises(ConnectorError, match="is a database, not an LDAP directory"):
            reg.pools.directory("default")
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_testing_a_directory_pool_never_raises() -> None:
    from liberty.connectors.db import try_pool

    # nothing listens there: the answer says so, in the shape a database's test has
    out = await try_pool(PoolConfig(url="ldap://cn=x@127.0.0.1:9"), timeout=2)
    assert out["ok"] is False and out["dialect"] == "ldap" and out["error"]


def test_the_ldap_step_names_a_pool_or_carries_its_server() -> None:
    from liberty.jobs.schema import Step

    base = {"name": "people", "type": "ldap_sync", "target_connector": "app", "target_query": "people_post"}
    assert Step.model_validate({**base, "pool": "directory"}).pool == "directory"
    with pytest.raises(ValueError, match="server, bind_dn, search_base"):
        Step.model_validate(base)
    Step.model_validate({**base, "server": "ldap://h", "bind_dn": "cn=x", "search_base": "dc=y"})


@pytest.mark.asyncio
async def test_where_a_pool_points_is_said_without_its_account() -> None:
    f = _with_directory()
    f.pools["jde"] = PoolConfig(url="oracle+oracledb://jde:secret@ora.example.com:1521/?service_name=JDEPD")
    f.pools["pg"] = PoolConfig(url="postgresql+asyncpg://app@db.example.com/nomasx1")
    reg = ConnectorRegistry(f)
    try:
        assert reg.pools.where("jde") == {"dialect": "oracle", "host": "ora.example.com", "port": 1521, "database": "JDEPD"}
        assert reg.pools.where("pg") == {"dialect": "postgresql", "host": "db.example.com", "port": None, "database": "nomasx1"}
        assert reg.pools.where("directory") == {"dialect": "ldaps", "host": "ad.example.com", "port": None, "database": "dc=example,dc=com"}
        assert "secret" not in str(reg.pools.where("jde"))
    finally:
        await reg.aclose()
