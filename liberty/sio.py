"""Socket.IO server — record locks + technical dashboard + log tail.

Mirrors v1's `socket_controller.py` design (rooms-as-locks + an "applications"
view of pools/users/locks), built on **python-socketio**'s `AsyncServer`. We
mount it onto the FastAPI app at `/socket.io/` via :func:`mount_sio` (see
:mod:`liberty.main`).

**Why Socket.IO instead of raw FastAPI WebSocket.** v1 used `fastapi_socketio`
for this and it worked. Socket.IO handles reconnects, room membership,
ack callbacks, multi-event dispatch, and namespace isolation for free —
reimplementing those on raw FastAPI WebSocket led to a buggy + brittle layer.

**Auth.** The JWT access token rides in the `auth` payload of the Socket.IO
``connect`` handshake (the client sends ``io({ auth: { token } })``). The
``connect`` handler decodes + verifies it via :class:`TokenService`; failure
returns ``False`` which fails the handshake cleanly (the client sees a
``connect_error`` event with our message).

**Protocol** (events client ⇄ server):
* ``lock`` ``{app, screen, key_values}`` → server checks the room, joins
  caller (if free) or denies (if held). Returns an **ack** to the caller:
  ``{ok: True, lock: {...}}`` or ``{ok: False, holder: {...}}``.
* ``unlock`` ``{app, screen, key_values}`` → caller leaves the room. Ack
  is just ``{ok: True}``.
* Server broadcasts ``lock.acquired`` / ``lock.released`` to **every**
  connected client (including the originator) so the UI uses one code
  path for self-vs-other state.
* ``dashboard.subscribe`` (superuser-only) → caller joins the
  ``dashboard`` room + receives an initial ``dashboard:snapshot``. Server
  re-emits the snapshot on every lock change + every 5s for pool stat
  drift.
* ``logs.subscribe`` (superuser-only) → caller joins the ``logs`` room +
  receives ``log:replay`` (buffered history) + live ``log:line`` events.

Single-instance assumption (no Redis adapter). v2 deploys are one process;
clustering would need `socketio.AsyncRedisManager` — call it out then, not
now.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque

import socketio

from liberty.auth.principal import Principal
from liberty.auth.tokens import TokenError, TokenService

_log = logging.getLogger(__name__)


# ── lock state ──────────────────────────────────────────────────────────────────────

def canonical_lock_key(app: str, screen: str, key_values: dict[str, Any]) -> str:
    """Canonicalise (app, screen, key_values) into a stable string room name.

    Column names are lowercased + sorted; values are stringified. So a lock
    request `{USR_ID: "alice"}` from one client and `{usr_id: "alice"}` from
    another collapse to the same room — same canonicalisation as v2's earlier
    raw-WS attempt + matches v1's behaviour.
    """
    items = sorted(
        (k.lower(), "" if v is None else str(v)) for k, v in (key_values or {}).items()
    )
    kv = "&".join(f"{k}={v}" for k, v in items)
    return f"lock:{app}:{screen}:{kv}"


@dataclass(slots=True)
class HeldLock:
    """One held lock — what the room represents, plus the holding session's metadata.

    Stored in the LockManager keyed by the room name. The room itself carries
    nothing beyond membership; this dataclass is where the user/timestamp lives
    so we can emit it to dashboards + show "Locked by Alice (3m ago)" banners."""
    room: str
    app: str
    screen: str
    key_values: dict[str, Any]
    sid: str
    user_id: str
    username: str
    acquired_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app, "screen": self.screen, "key_values": self.key_values,
            "sid": self.sid, "user_id": self.user_id, "username": self.username,
            "acquired_at": self.acquired_at,
        }


@dataclass(slots=True)
class ConnectedSession:
    """Per-sid state stored in `sio.session(sid)`. The `Principal` is built once
    at connect-time from the JWT claims; we never re-verify mid-session (the JWT's
    own expiry handles that — when it expires the client reconnects with a fresh
    one). `client_id` is operator-supplied via the `auth.client_id` payload."""
    principal: Principal
    client_id: str | None = None


class LockManager:
    """In-process record lock registry. Two indexes:

    * ``by_room`` — primary store. ``canonical_lock_key`` → :class:`HeldLock`.
    * ``by_sid`` — reverse index for fast auto-release on disconnect.

    Both kept in sync inside the two-line :meth:`_install` / :meth:`_remove`
    helpers. The class is callable from inside Socket.IO event handlers (which
    serialise on the single asyncio loop) — no lock needed."""

    def __init__(self) -> None:
        self._by_room: dict[str, HeldLock] = {}
        self._by_sid: dict[str, set[str]] = {}

    # ── inspection ──────────────────────────────────────────────────────────
    def get(self, room: str) -> HeldLock | None:
        return self._by_room.get(room)

    def snapshot(self) -> list[HeldLock]:
        """Every held lock, for the dashboard's "Locks" card + on subscribe."""
        return list(self._by_room.values())

    # ── mutators ────────────────────────────────────────────────────────────
    def _install(self, lock: HeldLock) -> None:
        self._by_room[lock.room] = lock
        self._by_sid.setdefault(lock.sid, set()).add(lock.room)

    def _remove(self, room: str) -> HeldLock | None:
        lock = self._by_room.pop(room, None)
        if lock is not None:
            rooms = self._by_sid.get(lock.sid)
            if rooms is not None:
                rooms.discard(room)
                if not rooms:
                    del self._by_sid[lock.sid]
        return lock

    def try_acquire(self, room: str, *, app: str, screen: str, key_values: dict[str, Any],
                    sid: str, user_id: str, username: str) -> tuple[bool, HeldLock]:
        """Try to lock *room* for *sid*. Returns ``(True, lock)`` on success (or
        idempotent re-acquire by the same sid), ``(False, current_holder)``
        when someone else holds it."""
        existing = self._by_room.get(room)
        if existing is not None:
            if existing.sid == sid:
                # Same session re-acquiring its own lock — no-op. Dialog re-mount
                # is the common path.
                return True, existing
            return False, existing
        lock = HeldLock(
            room=room, app=app, screen=screen, key_values=dict(key_values),
            sid=sid, user_id=user_id, username=username,
        )
        self._install(lock)
        return True, lock

    def release(self, room: str, *, sid: str) -> HeldLock | None:
        """Release *room* iff *sid* holds it. Returns the released lock, or
        ``None`` when the session didn't hold it (silently — clients may
        double-call on dialog close)."""
        existing = self._by_room.get(room)
        if existing is None or existing.sid != sid:
            return None
        return self._remove(room)

    def release_session(self, sid: str) -> list[HeldLock]:
        """Release every lock held by *sid*. Called on disconnect — no zombie
        locks if the tab dies. Returns the released locks so the caller can
        broadcast each release."""
        rooms = self._by_sid.pop(sid, None)
        if not rooms:
            return []
        released = []
        for room in list(rooms):
            lock = self._by_room.pop(room, None)
            if lock is not None:
                released.append(lock)
        return released


