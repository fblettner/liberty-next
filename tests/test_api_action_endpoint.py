"""An API endpoint served by a shared action (``action``): steps composed in the action designer —
queries, endpoints, conditions — exposed as one call, run with the endpoint's own permission.
Same app fixture pattern as test_api_plugin_endpoint.py."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import (
    ActionSettings, AISettings, AppSettings, AuthSettings, ChartSettings, ConnectorSettings, DashboardSettings, JobsSettings,
    MenuSettings, ScreenSettings, Settings,
)
from liberty.connectors.config import EndpointDef, PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app
from tests.test_web_plugins import JWT_SECRET, _h, _seed_db

_CONNECTORS_TOML = """
[pools.default]
url = "{db_url}"

[connectors.main]
type = "sql"
pool = "default"

[[connectors.main.queries]]
name = "codes_get"
sql = "SELECT ky, dl01 FROM codes WHERE ky = :KY"

[[connectors.main.queries]]
name = "code_put"
sql = "UPDATE codes SET dl01 = :DL01 WHERE ky = :KY"
writable = true

[connectors.studio]
type = "api"
base_url = "https://studio.test"

[[connectors.studio.endpoints]]
name = "describe_code"
action = "describe_code"
params = [ {{ name = "KY", required = true }}, {{ name = "suffix" }} ]

[[connectors.studio.endpoints]]
name = "rename_code"
action = "rename_code"
writable = true
body = '{{"KY": "{{{{KY}}}}", "label": "{{{{DL01}}}}", "by": "endpoint"}}'
params = [ {{ name = "KY", required = true }}, {{ name = "DL01" }} ]
response_map = {{ LABEL = "label" }}

[[connectors.studio.endpoints]]
name = "code_rows"
action = "code_rows"
params = [ {{ name = "KY" }} ]
rows_field = "found.rows"
row_map = {{ CODE = "ky", LABEL = "dl01" }}

[[connectors.studio.endpoints]]
name = "first_label"
action = "first_label"
params = [ {{ name = "KY" }} ]

[[connectors.studio.endpoints]]
name = "missing"
action = "nope"

[[connectors.studio.endpoints]]
name = "again"
action = "calls_itself"

[[connectors.studio.endpoints]]
name = "broken"
action = "broken"
"""

_ACTIONS_TOML = """
[actions.describe_code]
label = "Describe a code"
params = [ { name = "KY" }, { name = "suffix", default = "!" } ]

[[actions.describe_code.steps]]
id = "read"
type = "run_query"
connector = "main"
query = "codes_get"
bind_result = true
param_binds = [ { param = "KY", source = "INPUT.KY" } ]

[[actions.describe_code.steps]]
id = "none"
type = "if"
condition = { source = "read.rows", operator = "no_rows" }
then_steps = [ { id = "say", type = "notify", message = "no code {INPUT.KY}" } ]

[[actions.describe_code.steps]]
id = "out"
type = "return"
bindings = { code = "read.first_row.ky", label = "read.first_row.dl01", suffix = "INPUT.suffix" }

[actions.rename_code]
[[actions.rename_code.steps]]
id = "write"
type = "run_query"
connector = "main"
query = "code_put"
param_binds = [ { param = "KY", source = "INPUT.KY" }, { param = "DL01", source = "INPUT.label" } ]

[[actions.rename_code.steps]]
id = "again"
type = "call_api"
connector = "studio"
endpoint = "describe_code"
bind_result = true
param_binds = [ { param = "KY", source = "INPUT.KY" } ]

[[actions.rename_code.steps]]
id = "out"
type = "return"
bindings = { label = "again.first_row.label", by = "INPUT.by" }

[actions.code_rows]
[[actions.code_rows.steps]]
id = "read"
type = "run_query"
connector = "main"
query = "codes_get"
bind_result = true
param_binds = [ { param = "KY", source = "INPUT.KY" } ]

[[actions.code_rows.steps]]
id = "out"
type = "return"
bindings = { found = "read" }

[actions.first_label]
[[actions.first_label.steps]]
id = "rows"
type = "call_api"
connector = "studio"
endpoint = "code_rows"
bind_result = true
param_binds = [ { param = "KY", source = "INPUT.KY" } ]

[[actions.first_label.steps]]
id = "out"
type = "return"
bindings = { label = "rows.first_row.LABEL", code = "rows.first_row.CODE" }

[actions.calls_itself]
[[actions.calls_itself.steps]]
id = "loop"
type = "call_api"
connector = "studio"
endpoint = "again"

