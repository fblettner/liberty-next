"""``/api/charts`` route tests — listing + single-chart drill-in, permission filtering, the
``/info`` summary, and ``/admin/reload`` re-reads. Mirrors the layout of ``test_web_screens.py``."""
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
    Settings,
)
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-charts-test-secret"


def _connectors_toml(db_url: str) -> str:
    # Two queries on one connector — the chart's permission is gated by `sql:app1:users_get`,
    # so the "user" role (which only has that perm) sees one of the two charts. The other chart
    # points at `secret_get` which the user can't run → drops out of the catalog.
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
    return textwrap.dedent(
        """
        [charts.users_per_app]
        label = "Users per application"
        description = "Active user count grouped by application"
        connector = "app1"
        query = "users_get"

          [charts.users_per_app.spec]
          type = "bar"
          x = "APPS_ID"
          y = ["USR_ID"]
          aggregation = "count"

        # Locked behind the "secret" query — only roles with sql:app1:secret_get see it.
        [charts.secret_chart]
        label = "Secret"
        connector = "app1"
        query = "secret_get"

          [charts.secret_chart.spec]
          type = "bar"
          x = "X"
          y = ["Y"]
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
            # the "user" role can run `users_get` but not `secret_get` — the secret_chart drops out
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
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        charts=ChartSettings(config_path=Path(tmp_path / "charts.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def _h(client: TestClient, username: str) -> dict[str, str]:
    tok = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_list_charts_admin_sees_everything(app) -> None:
    with TestClient(app) as client:
        r = client.get("/api/charts", headers=_h(client, "admin"))
        assert r.status_code == 200
        charts = r.json()["charts"]
        ids = sorted(c["id"] for c in charts)
        assert ids == ["secret_chart", "users_per_app"]
        # Spec + label round-trip
        u = next(c for c in charts if c["id"] == "users_per_app")
        assert u["label"] == "Users per application"
        assert u["connector"] == "app1" and u["query"] == "users_get"
        assert u["spec"]["type"] == "bar" and u["spec"]["aggregation"] == "count"
        assert u["spec"]["x"] == "APPS_ID" and u["spec"]["y"] == ["USR_ID"]


def test_list_charts_pruned_by_permission(app) -> None:
    """A chart whose underlying query the caller can't run drops out of the catalog —
    same convention `/api/screens` uses. `nobody` (no perms) sees an empty list."""
    with TestClient(app) as client:
        # user: sees only the chart on users_get
        charts = client.get("/api/charts", headers=_h(client, "user")).json()["charts"]
        assert [c["id"] for c in charts] == ["users_per_app"]
        # nobody: sees nothing
        assert client.get("/api/charts", headers=_h(client, "nobody")).json()["charts"] == []


def test_get_one_chart_returns_full_body_and_hides_unreadable(app) -> None:
    with TestClient(app) as client:
        body = client.get("/api/charts/users_per_app", headers=_h(client, "admin")).json()
        assert body["id"] == "users_per_app" and body["spec"]["x"] == "APPS_ID"
        # user can read users_per_app but not secret_chart — the latter is hidden behind a 404
        # (not 403) so we don't leak its existence. Same convention the connector routes use.
        assert client.get("/api/charts/users_per_app", headers=_h(client, "user")).status_code == 200
        assert client.get("/api/charts/secret_chart", headers=_h(client, "user")).status_code == 404
        # Unknown chart → 404
        assert client.get("/api/charts/ghost", headers=_h(client, "admin")).status_code == 404


def test_charts_route_requires_auth(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/charts").status_code == 401
        assert client.get("/api/charts/users_per_app").status_code == 401


def test_info_reports_charts(app) -> None:
    with TestClient(app) as client:
        info = client.get("/info").json()
        assert info["charts"] == {"total": 2}


def test_reload_rereads_charts(app, tmp_path) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        (tmp_path / "charts.toml").write_text("")
        r = client.post("/admin/reload", headers=h)
        assert r.status_code == 200 and r.json()["charts"] == []
        assert client.get("/api/charts", headers=h).json()["charts"] == []
