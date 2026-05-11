"""ConnectorRegistry — builds and owns the connector set from ``connectors.toml``.

This is the single object the rest of the app talks to. It holds the pool
registry (shared by all SQL connectors) and one connector instance per
``[connectors.*]`` entry, and it knows how to tear them down on shutdown. Being
rebuilt from a fresh :class:`ConnectorsFile` is the basis for hot-reload.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from liberty.connectors.api import APIConnector
from liberty.connectors.base import UnknownConnectorError
from liberty.connectors.config import (
    ApiConnectorConfig,
    ConnectorsFile,
    SqlConnectorConfig,
    load_connectors_file,
)
from liberty.connectors.db import PoolRegistry
from liberty.connectors.sql import SQLConnector

Connector = SQLConnector | APIConnector


class ConnectorRegistry:
    """Holds every connector plus the shared :class:`PoolRegistry`."""

    def __init__(self, config: ConnectorsFile, *, http_client: httpx.AsyncClient | None = None) -> None:
        self.pools = PoolRegistry(config.pools)
        self._http_client = http_client
        self._connectors: dict[str, Connector] = {}
        for name, conn_cfg in config.connectors.items():
            if isinstance(conn_cfg, SqlConnectorConfig):
                self._connectors[name] = SQLConnector(name, conn_cfg, self.pools)
            elif isinstance(conn_cfg, ApiConnectorConfig):
                self._connectors[name] = APIConnector(name, conn_cfg, client=http_client)
            else:  # pragma: no cover - guarded by the discriminated union
                raise TypeError(f"Unsupported connector config: {type(conn_cfg)!r}")

    # -- lookup ------------------------------------------------------------ #

    def __contains__(self, name: object) -> bool:
        return name in self._connectors

    def __len__(self) -> int:
        return len(self._connectors)

    def names(self) -> list[str]:
        return list(self._connectors)

    def get(self, name: str) -> Connector:
        try:
            return self._connectors[name]
        except KeyError:
            raise UnknownConnectorError(
                f"Unknown connector {name!r}. Defined: {self.names() or '(none)'}."
            ) from None

    def sql(self, name: str) -> SQLConnector:
        conn = self.get(name)
        if not isinstance(conn, SQLConnector):
            raise UnknownConnectorError(f"Connector {name!r} is not a SQL connector.")
        return conn

    def api(self, name: str) -> APIConnector:
        conn = self.get(name)
        if not isinstance(conn, APIConnector):
            raise UnknownConnectorError(f"Connector {name!r} is not an API connector.")
        return conn

    def describe(self) -> list[dict]:
        return [conn.describe() for conn in self._connectors.values()]

    # -- lifecycle --------------------------------------------------------- #

    async def aclose(self) -> None:
        for conn in self._connectors.values():
            if isinstance(conn, APIConnector):
                await conn.aclose()
        await self.pools.dispose()


def load_connectors(
    path: Path | str, *, http_client: httpx.AsyncClient | None = None
) -> ConnectorRegistry:
    """Load ``connectors.toml`` at *path* and build a :class:`ConnectorRegistry`."""
    return ConnectorRegistry(load_connectors_file(path), http_client=http_client)
