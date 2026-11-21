"""The dependency walk (packages, the screens' history, clone-with-deps): what a seed needs to work elsewhere."""

from __future__ import annotations

from types import SimpleNamespace

from liberty.connectors.config import parse_connectors
from liberty.screens.config import parse_screens
from liberty.web.dependencies import Seed, collect_dependencies


class _Registry:
    def __init__(self, connectors: dict) -> None:
        self._conns = {n: SimpleNamespace(config=c) for n, c in parse_connectors({"connectors": connectors}).connectors.items()}
        self.dictionary = None

    def names(self):
        return list(self._conns)

    def get(self, n):
        return self._conns[n]


def _state(*, connectors: dict, screens: dict) -> SimpleNamespace:
    return SimpleNamespace(connectors=_Registry(connectors), screens=parse_screens({"screens": screens}),
                           menus=None, dashboards=None, charts=None, actions=None)


_API = {"type": "api", "base_url": "https://erp.example.com", "endpoints": [
    {"name": "addresses", "path": "/addresses", "rows_field": "rows"},
    {"name": "address_change", "path": "/addresses/change", "method": "POST", "writable": True},
    {"name": "address_add", "path": "/addresses/add", "method": "POST", "writable": True},
]}
_SQL = {"type": "sql", "queries": [{"name": "f0101_get", "sql": "SELECT 1"}]}


def _kinds(manifest) -> list[tuple[str, str | None, str]]:
    return sorted((d.kind, d.scope, d.name) for d in manifest.deps)


def _missing(manifest) -> list[tuple[str, str | None, str]]:
    # (the stand-in registry holds no pool: a SQL connector's pool is not what these tests are about)
    return [(m["kind"], m["scope"], m["name"]) for m in manifest.missing if m["kind"] != "pool"]


def test_a_screen_that_reads_an_api_endpoint_carries_the_endpoint_not_a_missing_query() -> None:
    state = _state(connectors={"erp": _API}, screens={"app": {"book": {
        "connector": "erp", "read_query": "addresses", "update_query": "address_change", "insert_query": "address_add"}}})
    manifest = collect_dependencies(state, [Seed("screen", "book", "app")])
    assert manifest.missing == []
    assert _kinds(manifest) == [
        ("api_endpoint", "erp", "address_add"), ("api_endpoint", "erp", "address_change"), ("api_endpoint", "erp", "addresses"),
        ("connector", None, "erp"), ("screen", "app", "book"),
    ]


def test_a_screen_read_by_a_query_that_saves_through_another_connectors_endpoints() -> None:
    """``save_connector``: the read is a SQL query of the screen's connector, the saves are endpoints of another."""
    state = _state(connectors={"jde": _SQL, "erp": _API}, screens={"app": {"book": {
        "connector": "jde", "read_query": "f0101_get", "save_connector": "erp", "update_query": "address_change"}}})
    manifest = collect_dependencies(state, [Seed("screen", "book", "app")])
    assert _missing(manifest) == []
    assert _kinds(manifest) == [
        ("api_endpoint", "erp", "address_change"), ("connector", None, "erp"), ("connector", None, "jde"),
        ("query", "jde", "f0101_get"), ("screen", "app", "book"),
    ]


def test_an_endpoint_that_does_not_exist_is_missing_as_an_endpoint() -> None:
    state = _state(connectors={"erp": _API}, screens={"app": {"book": {"connector": "erp", "read_query": "gone"}}})
    manifest = collect_dependencies(state, [Seed("screen", "book", "app")])
    assert _missing(manifest) == [("api_endpoint", "erp", "gone")]


def test_a_sql_query_that_does_not_exist_is_still_a_missing_query() -> None:
    state = _state(connectors={"jde": _SQL}, screens={"app": {"book": {"connector": "jde", "read_query": "gone_get"}}})
    manifest = collect_dependencies(state, [Seed("screen", "book", "app")])
    assert _missing(manifest) == [("query", "jde", "gone_get")]
