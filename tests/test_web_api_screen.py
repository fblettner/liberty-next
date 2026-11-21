"""A screen that reads an API endpoint: the grid's own route (``GET /api/sql/{c}/{q}``) serves the
endpoint's rows, under the endpoint's permission; the screen catalog and the integrity checks
accept it. Same app fixture pattern as test_web_plugins.py."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import (
    AISettings, AppSettings, AuthSettings, ChartSettings, ConnectorSettings, DashboardSettings, JobsSettings,
    MenuSettings, ScreenSettings, Settings,
)
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.jobs import JobDatabase
from liberty.main import create_app
from tests.test_web_plugins import JWT_SECRET, _h

_CONNECTORS = """
[pools.default]
url = "{db_url}"

[connectors.erp]
type = "api"
base_url = "https://erp.test/rest"

[[connectors.erp.endpoints]]
name = "addresses"
method = "POST"
path = "/form"
body = '{{"type": "{{{{AT1}}}}"}}'
params = [ {{ name = "AT1" }} ]
rows_field = "grid.rows"
row_map = {{ AN8 = "z_AN8_19", ALPH = "z_ALPH_20", AT1 = "z_AT1_50" }}
labels_field = "grid.columns"
more_field = "grid.more"

[[connectors.erp.endpoints]]
name = "ping"
path = "/ping"
"""

_SCREENS = """
[screens.erp.addresses]
label = "Addresses"
read_query = "addresses"
max_rows = 50

[[screens.erp.addresses.columns]]
name = "ALPH"
label = "Name"

[[screens.erp.addresses.columns]]
name = "AT1"
filter = true

[[screens.erp.addresses.columns]]
name = "AN8"
filter = true
"""

ANSWER = {"grid": {"columns": {"z_AN8_19": "Address Number", "z_ALPH_20": "Alpha Name", "z_AT1_50": "Sch Typ"}, "more": False,
                   "rows": [{"z_AN8_19": 1001, "z_ALPH_20": "AB Common", "z_AT1_50": "E"}, {"z_AN8_19": 1234, "z_ALPH_20": "Long, Ben", "z_AT1_50": "E"}]}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("LIBERTY_APPS_DIR", raising=False)
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_CONNECTORS.format(db_url=db_url))
    (tmp_path / "screens.toml").write_text(_SCREENS)
    (tmp_path / "jobs.toml").write_text("")

    async def seed() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        auth = AuthDatabase(pools, "default")
        await auth.create_schema()
        await JobDatabase(pools, "default").create_schema()
        async with auth.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("reader", permissions=["api:erp:addresses"])
            await svc.get_or_create_role("sqlonly", permissions=["sql:*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True)
            await svc.create_user("ann", password="annpw", roles=["reader"])
            await svc.create_user("bob", password="bobpw", roles=["sqlonly"])
        await pools.dispose()

    asyncio.run(seed())
    return create_app(Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
        jobs=JobsSettings(config_path=tmp_path / "jobs.toml", pool="default"),
    ))


def _answer_with(client: TestClient, sent: list, status: int = 200) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(status, json=ANSWER)

    client.app.state.connectors.get("erp")._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_the_grids_route_serves_an_endpoints_rows(env) -> None:
    with TestClient(env) as client:
        sent: list = []
        _answer_with(client, sent)
        r = client.get("/api/sql/erp/addresses?_stream=1&_screen=addresses&_app=erp&AT1=E&AN8=12&AN8_op=gte", headers=_h(client, "ann"))
        assert r.status_code == 200, r.text
        lines = [json.loads(line) for line in r.text.splitlines() if line]
        meta, rows, done = lines[0], [x for line in lines if line["kind"] == "rows" for x in line["rows"]], lines[-1]
        # the screen's columns: its order and label, the server's title otherwise; a filter only where the endpoint has the parameter
        assert [(c["name"], c["label"], c.get("filter", False)) for c in meta["columns"]] == [("ALPH", "Name", False), ("AT1", "Sch Typ", True), ("AN8", "Address Number", False)]
        assert meta["cap"] == 50                                    # the screen's own limit
        assert rows == [{"AN8": 1001, "ALPH": "AB Common", "AT1": "E"}, {"AN8": 1234, "ALPH": "Long, Ben", "AT1": "E"}]
        assert done["kind"] == "done" and done["total"] == 2 and done["truncated"] is False
        assert sent == [{"type": "E"}]                              # AN8 is not a parameter of the endpoint: not sent
        # not streamed (a lookup): the same rows as a query's result
        whole = client.get("/api/sql/erp/addresses?AT1=E", headers=_h(client, "ann")).json()
        assert whole["row_count"] == 2 and whole["statement_type"] == "SELECT" and whole["rows"][0]["AN8"] == 1001


def test_the_endpoints_permission_and_what_is_not_readable(env) -> None:
    with TestClient(env) as client:
        _answer_with(client, [])
        # sql:* is not the endpoint's permission; an endpoint without rows is not a query either
        assert client.get("/api/sql/erp/addresses?_stream=1", headers=_h(client, "bob")).status_code == 403
        assert client.get("/api/sql/erp/ping", headers=_h(client, "ann")).status_code == 403
        assert client.get("/api/sql/erp/ping", headers=_h(client, "admin")).status_code in (400, 404)
        # the catalog: the screen is theirs with the endpoint's permission
        mine = client.get("/api/screens", headers=_h(client, "ann")).json()["screens"]
        assert [s["id"] for s in mine.get("erp", [])] == ["addresses"]
        assert "erp" not in client.get("/api/screens", headers=_h(client, "bob")).json()["screens"]
        assert client.get("/api/screens/erp/addresses", headers=_h(client, "ann")).status_code == 200
        # integrity: a screen reading an endpoint with rows is not a missing query
        issues = client.get("/admin/config/integrity", headers=_h(client, "admin")).json()["issues"]
        assert not [i for i in issues if i["category"] == "Missing query"]


def test_a_failed_call_is_the_reads_error(env) -> None:
    with TestClient(env) as client:
        _answer_with(client, [], status=500)
        r = client.get("/api/sql/erp/addresses?_stream=1", headers=_h(client, "ann"))
        last = json.loads(r.text.splitlines()[-1])
        assert last["kind"] == "error" and "erp.addresses" in last["detail"]
        assert client.get("/api/sql/erp/addresses", headers=_h(client, "ann")).status_code == 502
