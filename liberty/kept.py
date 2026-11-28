"""Kept answers: what a page's function answered, stored so the page opens at once.

Some pages read a lot before they can draw — a review over every user and every rule, a synthesis of
a whole platform. They do not need the present second: what they show changes when a collection
runs, not while one looks. So the answer of the function behind the page is **kept**:

* the page asks ``POST /api/plugins/run`` with ``kept = "use"`` — the stored answer comes back at
  once, with the moment it was computed (``kept.at``) so an old reading is never taken for the
  present; nothing stored yet: it is computed, stored and returned;
* *Read again* asks with ``kept = "refresh"`` — computed now, however long, and stored for
  everybody;
* a job prepares it: the step ``liberty.kept:j_keep`` (``function = "module:j_function"``) runs the function and stores its answer, so
  the first person to open the page after a collection does not wait.

An answer is kept per function, parameters **and data scope**: a function reads through the
connectors, which apply the caller's row-level scope, so what one scope computed must never be
served to another. A job has no scope: its answer is the unrestricted one, served to the callers who
have none; a scoped caller's first opening computes theirs.

The permission (``plugin:<callable>``) is checked by the route before the store is read. One table,
``ly2_kept_answer``, on the auth pool; an answer that is not a JSON object is not kept.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from liberty.connectors.db import PoolRegistry
from liberty.schema_sync import SchemaChange, sync_tables

_log = logging.getLogger(__name__)

USE, REFRESH = "use", "refresh"
MODES = (USE, REFRESH)


class Base(DeclarativeBase):
    """Own metadata so ``create_all`` touches only the kept-answer table."""


class KeptAnswer(Base):
    __tablename__ = "ly2_kept_answer"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)        # sha-256 of the function, its parameters and the data scope
    callable: Mapped[str] = mapped_column(String(255), index=True)
    params: Mapped[str] = mapped_column(Text)                             # the parameters, as JSON (for a reader of the table)
    scoped: Mapped[str] = mapped_column(String(1), default="N")           # Y = computed under a row-level scope
    answer: Mapped[str] = mapped_column(Text)                             # the function's answer, as JSON
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    computed_by: Mapped[str | None] = mapped_column(String(255))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


def scope_of(principal: Any) -> list[Any]:
    """The row-level scope an answer computed for *principal* was read under — ``[]`` when unrestricted
    (a superuser, a user with no scope rule, a job)."""
    if principal is None or getattr(principal, "is_superuser", False):
        return []
    return [list(group) if isinstance(group, (list, tuple)) else group for group in (getattr(principal, "scope", None) or ())]


def answer_key(ref: str, params: dict[str, Any], scope: list[Any] | None = None) -> str:
    """What names a kept answer: the function, its parameters (their order does not matter), the scope — and the
    environment (pool) picked for the page or named by the job step, when there is one: a function reads the
    database of that environment, so one environment's answer is never another's. Nothing picked (the main
    database) names the answer as it always was."""
    from liberty.connectors.api import current_pool

    named = {"callable": ref, "params": {k: _plain(v) for k, v in params.items() if v is not None}, "scope": scope or []}
    pool = (current_pool.get() or "").strip()
    if pool:
        named["pool"] = pool
    said = json.dumps(named,
                      sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(said.encode("utf-8")).hexdigest()


def _plain(value: Any) -> Any:
    """A parameter as it counts in the key: a number and the text that writes it are the same thing (a page
    sends ``1``, a job's TOML may say ``"1"``); a parameter left empty is one not given."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    return value


class KeptStore:
    """Read and write :class:`KeptAnswer` on the configured pool (the auth pool)."""

    def __init__(self, pools: PoolRegistry, pool_name: str = "default") -> None:
        self._pools = pools
        self._pool_name = pool_name
        self._maker: async_sessionmaker[AsyncSession] | None = None

    def rebind(self, pools: PoolRegistry) -> None:
        """Point at a rebuilt :class:`PoolRegistry` (config reload) — see :mod:`liberty.runtime`."""
        self._pools = pools
        self._maker = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._maker is None:
            self._maker = async_sessionmaker(self._pools.engine(self._pool_name), expire_on_commit=False)
        async with self._maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def create_schema(self) -> list[SchemaChange]:
        """Create ``ly2_kept_answer`` if absent and forward-add missing columns (idempotent)."""
        async with self._pools.engine(self._pool_name).begin() as conn:
            return await conn.run_sync(sync_tables, Base.metadata)

    async def get(self, ref: str, params: dict[str, Any], scope: list[Any] | None = None) -> dict[str, Any] | None:
        """The answer kept for this function, these parameters and this scope: ``{answer, at, by, ms}`` — or None."""
        async with self.session() as s:
            row = (await s.execute(select(KeptAnswer).where(KeptAnswer.key == answer_key(ref, params, scope)))).scalar_one_or_none()
        if row is None:
            return None
        try:
            answer = json.loads(row.answer)
        except ValueError:
            return None
        return {"answer": answer, "at": _iso(row.computed_at), "by": row.computed_by, "ms": row.duration_ms}

    async def put(self, ref: str, params: dict[str, Any], answer: dict[str, Any], *, scope: list[Any] | None = None, by: str | None = None,
                  duration_ms: int | None = None) -> dict[str, Any]:
        """Keep *answer* (replacing the one before); returns its ``{at, by, ms}``."""
        key, now = answer_key(ref, params, scope), datetime.now(UTC)
        text = json.dumps(answer, default=str, ensure_ascii=False)
        async with self.session() as s:
            await s.execute(delete(KeptAnswer).where(KeptAnswer.key == key))
            s.add(KeptAnswer(key=key, callable=ref[:255], params=json.dumps(params, sort_keys=True, default=str, ensure_ascii=False), scoped="Y" if scope else "N",
                             answer=text, computed_at=now, computed_by=(by or None), duration_ms=duration_ms))
        return {"at": _iso(now), "by": by, "ms": duration_ms}

    async def forget(self, ref: str | None = None) -> int:
        """Drop the answers kept for one function — or all of them. Returns how many."""
        async with self.session() as s:
            done = await s.execute(delete(KeptAnswer) if ref is None else delete(KeptAnswer).where(KeptAnswer.callable == ref))
        return int(done.rowcount or 0)


def _iso(moment: datetime) -> str:
    """A moment as the pages read it — in UTC, also from a database that gives it back without its zone."""
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


_INJECTED = ("connectors", "ctx", "settings", "changesets", "app_state")


def environments_of(connectors: Any, connector: str) -> list[str | None]:
    """The environments of *connector* as an answer is kept under: ``None`` for its main database (what a page
    sends when nothing else is picked), then the name of each of its other databases (pools)."""
    from liberty.connectors.api import current_pool

    token = current_pool.set(None)
    try:
        conn = connectors.get(connector)
    finally:
        current_pool.reset(token)
    main = str(getattr(conn, "pool_name", "") or "")
    return [None, *sorted(str(p) for p in (getattr(conn, "allowed_pools", None) or ()) if str(p) != main)]


async def j_keep(*, connectors: Any, app_state: Any = None, settings: Any = None, ctx: Any = None, function: str, langs: str | None = None,
                 every_pool_of: str | None = None, **given: Any) -> dict[str, Any]:
    """Job step: run the page function *function* (``module:function``) and keep its answer, so the
    page opens at once. The function's parameters are the step's other values **that the function
    declares** — so the job's own parameters (``apps_id`` of a job run once per application) reach it
    and the rest is left out. They must be the ones the page sends: an answer is kept per parameters.
    ``langs = "en,fr"`` keeps one answer per language, passed as ``lang``. Kept as the unrestricted
    answer (a job has no data scope).

    ``every_pool_of = "<connector>"``: that connector runs on several databases, one per environment, and a
    page shows the one picked — an answer is kept per environment. The step then keeps one answer for EACH
    database of the connector, so the page opens at once whichever is picked; an environment that cannot be
    read is said and the others are kept (the step fails when none could be). A run that has its own
    environment (the job's or the step's) keeps that one only. Either way the connector's main database is
    kept as a page asks for it: under no environment's name.

    ``[[jobs.steps]] type = "python"``, ``callable = "liberty.kept:j_keep"``,
    ``op_kwargs = { function = "nomasx1.sod_review:j_review" }``."""
    import inspect

    from liberty.plugins.invoke import build_kwargs, call_target, normalise_result, resolve_callable

    store = getattr(app_state, "kept", None)
    if store is None:
        raise RuntimeError("kept answers: the store is not available (the auth pool could not be used at start-up)")
    target = resolve_callable(function)
    declared = [name for name, p in inspect.signature(target).parameters.items() if p.kind is not inspect.Parameter.VAR_KEYWORD and name not in _INJECTED]
    asked = {name: given[name] for name in declared if given.get(name) is not None}
    languages = [code.strip() for code in (langs or "").split(",") if code.strip()] if "lang" in declared else []
    who = str(getattr(getattr(ctx, "trigger", None), "triggered_by", "") or "") or "job"
    from liberty.connectors.api import current_pool

    # the environments to keep: the run's own, or every database of the connector named — the main one as None
    pools: list[str | None] = [current_pool.get()]
    if every_pool_of:
        known = environments_of(connectors, every_pool_of)
        pools = known if not pools[0] else [pools[0] if pools[0] in known else None]
    done: list[dict[str, Any]] = []
    failed: list[str] = []
    for pool in pools:
        token = current_pool.set(pool)
        try:
            for lang in languages or [None]:
                params = {**asked, **({"lang": lang} if lang else {})}
                started = time.monotonic()
                kwargs = build_kwargs(target, dict(params), injections=(("connectors", connectors), ("ctx", ctx), ("settings", settings)))
                try:
                    answer = normalise_result(await call_target(target, kwargs)).extras
                    if not isinstance(answer, dict) or not answer:
                        raise RuntimeError(f"kept answers: {function} did not answer an object — nothing to keep")
                except Exception as exc:
                    if len(pools) == 1:
                        raise
                    failed.append(f"{pool or 'main database'}: {str(exc).splitlines()[0][:200]}")
                    _log.warning("kept answers: %s %s not kept for %s (%s)", function, params, pool or "the main database", str(exc).splitlines()[0][:300])
                    continue
                took = int((time.monotonic() - started) * 1000)
                kept = await store.put(function, params, answer, by=who, duration_ms=took)
                _log.info("kept answers: %s %s kept%s (%d ms)", function, params, f" for {pool}" if pool else "", took)
                done.append({"params": params, **({"pool": pool} if pool else {}), "duration_ms": took, **kept})
        finally:
            current_pool.reset(token)
    if failed and not done:
        raise RuntimeError(f"kept answers: {function} could be read in no environment — " + "; ".join(failed))
    return {"function": function, "kept": done, "answers": len(done), **({"not_kept": failed} if failed else {})}
