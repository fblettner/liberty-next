"""The assistant's audit and usage record — one row per turn in ``ly2_ai_call``.

What is kept: who asked (the username), through which channel (``chat``, later ``mcp``), on
which application's page, the model, the four token counters, how long it took, how it ended,
and for each tool call its name, the summary of its input (the connector and query, the model,
the keywords — what the chat shows on the tool chip), whether it failed and why, its size and
its time. What is NOT kept: the question, the answer, and any row a tool returned.

Self-bootstrapping like the other framework stores (:mod:`liberty.schema_sync`), on the auth
pool, keyed by username (no FK: an OIDC user has no local row). Recording is best-effort — a
store that is down never breaks a conversation. ``[ai] usage_retention_days`` bounds it
(Monitoring → Retention; purged with the notifications loop).
"""

from __future__ import annotations

import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from sqlalchemy import DateTime, Integer, String, delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

from liberty.connectors.db import PoolRegistry
from liberty.schema_sync import SchemaChange, sync_tables

CHANNEL_CHAT = "chat"
CHANNEL_MCP = "mcp"
# A card of the chat applied by the user (`POST /ai/screen-write` / `/ai/screen-action`): the write
# itself — the turn that proposed it only recorded a proposal. With the MCP rows of the same tools,
# this is the list of what was changed through the assistant, by whom and on which record.
CHANNEL_APPLY = "apply"
# The tools that save something: called over MCP they ARE the change (in the chat they propose).
CHANGE_TOOLS = frozenset({"screen_write", "screen_write_many", "screen_action", "job_run", "api_run", "explorer_save_analysis", "explorer_save_dashboard"})
CHANGES_MAX = 200       # the most changes the summary lists (the newest)

