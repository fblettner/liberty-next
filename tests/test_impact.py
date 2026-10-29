"""The Developer page's Impact view (C12): the configuration graph from the dependency walker — what
an entity uses, what uses it transitively (the blast radius), unused / undefined entities — and the
walker fixes it relies on: menu items follow their real target types, dashboards are scoped."""

from __future__ import annotations

import textwrap
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_web_admin_ops import _h, make_env


@pytest.fixture
def env(tmp_path):
    app, base = make_env(tmp_path)
    # menu items of every target kind (read at startup, so rewritten before the client starts)
    (base / "menus.toml").write_text(textwrap.dedent("""
        [menus.foo]
        label = "Foo"
        [[menus.foo.items]]
        id = "u"
        label = "Users"
        type = "query"
        connector = "foo"
        target = "users_get"
        [[menus.foo.items]]
        id = "users_screen"
        label = "Users (screen)"
        type = "screen"
        target = "users"
        [[menus.foo.items]]
        id = "dash"
        label = "Overview"
        type = "dashboard"
        target = "foo.overview"
        [[menus.foo.items]]
        id = "folder"
        label = "A folder"
    """).lstrip())
    return app, base


@contextmanager
def _client(env):
    """The client, with the raw files the walker reads (Explorer, notifications, AI) inside the test's
    folder — their defaults point at the repository's config/."""
    app, base = env
    with TestClient(app) as client:
        st = app.state.settings
        st.explorer.config_path = Path(base / "explorer.toml")
        st.notify.config_path = Path(base / "notifications.toml")
        st.ai.config_path = Path(base / "ai.toml")
        yield client


def _impact(client: TestClient, h: dict[str, str], **q: str) -> dict:
    r = client.get("/admin/dev/impact/entity", params=q, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_catalogue_lists_every_entity_with_counts(env) -> None:
    with _client(env) as client:
        assert client.get("/admin/dev/impact", headers=_h(client, "reader")).status_code == 403
        h = _h(client, "admin")
        cat = client.get("/admin/dev/impact", headers=h).json()
        by = {(e["kind"], e["scope"], e["name"]): e for e in cat["entities"]}
        assert {"pool", "connector", "query", "screen", "menu_item", "chart", "dashboard", "action"} <= set(cat["kinds"])
        assert by[("dashboard", "foo", "overview")]["used_by"] == 1          # the menu item names it foo.overview
        assert by[("query", "foo2", "other_get")]["unused"] is True
        assert by[("menu_item", "foo", "folder")]["unused"] is False         # an entry point is never unused
        assert by[("dictionary_entry", "foo", "USR_NAME")]["unused"] is False
        assert cat["broken"] == []


def test_a_query_reaches_its_screens_menus_charts_dashboards_and_actions(env) -> None:
    with _client(env) as client:
        d = _impact(client, _h(client, "admin"), kind="query", scope="foo", name="users_get")
        assert [(u["kind"], u["name"]) for u in d["uses"]] == [("connector", "foo")]
        assert d["closure_by_kind"] == {"pool": 1, "connector": 1}
        reach = d["reach_by_kind"]
        assert reach["screen"] == 1 and reach["chart"] == 1 and reach["dashboard"] == 1 and reach["action"] == 1
        assert reach["menu_item"] == 3                                      # u (query), users_screen (screen), dash (dashboard)
        tree = d["used_by"]
        dash = next(n for n in tree if n["kind"] == "dashboard")
        assert tree[dash["parent"]]["kind"] == "chart" and dash["depth"] == 2  # query → chart → dashboard
        assert d["entity"]["link"] == {"editor": "connectors", "connector": "foo", "query": "users_get"}


def test_a_pool_change_does_not_reach_every_menu_folder(env) -> None:
    with _client(env) as client:
        d = _impact(client, _h(client, "admin"), kind="pool", name="default")
        names = {(n["kind"], n["name"]) for n in d["used_by"]}
        assert ("menu_item", "folder") not in names and ("menu_item", "u") in names


def test_unknown_entity_is_404(env) -> None:
    with _client(env) as client:
        r = client.get("/admin/dev/impact/entity", params={"kind": "query", "scope": "foo", "name": "nope"}, headers=_h(client, "admin"))
        assert r.status_code == 404


def test_a_menu_item_packages_its_target(env) -> None:
    """The walker followed only the v1 item types (table / form / chart): a screen, query or dashboard
    item reached nothing but its connector."""
    with _client(env) as client:
        seeds = [{"kind": "menu_item", "name": n, "scope": "foo"} for n in ("users_screen", "dash", "u")]
        deps = client.post("/admin/find-dependencies", json={"seeds": seeds}, headers=_h(client, "admin")).json()["deps"]
        got = {(d["kind"], d["scope"], d["name"]) for d in deps}
        assert ("screen", "foo", "users") in got and ("query", "foo", "users_get") in got
        assert ("dashboard", "foo", "overview") in got


def test_reasons_name_the_relation_and_screens_take_their_menu_label(env) -> None:
    with _client(env) as client:
        d = _impact(client, _h(client, "admin"), kind="query", scope="foo", name="users_get")
        reasons = {(n["kind"], n["name"]): n["reason"] for n in d["used_by"]}
        assert reasons[("screen", "users")] == "read_query"                 # not "screen foo.users.read_query"
        assert reasons[("menu_item", "users_screen")] == "target"
        assert d["uses"][0]["reason"] == "its connector"
        screen = next(n for n in d["used_by"] if n["kind"] == "screen")
        assert screen["label"] == "Users (screen)"                          # no label of its own: its menu item's


def test_short_reason_keeps_the_text_after_the_referrer() -> None:
    from liberty.impact import short_reason
    assert short_reason(("query", "jde", "get_x"), "query jde.get_x · its connector") == "its connector"
    assert short_reason(("dictionary_entry", "nomajde", "MCU"), "entry nomajde.MCU rules=LOOKUP") == "rules=LOOKUP"
    assert short_reason(("job", None, "sync"), "job sync step F0004") == "step F0004"
    assert short_reason(("screen", "a", "s"), "something else") == "something else"


def test_cli_route_is_superuser_only(env) -> None:
    with _client(env) as client:
        assert client.get("/admin/dev/cli", headers=_h(client, "reader")).status_code == 403
        body = client.get("/admin/dev/cli", headers=_h(client, "admin")).json()
        assert body["total"] >= 7 and body["commands"] == sum(len(s["commands"]) for s in body["scripts"])
