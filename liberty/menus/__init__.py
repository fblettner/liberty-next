"""App navigation menus — the v2 form of v1's ``ly_menus`` (+ ``ly_menus_l`` / ``ly_menus_filters``).

A menu belongs to an *app* (a connector); ``config/menus.toml`` holds one ``[menus.<app>]``
per app, each a flat list of items linked by ``parent`` (the backend builds the tree).
Items are folders (no ``target``) or leaves pointing at a connector query/endpoint; the
tree is resolved in the request's language and filtered to what the caller may run.
"""

from __future__ import annotations

from liberty.menus.config import (
    AppMenu,
    MenuItem,
    MenusFile,
    build_menu_tree,
    load_menus,
    parse_menus,
)

__all__ = [
    "AppMenu",
    "MenuItem",
    "MenusFile",
    "build_menu_tree",
    "load_menus",
    "parse_menus",
]
