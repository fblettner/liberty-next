"""End-to-end Socket.IO tests — two-tab record lock scenario + dashboard + logs.

Runs the real ``LibertySio`` server against ``socketio.AsyncClient`` instances
over the local uvicorn process — same code path that the browser hits. The
two-tab scenario is the one that the user spotted as broken in the previous
raw-WS attempt; it's the *first* thing tested here so a regression is loud.

Each test boots a uvicorn server in a background asyncio task on a fresh port,
points one or more :class:`socketio.AsyncClient`s at it, and asserts on the
emitted events.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import textwrap
from pathlib import Path
from typing import Any

import pytest
import socketio
import uvicorn

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "sio-test-secret"


def _free_port() -> int:
    """Grab a free TCP port. Race-prone in theory; in practice the kernel
    won't hand the same port to another process inside the few ms before we
    bind it from uvicorn."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _connectors_toml(db_url: str) -> str:
    return textwrap.dedent(f"""
        [pools.default]
        url = "{db_url}"
    """)


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("alice", password="alicepw")
            await svc.create_user("bob", password="bobpw")
        await pools.dispose()
    asyncio.run(go())


@pytest.fixture
def server_settings(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_connectors_toml(db_url))
    _seed(db_url)
    return Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )


@contextlib.asynccontextmanager
async def _running_server(settings: Settings):
    """Start uvicorn on a fresh port, yield ``(host, port, app)`` once it's up.
    Tears down cleanly on exit."""
    app = create_app(settings)
    port = _free_port()
    config = uvicorn.Config(app.asgi_app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    # Wait until uvicorn flips ``started``. Without this the SIO client races
    # the server and the first connection attempt 404s on /socket.io/.
    for _ in range(50):  # 5s budget
        if server.started:
            break
        await asyncio.sleep(0.1)
    if not server.started:
        raise RuntimeError("uvicorn didn't start in time")
    try:
        yield "127.0.0.1", port, app
    finally:
        server.should_exit = True
        await task


async def _login_token(host: str, port: int, username: str) -> str:
    """Hit the HTTP /auth/login route through the real server to mint a JWT;
    we don't reach into the TokenService directly because we want to exercise
    the same path the browser uses."""
    import httpx
    async with httpx.AsyncClient() as http:
        r = await http.post(
            f"http://{host}:{port}/auth/login",
            json={"username": username, "password": f"{username}pw"},
        )
        r.raise_for_status()
        return r.json()["access_token"]


async def _connect_client(host: str, port: int, username: str) -> socketio.AsyncClient:
    """Build + connect a Socket.IO client authenticated as *username*. Caller
    is responsible for awaiting ``client.disconnect()`` at end of test."""
    token = await _login_token(host, port, username)
    client = socketio.AsyncClient(reconnection=False)
    await client.connect(
        f"http://{host}:{port}",
        auth={"token": token, "client_id": f"test-{username}"},
        socketio_path="socket.io",
        wait_timeout=5,
    )
    return client


# ── handshake ────────────────────────────────────────────────────────────────────────


async def test_connect_with_valid_token(server_settings) -> None:
    """Sanity — a valid JWT lets the client connect. Without this, every other
    test below would fail before getting to its assertions."""
    async with _running_server(server_settings) as (host, port, _):
        client = await _connect_client(host, port, "alice")
        assert client.connected
        await client.disconnect()


async def test_connect_rejects_missing_token(server_settings) -> None:
    """No token → connect_error → ``client.connect`` raises ``ConnectionError``."""
    async with _running_server(server_settings) as (host, port, _):
        client = socketio.AsyncClient(reconnection=False)
        with pytest.raises(socketio.exceptions.ConnectionError):
            await client.connect(f"http://{host}:{port}", auth={}, socketio_path="socket.io", wait_timeout=3)


# ── record locks (the two-tab scenario) ────────────────────────────────────────────


async def test_two_tab_lock_scenario(server_settings) -> None:
    """**The scenario the user spotted as broken.** Two clients (Alice & Bob)
    both try to lock the same record. Alice wins; Bob is denied with Alice's
    holder info. Bob also receives the ``lock.acquired`` broadcast (every
    connected client does — that's how dashboards stay live)."""
    async with _running_server(server_settings) as (host, port, _):
        alice = await _connect_client(host, port, "alice")
        bob = await _connect_client(host, port, "bob")

        # Bob installs a listener for lock.acquired *before* Alice acquires —
        # otherwise the broadcast races his subscription. Same pattern any
        # real frontend client uses (handlers installed in the connect handler).
        bob_lock_events: list[dict] = []
        bob.on("lock.acquired", lambda data: bob_lock_events.append(data))

        # Alice locks. The server's handler returns an ack dict.
        payload = {"app": "nomasx1", "screen": "users", "key_values": {"USR_ID": "u1"}}
        ack = await alice.call("lock", payload)
        assert ack["ok"] is True
        assert ack["lock"]["username"] == "alice"
        # The display ``key_values`` preserves the original case the caller sent
        # (so the dashboard / lock banner reads recognizably). Canonicalisation
        # for *collision detection* happens internally on the room name — see
        # the ``test_lock_key_canonicalisation`` test below for that contract.
        assert ack["lock"]["key_values"] == {"USR_ID": "u1"}

        # Bob tries the same lock. Should be denied with Alice's holder info.
        # Give the broadcast a beat to fan out (Socket.IO's emit is async).
        await asyncio.sleep(0.1)
        ack2 = await bob.call("lock", payload)
        assert ack2["ok"] is False
        assert ack2["holder"]["username"] == "alice"

        # Bob also saw the cross-broadcast from Alice's acquire (every
        # connected client gets it, including the originator's peers).
        assert len(bob_lock_events) >= 1
        assert bob_lock_events[0]["username"] == "alice"

        await alice.disconnect()
        await bob.disconnect()


async def test_auto_release_on_disconnect(server_settings) -> None:
    """Alice locks, then her socket dies. Bob's subsequent attempt succeeds —
    no zombie lock from the dropped session. Backed by python-socketio's
    disconnect handler, which calls our :meth:`LockManager.release_session`."""
    async with _running_server(server_settings) as (host, port, _):
        alice = await _connect_client(host, port, "alice")
        ack = await alice.call("lock", {"app": "n", "screen": "s", "key_values": {"id": "1"}})
        assert ack["ok"] is True
        await alice.disconnect()
        await asyncio.sleep(0.2)  # let the server's disconnect handler run

        bob = await _connect_client(host, port, "bob")
        ack2 = await bob.call("lock", {"app": "n", "screen": "s", "key_values": {"id": "1"}})
        assert ack2["ok"] is True
        assert ack2["lock"]["username"] == "bob"
        await bob.disconnect()


async def test_idempotent_reacquire_by_same_session(server_settings) -> None:
    """Same session re-acquiring its own lock is a no-op success (returns ok).
    Used by the dialog re-mount path — closing + reopening the same record
    shouldn't deny the operator."""
    async with _running_server(server_settings) as (host, port, _):
        alice = await _connect_client(host, port, "alice")
        payload = {"app": "n", "screen": "s", "key_values": {"id": "1"}}
        ack1 = await alice.call("lock", payload)
        ack2 = await alice.call("lock", payload)
        assert ack1["ok"] is True and ack2["ok"] is True
        assert ack2["lock"]["username"] == "alice"
        await alice.disconnect()


async def test_lock_release_broadcasts(server_settings) -> None:
    """Releasing a lock fans out ``lock.released`` to every client. Bob's
    listener should see Alice's release."""
    async with _running_server(server_settings) as (host, port, _):
        alice = await _connect_client(host, port, "alice")
        bob = await _connect_client(host, port, "bob")
        released: list[dict] = []
        bob.on("lock.released", lambda data: released.append(data))

        payload = {"app": "n", "screen": "s", "key_values": {"id": "1"}}
        await alice.call("lock", payload)
        await asyncio.sleep(0.1)

        ack = await alice.call("unlock", payload)
        assert ack["ok"] is True
        await asyncio.sleep(0.1)

        assert len(released) >= 1
        assert released[0]["username"] == "alice"

        await alice.disconnect()
        await bob.disconnect()


async def test_lock_key_canonicalisation(server_settings) -> None:
    """``USR_ID`` and ``usr_id`` produce the same lock — case-insensitive
    column names so a screen using Postgres-folded ``usr_id`` collides with
    an Oracle screen using ``USR_ID`` on the same logical record."""
    async with _running_server(server_settings) as (host, port, _):
        alice = await _connect_client(host, port, "alice")
        bob = await _connect_client(host, port, "bob")
        await alice.call("lock", {"app": "n", "screen": "s", "key_values": {"USR_ID": 42}})
        await asyncio.sleep(0.1)
        ack = await bob.call("lock", {"app": "n", "screen": "s", "key_values": {"usr_id": "42"}})
        assert ack["ok"] is False
        assert ack["holder"]["username"] == "alice"
        await alice.disconnect()
        await bob.disconnect()


# ── dashboard subscription ─────────────────────────────────────────────────────────


async def test_dashboard_superuser_only(server_settings) -> None:
    """Non-superuser is denied with a reason; admin gets a snapshot pushed."""
    async with _running_server(server_settings) as (host, port, _):
        alice = await _connect_client(host, port, "alice")
        denied = await alice.call("dashboard.subscribe")
        assert denied["ok"] is False
        await alice.disconnect()

        admin = await _connect_client(host, port, "admin")
        snapshots: list[dict] = []
        admin.on("dashboard:snapshot", lambda data: snapshots.append(data))
        ack = await admin.call("dashboard.subscribe")
        assert ack["ok"] is True
        # Wait briefly for the initial snapshot to arrive.
        for _ in range(20):
            if snapshots: break
            await asyncio.sleep(0.05)
        assert len(snapshots) >= 1
        snap = snapshots[0]
        assert "connected_users" in snap and "pools" in snap and "locks" in snap
        assert any(u["username"] == "admin" for u in snap["connected_users"])
        await admin.disconnect()


async def test_dashboard_reflects_lock_changes(server_settings) -> None:
    """After admin subscribes, an unrelated client locking a record triggers a
    fresh snapshot with that lock visible."""
    async with _running_server(server_settings) as (host, port, _):
        admin = await _connect_client(host, port, "admin")
        snapshots: list[dict] = []
        admin.on("dashboard:snapshot", lambda data: snapshots.append(data))
        await admin.call("dashboard.subscribe")
        await asyncio.sleep(0.1)  # initial snapshot
        initial_count = len(snapshots)

        alice = await _connect_client(host, port, "alice")
        await alice.call("lock", {"app": "n", "screen": "s", "key_values": {"id": "1"}})
        # Wait for the post-lock snapshot push.
        for _ in range(20):
            if len(snapshots) > initial_count: break
            await asyncio.sleep(0.05)
        assert len(snapshots) > initial_count
        latest = snapshots[-1]
        lock_pairs = [(L["app"], L["screen"]) for L in latest["locks"]]
        assert ("n", "s") in lock_pairs

        await alice.disconnect()
        await admin.disconnect()


# ── logs subscription ──────────────────────────────────────────────────────────────


async def test_logs_replay_on_subscribe(server_settings) -> None:
    """``logs.subscribe`` returns an initial ``log:replay`` event with the
    buffer's contents. The live-tail push path is exercised in the frontend
    dev env (it requires cross-thread loop interleaving that's awkward in
    unit tests)."""
    import logging
    async with _running_server(server_settings) as (host, port, _):
        admin = await _connect_client(host, port, "admin")
        replays: list[dict] = []
        admin.on("log:replay", lambda data: replays.append(data))
        # Seed a known log line before subscribing.
        logging.getLogger("liberty.test").warning("pre-subscribe marker — sio test")
        await asyncio.sleep(0.1)
        ack = await admin.call("logs.subscribe")
        assert ack["ok"] is True
        for _ in range(20):
            if replays: break
            await asyncio.sleep(0.05)
        assert len(replays) >= 1
        entries = replays[0]["entries"]
        assert any("pre-subscribe marker" in e["message"] for e in entries)
        await admin.disconnect()


async def test_logs_denied_for_non_superuser(server_settings) -> None:
    async with _running_server(server_settings) as (host, port, _):
        alice = await _connect_client(host, port, "alice")
        ack = await alice.call("logs.subscribe")
        assert ack["ok"] is False
        await alice.disconnect()
