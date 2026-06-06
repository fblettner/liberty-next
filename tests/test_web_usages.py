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


def _state(*, menus: dict | None = None, screens: dict | None = None, actions: dict | None = None) -> SimpleNamespace:
    from liberty.actions import parse_actions
    return SimpleNamespace(
        menus=parse_menus({"menus": menus}) if menus is not None else None,
        screens=parse_screens({"screens": screens}) if screens is not None else None,
        connectors=None,
        charts=None,
        dashboards=None,
        actions=parse_actions({"actions": actions}) if actions is not None else None,
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


def test_query_usages_descends_into_column_groups_and_embedded_nested_forms() -> None:
    """A query referenced only by a column group's write-back query, or by a nested form embedded
    in a form tab, must NOT look unused — the reverse walk descends into the same nested screen
    structures integrity validates. Regression for ``f00921`` (used by ``f0092``'s columns) showing
    "0 references, safe to delete"."""
    screens = {
        "nomajde": {
            "f0092": {
                "connector": "nomajde",
                "read_query": "f0092_get",
                "column_groups": [
                    {"id": "extra", "update_query": "f00921_put", "insert_query": "f00921_post"},
                ],
                "dialog": {
                    "tabs": [
                        {"id": "main", "type": "form", "nested_forms": [
                            {"id": "child", "type": "nested_form", "read_query": "f00922_get",
                             "delete_query": "f00922_delete"},
                        ]},
                    ],
                },
            },
        },
    }
    state = _state(screens=screens)
    assert any(u.type == "column_group_update_query"
               for u in find_usages(state, kind="query", name="f00921_put", scope="nomajde"))
    assert any(u.type == "column_group_insert_query"
               for u in find_usages(state, kind="query", name="f00921_post", scope="nomajde"))
    assert any(u.type == "nested_form_read_query"
               for u in find_usages(state, kind="query", name="f00922_get", scope="nomajde"))
    assert any(u.type == "nested_form_delete_query"
               for u in find_usages(state, kind="query", name="f00922_delete", scope="nomajde"))


def test_screen_usages_finds_menu_via_read_query() -> None:
    """Searching the SCREEN (id ``f0004``) must surface the menu that targets its read query
    ``f0004_get`` — the case the operator reported as returning nothing."""
    state = _state(menus=_MENUS, screens=_SCREENS)
    usages = find_usages(state, kind="screen", name="f0004", scope="jde")
    assert any(u.type == "menu_target_screen" for u in usages)


def test_screen_usages_finds_navigate_action_pinning_it() -> None:
    """A row-menu navigate that pins a specific screen (NavigateAction.screen) must show up in
    that screen's usages — so renaming/auditing the screen surfaces the drill."""
    screens = {
        "jde": {
            "f0004": {"connector": "jde", "read_query": "f0004_get"},
            "f0004_objects": {"connector": "jde", "read_query": "f0004_objects_get"},
        },
    }
    # f0004's row menu drills into the f0004_objects SCREEN specifically.
    screens["jde"]["f0004"]["row_menu"] = [
        {"id": "objs", "label": "Objects", "type": "navigate", "to": "f0004_objects_get", "screen": "f0004_objects"},
    ]
    state = _state(screens=screens)
    usages = find_usages(state, kind="screen", name="f0004_objects", scope="jde")
    assert any(u.type == "action_navigate_screen" for u in usages)


def test_menu_target_query_respects_connector() -> None:
    """A menu item on a different connector must NOT match."""
    menus = {"jde": {"items": [{"id": "u", "label": "U", "type": "query", "connector": "other", "target": "f0004_get"}]}}
    state = _state(menus=menus, screens=_SCREENS)
    usages = find_usages(state, kind="query", name="f0004_get", scope="jde")
    assert not any(u.type == "menu_target_query" for u in usages)


def test_action_usages_finds_screen_call_action_and_composing_action() -> None:
    """find_usages(kind="action") surfaces both a screen's `call_action` and another shared action
    whose steps call it (composition) — what the Actions builder's Find-usages reads."""
    state = _state(
        screens={"jde": {"f1": {"connector": "jde", "read_query": "g",
                                 "on_insert": [{"id": "a", "type": "call_action", "ref": "create_role"}]}}},
        actions={
            "create_role": {"steps": []},
            "wrapper": {"steps": [{"id": "w", "type": "call_action", "ref": "create_role"}]},
        },
    )
    usages = find_usages(state, kind="action", name="create_role")
    labels = [u.label for u in usages]
    assert any("jde.f1" in lbl and "on_insert" in lbl for lbl in labels)
    assert any("wrapper" in lbl for lbl in labels)
    # an unreferenced action has no usages (safe to delete)
    assert find_usages(state, kind="action", name="wrapper") == []
