"""liberty.notify — the inbox store, the template renderer and the dispatcher's three channels
(with a fake auth backend, a fake SMTP and a fake Socket.IO layer)."""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.config import Settings
from liberty.connectors.db import PoolRegistry
from liberty.notify import ActionTarget, Dispatcher, Message, NotificationStore, render


@pytest.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'notify.db'}")
    pools = PoolRegistry()
    pools.register_engine("default", engine)
    st = NotificationStore(pools, "default")
    await st.create_schema()
    await st.create_schema()   # idempotent
    return st


@pytest.mark.asyncio
async def test_store_roundtrip(store: NotificationStore) -> None:
    rows = await store.add(["ann", "bob", "ann"], subject="Period closed", body="2026-03 closed", link="/sql/x/y", rule="period_closed", payload={"PERIOD": "2026-03"})
    assert [r["username"] for r in rows] == ["ann", "bob"]          # duplicates collapse
    assert await store.unread_count("ann") == 1 and await store.unread_count("cid") == 0
    mine = await store.list_for("ann")
    assert mine[0]["subject"] == "Period closed" and mine[0]["payload"] == {"PERIOD": "2026-03"} and mine[0]["read"] is False
    # only the owner's rows change; unknown ids are ignored
    assert await store.mark_read("bob", [mine[0]["id"]]) == 0
    assert await store.mark_read("ann", [mine[0]["id"], "nope"]) == 1
    assert await store.unread_count("ann") == 0 and (await store.list_for("ann"))[0]["read"] is True
    assert await store.list_for("ann", unread_only=True) == []
    await store.add(["ann"], subject="two"); await store.add(["ann"], subject="three")
    assert await store.mark_read("ann") == 2                          # every unread one
    assert await store.delete_older_than(0) >= 0                      # nothing older than now; no crash


def test_render_placeholders() -> None:
    p = {"PERIOD": "2026-03", "company": "00001", "n": None}
    assert render("Period {PERIOD} of {company} closed", p) == "Period 2026-03 of 00001 closed"
    assert render("{period} / {COMPANY} / {n}", p) == "2026-03 / 00001 / "       # any case, None → empty
    assert render("Hello {unknown}", p) == "Hello {unknown}"                     # left as written
    assert render("", p) == "" and render(None, p) == ""
    assert render("100% {bad", p) == "100% {bad"                                  # a malformed template is returned as is


@dataclass
class _User:
    username: str
    email: str | None = None
    roles: list[str] = field(default_factory=list)
    is_active: bool = True


class _Backend:
    def __init__(self, users: list[_User]) -> None:
        self.users = users

    async def list_users(self):
        return list(self.users)

    async def get_by_username(self, name: str):
        return next((u for u in self.users if u.username == name), None)


class _Sio:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, dict]] = []

    async def notify_user(self, username: str, payload: dict) -> None:
        self.pushed.append((username, payload))


class _Conn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, query: str, params: dict):
        self.calls.append((query, params))

    async def call(self, endpoint: str, params: dict):
        self.calls.append(("ep:" + endpoint, params))


class _Registry:
    def __init__(self) -> None:
        self.conn = _Conn()

    def get(self, name: str):
        if name != "hooks":
            raise KeyError(name)
        return self.conn


class _FakeSMTP:
    sent: list[tuple[str, list[str], str]] = []
    logins: list[tuple[str, str]] = []
    started_tls = 0

    def __init__(self, host, port, timeout=None):
        assert host == "smtp.test" and port == 2525

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        type(self).started_tls += 1

    def login(self, user, password):
        type(self).logins.append((user, password))

    def send_message(self, msg):
        type(self).sent.append((msg["Subject"], [a.strip() for a in msg["To"].split(",")], msg.get_content()))


