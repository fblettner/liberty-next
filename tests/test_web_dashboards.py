"""``/api/dashboards`` route tests — chart-reference resolution, permission filtering of widgets,
the ``/info`` summary, and ``/admin/reload`` re-reads."""
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
    ChartSettings,
    ConnectorSettings,
    DashboardSettings,
    Settings,
)
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-dash-test-secret"


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
        """
    )


def _charts_toml() -> str:
    """A saved chart that a dashboard widget references by id — `users_per_app` lives on the
    readable query, so its widget surfaces to the `user` role."""
    return textwrap.dedent(
        """
        [charts.users_per_app]
        label = "Users per app"
        connector = "app1"
        query = "users_get"

          [charts.users_per_app.spec]
          type = "bar"
          x = "APPS_ID"
          y = ["USR_ID"]
          aggregation = "count"
        """
    )


def _dashboards_toml() -> str:
    """Three widgets exercise everything in slice 3a:
    - A chart by id (resolves through charts.toml — readable by `user`)
    - An inline-spec chart (readable too)
    - A KPI widget against the `secret_get` query — the `user` role can't read it, so the
      widget drops out of the catalog (the dashboard still surfaces with two widgets).
    """
    return textwrap.dedent(
        """
        [dashboards.security_overview]
        label = "Security Overview"
        description = "Users + assignments at a glance"

          [[dashboards.security_overview.widgets]]
          type = "chart"
          chart = "users_per_app"
          col_span = 6

          [[dashboards.security_overview.widgets]]
          type = "chart"
          label = "Users by status"
          connector = "app1"
          query = "users_get"
          col_span = 6

            [dashboards.security_overview.widgets.spec]
            type = "pie"
            x = "USR_STATUS"
            y = ["USR_ID"]
            aggregation = "count"

          [[dashboards.security_overview.widgets]]
          type = "kpi"
          label = "Hidden users"
          connector = "app1"
          query = "secret_get"
          column = "USR_ID"
          aggregation = "count"
          col_span = 3

        # A second dashboard that references a chart that DOESN'T exist — should drop the widget
        # with a warning and surface the dashboard with empty widgets.
        [dashboards.orphan]
        label = "Orphan"

          [[dashboards.orphan.widgets]]
          type = "chart"
          chart = "ghost"
          col_span = 12
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
            await svc.get_or_create_role("user", permissions=["sql:app1:users_get"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("user", password="userpw", roles=["user"])
            await svc.create_user("nobody", password="nobodypw")
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_connectors_toml(db_url))
    (tmp_path / "charts.toml").write_text(_charts_toml())
    (tmp_path / "dashboards.toml").write_text(_dashboards_toml())
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        charts=ChartSettings(config_path=Path(tmp_path / "charts.toml")),
        dashboards=DashboardSettings(config_path=Path(tmp_path / "dashboards.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def _h(client: TestClient, username: str) -> dict[str, str]:
    tok = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_list_dashboards_admin_resolves_chart_refs(app) -> None:
    """Admin sees both dashboards. The reference widget gets the saved chart's connector/query/spec
    inlined server-side — the wire payload is uniform regardless of how the operator authored it."""
    with TestClient(app) as client:
        r = client.get("/api/dashboards", headers=_h(client, "admin"))
        assert r.status_code == 200
        dashboards = r.json()["dashboards"]
        # `security_overview` has 3 widgets; `orphan`'s sole widget references a missing chart → dropped
        sec = next(d for d in dashboards if d["id"] == "security_overview")
        assert sec["label"] == "Security Overview" and sec["description"] == "Users + assignments at a glance"
        assert len(sec["widgets"]) == 3
        # The reference widget has the saved chart's bits inlined + the saved chart's label
        ref = sec["widgets"][0]
        assert ref["type"] == "chart" and ref["chart_id"] == "users_per_app"
        assert ref["connector"] == "app1" and ref["query"] == "users_get"
        assert ref["spec"]["type"] == "bar" and ref["spec"]["x"] == "APPS_ID"
        assert ref["label"] == "Users per app"  # inherited from the saved chart
        # The inline widget keeps its own label + inline spec
        inline = sec["widgets"][1]
        assert inline["type"] == "chart" and "chart_id" not in inline
        assert inline["label"] == "Users by status" and inline["spec"]["type"] == "pie"
        # The KPI is there for admin (admin has *)
        kpi = sec["widgets"][2]
        assert kpi["type"] == "kpi" and kpi["aggregation"] == "count" and kpi["column"] == "USR_ID"
        # `orphan` has the unresolved reference dropped — the dashboard surfaces with no widgets
        orphan = next(d for d in dashboards if d["id"] == "orphan")
        assert orphan["widgets"] == []


def test_list_dashboards_filters_unreadable_widgets(app) -> None:
    """The `user` role can read `users_get` but not `secret_get` → the KPI drops out. The
    dashboard surfaces with the remaining two chart widgets."""
    with TestClient(app) as client:
        dashboards = client.get("/api/dashboards", headers=_h(client, "user")).json()["dashboards"]
        sec = next(d for d in dashboards if d["id"] == "security_overview")
        # 3 widgets in TOML → 2 here (KPI on secret_get pruned)
        assert [w["type"] for w in sec["widgets"]] == ["chart", "chart"]
        # `nobody` has no perms → both chart widgets drop too
        ds = client.get("/api/dashboards", headers=_h(client, "nobody")).json()["dashboards"]
        sec = next(d for d in ds if d["id"] == "security_overview")
        assert sec["widgets"] == []


def test_get_one_dashboard_404_on_unknown(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.get("/api/dashboards/security_overview", headers=h)
        assert r.status_code == 200 and r.json()["id"] == "security_overview"
        assert client.get("/api/dashboards/ghost", headers=h).status_code == 404


def test_dashboards_require_auth(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/dashboards").status_code == 401
        assert client.get("/api/dashboards/security_overview").status_code == 401


def test_info_reports_dashboards(app) -> None:
    with TestClient(app) as client:
        info = client.get("/info").json()
        assert info["dashboards"] == {"total": 2}


def test_reload_rereads_dashboards(app, tmp_path) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        (tmp_path / "dashboards.toml").write_text("")
        r = client.post("/admin/reload", headers=h)
        assert r.status_code == 200 and r.json()["dashboards"] == []
        assert client.get("/api/dashboards", headers=h).json()["dashboards"] == []


# --- dashboard filters (Phase 8 slice 3b) ---------------------------------- #


def test_filter_surfaced_when_caller_can_read_options(tmp_path) -> None:
    """The filter bar appears for callers who can read its options query (and have at least
    one readable widget). Caller without permission on the options query → filter dropped; the
    dashboard itself still renders with its widgets."""
    import asyncio
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_connectors_toml(db_url))
    (tmp_path / "charts.toml").write_text("")  # no charts; filter is inline
    (tmp_path / "dashboards.toml").write_text(textwrap.dedent("""
        [dashboards.ov]
        label = "Overview"

          [[dashboards.ov.filters]]
          id = "app"
          label = "Application"
          dictionary_key = "APPS_ID"

            [dashboards.ov.filters.options]
            connector = "app1"
            query = "users_get"
            value_column = "APPS_ID"
            label_column = "APPS_NAME"

          [[dashboards.ov.widgets]]
          type = "kpi"
          label = "Users"
          connector = "app1"
          query = "users_get"
          column = "USR_ID"
          aggregation = "count"
    """))

    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.get_or_create_role("user", permissions=["sql:app1:users_get"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("user", password="userpw", roles=["user"])
            await svc.create_user("nobody", password="nobodypw")
        await pools.dispose()

    asyncio.run(go())
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        charts=ChartSettings(config_path=Path(tmp_path / "charts.toml")),
        dashboards=DashboardSettings(config_path=Path(tmp_path / "dashboards.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        # Admin: filter surfaces with full options block
        d = client.get("/api/dashboards/ov", headers=_h(client, "admin")).json()
        assert "filters" in d and len(d["filters"]) == 1
        f = d["filters"][0]
        assert f["id"] == "app" and f["dictionary_key"] == "APPS_ID"
        assert f["options"]["value_column"] == "APPS_ID" and f["options"]["label_column"] == "APPS_NAME"
        # user: can read users_get → both widget AND filter (filter's options query == widget's query)
        d = client.get("/api/dashboards/ov", headers=_h(client, "user")).json()
        assert len(d["filters"]) == 1 and len(d["widgets"]) == 1
        # nobody: neither → no filter (key omitted), no widgets
        d = client.get("/api/dashboards/ov", headers=_h(client, "nobody")).json()
        assert "filters" not in d and d["widgets"] == []
