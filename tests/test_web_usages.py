"""Find-usages coverage — focused on the menu links a v1 migration leaves behind.

A menu opens a screen by linking to its *read query* (``<base>_get``), not the screen id. So
find-usages must surface that menu both when searching the query AND when searching the screen —
otherwise an operator sees "0 usages" on something that's wired into the sidebar.
"""

from __future__ import annotations

from types import SimpleNamespace

from liberty.menus.config import parse_menus
from liberty.screens.config import parse_screens
from liberty.web.usages import find_usages


def _state(*, menus: dict | None = None, screens: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        menus=parse_menus({"menus": menus}) if menus is not None else None,
        screens=parse_screens({"screens": screens}) if screens is not None else None,
        connectors=None,
        charts=None,
        dashboards=None,
    )


_MENUS = {
    "jde": {
        "items": [
            {"id": "users", "label": "Users", "type": "query", "target": "f0004_get"},
        ],
    },
}
_SCREENS = {
    "jde": {
        "f0004": {"connector": "jde", "read_query": "f0004_get"},
    },
}


def test_query_usages_includes_menu_target() -> None:
    state = _state(menus=_MENUS, screens=_SCREENS)
    usages = find_usages(state, kind="query", name="f0004_get", scope="jde")
    kinds = {u.type for u in usages}
    assert "menu_target_query" in kinds
    assert "screen_read_query" in kinds  # the screen reads it too


def test_screen_usages_finds_menu_via_read_query() -> None:
    """Searching the SCREEN (id ``f0004``) must surface the menu that targets its read query
    ``f0004_get`` — the case the operator reported as returning nothing."""
    state = _state(menus=_MENUS, screens=_SCREENS)
    usages = find_usages(state, kind="screen", name="f0004", scope="jde")
    assert any(u.type == "menu_target_screen" for u in usages)


def test_menu_target_query_respects_connector() -> None:
    """A menu item on a different connector must NOT match."""
    menus = {"jde": {"items": [{"id": "u", "label": "U", "type": "query", "connector": "other", "target": "f0004_get"}]}}
    state = _state(menus=menus, screens=_SCREENS)
    usages = find_usages(state, kind="query", name="f0004_get", scope="jde")
    assert not any(u.type == "menu_target_query" for u in usages)
