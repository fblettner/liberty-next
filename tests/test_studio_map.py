"""The studio's map of an application (``GET /admin/studio/map``): its menu, its screens with the data
and the services each uses, and what nothing uses."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.config import (
    ActionSettings, AISettings, AppSettings, AuthSettings, ChartSettings, ConnectorSettings, DashboardSettings, JobsSettings,
    MenuSettings, ScreenSettings, Settings,
)
from liberty.main import create_app
from tests.test_web_plugins import JWT_SECRET, _h, _seed_db

_CONNECTORS = """
[pools.default]
url = "{db_url}"

[connectors.main]
type = "sql"
pool = "default"
queries = [
  {{ name = "codes_get", sql = "SELECT 1 AS ky", label = "The codes" }},
  {{ name = "code_put", sql = "UPDATE codes SET dl01 = :DL01 WHERE ky = :KY", writable = true }},
  {{ name = "notes_get", sql = "SELECT 1 AS id" }},
  {{ name = "forgotten", sql = "SELECT 2 AS x" }},
]

[connectors.studio]
type = "api"
base_url = "https://studio.test"
endpoints = [ {{ name = "describe_code", action = "describe_code" }} ]
"""
_ACTIONS = """
[actions.describe_code]
label = "Describe a code"
steps = [ { id = "out", type = "return", bindings = { code = "INPUT.KY" } } ]
"""
_SCREENS = """
[screens.main.codes]
label = "Codes"
read_query = "codes_get"
update_query = "code_put"
row_menu = [
  { id = "card", type = "chain", steps = [
      { id = "known", type = "if", condition = { source = "KY", operator = "truthy" }, then_steps = [ { id = "svc", type = "call_action", ref = "describe_code" } ] },
      { id = "api", type = "call_api", connector = "studio", endpoint = "describe_code" } ] },
]
[screens.main.codes.dialog]
tabs = [ { id = "g", type = "form", fields = [ { name = "KY" } ] }, { id = "n", type = "nested_table", screen = "code_notes" } ]

[screens.main.code_notes]
read_query = "notes_get"
read_only = true

[screens.main.lost]
label = "A screen nobody reaches"
read_query = "codes_get"
"""
_MENUS = """
[menus.main]
items = [
  { id = "ref", label = "Reference" },
  { id = "codes", parent = "ref", label = "Codes", type = "screen", target = "codes" },
  { id = "home", label = "Overview", type = "dashboard", target = "overview" },
]
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    monkeypatch.setenv("LIBERTY_APPS_DIR", str(tmp_path / "config"))
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    for name, text in (("connectors.toml", _CONNECTORS.format(db_url=db_url)), ("actions.toml", _ACTIONS), ("screens.toml", _SCREENS),
                       ("menus.toml", _MENUS), ("jobs.toml", "")):
        (tmp_path / name).write_text(text)
    _seed_db(db_url)
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


def test_the_map_of_an_application(env) -> None:
    with TestClient(env) as client:
        h = _h(client, "admin")
        assert client.get("/admin/studio/map", headers=_h(client, "reader")).status_code == 403
        m = client.get("/admin/studio/map?app=main", headers=h).json()
        assert m["apps"] == ["main"] and m["app"] == "main"
        # the menu in display order, with its depth
        assert [(i["id"], i["depth"], i["type"], i["target"]) for i in m["menu"]] == [
            ("ref", 0, None, None), ("codes", 1, "screen", "codes"), ("home", 0, "dashboard", "overview")]
        # a screen names what it reads, saves through, calls (nested steps included), the services and the screens inside its form
        by_id = {s["id"]: s for s in m["screens"]}
        assert by_id["codes"] == {
            "id": "codes", "label": "Codes", "connector": "main", "read": "main.codes_get", "writes": ["main.code_put"],
            "calls": ["studio.describe_code"], "services": ["describe_code"], "nested": ["code_notes"], "form": True, "read_only": False,
            "in_menu": True, "reached": True}
        assert by_id["code_notes"]["in_menu"] is False and by_id["code_notes"]["reached"] is True       # opened inside another's form
        assert by_id["lost"]["reached"] is False
        # the data: what the screens use first, then what nothing here reads
        data = {d["key"]: d for d in m["data"]}
        assert [d["key"] for d in m["data"]] == ["main.code_put", "main.codes_get", "main.notes_get", "studio.describe_code", "main.forgotten"]
        assert data["main.codes_get"]["label"] == "The codes" and data["main.code_put"]["writable"] is True
        assert data["studio.describe_code"]["kind"] == "endpoint" and data["studio.describe_code"]["service"] == "describe_code"
        assert data["main.forgotten"]["used"] is False
        assert m["services"] == [{"id": "describe_code", "label": "Describe a code", "steps": 1, "screens": ["codes"],
                                  "endpoints": [{"connector": "studio", "endpoint": "describe_code", "label": None, "writable": False}], "missing": False}]
        # an unknown application: the first one
        assert client.get("/admin/studio/map?app=nope", headers=h).json()["app"] == "main"
