"""Async-over-sync engine adapter for python-oracledb **thick** mode.

Thick mode (OCI) is required for Oracle Native Network Encryption (NNE) — thin mode raises
``DPY-3001`` — but python-oracledb only supports asyncio in *thin* mode, and
``init_oracle_client()`` is process-global. So a thick Oracle pool can't use SQLAlchemy's
``AsyncEngine`` (which needs an async driver). This module wraps a **sync** SQLAlchemy
:class:`~sqlalchemy.engine.Engine` and presents the subset of the ``AsyncEngine`` /
``AsyncConnection`` interface the framework actually uses, running every DB call in a thread
so the event loop isn't blocked.

Thread safety: python-oracledb connections are **not** safe to use from multiple threads. Each
logical connection therefore gets a **dedicated single-worker executor** — every operation on
that connection (execute, stream, commit, close) runs on the *same* thread for its lifetime.

Streaming: a thick connection can't hand a live server-side cursor across threads safely, so
``stream()`` materialises the whole result in one call and ``partitions()`` slices it from
memory (same shape as :func:`liberty.connectors.thick.fetch_thick`). Fine for the JDE reads this
is used for; a truly enormous single query would want chunking, which thin mode already does.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterator

from sqlalchemy.engine import Engine


class _ThickMappings:
    """Mimics ``Result.mappings()`` — yields each row's ``._mapping`` (a dict-like)."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return [r._mapping for r in self._rows]

    def __iter__(self) -> Iterator[Any]:
        return (r._mapping for r in self._rows)


class _ThickResult:
    """A fully-materialised result — rows/keys/rowcount are captured in the worker thread so the
    async caller reads them without touching the (single-threaded) sync connection."""

    def __init__(self, keys: list[str], rows: list[Any], rowcount: int) -> None:
        self._keys = list(keys)
        self._rows = rows
        self.rowcount = rowcount

    def keys(self) -> list[str]:
        return self._keys

    def fetchall(self) -> list[Any]:
        return self._rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any | None:
        return self._rows[0][0] if self._rows else None

    def mappings(self) -> _ThickMappings:
        return _ThickMappings(self._rows)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._rows)


class _ThickStreamMappings:
    """``result.mappings()`` for the stream path — ``partitions(size)`` yields ``size``-chunk lists
    of each row's ``._mapping`` (the dict-like the streaming consumer iterates with ``.items()``)."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def partitions(self, size: int = 100) -> AsyncIterator[list[Any]]:
        step = max(1, int(size))
        for i in range(0, len(self._rows), step):
            yield [r._mapping for r in self._rows[i:i + step]]


class _ThickStreamResult:
    """The whole result held in memory; ``partitions()`` yields it in ``size`` chunks (thick can't
    stream a live cursor across threads — see the module docstring). ``keys()`` +
    ``mappings().partitions()`` mirror the SQLAlchemy streaming surface ``SQLConnector.execute_stream``
    consumes; ``cursor`` is absent (None) so column typing falls back to names-only, which is fine."""

    cursor = None

    def __init__(self, keys: list[str], rows: list[Any]) -> None:
        self._keys = list(keys)
        self._rows = rows

    def keys(self) -> list[str]:
        return self._keys

    def mappings(self) -> _ThickStreamMappings:
        return _ThickStreamMappings(self._rows)

    async def partitions(self, size: int = 100) -> AsyncIterator[list[Any]]:
        step = max(1, int(size))
        for i in range(0, len(self._rows), step):
            yield self._rows[i:i + step]


class _ThickConn:
    """One logical connection, pinned to a single worker thread (``_run`` submits there)."""

    def __init__(self, executor: ThreadPoolExecutor, sync_conn: Any) -> None:
        self._ex = executor
        self._c = sync_conn

    async def _run(self, fn: Any, *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._ex, fn, *args)

    async def execute(self, statement: Any, parameters: Any = None, **_: Any) -> _ThickResult:
        def _do() -> _ThickResult:
            r = self._c.execute(statement, parameters or {})
            if r.returns_rows:
                rows = r.fetchall()
                keys = list(r.keys())
            else:
                rows, keys = [], []
            return _ThickResult(keys, rows, r.rowcount or 0)
        return await self._run(_do)

    async def stream(self, statement: Any, parameters: Any = None, *,
                     execution_options: Any = None, **_: Any) -> _ThickStreamResult:
        def _do() -> _ThickStreamResult:
            r = self._c.execute(statement, parameters or {})
            rows = r.fetchall()
            keys = list(r.keys())
            return _ThickStreamResult(keys, rows)
        return await self._run(_do)

    async def commit(self) -> None:
        await self._run(self._c.commit)

    async def rollback(self) -> None:
        await self._run(self._c.rollback)


class ThickAsyncEngine:
    """Presents the ``AsyncEngine`` surface the framework uses over a **sync** Engine. Drop-in for
    a thick Oracle pool — ``PoolRegistry.engine()`` returns this so connectors / ETL / screens run
    unchanged. Only the methods the codebase actually calls are implemented."""

    def __init__(self, sync_engine: Engine) -> None:
        self._engine = sync_engine

    # -- passthrough attributes callers read -------------------------------- #
    @property
    def url(self) -> Any:
        return self._engine.url

    @property
    def dialect(self) -> Any:
        return self._engine.dialect

    @property
    def sync_engine(self) -> Engine:
        return self._engine

    # -- connection context managers ---------------------------------------- #
    @asynccontextmanager
    async def connect(self) -> AsyncIterator[_ThickConn]:
        loop = asyncio.get_running_loop()
        ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oracle-thick")
        try:
            conn = await loop.run_in_executor(ex, self._engine.connect)
            try:
                yield _ThickConn(ex, conn)
            finally:
                await loop.run_in_executor(ex, conn.close)
        finally:
            ex.shutdown(wait=False)

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_ThickConn]:
        """``engine.begin()`` — a transaction that commits on clean exit, rolls back on error."""
        loop = asyncio.get_running_loop()
        ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oracle-thick")
        try:
            conn = await loop.run_in_executor(ex, self._engine.connect)
            trans = await loop.run_in_executor(ex, conn.begin)
            try:
                tc = _ThickConn(ex, conn)
                yield tc
                await loop.run_in_executor(ex, trans.commit)
            except BaseException:
                await loop.run_in_executor(ex, trans.rollback)
                raise
            finally:
                await loop.run_in_executor(ex, conn.close)
        finally:
            ex.shutdown(wait=False)

    async def dispose(self) -> None:
        await asyncio.to_thread(self._engine.dispose)
