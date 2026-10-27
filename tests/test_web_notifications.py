"""``/api/notifications`` (own inbox) and ``POST /admin/notify/test`` (superuser)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.main import create_app
from tests.test_web_access import JWT_SECRET, _h, _seed


def _app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    (tmp_path / "connectors.toml").write_text(f'[pools.default]\nurl = "{db_url}"\n')
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def test_inbox_routes_and_test_send(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/notifications").status_code == 401
        h = _h(client, "admin")
        assert client.get("/api/notifications", headers=h).json() == {"notifications": []}
        assert client.get("/api/notifications/unread_count", headers=h).json() == {"unread": 0}
        # a superuser sends a test message to themselves (the default recipient) and to a role
        r = client.post("/admin/notify/test", json={"subject": "Hello {who}", "body": "b", "payload": {"who": "admin"}}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["portal_users"] == ["admin"] and r.json()["errors"] == {}
        r = client.post("/admin/notify/test", json={"channels": ["portal", "email"], "recipients": ["role:admin", "email:ext@partner.test"], "subject": "x"}, headers=h)
        assert r.json()["portal_users"] == ["admin"] and "smtp_host" in r.json()["errors"]["email"]   # no SMTP configured
        rows = client.get("/api/notifications", headers=h).json()["notifications"]
        assert [n["subject"] for n in rows] == ["x", "Hello admin"] and all(n["read"] is False for n in rows)
        assert client.get("/api/notifications/unread_count", headers=h).json() == {"unread": 2}
        assert client.get("/api/notifications?unread=1&limit=1", headers=h).json()["notifications"][0]["subject"] == "x"
        # mark one, then all
        assert client.post("/api/notifications/read", json={"ids": [rows[0]["id"]]}, headers=h).json() == {"marked": 1}
        assert client.post("/api/notifications/read", json={}, headers=h).json() == {"marked": 1}
        assert client.get("/api/notifications/unread_count", headers=h).json() == {"unread": 0}
        # without a token: refused; a malformed action: 422
        assert client.post("/admin/notify/test", json={"subject": "x"}).status_code == 401
        r = client.post("/admin/notify/test", json={"subject": "x", "action": {"query": "q"}}, headers=h)
        assert r.status_code == 422
