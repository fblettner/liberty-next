"""ConnectorRegistry — builds and owns the connector set from ``connectors.toml``.

This is the single object the rest of the app talks to. It holds the pool
registry (shared by all SQL connectors) and one connector instance per
``[connectors.*]`` entry, and it knows how to tear them down on shutdown. Being
rebuilt from a fresh :class:`ConnectorsFile` is the basis for hot-reload.
"""

from __future__ import annotations

import logging
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
from liberty.connectors.dictionary import DictionaryFile, load_dictionary
from liberty.connectors.sql import SQLConnector
from liberty.licensing import ALWAYS_LICENSED_CONNECTORS, LicenseResult

_log = logging.getLogger(__name__)

Connector = SQLConnector | APIConnector


class ConnectorRegistry:
    """Holds every connector, the shared :class:`PoolRegistry`, and the shared field
    :class:`~liberty.connectors.dictionary.DictionaryFile` (passed to each SQL connector
    so its result-column hints resolve labels/formats)."""

    def __init__(
        self,
        config: ConnectorsFile,
        *,
        dictionary: DictionaryFile | None = None,
        http_client: httpx.AsyncClient | None = None,
        master_key: str = "",
        default_language: str = "en",
        oracle_thick: bool = False,
    ) -> None:
        self.pools = PoolRegistry(config.pools, master_key=master_key, oracle_thick=oracle_thick)
        self.dictionary = dictionary or DictionaryFile()
        self._http_client = http_client
        self._connectors: dict[str, Connector] = {}
        for name, conn_cfg in config.connectors.items():
            if isinstance(conn_cfg, SqlConnectorConfig):
                # pass the pool's row-cap default through; the connector folds query → connector → pool
                pool_cfg = config.pools.get(conn_cfg.pool)
                self._connectors[name] = SQLConnector(
                    name, conn_cfg, self.pools, dictionary=self.dictionary,
                    pool_max_rows=pool_cfg.max_rows if pool_cfg else None,
                    default_language=default_language,
                )
            elif isinstance(conn_cfg, ApiConnectorConfig):
                self._connectors[name] = APIConnector(name, conn_cfg, client=http_client, master_key=master_key)
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
        # Drop the Oracle column-type introspection cache so a hot-reload that swaps pools or
        # schemas gets fresh metadata on the next write. Same for the audit-table existence
        # cache — a swapped pool may target a fresh DB that needs the AUD_ tables created
        # again. Imported lazily to keep ``sql`` an internal detail of the registry's
        # clients, not a top-level dep here.
        from liberty.connectors.sql import reset_audit_table_cache, reset_oracle_column_cache
        reset_oracle_column_cache()
        reset_audit_table_cache()


def load_connectors(
    path: Path | str,
    *,
    dictionary_path: Path | str | None = None,
    http_client: httpx.AsyncClient | None = None,
    master_key: str = "",
    license: LicenseResult | None = None,
    default_language: str = "en",
    oracle_thick: bool = False,
) -> ConnectorRegistry:
    """Load ``connectors.toml`` at *path* (and the shared ``dictionary.toml`` — *dictionary_path*,
    or ``dictionary.toml`` next to *path* — a missing file is fine) and build a :class:`ConnectorRegistry`.

    *master_key* (see :mod:`liberty.crypto`) decrypts any ``ENC:`` auth secrets in API connector configs.
    *license* (see :mod:`liberty.licensing`): connectors with ``licensed = true`` that this key doesn't
    cover are dropped (logged) — without a key, the open framework simply doesn't load them. Names in
    ``ALWAYS_LICENSED_CONNECTORS`` are gated the same way **regardless** of their on-disk ``licensed``
    flag — operators can't bypass licensing on those by unchecking the Settings checkbox.
    """
    path = Path(path)
    dict_path = Path(dictionary_path) if dictionary_path else path.with_name("dictionary.toml")
    cfg = load_connectors_file(path)
    gated = {
        name for name, c in cfg.connectors.items()
        if (getattr(c, "licensed", False) or name in ALWAYS_LICENSED_CONNECTORS)
        and not (license is not None and license.covers(name))
    }
    if gated:
        _log.warning(
            "license: not loading licensed connector(s) %s — %s",
            ", ".join(sorted(gated)),
            "no valid license key" if license is None or not license.valid else "not covered by the license key",
        )
        cfg = cfg.model_copy(update={"connectors": {n: c for n, c in cfg.connectors.items() if n not in gated}})
    return ConnectorRegistry(
        cfg,
        dictionary=load_dictionary(dict_path),
        http_client=http_client,
        master_key=master_key,
        default_language=default_language,
        oracle_thick=oracle_thick,
    )