_SUMMARY_MAX = 200     # a tool's input summary, as kept
_ERROR_MAX = 300
_TOOLS_MAX = 60        # tool calls kept per turn (the loop's own cap is far below)
_APP_LINE = re.compile(r"^\s*app:\s*(\S+)", re.MULTILINE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Own metadata so ``create_all`` touches only this table."""


class AiCall(Base):
    __tablename__ = "ly2_ai_call"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    channel: Mapped[str] = mapped_column(String(16), default=CHANNEL_CHAT)
    app: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_errors: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(String(_ERROR_MAX), nullable=True)
    tools: Mapped[list] = mapped_column(JSON, default=list)   # [{name, input, ok, ms, chars[, error]}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "at": self.at.isoformat() if self.at else None, "username": self.username,
            "channel": self.channel, "app": self.app, "model": self.model,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens, "cache_creation_tokens": self.cache_creation_tokens,
            "tool_calls": self.tool_calls, "tool_errors": self.tool_errors, "duration_ms": self.duration_ms,
            "stop_reason": self.stop_reason, "error": self.error, "tools": list(self.tools or []),
        }


def app_of(context: str | None) -> str | None:
    """The application of the page context the browser sent (its ``app: <name>`` line)."""
    m = _APP_LINE.search(context or "")
    return m.group(1)[:128] if m else None


class TurnRecorder:
    """Folds the events of one turn (:class:`liberty.ai.assistant.ChatEvent` dicts) into the
    row to keep. Fed by whoever serialises the stream; ``row()`` is valid at any point, so a
    turn the client abandoned half-way is still recorded with what happened."""

    def __init__(self, *, username: str, model: str, channel: str = CHANNEL_CHAT, app: str | None = None) -> None:
        self.username = username
        self.model = model
        self.channel = channel
        self.app = app
        self._started = time.monotonic()
        self._tools: list[dict[str, Any]] = []
        self._open: dict[str, float] = {}      # tool name → when its call started
        self._usage: dict[str, int] = {}
        self._stop: str | None = None
        self._error: str | None = None

    def observe(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "tool_call":
            name = str(event.get("name") or "")
            self._open[name] = time.monotonic()
            if len(self._tools) < _TOOLS_MAX:
                self._tools.append({"name": name, "input": str(event.get("summary") or "")[:_SUMMARY_MAX], "ok": None})
        elif kind == "tool_result":
            name = str(event.get("name") or "")
            started = self._open.pop(name, None)
            for call in self._tools:
                if call["name"] == name and call["ok"] is None:
                    call["ok"] = bool(event.get("ok"))
                    if started is not None:
                        call["ms"] = int((time.monotonic() - started) * 1000)
                    if event.get("size") is not None:
                        call["chars"] = int(event["size"])
                    if not call["ok"]:
                        call["error"] = str(event.get("summary") or "")[:_ERROR_MAX]
                    break
        elif kind == "done":
            self._stop = event.get("stop_reason")
            self._usage = dict(event.get("usage") or {})
        elif kind == "error":
            self._error = str(event.get("message") or "error")[:_ERROR_MAX]
            self._usage = dict(event.get("usage") or self._usage)

    def row(self) -> AiCall:
        u = self._usage
        return AiCall(
            id=str(uuid.uuid4()), at=_utcnow(), username=self.username, channel=self.channel, app=self.app, model=self.model,
            input_tokens=int(u.get("input_tokens") or 0), output_tokens=int(u.get("output_tokens") or 0),
            cache_read_tokens=int(u.get("cache_read_input_tokens") or 0),
            cache_creation_tokens=int(u.get("cache_creation_input_tokens") or 0),
            tool_calls=len(self._tools), tool_errors=sum(1 for c in self._tools if c["ok"] is False),
            duration_ms=int((time.monotonic() - self._started) * 1000),
            # neither a done nor an error: the client went away before the end
            stop_reason=self._stop or ("error" if self._error else "abandoned"), error=self._error,
            tools=list(self._tools),
        )


class AiUsageStore:
    """The ``ly2_ai_call`` table on the configured pool (the auth pool)."""

    def __init__(self, pools: PoolRegistry, pool_name: str = "default") -> None:
        self._pools = pools
        self._pool_name = pool_name
        self._maker: async_sessionmaker[AsyncSession] | None = None

    @property
    def pool_name(self) -> str:
        return self._pool_name

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
        engine = self._pools.engine(self._pool_name)
        async with engine.begin() as conn:
            return await conn.run_sync(sync_tables, Base.metadata)

    async def record(self, recorder: TurnRecorder) -> None:
        async with self.session() as s:
            s.add(recorder.row())

    async def count(self) -> int:
        async with self.session() as s:
            return int((await s.execute(select(func.count()).select_from(AiCall))).scalar() or 0)

    async def delete_older_than(self, days: int, *, dry_run: bool = False) -> int:
        """Retention: drop the turns older than *days* (*dry_run*: count them only)."""
        cutoff = _utcnow() - timedelta(days=days)
        async with self.session() as s:
            n = int((await s.execute(sa_delete(AiCall).where(AiCall.at < cutoff))).rowcount or 0)
            if dry_run:
                await s.rollback()
            return n

    async def recent(
        self, *, days: int, limit: int = 100, username: str | None = None, channel: str | None = None, failed: bool = False,
    ) -> list[dict[str, Any]]:
        """The latest turns, newest first — narrowed to a user, a channel, or the ones with a
        failure (the turn itself, or one of its tool calls)."""
        stmt = select(AiCall).where(AiCall.at >= _utcnow() - timedelta(days=days))
        if username:
            stmt = stmt.where(AiCall.username == username)
        if channel:
            stmt = stmt.where(AiCall.channel == channel)
        if failed:
            stmt = stmt.where((AiCall.tool_errors > 0) | (AiCall.error.is_not(None)))
        stmt = stmt.order_by(AiCall.at.desc()).limit(max(1, min(limit, 500)))
        async with self.session() as s:
            return [r.to_dict() for r in (await s.execute(stmt)).scalars().all()]

    async def summary(self, *, days: int) -> dict[str, Any]:
        """The period in figures: totals, per user, per day, per model, per channel, per tool,
        and what was changed through the assistant. Aggregated here, not in SQL: the per-tool
        figures live in a JSON column and the day is the server's date — the same code on every
        database. A period is a few thousand rows.

        A *model* is what answered a question of the chat: the rows of the other channels (a tool
        called over MCP, a card applied) carry no model and no tokens — they are counted per
        channel, not as models. ``cost`` is an estimate at list price (:mod:`liberty.ai.catalog`),
        None in a bucket holding a model without a listed price (``unpriced`` names them)."""
        from liberty.ai.catalog import estimate_cost

        since = _utcnow() - timedelta(days=days)
        async with self.session() as s:
            rows = (await s.execute(select(AiCall).where(AiCall.at >= since).order_by(AiCall.at.desc()))).scalars().all()

        def bucket() -> dict[str, Any]:
            return {"turns": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
                    "tool_calls": 0, "tool_errors": 0, "errors": 0, "duration_ms": 0, "cost": 0.0}

        unpriced: set[str] = set()

        def add(b: dict[str, Any], r: AiCall, cost: float | None) -> None:
            b["turns"] += 1
            b["input_tokens"] += r.input_tokens
            b["output_tokens"] += r.output_tokens
            b["cache_read_tokens"] += r.cache_read_tokens
            b["cache_creation_tokens"] += r.cache_creation_tokens
            b["tool_calls"] += r.tool_calls
            b["tool_errors"] += r.tool_errors
            b["errors"] += 1 if r.error else 0
            b["duration_ms"] += r.duration_ms
            if cost is None:
                b["unpriced"] = True
            else:
                b["cost"] += cost

        totals = bucket()
        by_user: dict[str, dict[str, Any]] = {}
        by_day: dict[str, dict[str, Any]] = {}
        by_model: dict[str, dict[str, Any]] = {}
        by_channel: dict[str, dict[str, Any]] = {}
        by_tool: dict[str, dict[str, int]] = {}
        changes: list[dict[str, Any]] = []
        for r in rows:
            chat = r.channel == CHANNEL_CHAT
            tokens = r.input_tokens + r.output_tokens + r.cache_read_tokens + r.cache_creation_tokens
            cost: float | None = 0.0
            if chat and tokens:
                cost = estimate_cost(r.model, input_tokens=r.input_tokens, output_tokens=r.output_tokens,
                                     cache_read_tokens=r.cache_read_tokens, cache_creation_tokens=r.cache_creation_tokens)
                if cost is None:
                    unpriced.add(r.model)
            add(totals, r, cost)
            add(by_user.setdefault(r.username, bucket()), r, cost)
            add(by_day.setdefault(r.at.date().isoformat() if r.at else "", bucket()), r, cost)
            add(by_channel.setdefault(r.channel or CHANNEL_CHAT, bucket()), r, cost)
            if chat:
                add(by_model.setdefault(r.model, bucket()), r, cost)
            for call in r.tools or []:
                name = str(call.get("name") or "")
                t = by_tool.setdefault(name, {"calls": 0, "errors": 0, "ms": 0, "chars": 0})
                t["calls"] += 1
                t["errors"] += 1 if call.get("ok") is False else 0
                t["ms"] += int(call.get("ms") or 0)
                t["chars"] += int(call.get("chars") or 0)
                # a change made through the assistant: a card the user applied, or a tool that
                # saves called over MCP (in the chat such a call only proposes)
                if len(changes) < CHANGES_MAX and (r.channel == CHANNEL_APPLY or (r.channel == CHANNEL_MCP and name in CHANGE_TOOLS)):
                    changes.append({"at": r.at.isoformat() if r.at else None, "username": r.username, "channel": r.channel, "app": r.app,
                                    "tool": name, "what": str(call.get("input") or ""), "ok": call.get("ok"),
                                    **({"error": call["error"]} if call.get("error") else {})})

        def finish(b: dict[str, Any]) -> dict[str, Any]:
            return {**b, "cost": None if b.pop("unpriced", False) and not b["cost"] else round(b["cost"], 4)}

        def listed(d: dict[str, dict[str, Any]], key: str, order: str, *, reverse: bool = True) -> list[dict[str, Any]]:
            return [{key: k, **(finish(v) if "cost" in v else v)}
                    for k, v in sorted(d.items(), key=lambda kv: kv[1][order] if order in kv[1] else kv[0], reverse=reverse)]

        return {
            "days": days, "totals": finish(totals),
            "by_user": listed(by_user, "username", "input_tokens"),
            "by_day": [{"day": k, **finish(v)} for k, v in sorted(by_day.items())],
            "by_model": listed(by_model, "model", "input_tokens"),
            "by_channel": listed(by_channel, "channel", "turns"),
            "by_tool": listed(by_tool, "name", "calls"),
            "changes": changes,
            "unpriced": sorted(unpriced),
        }