@pytest.mark.asyncio
async def test_dispatch_all_channels_and_recipients(store: NotificationStore, monkeypatch) -> None:
    backend = _Backend([
        _User("ann", "ann@corp.test", ["filiale"]), _User("bob", "bob@corp.test", ["filiale", "group"]),
        _User("gone", "gone@corp.test", ["filiale"], is_active=False), _User("noemail", None, ["group"]),
    ])
    sio = _Sio(); registry = _Registry()
    settings = Settings()
    settings.notify.smtp_host = "smtp.test"; settings.notify.smtp_port = 2525
    settings.notify.smtp_user = "mailer"; settings.notify.smtp_password = "pw"; settings.notify.smtp_from = "liberty@corp.test"
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent.clear(); _FakeSMTP.logins.clear()
    d = Dispatcher(store, settings, auth_backend=lambda: backend, connectors=lambda: registry, sio=lambda: sio)

    msg = Message(subject="Period {PERIOD} closed", body="Closed by {user}", link="/sql/sales/periods?PERIOD={PERIOD}", rule="period_closed", payload={"PERIOD": "2026-03", "user": "ann"})
    report = await d.dispatch(
        msg, channels=["portal", "email", "action"],
        recipients=["role:filiale", "user:bob", "email:ext@partner.test", "user:ghost", "weird:x"],
        action=ActionTarget(connector="hooks", query="notify_webhook_post", params={"TEXT": "{PERIOD} is closed"}),
    )
    # portal: the role's active users + the user (bob once) + ghost (a portal row needs no local row) — rendered, pushed
    assert report.portal_users == ["ann", "bob", "ghost"]
    assert [u for u, _ in sio.pushed] == ["ann", "bob", "ghost"]
    ann = (await store.list_for("ann"))[0]
    assert ann["subject"] == "Period 2026-03 closed" and ann["body"] == "Closed by ann" and ann["link"] == "/sql/sales/periods?PERIOD=2026-03" and ann["rule"] == "period_closed"
    # email: every recipient with an address, once
    assert report.emails == ["ann@corp.test", "bob@corp.test", "ext@partner.test"]
    assert _FakeSMTP.sent[-1][0] == "Period 2026-03 closed" and _FakeSMTP.sent[-1][1] == report.emails and _FakeSMTP.logins[-1] == ("mailer", "pw")
    assert _FakeSMTP.started_tls >= 1
    # action: the payload + the rendered params + SUBJECT / BODY / RULE
    assert report.action_ran is True
    q, params = registry.conn.calls[-1]
    assert q == "notify_webhook_post" and params["TEXT"] == "2026-03 is closed" and params["SUBJECT"] == "Period 2026-03 closed" and params["RULE"] == "period_closed" and params["PERIOD"] == "2026-03"
    assert report.errors == {}


@pytest.mark.asyncio
async def test_dispatch_isolates_channel_failures(store: NotificationStore) -> None:
    backend = _Backend([_User("ann", "ann@corp.test")])
    settings = Settings()                                   # no smtp_host
    registry = _Registry()
    d = Dispatcher(store, settings, auth_backend=lambda: backend, connectors=lambda: registry)
    report = await d.dispatch(Message(subject="s"), channels=["portal", "email", "action"], recipients=["user:ann"], action=ActionTarget(connector="nope", query="q"))
    assert report.portal_users == ["ann"]                   # the portal row landed
    assert "smtp_host" in report.errors["email"] and "nope" in report.errors["action"]
    # no store at all: the portal channel reports, the others still run
    d2 = Dispatcher(None, settings, auth_backend=lambda: backend, connectors=lambda: registry)
    report = await d2.dispatch(Message(subject="s"), channels=["portal"], recipients=["user:ann"])
    assert "unavailable" in report.errors["portal"]
    # an unknown channel name is ignored; no channel at all defaults to portal
    report = await d.dispatch(Message(subject="s"), channels=["pigeon"], recipients=["user:ann"])
    assert report.portal_users == ["ann"]
    # an email-only recipient gets no portal row
    report = await d.dispatch(Message(subject="s"), channels=["portal"], recipients=["email:x@y.test"])
    assert report.portal_users == []


@pytest.mark.asyncio
async def test_resolve_recipient_without_prefix(store: NotificationStore) -> None:
    """An address with no prefix is an address (as a job's alerts.recipients read it); another bare
    word is ignored with a warning, not silently."""
    from liberty.notify.dispatcher import Dispatcher
    d = Dispatcher(store, None, auth_backend=lambda: None, connectors=lambda: None)
    out = await d.resolve(["ops@corp.test", "email:cfo@corp.test", "bogus", "user:"])
    assert [(r.username, r.email) for r in out] == [(None, "ops@corp.test"), (None, "cfo@corp.test")]
