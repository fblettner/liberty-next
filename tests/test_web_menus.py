from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, MenuSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-menus-test-secret"


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
        name = "roles_get"
        sql = "SELECT 1 AS id"
        [[connectors.app1.queries]]
        name = "secret_get"
        sql = "SELECT 1 AS id"
        """
    )


def _menus_toml() -> str:
    return textwrap.dedent(
        """
        [menus.app1]
        label = "App One"

        [[menus.app1.items]]
        id = "security"
        label = "Security"
        l.fr = "Sécurité"

        [[menus.app1.items]]
        id = "security.users"
        parent = "security"
        label = "Users"
        l.fr = "Utilisateurs"
        type = "query"
        target = "users_get"

        [[menus.app1.items]]
        id = "security.roles"
        parent = "security"
        label = "Roles"
        type = "query"
        target = "roles_get"

        [[menus.app1.items]]
        id = "import"
        label = "Import"
        type = "job"
        target = "load-items"

        [[menus.app1.items]]
        id = "security.secret"
        parent = "security"
        label = "Secret"
        type = "query"
        target = "secret_get"

        [[menus.app1.items]]
        id = "empty_folder"
        label = "Nothing here"
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
            # "user" can see users_get + roles_get on app1 but not secret_get
            await svc.get_or_create_role("user", permissions=["sql:app1:users_get", "sql:app1:roles_get"])
            await svc.get_or_create_role("loader", permissions=["job:load-items"])
            await svc.get_or_create_role("denied", permissions=["*", "!job:load-items"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("user", password="userpw", roles=["user"])
            await svc.create_user("loader", password="loaderpw", roles=["loader"])
            await svc.create_user("denied", password="deniedpw", roles=["denied"])
            await svc.create_user("nobody", password="nobodypw")
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_connectors_toml(db_url))
    (tmp_path / "menus.toml").write_text(_menus_toml())
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        menus=MenuSettings(config_path=Path(tmp_path / "menus.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def _h(client: TestClient, username: str) -> dict[str, str]:
    tok = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_menus_full_for_admin_and_localized(app) -> None:
    with TestClient(app) as client:
        r = client.get("/api/menus", headers=_h(client, "admin"))
        assert r.status_code == 200
        app1 = r.json()["menus"]["app1"]
        assert app1["app"] == "app1" and app1["label"] == "App One"
        sec = app1["items"][0]
        assert sec["id"] == "security" and sec["label"] == "Security"  # default language
        assert [c["target"] for c in sec["items"]] == ["users_get", "roles_get", "secret_get"]
        assert all(c["connector"] == "app1" for c in sec["items"])
        # the empty folder collapses away
        assert [n["id"] for n in app1["items"]] == ["security", "import"]
        # X-Liberty-Lang switches the labels
        fr = client.get("/api/menus", headers={**_h(client, "admin"), "X-Liberty-Lang": "fr"}).json()["menus"]["app1"]
        assert fr["items"][0]["label"] == "Sécurité"
        assert fr["items"][0]["items"][0]["label"] == "Utilisateurs"


def test_menus_pruned_by_permission(app) -> None:
    with TestClient(app) as client:
        # "user" can't run secret_get → that leaf is gone, the rest stay
        app1 = client.get("/api/menus", headers=_h(client, "user")).json()["menus"]["app1"]
        assert [c["target"] for c in app1["items"][0]["items"]] == ["users_get", "roles_get"]
        # "nobody" can run nothing → the whole app's menu disappears
        assert client.get("/api/menus", headers=_h(client, "nobody")).json()["menus"] == {}


def test_one_app_menu_and_404(app) -> None:
    with TestClient(app) as client:
        r = client.get("/api/menus/app1", headers=_h(client, "admin"))
        assert r.status_code == 200 and r.json()["app"] == "app1"
        assert client.get("/api/menus/ghost", headers=_h(client, "admin")).status_code == 404
        # no accessible items → 404 too
        assert client.get("/api/menus/app1", headers=_h(client, "nobody")).status_code == 404


def test_menus_requires_auth(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/menus").status_code == 401
        assert client.get("/api/menus/app1").status_code == 401


def test_info_reports_menu_apps(app) -> None:
    with TestClient(app) as client:
        assert client.get("/info").json()["menus"] == {"apps": ["app1"]}


def test_reload_rereads_menus(app, tmp_path) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        (tmp_path / "menus.toml").write_text("")  # wipe the menu file
        r = client.post("/admin/reload", headers=h)
        assert r.status_code == 200 and r.json()["menu_apps"] == []
        assert client.get("/api/menus", headers=h).json()["menus"] == {}


# --- type = "dashboard" leaf ---------------------------------------------- #


def test_menu_with_dashboard_leaf(tmp_path) -> None:
    """A `type = "dashboard"` menu leaf surfaces iff its target dashboard exists in
    config/dashboards.toml. Same patternas the connector-backed leaves use sql:c:q."""
    import asyncio
    from liberty.config import ChartSettings, DashboardSettings
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_connectors_toml(db_url))
    (tmp_path / "dashboards.toml").write_text(textwrap.dedent("""
        [dashboards.app1.overview]
        label = "Overview"
        widgets = []
    """))
    (tmp_path / "menus.toml").write_text(textwrap.dedent("""
        [menus.app1]
        label = "App One"

        [[menus.app1.items]]
        id = "dash"
        label = "Dashboards"

        [[menus.app1.items]]
        id = "dash.overview"
        parent = "dash"
        label = "Overview"
        type = "dashboard"
        target = "app1.overview"

        [[menus.app1.items]]
        id = "dash.missing"
        parent = "dash"
        label = "Missing"
        type = "dashboard"
        target = "app1.ghost"
    """))

    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
        await pools.dispose()

    asyncio.run(go())
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        menus=MenuSettings(config_path=Path(tmp_path / "menus.toml")),
        charts=ChartSettings(config_path=Path(tmp_path / "_no_charts.toml")),
        dashboards=DashboardSettings(config_path=Path(tmp_path / "dashboards.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        tree = client.get("/api/menus/app1", headers=_h(client, "admin")).json()
        folder = tree["items"][0]
        # The dashboard leaf shows when the target exists; the orphan leaf with `target = "ghost"` is pruned.
        leaves = [i for i in folder["items"] if i["type"] == "dashboard"]
        assert [l["target"] for l in leaves] == ["app1.overview"]
        # `connector` is absent on a dashboard leaf (the catalog is flat, no connector segment).
        assert "connector" not in leaves[0]
        # No ``home`` set on this app → no ``home_path`` on the wire.
        assert "home_path" not in tree


def test_app_menu_home_path_resolves_to_dashboard_route(tmp_path) -> None:
    """``AppMenu.home`` (a menu item id) resolves to a frontend route on the wire — the
    workspace picker reads ``home_path`` and navigates the operator straight there when they
    pick an app. A dashboard home → ``/dashboard/<id>``; a query home → ``/sql/<c>/<q>``."""
    from liberty.config import ChartSettings, DashboardSettings
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(textwrap.dedent(f"""
        [pools.default]
        url = "{db_url}"

        [connectors.app1]
        type = "sql"
        pool = "default"
        home = "dash.overview"
        queries = [{{ name = "users_get", sql = "SELECT 1" }}]
    """))
    (tmp_path / "dashboards.toml").write_text(textwrap.dedent("""
        [dashboards.app1.overview]
        label = "Overview"
    """))
    (tmp_path / "menus.toml").write_text(textwrap.dedent("""
        [menus.app1]
        label = "App One"

        [[menus.app1.items]]
        id = "dash"
        label = "Dashboards"

        [[menus.app1.items]]
        id = "dash.overview"
        parent = "dash"
        label = "Overview"
        type = "dashboard"
        target = "app1.overview"
    """))

    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
        await pools.dispose()

    asyncio.run(go())
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        menus=MenuSettings(config_path=Path(tmp_path / "menus.toml")),
        charts=ChartSettings(config_path=Path(tmp_path / "_no_charts.toml")),
        dashboards=DashboardSettings(config_path=Path(tmp_path / "dashboards.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        tree = client.get("/api/menus/app1", headers=_h(client, "admin")).json()
        # The dashboard's ``home`` resolves to a frontend ``/dashboard/<id>`` route.
        assert tree["home_path"] == "/dashboard/app1.overview"
        # Pointing ``home`` at a missing item id fails parse — covered separately by the
        # ``AppMenu._check`` validator (raises ValueError on load_menus / parse_menus).


def test_job_leaf_visible_with_its_permission_only(app) -> None:
    """A ``job`` leaf shows for ``job:<id>`` (or a menu allow), hides on ``!job:<id>``; the admin
    sees it; a user without it does not."""
    with TestClient(app) as client:
        def tops(user: str) -> list[str]:
            menus = client.get("/api/menus", headers=_h(client, user)).json()["menus"]
            return [it["id"] for it in menus.get("app1", {}).get("items", [])]
        assert "import" in tops("admin")
        assert tops("loader") == ["import"]
        assert "import" not in tops("user")
        assert "import" not in tops("denied")
        leaf = next(it for it in client.get("/api/menus", headers=_h(client, "loader")).json()["menus"]["app1"]["items"] if it["id"] == "import")
        assert leaf["type"] == "job" and leaf["target"] == "load-items" and "connector" not in leaf


def test_home_by_role_picks_the_users_first_listed_role(tmp_path) -> None:
    """A connector's ``home_by_role`` gives a role its own landing item; a user lands on the
    item of their first role listed there, anyone else on ``home``."""
    from liberty.config import ChartSettings, DashboardSettings
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(textwrap.dedent(f"""
        [pools.default]
        url = "{db_url}"

        [connectors.app1]
        type = "sql"
        pool = "default"
        home = "dash.overview"
        home_by_role = {{ auditor = "users" }}
        queries = [{{ name = "users_get", sql = "SELECT 1" }}]
    """))
    (tmp_path / "dashboards.toml").write_text('[dashboards.app1.overview]\nlabel = "Overview"\n')
    (tmp_path / "menus.toml").write_text(textwrap.dedent("""
        [menus.app1]
        label = "App One"

        [[menus.app1.items]]
        id = "dash.overview"
        label = "Overview"
        type = "dashboard"
        target = "app1.overview"

        [[menus.app1.items]]
        id = "users"
        label = "Users"
        type = "query"
        target = "users_get"
    """))

    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.get_or_create_role("auditor", permissions=["sql:app1:users_get"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("aud", password="audpw", roles=["auditor"])
        await pools.dispose()

    asyncio.run(go())
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        menus=MenuSettings(config_path=Path(tmp_path / "menus.toml")),
        charts=ChartSettings(config_path=Path(tmp_path / "_no_charts.toml")),
        dashboards=DashboardSettings(config_path=Path(tmp_path / "dashboards.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/menus/app1", headers=_h(client, "admin")).json()["home_path"] == "/dashboard/app1.overview"
        r = client.post("/auth/login", json={"username": "aud", "password": "audpw"})
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        assert client.get("/api/menus/app1", headers=h).json()["home_path"] == "/sql/app1/users_get"


def test_nomaflow_page_items_need_nomaflow_view() -> None:
    """A ``page`` leaf is shown to everyone, except a feature area with its own permission: the
    nomaflow pages need ``nomaflow:view`` (``nomaflow:edit`` includes it)."""
    from liberty.auth.principal import Principal
    from liberty.dashboards import DashboardsFile
    from liberty.menus import MenuItem
    from liberty.web.menus import _keeper, page_permission

    assert page_permission("/nomaflow") == "nomaflow:view"
    assert page_permission("/nomaflow/runs") == "nomaflow:view"
    assert page_permission("/nomaflowx") is None and page_permission("/explorer") is None
    jobs = MenuItem(id="jobs", label="Jobs", type="page", target="/nomaflow/schedule")
    explorer = MenuItem(id="explorer", label="Explorer", type="page", target="/explorer")
    dash = DashboardsFile()
    keep = lambda perms: _keeper(Principal(id="1", username="u", permissions=perms), dash, "nomaflow")  # noqa: E731
    assert keep(())(explorer, "nomaflow") is True
    assert keep(())(jobs, "nomaflow") is False
    assert keep(("nomaflow:view",))(jobs, "nomaflow") is True
    assert keep(("nomaflow:edit",))(jobs, "nomaflow") is True
    assert keep(("menu:nomaflow:jobs",))(jobs, "nomaflow") is True
    assert keep(("*", "!menu:nomaflow:jobs"))(jobs, "nomaflow") is False
