"""A new password at the first sign-in, and the password reset by e-mail.

* An account created (or reset by an administrator) with a temporary password carries
  ``must_change_password``; its session can call nothing but the change itself (enforced by the
  server, not only by the sign-in page) until a new password is chosen. Local accounts only.
* ``/auth/forgot-password`` mails a one-hour, single-use link when the username **and** the
  e-mail match a local account — and answers the same in every case.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from liberty.auth.authstore import DbAuthBackend, TomlAuthBackend
from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AppSettings, AuthSettings, ConnectorSettings, NotifySettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "password-flows-test-secret-key"


# --------------------------------------------------------------------------- #
# the flag in both user stores
# --------------------------------------------------------------------------- #


@pytest.fixture(params=["toml", "db"])
def store(request, tmp_path):
    if request.param == "toml":
        yield TomlAuthBackend(tmp_path / "auth.toml")
        return
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")})
    backend = DbAuthBackend(AuthDatabase(pools, "default"))
    yield backend
    asyncio.run(pools.dispose())


def test_the_flag_in_both_stores(store) -> None:
    async def go():
        await store.ready()
        u = await store.create_user("carol", password="temporary1", must_change_password=True)
        assert u.must_change_password and u.public_dict()["must_change_password"] is True
        sso = await store.create_user("sso", provider="oidc", provider_subject="x", must_change_password=True)
        assert not sso.must_change_password                         # no local password: never asked
        assert (await store.set_password("carol", "chosen-by-carol")).must_change_password is False
        assert (await store.set_password("carol", "temporary2", must_change=True)).must_change_password is True
        assert (await store.set_must_change("carol", False)).must_change_password is False
        assert (await store.set_must_change("sso", True)).must_change_password is False
        assert (await store.authenticate("carol", "temporary2")).username == "carol"
    asyncio.run(go())


# --------------------------------------------------------------------------- #
# the routes
# --------------------------------------------------------------------------- #


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw1", is_superuser=True, roles=["admin"])
            await svc.create_user("carol", password="temporary1", email="Carol@Example.com", roles=["admin"],
                                  must_change_password=True)
            await svc.create_user("dave", password="davepw12", email="dave@example.com")
            await svc.create_user("eve", password="evepw123", email="eve@example.com", is_active=False)
            await svc.create_user("sso", email="sso@example.com", provider="oidc", provider_subject="s-1")
        await pools.dispose()

    asyncio.run(go())


class _Mailbox:
    """Stands in for the notification dispatcher: keeps what would have been mailed."""

    def __init__(self) -> None:
        self.sent: list[tuple[list[str], str, str]] = []

    def _send_email(self, addresses, subject, body, link=None, attachments=None) -> None:
        self.sent.append((list(addresses), subject, body))


@pytest.fixture
def env(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')
    _seed(db_url)
    app = create_app(Settings(
        app=AppSettings(static_dir="", public_url="https://liberty.example.com/app"),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        notify=NotifySettings(config_path=tmp_path / "notifications.toml", smtp_host="smtp.example.com"),
    ))
    mailbox = _Mailbox()
    with TestClient(app) as client:
        app.state.notify = mailbox
        yield client, mailbox


def _login(client: TestClient, username: str, password: str) -> dict:
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _h(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_a_temporary_password_allows_nothing_but_the_change(env) -> None:
    client, _ = env
    t = _login(client, "carol", "temporary1")
    assert client.get("/auth/me", headers=_h(t)).status_code == 200
    blocked = client.get("/api/connectors", headers=_h(t))
    assert blocked.status_code == 403 and blocked.json()["detail"] == "password change required"
    assert blocked.headers["x-liberty-password-change"] == "required"
    # the refresh keeps the flag: it re-reads the account
    again = client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]}).json()
    assert client.get("/api/connectors", headers=_h(again)).status_code == 403

    r = client.post("/auth/change-password", json={"current_password": "temporary1", "new_password": "carol-chose"},
                    headers=_h(again))
    assert r.status_code == 200 and r.json()["ok"] is True
    fresh = r.json()                                  # a new pair, without the flag: no new sign-in needed
    assert client.get("/api/connectors", headers=_h(fresh)).status_code == 200
    assert client.get("/api/connectors", headers=_h(_login(client, "carol", "carol-chose"))).status_code == 200


def test_an_administrator_creates_or_resets_with_a_temporary_password(env) -> None:
    client, _ = env
    admin = _h(_login(client, "admin", "adminpw1"))
    r = client.post("/admin/access/users", json={"username": "frank", "password": "temporary9", "must_change_password": True},
                    headers=admin)
    assert r.status_code == 201 and r.json()["must_change_password"] is True
    assert client.get("/api/connectors", headers=_h(_login(client, "frank", "temporary9"))).status_code == 403
    # a reset by the administrator, temporary again
    assert client.post("/admin/access/users/dave/password", json={"password": "temporary8", "must_change": True},
                       headers=admin).status_code == 200
    assert client.get("/api/connectors", headers=_h(_login(client, "dave", "temporary8"))).status_code == 403
    # and the flag can be lifted without a new password
    r = client.patch("/admin/access/users/dave", json={"must_change_password": False}, headers=admin)
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    assert client.get("/api/connectors", headers=_h(_login(client, "dave", "temporary8"))).status_code == 200


def _link_token(body: str) -> str:
    url = re.search(r"https://\S+", body).group(0)
    assert url.startswith("https://liberty.example.com/app/reset-password?")
    return parse_qs(urlparse(url).query)["token"][0]


def test_forgot_password_mails_a_single_use_link(env) -> None:
    client, mailbox = env
    assert client.get("/info").json()["auth"]["password_reset"] is True
    # username and e-mail must both match (the e-mail case-insensitively)
    r = client.post("/auth/forgot-password", json={"username": "carol", "email": "carol@example.COM"},
                    headers={"X-Liberty-Lang": "fr"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    (to, subject, body), = mailbox.sent
    assert to == ["Carol@Example.com"] and subject == "Réinitialisation du mot de passe" and "carol" in body
    token = _link_token(body)

    r = client.post("/auth/reset-password", json={"token": token, "new_password": "reset-by-mail"})
    assert r.status_code == 200
    t = _login(client, "carol", "reset-by-mail")
    assert client.get("/api/connectors", headers=_h(t)).status_code == 200   # the reset cleared "change at sign-in"
    # once: the password it was issued for is gone
    again = client.post("/auth/reset-password", json={"token": token, "new_password": "another-one"})
    assert again.status_code == 400 and "already used" in again.json()["detail"]


@pytest.mark.parametrize("username,email", [
    ("dave", "someone@else.com"),     # the e-mail doesn't match
    ("nobody", "dave@example.com"),   # no such account
    ("eve", "eve@example.com"),       # inactive
    ("sso", "sso@example.com"),       # an SSO account: no local password
])
def test_forgot_password_says_the_same_and_sends_nothing(env, username, email) -> None:
    client, mailbox = env
    r = client.post("/auth/forgot-password", json={"username": username, "email": email})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert mailbox.sent == []


def test_a_link_stops_working_once_the_password_changes(env) -> None:
    client, mailbox = env
    client.post("/auth/forgot-password", json={"username": "dave", "email": "dave@example.com"})
    token = _link_token(mailbox.sent[0][2])
    t = _login(client, "dave", "davepw12")
    assert client.post("/auth/change-password", json={"current_password": "davepw12", "new_password": "dave-new-1"},
                       headers=_h(t)).status_code == 200
    assert client.post("/auth/reset-password", json={"token": token, "new_password": "hijack-it"}).status_code == 400
    assert client.post("/auth/reset-password", json={"token": "garbage", "new_password": "hijack-it"}).status_code == 400
    # an access token is not a reset link
    assert client.post("/auth/reset-password", json={"token": t["access_token"], "new_password": "hijack-it"}).status_code == 400
