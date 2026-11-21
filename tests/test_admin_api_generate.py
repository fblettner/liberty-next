"""``POST /admin/config/api/generate`` — the endpoint editor's "Create from…": a connector's
generator (a plugin function) run step by step against the saved connector, its built endpoint
validated before the editor adds it. Same app fixture pattern as test_web_plugins.py."""

from __future__ import annotations

import json
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

[[connectors.remote.generators]]
id = "thing"
label = "A thing"
callable = "genplg.make:j_thing_endpoint"

[[connectors.remote.generators]]
id = "screen"
label = "A screen"
callable = "genplg.make:j_thing_screen"

[[connectors.remote.generators]]
id = "broken"
label = "A broken one"
callable = "genplg.make:j_broken_endpoint"

[[connectors.remote.generators]]
id = "elsewhere"
label = "Not a plugin entry point"
callable = "os:getcwd"

[[connectors.remote.endpoints]]
name = "ping"
path = "/ping"
"""

_PLUGIN = '''
async def j_thing_endpoint(*, connectors, connector, step, query="", candidate="", options=None, **_):
    assert connectors.api(connector).name == connector          # the saved connector, by its name
    if step == "search":
        if not query:
            raise ValueError("type something")
        return {"candidates": [{"id": "T1", "label": "Thing one", "description": query}, {"label": "no id: dropped"}]}
    if step == "options":
        return {"options": [{"name": "fields", "label": "Fields", "kind": "multi", "choices": [{"value": "a"}, {"value": "b"}]}],
                "note": f"about {candidate}"}
    wanted = (options or {}).get("fields") or []
    return {"endpoint": {"name": f"thing_{candidate}", "method": "POST", "path": "/things",
                         "body": "{" + ", ".join(f'"{f}": "{{{{{f.upper()}}}}}"' for f in wanted) + "}",
                         "params": [{"name": f.upper(), "label": f, "help": "Required."} for f in wanted]}}


async def j_thing_screen(*, step, candidate="", **_):
    if step != "build":
        return {"candidates": [{"id": "T1"}], "options": []}
    read = {"name": f"{candidate}_rows", "path": "/things", "rows_field": "items", "params": [{"name": "KIND", "required": True}],
            "source": {"generator": "thing", "candidate": candidate, "options": {}}}
    save = {"name": f"{candidate}_save", "method": "POST", "path": "/things", "writable": True,
            "body": '{"id": "{{ID}}", "kind": "{{KIND}}"}', "params": [{"name": "ID"}, {"name": "KIND", "required": True}]}
    return {"endpoints": [read, save], "app": "remote", "note": "read + save",
            "screen": {"id": f"{candidate}_screen", "label": "Things", "connector": "remote", "read_query": read["name"],
                       "update_query": save["name"], "insert_query": save["name"], "columns": [{"name": "ID", "key": True}]}}


async def j_broken_endpoint(*, step, **_):
    return {"endpoint": {"name": "x", "method": "FETCH", "path": "/"}} if step == "build" else {"candidates": [{"id": "1"}]}
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    pkg = tmp_path / "plugins" / "genplg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "make.py").write_text(textwrap.dedent(_PLUGIN))
    monkeypatch.setenv("LIBERTY_APPS_DIR", str(tmp_path / "config"))
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


def _gen(client: TestClient, user: str = "admin", **body):
    return client.post("/admin/config/api/generate", headers=_h(client, user), json={"connector": "remote", "generator": "thing", **body})


def test_the_three_steps_build_an_endpoint_the_editor_can_add(env) -> None:
    with TestClient(env) as client:
        found = _gen(client, step="search", query="one")
        assert found.status_code == 200, found.text
        assert found.json() == {"candidates": [{"id": "T1", "label": "Thing one", "description": "one"}]}
        asked = _gen(client, step="options", candidate="T1").json()
        assert asked["note"] == "about T1" and asked["options"][0]["name"] == "fields"
        built = _gen(client, step="build", candidate="T1", options={"fields": ["a", "b"]})
        assert built.status_code == 200, built.text
        endpoint = built.json()["endpoint"]
        # validated against the endpoint model; defaults left out, as the editor saves them
        assert endpoint["name"] == "thing_T1" and endpoint["method"] == "POST" and endpoint["path"] == "/things"
        assert endpoint["body"] == '{"a": "{{A}}", "b": "{{B}}"}' and [p["name"] for p in endpoint["params"]] == ["A", "B"]
        assert "content_type" not in endpoint
        # where it comes from is kept on it, for "Refresh"
        assert endpoint["source"] == {"generator": "thing", "candidate": "T1", "options": {"fields": ["a", "b"]}}


def test_a_generated_endpoint_whose_generator_is_gone_is_reported(env) -> None:
    from liberty.connectors.config import EndpointDef, EndpointSource
    from liberty.web.integrity import check_integrity

    with TestClient(env) as client:
        cfg = client.app.state.connectors.api("remote").config
        cfg.endpoints.append(EndpointDef(name="ok", path="/a", source=EndpointSource(generator="thing", candidate="T1")))
        cfg.endpoints.append(EndpointDef(name="orphan", path="/b", source=EndpointSource(generator="removed", candidate="T9")))
        found = [i.message for i in check_integrity(client.app.state) if i.category == "Generated endpoint without its generator"]
        assert len(found) == 1 and "remote.orphan" in found[0] and "'removed'" in found[0]


def test_it_is_for_superusers_and_says_what_went_wrong(env) -> None:
    with TestClient(env) as client:
        assert client.post("/admin/config/api/generate", json={}).status_code == 401
        assert _gen(client, "merger", step="search", query="x").status_code == 403
        # the generator's own error, as its message
        r = _gen(client, step="search", query="")
        assert r.status_code == 400 and "type something" in r.json()["detail"]
        assert _gen(client, step="delete").status_code == 422
        h = _h(client, "admin")
        post = lambda **b: client.post("/admin/config/api/generate", headers=h, json=b)  # noqa: E731
        assert post(connector="nope", generator="thing", step="search").status_code == 404
        assert post(connector="remote", generator="nope", step="search").status_code == 404
        # a callable that is not a plugin entry point is never imported
        r = post(connector="remote", generator="elsewhere", step="search")
        assert r.status_code == 404 and "Unknown plugin callable" in r.json()["detail"]
        # an endpoint the model refuses is not handed to the editor
        r = post(connector="remote", generator="broken", step="build", candidate="1")
        assert r.status_code == 400 and "invalid endpoint" in r.json()["detail"]


def test_a_generator_builds_a_whole_screen_and_the_scaffold_creates_it(env) -> None:
    import httpx

    with TestClient(env) as client:
        h = _h(client, "admin")
        built = client.post("/admin/config/api/generate", headers=h, json={"connector": "remote", "generator": "screen", "step": "build", "candidate": "T1"})
        assert built.status_code == 200, built.text
        bundle = built.json()
        assert [e["name"] for e in bundle["endpoints"]] == ["T1_rows", "T1_save"] and bundle["screen"]["read_query"] == "T1_rows"
        assert bundle["endpoints"][0]["source"]["generator"] == "thing" and bundle["app"] == "remote"
        # one validated pass: the endpoints on the connector, the screen, a menu item; then a reload
        made = client.post("/admin/assistant/scaffold", headers=h, json={
            "connector": "remote", "app": "remote", "endpoints": bundle["endpoints"], "screen": bundle["screen"],
            "menu": {"app": "remote", "id": "t1", "label": "Things", "target": "T1_screen", "connector": "remote"}})
        assert made.status_code == 200, made.text
        assert made.json()["created"]["endpoints"] == ["T1_rows", "T1_save"] and made.json()["created"]["screen"] == "remote.T1_screen"
        # a second time: the endpoints exist
        again = client.post("/admin/assistant/scaffold", headers=h, json={"connector": "remote", "app": "remote", "endpoints": bundle["endpoints"],
                                                                         "screen": {**bundle["screen"], "id": "other"}})
        assert again.status_code == 409 and "T1_rows" in again.json()["detail"]
        # the screen works: it reads, it saves with the parameters it was read with, a required one cannot be left out
        sent: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append((request.method, json.loads(request.content) if request.content else dict(request.url.params)))
            return httpx.Response(200, json={"items": [{"ID": 1}]})

        client.app.state.connectors.get("remote")._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert client.get("/api/sql/remote/T1_rows?KIND=a", headers=h).json()["rows"] == [{"ID": 1}]
        missing = client.get("/api/sql/remote/T1_rows", headers=h)
        assert missing.status_code == 502 and "'KIND'" in missing.json()["detail"] and "required" in missing.json()["detail"]
        saved = client.post("/api/sql/remote/T1_save", headers=h, json={"params": {"KIND": "a", "ID": 7}})
        assert saved.status_code == 200 and sent[-1] == ("POST", {"id": "7", "kind": "a"})
        assert client.post("/api/sql/remote/T1_save", headers=h, json={"params": {"ID": 7}}).status_code == 502
        assert len(sent) == 2                                    # refused before anything was sent