# ── log buffer (in-memory ring) ─────────────────────────────────────────────────────

@dataclass(slots=True, frozen=True)
class LogEntry:
    ts: float
    level: str
    name: str
    message: str
    exc_info: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "level": self.level, "name": self.name,
                "message": self.message, "exc_info": self.exc_info}


class LogRingBuffer:
    """Bounded `deque` of recent log lines + a registered async sink. The sink
    is the Socket.IO server's emit-to-room helper, set after construction."""

    def __init__(self, max_entries: int = 500) -> None:
        self._buf: Deque[LogEntry] = deque(maxlen=max_entries)
        self._sink: Any = None  # async callable: (LogEntry) -> None

    def snapshot(self) -> list[LogEntry]:
        return list(self._buf)

    def set_sink(self, sink: Any) -> None:
        self._sink = sink

    def append_sync(self, entry: LogEntry, *, loop: asyncio.AbstractEventLoop) -> None:
        """Synchronous append called from the logging handler (any thread). Pushes
        the entry into the deque immediately, then schedules the sink on the
        asyncio loop. Buffer is updated *before* the loop tick so even if the
        sink fails, the snapshot still has the entry for the next subscriber."""
        self._buf.append(entry)
        if self._sink is None:
            return
        try:
            loop.call_soon_threadsafe(asyncio.create_task, self._sink(entry))
        except RuntimeError:
            # loop is shutting down — drop silently.
            pass


