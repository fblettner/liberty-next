"""Database pool registry — v1's multi-tenant ``apps_pool`` concept, minus the
metadata tables.

One :class:`~sqlalchemy.ext.asyncio.AsyncEngine` per named pool, created lazily
on first use so an unreachable database in the config never blocks startup and
test configs never open real connections. SQL connectors reference pools by
name; the registry owns their lifecycle.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from liberty.connectors.base import UnknownPoolError
from liberty.connectors.config import PoolConfig


class PoolRegistry:
    """Holds pool configs and lazily materialises one engine per pool."""

    def __init__(self, configs: dict[str, PoolConfig] | None = None) -> None:
        self._configs: dict[str, PoolConfig] = dict(configs or {})
        self._engines: dict[str, AsyncEngine] = {}

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

    def engine(self, name: str) -> AsyncEngine:
        """Return (creating on first call) the engine for pool *name*."""
        engine = self._engines.get(name)
        if engine is not None:
            return engine
        cfg = self._config(name)
        kwargs: dict[str, object] = {
            "echo": cfg.echo,
            "pool_pre_ping": cfg.pool_pre_ping,
            "pool_recycle": cfg.pool_recycle,
        }
        # SQLite uses StaticPool/NullPool, which reject QueuePool sizing args.
        if not cfg.url.startswith("sqlite"):
            kwargs["pool_size"] = cfg.pool_size
            kwargs["max_overflow"] = cfg.max_overflow
        engine = create_async_engine(cfg.url, **kwargs)
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