[actions.broken]
[[actions.broken.steps]]
id = "read"
type = "run_query"
connector = "main"
query = "no_such_query"
"""


def _more_users(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        auth = AuthDatabase(pools, "default")
        async with auth.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("studio", permissions=["api:studio:describe_code", "api:studio:rename_code"])
            await svc.create_user("caller", password="callerpw", roles=["studio"])
        await pools.dispose()
    asyncio.run(go())


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    monkeypatch.setenv("LIBERTY_APPS_DIR", str(tmp_path / "config"))
    db = tmp_path / "app.db"
    db_url = f"sqlite+aiosqlite:///{db}"
    (tmp_path / "connectors.toml").write_text(_CONNECTORS_TOML.format(db_url=db_url))
    (tmp_path / "actions.toml").write_text(_ACTIONS_TOML)
    (tmp_path / "jobs.toml").write_text("")
    _seed_db(db_url)
    _more_users(db_url)
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE codes (ky TEXT PRIMARY KEY, dl01 TEXT)")
        c.executemany("INSERT INTO codes VALUES (?, ?)", [("001", "one"), ("002", "two")])
    return create_app(Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        actions=ActionSettings(config_path=tmp_path / "actions.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
        jobs=JobsSettings(config_path=tmp_path / "jobs.toml", pool="default"),
    ))


def test_an_endpoint_served_by_a_shared_action(env) -> None:
    with TestClient(env) as client:
        call = lambda name, user="admin", **params: client.post(f"/api/http/studio/{name}", headers=_h(client, user), json=params).json()  # noqa: E731
        # the endpoint's parameters are the action's inputs (its own default for one not given); what it returns is the answer
        out = call("describe_code", KY="001")
        assert out["success"] is True and out["json"] == {"code": "001", "label": "one", "suffix": "!"} and out["url"] == "action:describe_code"
        assert call("describe_code", KY="002", suffix="?")["json"]["suffix"] == "?"
        # a notify step is a message of the answer; nothing returned for what was not found
        assert call("describe_code", KY="999")["json"] == {"suffix": "!", "messages": ["no code 999"]}
        # a body gives the inputs (fixed values and variables); an action may call another endpoint served by an action
        out = call("rename_code", KY="002", DL01="deux")
        assert out["success"] is True and out["json"] == {"label": "deux", "by": "endpoint"} and out["mapped"] == {"LABEL": "deux"}
        # an endpoint that says where its rows are: a screen reads them, and a step that calls it gets them BY COLUMN NAME
        assert client.get("/api/sql/studio/code_rows?KY=001", headers=_h(client, "admin")).json()["rows"] == [{"CODE": "001", "LABEL": "one"}]
        assert call("code_rows", KY="001")["rows"] == [{"CODE": "001", "LABEL": "one"}]
        assert call("first_label", KY="001")["json"] == {"label": "one", "code": "001"}
        # what cannot be: a required parameter missing, an action that is not defined, one that calls its own endpoint, a failing step
        assert "KY" in call("describe_code")["error"]
        assert call("missing")["error"] == "missing: the shared action 'nope' is not defined (actions.toml)"
        assert "calls the endpoint it serves" in call("again")["error"]
        assert call("broken")["success"] is False and "no_such_query" in call("broken")["error"]


def test_it_runs_with_its_own_permission(env) -> None:
    with TestClient(env) as client:
        h = _h(client, "caller")
        # the caller may call the two endpoints and nothing else: not the query the action reads, not the one it writes with
        assert client.get("/api/sql/main/codes_get?KY=001", headers=h).status_code == 403
        assert client.post("/api/sql/main/code_put", headers=h, json={"params": {"KY": "001", "DL01": "x"}}).status_code == 403
        assert client.post("/api/http/studio/broken", headers=h, json={}).status_code == 403
        # through the endpoints, the steps run
        out = client.post("/api/http/studio/describe_code", headers=h, json={"KY": "001"}).json()
        assert out["success"] is True and out["json"]["label"] == "one"
        out = client.post("/api/http/studio/rename_code", headers=h, json={"KY": "001", "DL01": "un"}).json()
        assert out["success"] is True and out["json"]["label"] == "un"
        # someone without the endpoint's permission is refused before anything runs
        assert client.post("/api/http/studio/describe_code", headers=_h(client, "reader"), json={"KY": "001"}).status_code == 403


def test_the_configuration_knows_about_it(env) -> None:
    with pytest.raises(ValueError, match="not both"):
        EndpointDef(name="x", action="a", callable="m:f")
    with TestClient(env) as client:
        h = _h(client, "admin")
        issues = [i["message"] for i in client.get("/admin/config/integrity", headers=h).json()["issues"] if i["category"] == "Endpoint action not found"]
        assert len(issues) == 1 and "studio.missing" in issues[0] and "'nope'" in issues[0]
        described = {e["name"]: e.get("action") for c in client.get("/api/connectors", headers=h).json()["connectors"] if c["name"] == "studio" for e in c["endpoints"]}
        assert described["describe_code"] == "describe_code"
