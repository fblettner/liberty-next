"""Thick-mode Oracle fetch in an isolated subprocess — for LOBs over a database link.

python-oracledb supports asyncio **only in thin mode**, and thin mode **cannot fetch a LOB over
a database link** (it fails with ORA-22992 / ORA-03149 — the LOB comes back as a remote locator
the client can't dereference). The thick (OCI) client *can* fetch it — but it's synchronous-only
and ``init_oracle_client()`` is process-global, so it can't be switched on inside the async server.

So this module runs the offending queries (a handful of BLOB-over-dblink SELECTs) in a
**short-lived spawned subprocess** that enables thick mode there and there alone. The async parent
stays thin; the child returns the rows (LOBs already materialised as ``bytes`` / ``str``) over a
pipe. Intended for small, bounded result sets (e.g. JDE composite / E1-page definition BLOBs) —
it ``fetchall``\\ s, so don't point it at millions of rows.

Requires the Oracle Instant Client in the image (see the Dockerfile). ``init_oracle_client``'s
``lib_dir`` comes from ``LIBERTY_ORACLE_CLIENT_LIB`` when set, else the default library search path.
"""
from __future__ import annotations

import asyncio
import multiprocessing
import os
from typing import Any

from liberty.connectors.base import ConnectorError

# Cap the wait on the child so a hung OCI call can't wedge a run forever.
_DEFAULT_TIMEOUT_S = 300


class ThickFetchError(ConnectorError):
    """A thick-mode subprocess fetch failed (client init, connect, or the query itself)."""


def _worker(conn: Any, connect_params: dict[str, str], sql: str, params: dict[str, Any]) -> None:
    """Runs in the spawned child: enable the thick client, fetch, ship rows back as dicts.
    Any failure is sent as an ``{"error": …}`` message — the child never crashes silently."""
    try:
        import oracledb  # imported in the child so thick init stays process-local
        lib_dir = os.environ.get("LIBERTY_ORACLE_CLIENT_LIB", "").strip() or None
        oracledb.init_oracle_client(lib_dir=lib_dir)   # THICK mode — this process only
        oracledb.defaults.fetch_lobs = False           # LOBs → bytes/str inline, not locators
        db = oracledb.connect(**connect_params)
        try:
            cur = db.cursor()
            cur.execute(sql, params or {})
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            db.close()
        conn.send({"rows": rows})
    except Exception as e:  # noqa: BLE001 — report, don't crash
        conn.send({"error": f"{type(e).__name__}: {e}"})
    finally:
        conn.close()


def _run_subprocess(connect_params: dict[str, str], sql: str,
                    params: dict[str, Any] | None, timeout: int) -> list[dict[str, Any]]:
    ctx = multiprocessing.get_context("spawn")   # clean process — no inherited thin/async state
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_worker, args=(child_conn, connect_params, sql, params), daemon=True)
    proc.start()
    child_conn.close()   # parent keeps only the receive end
    try:
        if not parent_conn.poll(timeout):
            raise ThickFetchError(f"thick-mode fetch timed out after {timeout}s")
        result = parent_conn.recv()
    finally:
        parent_conn.close()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
    if "error" in result:
        raise ThickFetchError(result["error"])
    return result["rows"]


async def fetch_thick(pools: Any, pool_name: str, sql: str,
                      params: dict[str, Any] | None = None, *,
                      timeout: int = _DEFAULT_TIMEOUT_S) -> list[dict[str, Any]]:
    """Run *sql* against Oracle pool *pool_name* in a thick-mode subprocess; return the rows as a
    list of dicts (uppercase column name → value; BLOB/CLOB already materialised as bytes/str).

    Use ONLY for queries thin mode can't do — a LOB SELECT over a database link. The parent stays
    async/thin; the subprocess is the only place thick mode is turned on. *pools* is a
    :class:`~liberty.connectors.db.PoolRegistry` (decrypted creds + DSN come from
    :meth:`PoolRegistry.oracle_connect_params`). Raises :class:`ThickFetchError` on failure —
    including a clear message when the Instant Client isn't installed."""
    connect_params = pools.oracle_connect_params(pool_name)
    return await asyncio.to_thread(_run_subprocess, connect_params, sql, params, timeout)
