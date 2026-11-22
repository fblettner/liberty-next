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


# ── an HTTP server as a pool: what an API connector names as its server ──────────────────────────
def _with_server() -> ConnectorsFile:
    from liberty.connectors.config import ApiConnectorConfig, ApiTargetConfig, EndpointDef

    f = _file()
    f.pools["jde_ais"] = PoolConfig(url="https://JDE@ais.example.com:7077/jderest/v2", password="ENC:not-a-real-secret",
                                    signing_key="/etc/liberty/ais.pem", certificate="/etc/liberty/ais.cer", verify_ssl=False)
    f.pools["jde_ais_test"] = PoolConfig(url="http://TESTER:in-url@ais-test.example.com/jderest/v2")
    f.connectors["ais"] = ApiConnectorConfig(
        type="api", server="jde_ais", auth_type="basic", pool="jde_prod",
        targets={"jde_test": ApiTargetConfig(server="jde_ais_test")},
        endpoints=[EndpointDef(name="ping", path="/defaultconfig")],
    )
    return f


@pytest.mark.asyncio
async def test_an_api_connector_reads_its_server_from_the_pool() -> None:
    reg = ConnectorRegistry(_with_server())
    try:
        api = reg.api("ais")
        assert api.server_problem is None
        assert api.config.base_url == "https://ais.example.com:7077/jderest/v2"      # the address without the account
        assert (api.config.auth_username, api.config.verify_ssl) == ("JDE", False)
        # the pool's one secret serves the scheme the connector uses: a password, or a token
        assert api.config.auth_password == api.config.auth_token == "ENC:not-a-real-secret"
        assert (api.config.auth_jwt_key, api.config.auth_jwt_certificate) == ("/etc/liberty/ais.pem", "/etc/liberty/ais.cer")
        # each environment names its own server pool
        test = api.for_pool("jde_test")
        assert test.config.base_url == "http://ais-test.example.com/jderest/v2"
        assert (test.config.auth_username, test.config.auth_password, test.config.verify_ssl) == ("TESTER", "in-url", True)
        # a server pool is a pool like another: listed, located, and it runs no SQL
        assert reg.pools.is_server("jde_ais") and not reg.pools.is_server("default")
        assert reg.pools.where("jde_ais") == {"dialect": "https", "host": "ais.example.com", "port": 7077, "database": "jderest/v2"}
        from liberty.connectors.base import ConnectorError
        with pytest.raises(ConnectorError, match="is an HTTP server: it runs no SQL"):
            reg.pools.engine("jde_ais")
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_a_server_pool_that_is_missing_is_said_on_the_call_not_at_startup() -> None:
    f = _with_server()
    del f.pools["jde_ais"]
    f.connectors["ais"].targets["jde_test"].server = "default"          # a database, not a server
    reg = ConnectorRegistry(f)                                           # the installation still loads
    try:
        api = reg.api("ais")
        assert "names the server pool 'jde_ais': there is no such pool" in api.server_problem
        out = await api.call("ping")
        assert out.success is False and out.status_code == 0 and "Liberty → Pools" in out.error
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_a_connector_without_a_server_is_as_before() -> None:
    from liberty.connectors.config import ApiConnectorConfig
    from liberty.connectors.server import with_servers

    own = ApiConnectorConfig(type="api", base_url="https://api.example.com", auth_type="bearer", auth_token="t")
    effective, problem = with_servers(own, {})
    assert effective is own and problem is None


@pytest.mark.asyncio
async def test_testing_a_server_pool_never_raises() -> None:
    from liberty.connectors.db import try_pool

    out = await try_pool(PoolConfig(url="http://user@127.0.0.1:9/api", password="x"), timeout=2)
    assert out["ok"] is False and out["dialect"] == "http" and out["error"]


def test_an_upgrade_keeps_the_server_an_installation_named() -> None:
    from liberty import config_merge

    shipped = {"type": "api", "server": "jde_ais", "auth_type": "basic", "endpoints": []}
    live = {"type": "api", "server": "ais_production", "auth_type": "basic", "endpoints": []}
    merged, _ = config_merge._with_live_secrets(shipped, live)
    assert merged["server"] == "ais_production"          # the installation's choice, and it does not make the connector look changed
    assert config_merge._same(merged, live)


@pytest.mark.asyncio
async def test_a_server_pool_shipped_without_its_address_does_not_stop_the_installation() -> None:
    # what an application ships: the pool named, its URL a reference the installation has not set yet
    f = _with_server()
    f.pools["jde_ais"] = PoolConfig(url="")
    reg = ConnectorRegistry(f)
    try:
        api = reg.api("ais")
        assert "names the server pool 'jde_ais': it has no URL yet" in api.server_problem
        assert (await api.call("ping")).success is False
        # a database named as a server is said for what it is
        f.connectors["ais"].server = "default"
        other = ConnectorRegistry(f)
        assert "it is not an HTTP server" in other.api("ais").server_problem
        await other.aclose()
    finally:
        await reg.aclose()
