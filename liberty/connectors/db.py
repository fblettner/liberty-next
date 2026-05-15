"""Database pool registry — v1's multi-tenant ``apps_pool`` concept, minus the
metadata tables.

One :class:`~sqlalchemy.ext.asyncio.AsyncEngine` per named pool, created lazily
on first use so an unreachable database in the config never blocks startup and
test configs never open real connections. SQL connectors reference pools by
name; the registry owns their lifecycle.
"""

from __future__ import annotations

import logging

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from liberty.connectors.base import UnknownPoolError
from liberty.connectors.config import PoolConfig
from liberty.crypto import decrypt_or_keep, is_encrypted

_log = logging.getLogger(__name__)


class PoolRegistry:
    """Holds pool configs and lazily materialises one engine per pool."""

    def __init__(self, configs: dict[str, PoolConfig] | None = None, *, master_key: str = "") -> None:
        self._configs: dict[str, PoolConfig] = dict(configs or {})
        self._engines: dict[str, AsyncEngine] = {}
        self._master_key = master_key

    def names(self) -> list[str]:
        return list(self._configs)

    def has(self, name: str) -> bool:
        return name in self._configs

    def _config(self, name: str) -> PoolConfig:
        cfg = self._configs.get(name)
        if cfg is None:
            raise UnknownPoolError(f"Unknown pool {name!r}. Defined: {sorted(self._configs) or '(none)'}.")
        if not cfg.url.strip():
            raise UnknownPoolError(
                f"Pool {name!r} has an empty url — set the referenced env var "
                "(e.g. LIBERTY_DB_URL) or edit config/connectors.toml."
            )
        return cfg

    def schemas(self, name: str) -> dict[str, str]:
        """The ``[pools.<name>] schemas`` map (``#SCHEMA.<NAME>#`` placeholder → real schema name);
        empty when the pool doesn't define any (or doesn't exist)."""
        cfg = self._configs.get(name)
        return dict(cfg.schemas) if cfg else {}

    def dialect(self, name: str) -> str:
        """The SQLAlchemy backend name for pool *name* (``postgresql`` / ``oracle`` / …) —
        a live engine's own dialect if one is registered, else the explicit
        ``[pools.<name>] dialect``, else derived from the URL."""
        engine = self._engines.get(name)
        if engine is not None:
            return engine.dialect.name
        cfg = self._config(name)
        if cfg.dialect:
            return cfg.dialect
        return make_url(cfg.url).get_backend_name()

    def trim_strings(self, name: str) -> bool:
        """Whether the SQL connector should strip trailing whitespace from string cells on this
        pool's reads. Honours the pool's explicit ``trim_strings`` flag; auto-enables on Oracle
        dialect (where CHAR/NCHAR columns are space-padded — v1's behaviour); off elsewhere.
        Unknown pools default to off."""
        cfg = self._configs.get(name)
        if cfg is None:
            return False
        if cfg.trim_strings is not None:
            return cfg.trim_strings
        return self.dialect(name) == "oracle"

    def coalesce_nulls(self, name: str) -> bool:
        """Whether the SQL connector should replace ``None`` bind values with type-appropriate
        sentinels (``''`` for char columns, ``0`` for number columns) on INSERT / UPDATE / MERGE
        against this pool. Honours the explicit flag; auto-enables on Oracle. Off elsewhere."""
        cfg = self._configs.get(name)
        if cfg is None:
            return False
        if cfg.coalesce_nulls is not None:
            return cfg.coalesce_nulls
        return self.dialect(name) == "oracle"

    def _resolved_url(self, name: str, cfg: PoolConfig):
        """The pool's URL with its password resolved: a separate ``password`` (or an ``ENC:``
        password embedded in the URL) is decrypted via the crypto master key and re-set on the URL
        object (which escapes it properly — so ``@`` / ``/`` / ``:`` in the password are safe). A
        wrong/missing key leaves the ``ENC:`` value as-is (a logged warning, not a crash — the
        connection will then fail loudly with bad credentials, like v1)."""
        url = make_url(cfg.url)
        raw_pw = cfg.password if cfg.password is not None else url.password
        if raw_pw is None or not is_encrypted(raw_pw) and cfg.password is None:
            return url  # nothing to substitute (URL used as-is)
        pw, err = decrypt_or_keep(raw_pw, self._master_key)
        if err:
            _log.warning("pool %r: %s — connecting with the configured value as-is", name, err)
        return url.set(password=pw)

    def engine(self, name: str) -> AsyncEngine:
        """Return (creating on first call) the engine for pool *name*."""
        engine = self._engines.get(name)
        if engine is not None:
            return engine
        cfg = self._config(name)
        url = self._resolved_url(name, cfg)
        kwargs: dict[str, object] = {
            "echo": cfg.echo,
            "pool_pre_ping": cfg.pool_pre_ping,
            "pool_recycle": cfg.pool_recycle,
        }
        # SQLite uses StaticPool/NullPool, which reject QueuePool sizing args.
        if url.get_backend_name() != "sqlite":
            kwargs["pool_size"] = cfg.pool_size
            kwargs["max_overflow"] = cfg.max_overflow
        engine = create_async_engine(url, **kwargs)
        self._engines[name] = engine
        return engine

    def register_engine(self, name: str, engine: AsyncEngine) -> None:
        """Inject a pre-built engine (used by tests against in-memory SQLite)."""
        self._engines[name] = engine

    async def dispose(self) -> None:
        """Dispose every materialised engine and forget it."""
        for engine in self._engines.values():
            await engine.dispose()
        self._engines.clear()
