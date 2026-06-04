"""Integrity engine — broken-reference detection across the loaded config."""

from __future__ import annotations

from types import SimpleNamespace

from liberty.menus.config import parse_menus
from liberty.screens.config import parse_screens
from liberty.web.integrity import check_integrity


class _Cfg:
    def __init__(self, names: list[str], *, home: str | None = None) -> None:
        self._names = names
        self.home = home

    def iter_named_queries(self):
        return [(n, object()) for n in self._names]


class _Registry:
    def __init__(self, conns: dict[str, list[str]], *, homes: dict[str, str] | None = None) -> None:
        self._conns = {n: SimpleNamespace(config=_Cfg(q, home=(homes or {}).get(n))) for n, q in conns.items()}
        self.dictionary = None

    def names(self):
        return list(self._conns)

    def get(self, n):
        return self._conns[n]


def _state(*, conns, screens=None, menus=None, homes=None):
    return SimpleNamespace(
        connectors=_Registry(conns, homes=homes),
        screens=parse_screens({"screens": screens}) if screens is not None else None,
        menus=parse_menus({"menus": menus}) if menus is not None else None,
        dashboards=None,
        charts=None,
    )


def _cats(issues):
    return [(i.severity, i.category, i.message) for i in issues]


def test_detects_broken_menu_and_screen_query() -> None:
    state = _state(
        conns={"jde": ["f0004_get", "f0004_put"]},
        screens={"jde": {"f0004": {"connector": "jde", "read_query": "f0004_get", "update_query": "missing_put"}}},
        menus={"jde": {"items": [
            {"id": "ok", "label": "OK", "type": "screen", "target": "f0004"},
            {"id": "bad", "label": "Bad", "type": "screen", "target": "ghost_screen"},
            {"id": "badq", "label": "BadQ", "type": "query", "target": "no_such_get"},
        ]}},
    )
    issues = check_integrity(state)
    cats = _cats(issues)
    # broken menu → screen, broken menu → query, screen missing update_query
    assert any(c[1] == "Broken menu target" and "ghost_screen" in c[2] for c in cats)
    assert any(c[1] == "Broken menu target" and "no_such_get" in c[2] for c in cats)
    assert any(c[1] == "Missing query" and "missing_put" in c[2] for c in cats)
    # the good menu item produces no issue
    assert not any("OK" in c[2] for c in cats)


def test_clean_config_has_no_errors() -> None:
    state = _state(
        conns={"jde": ["f0004_get"]},
        screens={"jde": {"f0004": {"connector": "jde", "read_query": "f0004_get"}}},
        menus={"jde": {"items": [{"id": "u", "label": "U", "type": "screen", "target": "f0004"}]}},
    )
    issues = check_integrity(state)
    assert not [i for i in issues if i.severity == "error"]


def test_unused_connector_and_orphan_screen_warn() -> None:
    state = _state(
        conns={"jde": ["f0004_get"], "ais_backup": ["ping_get"]},
        screens={"jde": {
            "f0004": {"connector": "jde", "read_query": "f0004_get"},
            "lonely": {"connector": "jde", "read_query": "f0004_get"},  # reachable via the query menu? no — different sid, same rq
        }},
        menus={"jde": {"items": [{"id": "u", "label": "U", "type": "screen", "target": "f0004"}]}},
    )
    issues = check_integrity(state)
    cats = _cats(issues)
    assert any(c[0] == "warning" and c[1] == "Unused connector" and "ais_backup" in c[2] for c in cats)
    assert any(c[0] == "warning" and c[1] == "Orphan screen" and "lonely" in c[2] for c in cats)


def test_broken_home_detected() -> None:
    state = _state(
        conns={"jde": ["f0004_get"]},
        homes={"jde": "no_such_item"},
        menus={"jde": {"items": [{"id": "u", "label": "U", "type": "screen", "target": "x"}]}},
        screens={"jde": {"x": {"connector": "jde", "read_query": "f0004_get"}}},
    )
    issues = check_integrity(state)
    assert any(i.category == "Broken home" and "no_such_item" in i.message for i in issues)
