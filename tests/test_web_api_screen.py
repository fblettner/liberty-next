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

[[connectors.erp.endpoints]]
name = "address_save"
method = "POST"
path = "/save"
writable = true
body = '{{"an8": "{{{{AN8}}}}", "name": "{{{{ALPH}}}}", "was": "{{{{AN8_ORIGINAL}}}}"}}'
params = [ {{ name = "AN8" }}, {{ name = "ALPH" }}, {{ name = "AN8_ORIGINAL" }} ]
error_fields = ["errors"]
warning_fields = ["warnings"]
response_map = {{ AN8 = "saved.an8" }}

[connectors.jde]
type = "sql"
pool = "default"
dd_prefix = 2

[[connectors.jde.queries]]
name = "f0101_get"
sql = "SELECT 9999 AS ABAN8, 'TEST' AS ABALPH, 'C' AS ABAT1"
"""

_SCREENS = """
[screens.erp.addresses]
label = "Addresses"
read_query = "addresses"
update_query = "address_save"
insert_query = "address_save"
max_rows = 50
editable_when = [ { field = "AT1", value = "E" } ]

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

# read by a query, saved through the API's endpoint: the columns are the table's, the variables the form's
_SCREENS += """
[screens.jde.book]
label = "Address book"
read_query = "f0101_get"
save_connector = "erp"
update_query = "address_save"
editable_when = [ { field = "ABAT1", value = "C" } ]

[[screens.jde.book.columns]]
name = "ABAN8"
key = true

[[screens.jde.book.columns]]
name = "ABALPH"
dd = "ALPH"

[[screens.jde.book.columns]]
name = "ABAT1"
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
            await svc.get_or_create_role("writer", permissions=["api:erp:addresses", "api:erp:address_save"])
            await svc.create_user("wim", password="wimpw", roles=["writer"])
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


def test_a_row_is_saved_through_an_endpoint(env) -> None:
    """The grid's save (``POST /api/sql/{c}/{q}`` with the row's values) on a screen whose update /
    insert queries are endpoints marked as changing data."""
    with TestClient(env) as client:
        sent: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            sent.append((request.url.path, body))
            if body.get("name") == "":
                return httpx.Response(200, json={"errors": [{"TITLE": "Error: Alpha Name required"}]})
            return httpx.Response(200, json={"errors": [], "warnings": ["Duplicate name"], "saved": {"an8": 4242}})

        client.app.state.connectors.get("erp")._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        row = {"params": {"AN8": 1234, "ALPH": "  Long, Benjamin ", "AT1": "E", "AN8_ORIGINAL": 1234, "AT1_ORIGINAL": "E"}}
        # the endpoint's own permission: reading the screen is not enough to save
        assert client.post("/api/sql/erp/address_save", json=row, headers=_h(client, "ann")).status_code == 403
        r = client.post("/api/sql/erp/address_save", json=row, headers=_h(client, "wim"))
        assert r.status_code == 200, r.text
        out = r.json()
        # answered as a statement: one row, the values the endpoint maps out of its answer, the server's warnings
        assert out["rowcount"] == 1 and out["statement_type"] == "UPDATE" and out["resolved_binds"] == {"AN8": 4242}
        assert out["warnings"] and "Duplicate name" in str(out["warnings"])
        # the row's values went to the endpoint's variables by name; the rest of the row is not sent
        assert sent[-1] == ("/rest/save", {"an8": "1234", "name": "Long, Benjamin", "was": "1234"})
        # the server refuses (its error fields): the save fails with its words
        r = client.post("/api/sql/erp/address_save", json={"params": {"AN8": 1, "AT1": "E"}}, headers=_h(client, "wim"))
        assert r.status_code == 502 and "Alpha Name required" in r.json()["detail"]
        # the screen's editable_when holds for an API save too
        r = client.post("/api/sql/erp/address_save", json={"params": {"AN8": 2, "ALPH": "x", "AT1_ORIGINAL": "C"}}, headers=_h(client, "wim"))
        assert r.status_code == 409
        # an endpoint that neither holds rows nor changes data is not a screen's query
        assert client.post("/api/sql/erp/ping", json={}, headers=_h(client, "admin")).status_code == 400
        # the catalog says the screen saves; integrity knows the endpoints
        detail = client.get("/api/screens/erp/addresses", headers=_h(client, "wim")).json()
        assert detail["update_query"] == "address_save"
        issues = client.get("/admin/config/integrity", headers=_h(client, "admin")).json()["issues"]
        assert not [i for i in issues if i["category"] == "Missing query"]
        # importing rows is said not to be available
        assert client.post("/api/sql/erp/_import", json={"rows": []}, headers=_h(client, "wim")).status_code == 400


def test_a_screen_read_by_a_query_saves_through_another_connectors_endpoint(env) -> None:
    """``save_connector``: the screen keeps its query and its columns (``ABAN8``, ``ABALPH``); its
    save goes to an API endpoint whose variables (``AN8``, ``ALPH``) take the columns carrying
    those dictionary items — the hint's ``dd``, or the name minus the connector's ``dd_prefix``."""
    with TestClient(env) as client:
        sent: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"errors": [], "warnings": [], "saved": {"an8": 9999}})

        client.app.state.connectors.get("erp")._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        row = {"params": {"ABAN8": 9999, "ABALPH": "New name", "ABAT1": "C", "ABAN8_ORIGINAL": 9999, "ABAT1_ORIGINAL": "C"}}
        # the screen names itself: another screen (erp/addresses) saves through the same endpoint
        url = "/api/sql/erp/address_save?_screen=book&_app=jde"
        r = client.post(url, json=row, headers=_h(client, "wim"))
        assert r.status_code == 200, r.text
        assert r.json()["statement_type"] == "UPDATE" and r.json()["rowcount"] == 1
        # ALPH ← ABALPH (its dd), AN8 ← ABAN8 (the name minus the prefix); AN8_ORIGINAL has no column: left out
        assert sent[-1] == {"an8": "9999", "name": "New name", "was": ""}
        # THIS screen's editable_when, on its own column
        locked = {"params": {"ABAN8": 9999, "ABALPH": "x", "ABAT1_ORIGINAL": "V"}}
        assert client.post(url, json=locked, headers=_h(client, "wim")).status_code == 409
        # the endpoint's permission is the save's: reading the screen is not enough
        assert client.post(url, json=row, headers=_h(client, "ann")).status_code == 403
        # the catalog carries it; integrity, usages and a rename follow the save connector
        detail = client.get("/api/screens/jde/book", headers=_h(client, "admin")).json()
        assert detail["save_connector"] == "erp" and detail["update_query"] == "address_save" and detail["connector"] == "jde"
        issues = client.get("/admin/config/integrity", headers=_h(client, "admin")).json()["issues"]
        assert not [i for i in issues if i["category"] in ("Missing query", "Missing connector")], issues


def test_the_columns_that_feed_an_endpoints_variables() -> None:
    from liberty.connectors.api_rows import columns_for, write_params
    from liberty.connectors.config import ColumnHint, EndpointDef, ParamDef

    endpoint = EndpointDef(name="save", params=[ParamDef(name="AN8"), ParamDef(name="ALPH"), ParamDef(name="TAX"), ParamDef(name="MAX", default="10")])
    hints = [ColumnHint(name="ABAN8"), ColumnHint(name="NAME", dd="ALPH"), ColumnHint(name="TAX")]
    fed = columns_for(endpoint, hints, 2)
    assert fed == {"an8": "aban8", "alph": "name"}                       # TAX has its own column: not listed
    # without hints, the names the row came with (a connector with a prefix)
    assert columns_for(endpoint, None, 2, {"ABAN8": 1, "ABALPH": "x", "ABAN8_ORIGINAL": 1}) == {"an8": "aban8", "alph": "abalph"}
    assert columns_for(endpoint, None, 0, {"ABAN8": 1}) == {}
    # a column the row carries blank is GIVEN blank (the user cleared it); one it does not carry is left out;
    # a blank where the variable has a default leaves the default to apply
    assert write_params(endpoint, {"ABAN8": 9999, "NAME": "  ", "MAX": ""}, fed) == {"AN8": 9999, "ALPH": ""}
    # an update served by a function: only what changed since the row was loaded — the key (required) always
    keyed = EndpointDef(name="save", params=[ParamDef(name="AN8", required=True), ParamDef(name="ALPH"), ParamDef(name="TAX")])
    row = {"ABAN8": 9999, "NAME": "TEST ", "TAX": "124", "ABAN8_ORIGINAL": 9999, "NAME_ORIGINAL": "TEST", "TAX_ORIGINAL": "123"}
    assert write_params(keyed, row, columns_for(keyed, hints, 2), changed_only=True) == {"AN8": 9999, "TAX": "124"}
    assert write_params(keyed, row, columns_for(keyed, hints, 2)) == {"AN8": 9999, "ALPH": "TEST", "TAX": "124"}
