"""What a menu grant gives: a role allows a folder or an entry — read, or read and change — and the
server works out what the screens under it need (liberty.auth.implied + liberty.web.menu_access)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from liberty.actions.config import parse_actions
from liberty.auth import implied
from liberty.auth.principal import Principal
from liberty.connectors.config import parse_connectors
from liberty.dashboards.config import parse_dashboards
from liberty.menus.config import parse_menus
from liberty.screens.config import parse_screens
from liberty.web.menu_access import access_of, build


class _Registry:
    def __init__(self, connectors: dict) -> None:
        self._conns = {n: SimpleNamespace(config=c) for n, c in parse_connectors({"connectors": connectors}).connectors.items()}
        self.dictionary = None

    def names(self):
        return list(self._conns)

    def get(self, n):
        return self._conns[n]


_SQL = {"type": "sql", "queries": [
    {"name": "orders_get", "sql": "SELECT 1"},
    {"name": "orders_put", "sql": "UPDATE t SET a = 1", "writable": True},
    {"name": "orders_post", "sql": "INSERT INTO t VALUES (1)", "writable": True},
    {"name": "lines_get", "sql": "SELECT 1"},
    {"name": "lines_put", "sql": "UPDATE l SET a = 1", "writable": True},
    {"name": "customers_get", "sql": "SELECT 1"},
    {"name": "customers_put", "sql": "UPDATE c SET a = 1", "writable": True},
    {"name": "close_order", "sql": "UPDATE t SET closed = 1", "writable": True},
    {"name": "order_history", "sql": "SELECT 1"},
    {"name": "totals", "sql": "SELECT 1"},
    {"name": "stock_get", "sql": "SELECT 1"},
]}
_API = {"type": "api", "base_url": "https://erp.example.com", "endpoints": [
    {"name": "ship", "path": "/ship", "method": "POST", "writable": True},
    {"name": "tracking", "path": "/tracking"},
]}
_SCREENS = {"shop": {
    "orders": {
        "connector": "shop", "read_query": "orders_get", "update_query": "orders_put", "insert_query": "orders_post",
        "row_click_screen": "customers",
        "dialog": {"tabs": [{"id": "main", "type": "form", "label": "Order"},
                            {"id": "lines", "type": "nested_table", "label": "Lines", "screen": "lines"}]},
        "actions": [
            {"id": "close", "type": "run_query", "query": "close_order"},
            {"id": "history", "type": "run_query", "query": "order_history"},
            {"id": "send", "type": "call_api", "connector": "erp", "endpoint": "ship"},
            {"id": "track", "type": "call_api", "connector": "erp", "endpoint": "tracking"},
            {"id": "price", "type": "call_plugin", "callable": "shop.pricing:j_reprice"},
            {"id": "all", "type": "call_action", "ref": "archive"},
        ],
    },
    "lines": {"connector": "shop", "read_query": "lines_get", "update_query": "lines_put"},
    "customers": {"connector": "shop", "read_query": "customers_get", "update_query": "customers_put"},
    "stock": {"connector": "shop", "read_query": "stock_get"},
}}
_ACTIONS = {"archive": {"label": "Archive", "steps": [{"id": "s1", "type": "call_plugin", "callable": "shop.archive:j_archive"}]}}
_MENUS = {"shop": {"label": "Shop", "items": [
    {"id": "sales", "label": "Sales"},
    {"id": "orders", "parent": "sales", "label": "Orders", "type": "screen", "target": "orders"},
    {"id": "customers", "parent": "sales", "label": "Customers", "type": "screen", "target": "customers"},
    {"id": "warehouse", "label": "Warehouse"},
    {"id": "stock", "parent": "warehouse", "label": "Stock", "type": "screen", "target": "stock"},
    {"id": "board", "label": "Board", "type": "dashboard", "target": "shop.board"},
    {"id": "load", "parent": "warehouse", "label": "Load", "type": "job", "target": "shop-load"},
    {"id": "jobs", "parent": "warehouse", "label": "Jobs", "type": "page", "target": "/nomaflow"},
    {"id": "site", "label": "Site", "type": "url", "target": "https://example.com"},
]}}
_DASHBOARDS = {"shop": {"board": {"label": "Board", "widgets": [
    {"type": "stat", "label": "Total", "connector": "shop", "query": "totals", "value_column": "N"}]}}}


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        connectors=_Registry({"shop": _SQL, "erp": _API}), screens=parse_screens({"screens": _SCREENS}),
        menus=parse_menus({"menus": _MENUS}), dashboards=parse_dashboards({"dashboards": _DASHBOARDS}),
        charts=None, actions=parse_actions({"actions": _ACTIONS}), explorer=None,
    )


@pytest.fixture
def access():
    state = _state()
    implied.set_source(lambda: access_of(state))
    yield access_of(state)
    implied.set_source(None)


def _user(*permissions: str) -> Principal:
    return Principal(id="1", username="u", permissions=permissions)


# ── what an entry needs ───────────────────────────────────────────────────────────────────

def test_a_screen_entry_reads_what_it_shows_and_changes_what_it_writes(access) -> None:
    need = access.needs[("shop", "orders")]
    assert need["read"] == (
        "api:erp:tracking",                      # an action that only reads
        "sql:shop:customers_get",                  # the screen a row click opens: its READ
        "sql:shop:lines_get",                      # the tab of its record form
        "sql:shop:order_history", "sql:shop:orders_get",
    )
    assert need["change"] == (
        "api:erp:ship", "plugin:shop.archive:j_archive", "plugin:shop.pricing:j_reprice",
        "sql:shop:close_order", "sql:shop:lines_put", "sql:shop:orders_post", "sql:shop:orders_put",
    )
    # the screen reached by a row click is changed through its OWN entry
    assert "sql:shop:customers_put" not in need["change"]
    assert access.chains[("shop", "orders")] == ("orders", "sales")


def test_the_other_kinds_of_entry(access) -> None:
    assert access.needs[("shop", "board")]["read"] == ("dashboard:shop.board", "sql:shop:totals")
    assert access.needs[("shop", "load")] == {"read": (), "change": ("job:shop-load",)}      # running it is never "read"
    assert access.needs[("shop", "jobs")]["read"] == ("nomaflow:view",)
    assert access.needs[("shop", "site")] == {"read": (), "change": ()}
    assert ("shop", "sales") not in access.needs      # a folder gives through its entries


# ── the check ─────────────────────────────────────────────────────────────────────────────

def test_a_folder_at_read_level_gives_its_screens_reads_and_nothing_that_writes(access) -> None:
    u = _user("menu:shop:sales")
    assert u.has_permission("sql:shop:orders_get") and u.has_permission("sql:shop:lines_get") and u.has_permission("sql:shop:customers_get")
    assert not u.has_permission("sql:shop:orders_put")
    assert not u.has_permission("plugin:shop.pricing:j_reprice")
    assert not u.has_permission("sql:shop:stock_get")       # another folder


def test_read_and_change_gives_the_writes_too(access) -> None:
    u = _user("menu:shop:sales", "edit:shop:sales")
    for p in ("sql:shop:orders_put", "sql:shop:lines_put", "sql:shop:close_order", "api:erp:ship", "plugin:shop.archive:j_archive", "sql:shop:customers_put"):
        assert u.has_permission(p), p
    assert not u.has_permission("sql:shop:something_else")


def test_change_on_one_entry_of_a_folder_that_is_read(access) -> None:
    u = _user("menu:shop:sales", "edit:shop:orders")
    assert u.has_permission("sql:shop:orders_put")
    assert u.has_permission("sql:shop:customers_get") and not u.has_permission("sql:shop:customers_put")


def test_the_nearest_rule_decides(access) -> None:
    # the folder is allowed, one entry is taken out of it
    u = _user("menu:shop:sales", "edit:shop:sales", "!menu:shop:customers")
    assert u.has_permission("sql:shop:orders_put")
    assert not u.has_permission("sql:shop:customers_put")
    assert u.has_permission("sql:shop:customers_get")       # still read: the orders' row click opens it
    # the folder is denied, one entry is given back
    u = _user("!menu:shop:sales", "menu:shop:orders")
    assert u.has_permission("sql:shop:orders_get") and not u.has_permission("sql:shop:customers_put")
    # change denied on one entry of a folder given in change
    u = _user("menu:shop:sales", "edit:shop:sales", "!edit:shop:orders")
    assert u.has_permission("sql:shop:orders_get") and not u.has_permission("sql:shop:orders_put")
    assert u.has_permission("sql:shop:customers_put")


def test_change_without_the_entry_gives_nothing(access) -> None:
    assert not _user("edit:shop:sales").has_permission("sql:shop:orders_put")
    assert not _user("edit:shop:sales").has_permission("sql:shop:orders_get")


def test_an_explicit_deny_of_the_permission_wins_over_a_menu_grant(access) -> None:
    u = _user("menu:shop:sales", "edit:shop:sales", "!sql:shop:close_order")
    assert u.has_permission("sql:shop:orders_put") and not u.has_permission("sql:shop:close_order")


def test_a_pattern_on_the_nodes_counts(access) -> None:
    u = _user("menu:shop:*")
    assert u.has_permission("sql:shop:stock_get") and u.has_permission("sql:shop:totals")
    assert not u.has_permission("sql:shop:orders_put")      # menu:shop:* is not edit:shop:*
    assert not u.has_permission("job:shop-load")            # reading never runs a job
    assert _user("menu:shop:*", "edit:shop:load").has_permission("job:shop-load")


def test_a_rule_on_the_whole_application_is_the_outermost_one(access) -> None:
    # the application in change, one folder read-only: the folder's own rule wins for what it holds
    u = _user("menu:shop:*", "edit:shop:*", "!edit:shop:sales")
    assert u.has_permission("sql:shop:orders_get") and not u.has_permission("sql:shop:orders_put")
    assert u.has_permission("job:shop-load")                 # another folder: still in change
    # the application in read, one folder hidden, one entry of it given back
    u = _user("menu:shop:*", "!menu:shop:sales", "menu:shop:customers")
    assert not u.has_permission("sql:shop:orders_get") and u.has_permission("sql:shop:customers_get")
    assert u.has_permission("sql:shop:stock_get")
    # the application in read, change on one folder (how the predefined roles are written)
    u = _user("menu:shop:*", "edit:shop:sales")
    assert u.has_permission("sql:shop:orders_put") and not u.has_permission("job:shop-load")


def test_what_the_browser_is_told(access) -> None:
    assert implied.implied(("menu:shop:warehouse",)) == ["nomaflow:view", "sql:shop:stock_get"]
    assert implied.implied(("menu:shop:warehouse", "edit:shop:warehouse")) == ["job:shop-load", "nomaflow:view", "sql:shop:stock_get"]
    assert implied.implied(("sql:shop:orders_get",)) == []
    got = implied.implied(("menu:shop:orders", "edit:shop:orders"))
    assert "sql:shop:orders_put" in got and "sql:shop:customers_put" not in got


def test_nothing_is_implied_without_a_running_application() -> None:
    implied.set_source(None)
    assert not _user("menu:shop:sales").has_permission("sql:shop:orders_get")
    assert implied.implied(("menu:shop:sales",)) == []


def test_a_source_that_fails_implies_nothing() -> None:
    def broken():
        raise RuntimeError("configuration being reloaded")

    implied.set_source(broken)
    try:
        assert not _user("menu:shop:sales").has_permission("sql:shop:orders_get")
    finally:
        implied.set_source(None)


def test_the_answer_follows_a_reload() -> None:
    state = _state()
    first = access_of(state)
    assert access_of(state) is first                    # asked again: the same answer, not rebuilt
    state.menus = parse_menus({"menus": {"shop": {"label": "Shop", "items": [
        {"id": "stock", "label": "Stock", "type": "screen", "target": "stock"}]}}})
    second = access_of(state)
    assert second is not first and list(second.needs) == [("shop", "stock")]
    assert build(state).needs == second.needs
