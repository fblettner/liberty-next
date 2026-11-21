"""An API endpoint served by a plugin function (``callable``): what one HTTP call cannot do — a
flow of several calls — offered as an endpoint, so a screen can read it or save through it, an
action or a job can call it. Same app fixture pattern as test_admin_api_generate.py."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.config import (
    AISettings, AppSettings, AuthSettings, ChartSettings, ConnectorSettings, DashboardSettings, JobsSettings,
    MenuSettings, ScreenSettings, Settings,
)
from liberty.main import create_app
from tests.test_web_plugins import JWT_SECRET, _h, _seed_db

_CONNECTORS_TOML = """
[pools.default]
url = "{db_url}"

[connectors.remote]
type = "api"
base_url = "https://remote.test/rest"

[[connectors.remote.endpoints]]
name = "row_change"
callable = "flowplg.rows:j_change_row"
writable = true
body = '{{"table": "codes", "find": {{"KY": "*{{{{KY}}}}"}}, "cells": {{"DL01": "{{{{DL01}}}}"}}}}'
params = [ {{ name = "KY" }}, {{ name = "DL01" }} ]
error_fields = ["errors"]
warning_fields = ["warnings"]
response_map = {{ FOUND = "found" }}

[[connectors.remote.endpoints]]
name = "rows"
callable = "flowplg.rows:j_rows"
params = [ {{ name = "prefix" }} ]
rows_field = "items"

[[connectors.remote.endpoints]]
name = "elsewhere"
callable = "os:getcwd"
writable = true
"""

_PLUGIN = '''
async def j_change_row(*, connectors, connector, settings, table, find, cells, **_):
    assert connectors.api(connector).name == connector and settings is not None
    key = find["KY"]
    if key == "*ZZZ":
        raise ValueError("no row " + key)                       # the flow's own refusal
    if cells["DL01"] == "":
        return {"errors": ["Description required"]}             # the server's, in the answer
    return {"found": 1, "table": table, "cells": cells, "warnings": ["check the code"] if key == "*W" else []}


def j_rows(*, prefix="", **_):
    return {"items": [{"KY": prefix + "1", "DL01": "one"}, {"KY": prefix + "2", "DL01": "two"}]}
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    pkg = tmp_path / "plugins" / "flowplg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "rows.py").write_text(textwrap.dedent(_PLUGIN))
    monkeypatch.setenv("LIBERTY_APPS_DIR", str(tmp_path / "config"))
    monkeypatch.syspath_prepend(str(tmp_path / "plugins"))
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(_CONNECTORS_TOML.format(db_url=db_url))
    (tmp_path / "jobs.toml").write_text("")
    _seed_db(db_url)
    return create_app(Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
        jobs=JobsSettings(config_path=tmp_path / "jobs.toml", pool="default"),
    ))


def test_an_endpoint_served_by_a_function(env) -> None:
    with TestClient(env) as client:
        call = lambda name, **params: client.post(f"/api/http/remote/{name}", headers=_h(client, "admin"), json=params).json()  # noqa: E731
        # the body gives the function's arguments once its placeholders are filled; the answer is the endpoint's JSON
        out = call("row_change", KY="002", DL01='Test "3"')
        assert out["success"] is True and out["json"]["cells"] == {"DL01": 'Test "3"'} and out["json"]["table"] == "codes"
        assert out["mapped"] == {"FOUND": 1} and out["url"] == "plugin:flowplg.rows:j_change_row"
        # a warning is reported, the call stands; an error in the answer or raised by the function fails it
        assert call("row_change", KY="W", DL01="x")["warnings"] == "check the code"
        refused = call("row_change", KY="002", DL01="")
        assert refused["success"] is False and refused["error"] == "Description required"
        # "not given" is not "blank": a variable the caller did not send reaches the function as None
        # (a function that types values in a form leaves alone the field nobody sent)
        assert call("row_change", KY="002")["json"]["cells"] == {"DL01": None}
        gone = call("row_change", KY="ZZZ", DL01="x")
        assert gone["success"] is False and gone["error"] == "no row *ZZZ"
        # without a body: the parameters by name; a function that is not a plugin entry point is refused
        assert call("rows", prefix="A")["json"]["items"][0] == {"KY": "A1", "DL01": "one"}
        assert "Unknown plugin callable" in call("elsewhere")["error"]
        issues = [i["message"] for i in client.get("/admin/config/integrity", headers=_h(client, "admin")).json()["issues"]
                  if i["category"] == "Endpoint function not found"]
        assert len(issues) == 1 and "remote.elsewhere" in issues[0]


def test_a_screen_reads_and_saves_through_them(env) -> None:
    with TestClient(env) as client:
        h = _h(client, "admin")
        read = client.get("/api/sql/remote/rows?prefix=B", headers=h).json()
        assert [r["KY"] for r in read["rows"]] == ["B1", "B2"] and [c["name"] for c in read["columns"]] == ["KY", "DL01"]
        saved = client.post("/api/sql/remote/row_change", headers=h, json={"params": {"KY": "002", "DL01": "changed"}})
        assert saved.status_code == 200 and saved.json()["rowcount"] == 1 and saved.json()["resolved_binds"] == {"FOUND": 1}
        refused = client.post("/api/sql/remote/row_change", headers=h, json={"params": {"KY": "ZZZ", "DL01": "x"}})
        assert refused.status_code == 502 and "no row *ZZZ" in refused.json()["detail"]
