"""Thick-mode subprocess fetch — connect-param extraction + subprocess plumbing.

The actual thick fetch needs Oracle Instant Client + a live DB, so it can't run in CI; but the
credential extraction (:meth:`PoolRegistry.oracle_connect_params`) and the subprocess error
propagation (:func:`liberty.connectors.thick.fetch_thick`) are both testable here.
"""
from __future__ import annotations

import pytest

from liberty.connectors.base import ConnectorError
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.connectors.thick import ThickFetchError, fetch_thick
from liberty.crypto import encrypt


def test_oracle_connect_params_extracts_decrypted_creds() -> None:
    mk = "thick-test-key"
    # password in the separate ENC field, special chars, service_name in the query
    pools = PoolRegistry(
        {"jde": PoolConfig(url="oracle+oracledb://scott@h:1521/?service_name=ORCLPDB",
                           password=encrypt("t1ger@/:", mk))},
        master_key=mk,
    )
    assert pools.oracle_connect_params("jde") == {
        "user": "scott", "password": "t1ger@/:", "dsn": "h:1521/ORCLPDB",
    }


def test_oracle_connect_params_uses_sid_when_no_service() -> None:
    pools = PoolRegistry({"jde": PoolConfig(url="oracle+oracledb://u:pw@host:1600/XE")})
    p = pools.oracle_connect_params("jde")
    assert p == {"user": "u", "password": "pw", "dsn": "host:1600/XE"}


def test_oracle_connect_params_defaults_port_1521() -> None:
    pools = PoolRegistry({"jde": PoolConfig(url="oracle+oracledb://u:pw@host/?service_name=S")})
    assert pools.oracle_connect_params("jde")["dsn"] == "host:1521/S"


def test_oracle_connect_params_rejects_non_oracle() -> None:
    pools = PoolRegistry({"pg": PoolConfig(url="postgresql+asyncpg://u:pw@h:5432/db")})
    with pytest.raises(ConnectorError, match="not oracle"):
        pools.oracle_connect_params("pg")


@pytest.mark.asyncio
async def test_fetch_thick_surfaces_error_as_thickfetcherror() -> None:
    """No Instant Client in CI → init_oracle_client fails in the child; if a client IS present,
    the bogus DSN refuses instantly. Either way it comes back as a clean ThickFetchError —
    proving the spawn/pipe plumbing and error propagation (not a raw crash)."""
    pools = PoolRegistry({
        "jde": PoolConfig(url="oracle+oracledb://u:pw@127.0.0.1:1/?service_name=NOPE"),
    })
    with pytest.raises(ThickFetchError):
        await fetch_thick(pools, "jde", "SELECT 1 FROM DUAL", timeout=15)
