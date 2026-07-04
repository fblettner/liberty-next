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

    @property
    def master_key(self) -> str:
        """The AES-GCM master key used for ``ENC:`` secrets — pool / API auth
        passwords and the write-side PASSWORD dictionary rule. Empty when none
        is configured (callers then store / read plaintext)."""
        return self._master_key

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

    def dblinks(self, name: str) -> dict[str, str]:
        """The ``[pools.<name>] dblinks`` map (``#DBLINK.<NAME>#`` placeholder → a DB-link suffix
        like ``@ORCLPROD``); empty when the pool doesn't define any (or doesn't exist). Unlike
        ``schemas``, an unmapped / empty token resolves to ``""`` — the placeholder is dropped."""
        cfg = self._configs.get(name)
        return dict(cfg.dblinks) if cfg else {}

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
        pool's reads. Returns the pool's explicit ``trim_strings`` flag (off by default —
        operator opts in per pool, typically for Oracle pools with CHAR / NCHAR space-padded
        columns like JD Edwards). Unknown pools default to off."""
        cfg = self._configs.get(name)
        return bool(cfg.trim_strings) if cfg is not None else False

    def coalesce_nulls(self, name: str) -> bool:
        """Whether the SQL connector should replace empty bind values (``None`` *or* ``""``)
        with type-appropriate sentinels (``" "`` — a single space — for char columns, ``0`` for
        number columns) on INSERT / UPDATE / MERGE against this pool. Returns the pool's explicit
        ``coalesce_nulls`` flag (off by default — operator opts in per pool, typically for Oracle
        pools whose NOT-NULL string columns can't accept ``''``). Unknown pools default to off."""
        cfg = self._configs.get(name)
        return bool(cfg.coalesce_nulls) if cfg is not None else False

    def debug_sql(self, name: str) -> bool:
        """Whether the SQL connector should log the resolved statement + final binds for every
        execution on this pool. Returns the pool's explicit ``debug_sql`` flag (off by default —
        operator opts in per pool to debug e.g. writes that affect 0 rows). Unknown pools default
        to off."""
        cfg = self._configs.get(name)
        return bool(cfg.debug_sql) if cfg is not None else False


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
        # Oracle fetch batch — the oracledb driver defaults cursor.arraysize to 100, which means
        # ~10x more DB round-trips than asyncpg on a large read. The oracledb dialect accepts
        # ``arraysize`` as a create_engine kwarg (sets the default for every cursor, streaming
        # included), so raising it here speeds up big tables. Oracle only; other backends ignore it.
        if cfg.arraysize and url.get_backend_name() == "oracle":
            kwargs["arraysize"] = int(cfg.arraysize)
        engine = create_async_engine(url, **kwargs)
        # SQLite ships with FK enforcement OFF by default — without ``PRAGMA
        # foreign_keys=ON`` per-connection, ON DELETE CASCADE silently no-ops on
        # the test fixture + any tiny SQLite deployment, leaving orphan rows in
        # nomaflow_step_runs / nomaflow_run_logs when the parent JobRun is
        # deleted (retention sweep, manual cleanup). Postgres + Oracle enforce
        # FKs by default so the listener is a no-op there. Registered through
        # SQLAlchemy's ``connect`` event so it fires on every new connection
        # the pool hands out, not just the first.
        if url.get_backend_name() == "sqlite":
            from sqlalchemy import event

            @event.listens_for(engine.sync_engine, "connect")
            def _enable_sqlite_fk(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
                cursor = dbapi_conn.cursor()
                try:
                    cursor.execute("PRAGMA foreign_keys=ON")
                finally:
                    cursor.close()
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
