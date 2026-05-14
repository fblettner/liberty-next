"""``/api/screens`` route tests — permission-filtered listing, single-screen drill-in,
hot-reload, and the ``/info`` summary. Mirrors the layout of ``test_web_menus.py`` so the
two stay easy to keep in sync."""
from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import (
    AISettings,
    AppSettings,
    AuthSettings,
    ConnectorSettings,
    ScreenSettings,
    Settings,
)
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-screens-test-secret"


def _connectors_toml(db_url: str) -> str:
    return textwrap.dedent(
        f"""
        [pools.default]
        url = "{db_url}"

        [connectors.app1]
        type = "sql"
        pool = "default"
        [[connectors.app1.queries]]
        name = "users_get"
        sql = "SELECT 1 AS id"
        [[connectors.app1.queries]]
        name = "secret_get"
        sql = "SELECT 1 AS id"
        # A cross-pool screen target — same DB pool, different connector name. The screen sets
        # `connector = "external"` so its permission gates on `sql:external:...` not `sql:app1:...`.
        [connectors.external]
        type = "sql"
        pool = "default"
        [[connectors.external.queries]]
        name = "things_get"
        sql = "SELECT 1 AS id"
        """
    )


def _screens_toml() -> str:
    return textwrap.dedent(
        """
        [screens.app1.users]
        label = "Users"
        description = "User accounts"
        read_query = "users_get"
        update_query = "users_get"
        audit = true

        [screens.app1.users.dialog]
        title = "User"

        [[screens.app1.users.dialog.tabs]]
        id = "general"
        label = "General"

        [[screens.app1.users.dialog.tabs.fields]]
        name = "USR_ID"

        # A screen whose read query lives on a different connector (cross-pool).
        [screens.app1.things]
        label = "Things"
        connector = "external"
        read_query = "things_get"

        # A screen the "user" role cannot read — its permission is locked behind sql:app1:secret_get
        [screens.app1.secret]
        label = "Secret"
        read_query = "secret_get"
        """
    )


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            # "user" can read users_get on app1 and things_get on external — *not* secret_get
            await svc.get_or_create_role(
                "user", permissions=["sql:app1:users_get", "sql:external:things_get"],
            )
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("user", password="userpw", roles=["user"])
            await svc.create_user("nobody", password="nobodypw")
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_connectors_toml(db_url))
    (tmp_path / "screens.toml").write_text(_screens_toml())
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        screens=ScreenSettings(config_path=Path(tmp_path / "screens.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def _h(client: TestClient, username: str) -> dict[str, str]:
    tok = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_list_screens_admin_sees_everything(app) -> None:
    with TestClient(app) as client:
        r = client.get("/api/screens", headers=_h(client, "admin"))
        assert r.status_code == 200
        app1 = r.json()["screens"]["app1"]
        ids = [s["id"] for s in app1]
        assert ids == ["users", "things", "secret"]
        # Effective connector — explicit `connector` wins; same-app screens inherit the app name.
        connectors = {s["id"]: s["connector"] for s in app1}
        assert connectors == {"users": "app1", "things": "external", "secret": "app1"}
        users = next(s for s in app1 if s["id"] == "users")
        assert users["label"] == "Users" and users["description"] == "User accounts"
        assert users["read_query"] == "users_get" and users["update_query"] == "users_get"
        assert users["audit"] is True and users["has_dialog"] is True
        # Slice 6 follow-up — the catalog also flags row_menu / actions presence so the
        # TableView fetches the full body even on screens without a dialog.
        assert users["has_row_menu"] is False and users["has_actions"] is False


def test_list_screens_pruned_by_permission(app) -> None:
    with TestClient(app) as client:
        # "user" can read users_get + things_get; the secret screen drops out
        app1 = client.get("/api/screens", headers=_h(client, "user")).json()["screens"]["app1"]
        assert [s["id"] for s in app1] == ["users", "things"]
        # "nobody" sees no screens at all → the app disappears from the dict
        assert client.get("/api/screens", headers=_h(client, "nobody")).json()["screens"] == {}


def test_get_one_app_screens_and_404(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.get("/api/screens/app1", headers=h)
        assert r.status_code == 200 and r.json()["app"] == "app1"
        # Unknown app → 404
        assert client.get("/api/screens/ghost", headers=h).status_code == 404
        # An app whose screens the caller can't read → 404 (parity with /api/menus)
        assert client.get("/api/screens/app1", headers=_h(client, "nobody")).status_code == 404


def test_get_one_screen_returns_full_body_and_hides_unreadable(app) -> None:
    with TestClient(app) as client:
        body = client.get("/api/screens/app1/users", headers=_h(client, "admin")).json()
        assert body["id"] == "users" and body["has_dialog"] is True
        # The full descriptor includes the dialog body so the frontend can render the form.
        assert body["dialog"]["title"] == "User"
        assert body["dialog"]["tabs"][0]["fields"][0]["name"] == "USR_ID"
        # "user" can read users → 200; cannot read secret → 404 (not 403, so we don't leak its existence)
        assert client.get("/api/screens/app1/users", headers=_h(client, "user")).status_code == 200
        assert client.get("/api/screens/app1/secret", headers=_h(client, "user")).status_code == 404
        # Unknown screen → 404 too
        assert client.get("/api/screens/app1/ghost", headers=_h(client, "admin")).status_code == 404


def test_screens_route_requires_auth(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/screens").status_code == 401
        assert client.get("/api/screens/app1").status_code == 401
        assert client.get("/api/screens/app1/users").status_code == 401


def test_info_reports_screen_apps(app) -> None:
    with TestClient(app) as client:
        info = client.get("/info").json()
        assert info["screens"] == {"apps": ["app1"], "total": 3}


def test_reload_rereads_screens(app, tmp_path) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Wipe the screens file → reload should drop everything
        (tmp_path / "screens.toml").write_text("")
        r = client.post("/admin/reload", headers=h)
        assert r.status_code == 200 and r.json()["screen_apps"] == []
        assert client.get("/api/screens", headers=h).json()["screens"] == {}
