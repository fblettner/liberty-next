"""Menu ``screen`` leaves — the leaf targets a screen *id*; the runtime resolves it to the
screen's read query. So permission-gating must check ``sql:<connector>:<read_query>`` (resolved
from the designed screen), not ``sql:<connector>:<screen_id>``."""

from __future__ import annotations

from liberty.auth.principal import Principal
from liberty.dashboards import DashboardsFile
from liberty.menus import MenuItem
from liberty.screens.config import parse_screens
from liberty.web.menus import _keeper, _screen_perm


_SCREENS = parse_screens({"screens": {"jde": {
    # Two screens on the SAME read query — the case bare read-query targeting can't handle.
    "f0004_basic": {"connector": "jde", "read_query": "f0004_get", "label": "Address (basic)"},
    "f0004_full": {"connector": "jde", "read_query": "f0004_get", "label": "Address (full)"},
}}})


def test_screen_perm_resolves_read_query() -> None:
    assert _screen_perm(_SCREENS, "jde", "f0004_basic") == "sql:jde:f0004_get"
    assert _screen_perm(_SCREENS, "jde", "missing") is None
    assert _screen_perm(None, "jde", "f0004_basic") is None


def _keep(perms: tuple[str, ...], item: MenuItem) -> bool:
    p = Principal(id="u", username="u", permissions=perms)
    keep = _keeper(p, DashboardsFile(), "jde", _SCREENS)
    return keep(item, "jde")


def test_screen_leaf_kept_when_read_query_permitted() -> None:
    item = MenuItem(id="m", label="Addresses", type="screen", target="f0004_full")
    assert _keep(("sql:jde:f0004_get",), item) is True
    # No permission on the underlying read query → hidden.
    assert _keep(("sql:jde:other_get",), item) is False
    # First-class menu allow still surfaces it.
    assert _keep(("menu:jde:m",), item) is True