class _LogHandler(logging.Handler):
    """Bridge `logging.LogRecord`s onto the buffer. Captures the asyncio loop
    at install time so handler emissions from non-loop threads (SQLAlchemy
    worker threads, etc.) post into the loop via `call_soon_threadsafe`."""

    def __init__(self, buffer: LogRingBuffer, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(level=logging.NOTSET)
        self._buf = buffer
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception as exc:  # noqa: BLE001
            msg = f"<log format error: {exc}> {record.msg!r}"
        exc_info = None
        if record.exc_info:
            exc_info = self.format(record)
            if exc_info.startswith(msg):
                exc_info = exc_info[len(msg):].lstrip("\n")
        entry = LogEntry(
            ts=record.created, level=record.levelname, name=record.name,
            message=msg, exc_info=exc_info,
        )
        self._buf.append_sync(entry, loop=self._loop)


# ── server factory ──────────────────────────────────────────────────────────────────

DASHBOARD_REFRESH_S = 5.0
LOCK_NS = "/"                # default namespace; everything lives here for simplicity
DASHBOARD_ROOM = "dashboard"
LOGS_ROOM = "logs"


class LibertySio:
    """Wires :class:`socketio.AsyncServer` + :class:`LockManager` +
    :class:`LogRingBuffer` together. Exposed singleton-like on
    ``app.state.sio`` — built in the FastAPI lifespan.

    All Socket.IO event handlers + the dashboard refresh loop live on this
    object so they can reach the lock manager, log buffer, and the FastAPI
    app state (for pool / connector / license info)."""

    def __init__(self, app: Any, token_service: TokenService) -> None:
        self.app = app                  # FastAPI app (for app.state.connectors etc.)
        self.token_service = token_service
        self.locks = LockManager()
        self.logs = LogRingBuffer()
        # sid → ConnectedSession. Maintained here (not in `sio.save_session`)
        # so the dashboard's sync payload builder can walk it without awaiting.
        # python-socketio's session API is async, which doesn't fit a sync
        # snapshot helper; this is a thin parallel index we update in connect /
        # disconnect.
        self._sessions: dict[str, ConnectedSession] = {}
        # `cors_allowed_origins="*"` is safe here because the Socket.IO handshake
        # already requires our JWT in the `auth` payload — an attacker who lacks
        # the token can't connect even if they reach the endpoint.
        self.sio = socketio.AsyncServer(
            async_mode="asgi",
            cors_allowed_origins="*",
            logger=False, engineio_logger=False,
        )
        self._dashboard_task: asyncio.Task[None] | None = None
        self._log_handler: _LogHandler | None = None
        self._register_handlers()

    # ── Socket.IO handlers ──────────────────────────────────────────────────
    def _register_handlers(self) -> None:
        sio = self.sio

        @sio.event
        async def connect(sid: str, environ: dict[str, Any], auth: dict[str, Any] | None) -> bool:
            """Handshake auth. The client sends ``io({ auth: { token, client_id? } })``;
            we decode the JWT, build a :class:`Principal`, store it on the session.
            Reject (False) on any failure — the client sees a clean ``connect_error``."""
            _ = environ
            token = (auth or {}).get("token") if isinstance(auth, dict) else None
            if not token:
                _log.info("sio.connect rejected: no token")
                raise socketio.exceptions.ConnectionRefusedError("missing token")
            try:
                claims = self.token_service.decode(token, expected_type="access")
            except TokenError as exc:
                _log.info("sio.connect rejected: %s", exc)
                raise socketio.exceptions.ConnectionRefusedError(f"invalid token: {exc}") from exc
            principal = Principal.from_claims(claims)
            client_id = (auth or {}).get("client_id") if isinstance(auth, dict) else None
            sess = ConnectedSession(principal=principal, client_id=client_id)
            self._sessions[sid] = sess
            await sio.save_session(sid, sess)
            _log.info("sio.connect sid=%s user=%s client=%s", sid, principal.username, client_id)
            return True

        @sio.event
        async def disconnect(sid: str) -> None:
            """Auto-release every lock + every room membership when the client
            disconnects. Each released lock emits a ``lock.released`` broadcast so
            other clients' dialogs see the change immediately."""
            sess = self._sessions.pop(sid, None)
            released = self.locks.release_session(sid)
            for lock in released:
                await sio.emit("lock.released", {"room": lock.room, **lock.to_dict()})
            _log.info("sio.disconnect sid=%s user=%s released=%d",
                      sid, sess.principal.username if sess else "?", len(released))
            # Always push — connected_users count changed even when no locks were held.
            await self._push_dashboard()

        @sio.on("lock")
        async def on_lock(sid: str, payload: dict[str, Any]) -> dict[str, Any]:
            """Acquire a lock. Returns an ack dict the client awaits via
            ``socket.emit('lock', payload, ack)``."""
            sess = self._sessions.get(sid)
            if sess is None:
                return {"ok": False, "reason": "no session"}
            if not isinstance(payload, dict):
                return {"ok": False, "reason": "bad request"}
            app_id = payload.get("app"); screen_id = payload.get("screen"); kv = payload.get("key_values") or {}
            if not (isinstance(app_id, str) and isinstance(screen_id, str) and isinstance(kv, dict)):
                return {"ok": False, "reason": "bad request"}
            room = canonical_lock_key(app_id, screen_id, kv)
            ok, lock = self.locks.try_acquire(
                room, app=app_id, screen=screen_id, key_values=kv,
                sid=sid, user_id=sess.principal.id, username=sess.principal.username,
            )
            if ok:
                # Join the room *and* broadcast to everyone (so every UI's lock
                # map updates). The originator gets the broadcast too — one code
                # path on the client for self-vs-other lock state.
                await sio.enter_room(sid, room)
                await sio.emit("lock.acquired", {"room": room, **lock.to_dict()})
                await self._push_dashboard()
                return {"ok": True, "lock": lock.to_dict()}
            return {"ok": False, "holder": lock.to_dict()}

        @sio.on("unlock")
        async def on_unlock(sid: str, payload: dict[str, Any]) -> dict[str, Any]:
            if not isinstance(payload, dict):
                return {"ok": False, "reason": "bad request"}
            app_id = payload.get("app"); screen_id = payload.get("screen"); kv = payload.get("key_values") or {}
            if not (isinstance(app_id, str) and isinstance(screen_id, str) and isinstance(kv, dict)):
                return {"ok": False, "reason": "bad request"}
            room = canonical_lock_key(app_id, screen_id, kv)
            released = self.locks.release(room, sid=sid)
            if released is None:
                return {"ok": False, "reason": "not held"}
            await sio.leave_room(sid, room)
            await sio.emit("lock.released", {"room": room, **released.to_dict()})
            await self._push_dashboard()
            return {"ok": True}

        @sio.on("dashboard.subscribe")
        async def on_dashboard_subscribe(sid: str, _payload: Any = None) -> dict[str, Any]:
            sess = self._sessions.get(sid)
            if sess is None or not sess.principal.is_superuser:
                return {"ok": False, "reason": "superuser only"}
            await sio.enter_room(sid, DASHBOARD_ROOM)
            snap = self._dashboard_payload()
            await sio.emit("dashboard:snapshot", snap, to=sid)
            self._ensure_dashboard_refresh()
            return {"ok": True}

        @sio.on("dashboard.unsubscribe")
        async def on_dashboard_unsubscribe(sid: str, _payload: Any = None) -> dict[str, Any]:
            await sio.leave_room(sid, DASHBOARD_ROOM)
            return {"ok": True}

        @sio.on("logs.subscribe")
        async def on_logs_subscribe(sid: str, _payload: Any = None) -> dict[str, Any]:
            sess = self._sessions.get(sid)
            if sess is None or not sess.principal.is_superuser:
                return {"ok": False, "reason": "superuser only"}
            await sio.enter_room(sid, LOGS_ROOM)
            await sio.emit("log:replay",
                           {"entries": [e.to_dict() for e in self.logs.snapshot()]},
                           to=sid)
            return {"ok": True}

        @sio.on("logs.unsubscribe")
        async def on_logs_unsubscribe(sid: str, _payload: Any = None) -> dict[str, Any]:
            await sio.leave_room(sid, LOGS_ROOM)
            return {"ok": True}

    # ── helpers ─────────────────────────────────────────────────────────────
    def _dashboard_payload(self) -> dict[str, Any]:
        """Build the dashboard snapshot — aggregates connected sessions, held
        locks, per-pool stats, AI / license / runtime info. Pure sync (no
        awaits) so it can be called from the lock handlers + the periodic
        refresh loop alike. One JSON-serialisable dict; the client treats
        every push as a full replacement."""
        state = self.app.state

        # Per-pool stats. ``materialised: false`` for pools whose engine hasn't
        # been opened yet (lazy creation); counters absent in that case.
        pools = []
        connectors = getattr(state, "connectors", None)
        if connectors is not None:
            for name in connectors.pools.names():
                try:
                    dialect = connectors.pools.dialect(name)
                except Exception:  # noqa: BLE001
                    dialect = "unknown"
                engine = connectors.pools._engines.get(name)  # noqa: SLF001
                if engine is None:
                    pools.append({"name": name, "dialect": dialect, "materialised": False})
                    continue
                pool = engine.sync_engine.pool if hasattr(engine, "sync_engine") else engine.pool
                def safe(fn: str) -> Any:
                    try:
                        return getattr(pool, fn)()
                    except Exception:  # noqa: BLE001
                        return None
                pools.append({
                    "name": name, "dialect": dialect, "materialised": True,
                    "size": safe("size"), "checked_out": safe("checkedout"),
                    "checked_in": safe("checkedin"), "overflow": safe("overflow"),
                    "max_overflow": getattr(pool, "_max_overflow", None),
                    "class": type(pool).__name__,
                })

        # Connected users — straight from our own sid → session map.
        users = [
            {
                "sid": sid,
                "user_id": sess.principal.id,
                "username": sess.principal.username,
                "is_superuser": sess.principal.is_superuser,
                "client_id": sess.client_id,
            }
            for sid, sess in sorted(self._sessions.items())
        ]

        # AI / license / runtime summaries.
        ai = getattr(state, "ai", None)
        ai_summary = (
            {"enabled": True, "model": ai.settings.model if ai is not None else None}
            if ai is not None else {"enabled": False}
        )
        license_info = getattr(state, "license", None)
        license_summary = license_info.public_dict() if license_info is not None else {"mode": "restricted"}
        screens = getattr(state, "screens", None)
        screen_count = sum(len(s) for s in (screens.screens.values() if screens else []))
        connector_count = len(connectors.names()) if connectors is not None else 0

        return {
            "ts": time.time(),
            "uptime_s": time.time() - _START_TIME,
            "runtime": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "connector_count": connector_count,
                "screen_count": screen_count,
            },
            "connected_users": users,
            "locks": [lock.to_dict() for lock in self.locks.snapshot()],
            "pools": pools,
            "ai": ai_summary,
            "license": license_summary,
        }

    async def _push_dashboard(self) -> None:
        """Re-emit a fresh dashboard snapshot to every subscriber. Called after
        a lock change so the dashboard's Locks card stays live without polling.
        Cheap — the snapshot is in-memory state, no DB I/O."""
        # ``sio.emit(..., room=...)`` fans out to every sid in the room. If the
        # room is empty (no subscribers), this is a no-op.
        await self.sio.emit("dashboard:snapshot", self._dashboard_payload(), room=DASHBOARD_ROOM)

    def _ensure_dashboard_refresh(self) -> None:
        """Start (once) the periodic re-broadcast loop for dashboard subscribers.
        Pool stats don't have change events, so we re-push every 5s to pick up
        drift in checked_out / overflow. Cancelled on shutdown via :meth:`stop`."""
        if self._dashboard_task is not None and not self._dashboard_task.done():
            return
        async def loop() -> None:
            try:
                while True:
                    await asyncio.sleep(DASHBOARD_REFRESH_S)
                    await self._push_dashboard()
            except asyncio.CancelledError:
                return
        self._dashboard_task = asyncio.create_task(loop())

    # ── log buffer wiring ───────────────────────────────────────────────────
    def attach_log_handler(self, *, loop: asyncio.AbstractEventLoop) -> None:
        """Install the in-memory log handler on the root logger + wire the
        buffer's async sink to ``sio.emit('log:line', …, room=LOGS_ROOM)``.
        Captures *loop* so cross-thread emissions can post into it via
        ``call_soon_threadsafe``."""
        async def sink(entry: LogEntry) -> None:
            await self.sio.emit("log:line", entry.to_dict(), room=LOGS_ROOM)
        self.logs.set_sink(sink)
        if self._log_handler is None:
            self._log_handler = _LogHandler(self.logs, loop)
            logging.getLogger().addHandler(self._log_handler)

    def detach_log_handler(self) -> None:
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
        self.logs.set_sink(None)

    async def stop(self) -> None:
        """Cancel the dashboard refresh task + detach the log handler. Called
        from the FastAPI lifespan's teardown."""
        if self._dashboard_task is not None and not self._dashboard_task.done():
            self._dashboard_task.cancel()
            try:
                await self._dashboard_task
            except asyncio.CancelledError:
                pass
        self.detach_log_handler()


# Process start time — surfaced in the dashboard's "Uptime" card.
_START_TIME = time.time()


# ── ASGI mount helper ──────────────────────────────────────────────────────────────

def make_asgi_app(sio: socketio.AsyncServer, fastapi_app: Any) -> Any:
    """Wrap a FastAPI app with the Socket.IO ASGI app — Socket.IO traffic
    (the ``/socket.io/`` path) goes to the Engine.IO server, everything else
    falls through to FastAPI. This is the supported python-socketio pattern
    for "FastAPI + Socket.IO at the same port"."""
    return socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path="socket.io")
