from __future__ import annotations

import textwrap
import tomllib

import pytest

from liberty.menus import AppMenu, MenuItem, build_menu_tree, load_menus, parse_menus


def test_menu_item_label_for_and_folder_vs_leaf() -> None:
    leaf = MenuItem(id="users", label="Users", l={"fr": "Utilisateurs"}, type="query", target="users_get")
    assert leaf.label_for("fr") == "Utilisateurs"
    assert leaf.label_for("de") == "Users" and leaf.label_for(None) == "Users"  # no translation → default
    folder = MenuItem(id="sec", label="Security")
    assert folder.type is None
    # a folder can't carry target/connector/params; a leaf needs a target; extra keys rejected
    with pytest.raises(Exception):
        MenuItem(id="bad", label="x", target="t")  # target without type → not a leaf, not a clean folder
    with pytest.raises(Exception):
        MenuItem(id="bad", label="x", type="query")  # type without target
    with pytest.raises(Exception):
        MenuItem(id="bad", label="x", connector="c")  # folder (no type) carrying a connector
    with pytest.raises(Exception):
        MenuItem(id="x", label="x", typo=1)  # type: ignore[call-arg]
    # a leaf may carry params + connector
    assert MenuItem(id="ok", label="x", type="query", target="t", connector="c", params={"a": 1}).params == {"a": 1}


def test_app_menu_validates_ids_and_parents() -> None:
    AppMenu(items=[MenuItem(id="a", label="A"), MenuItem(id="b", label="B", parent="a")])  # ok
    with pytest.raises(Exception):  # duplicate id
        AppMenu(items=[MenuItem(id="a", label="A"), MenuItem(id="a", label="A2")])
    with pytest.raises(Exception):  # dangling parent
        AppMenu(items=[MenuItem(id="b", label="B", parent="nope")])
    with pytest.raises(Exception):  # parent cycle
        AppMenu(items=[MenuItem(id="a", label="A", parent="b"), MenuItem(id="b", label="B", parent="a")])


def _menu() -> AppMenu:
    return AppMenu(
        label="Demo App",
        items=[
            MenuItem(id="sec", label="Security", l={"fr": "Sécurité"}, icon="shield"),
            MenuItem(id="sec.users", parent="sec", label="Users", l={"fr": "Utilisateurs"}, type="query", target="users_get"),
            MenuItem(id="sec.roles", parent="sec", label="Roles", type="query", target="roles_get"),
            MenuItem(id="sec.admin_only", parent="sec", label="Admin Tools", type="query", target="admin_get", roles=["admin"]),
            MenuItem(id="empty", label="Empty Folder"),  # no children → must collapse away
            MenuItem(id="ext", label="Things", type="endpoint", target="list_things", connector="other_api"),
        ],
    )


def test_build_menu_tree_nests_resolves_labels_and_drops_empty_folders() -> None:
    tree = build_menu_tree(_menu(), app="demo", language="fr")
    assert [n["id"] for n in tree] == ["sec", "ext"]  # "empty" folder dropped (no children)
    sec = tree[0]
    assert sec["label"] == "Sécurité" and sec["icon"] == "shield" and "type" not in sec
    assert [c["id"] for c in sec["items"]] == ["sec.users", "sec.roles", "sec.admin_only"]
    users = sec["items"][0]
    assert users == {"id": "sec.users", "label": "Utilisateurs", "type": "query", "connector": "demo", "target": "users_get"}
    assert sec["items"][1]["connector"] == "demo"  # default → the app
    ext = tree[1]
    assert ext == {"id": "ext", "label": "Things", "type": "endpoint", "connector": "other_api", "target": "list_things"}


def test_build_menu_tree_prunes_with_keep_and_collapses_now_empty_folders() -> None:
    # keep only the "users" leaf — "sec" survives (has a kept child), "ext" drops, and so does
    # "sec.roles"/"sec.admin_only".
    tree = build_menu_tree(_menu(), app="demo", keep=lambda item, conn: item.target == "users_get")
    assert [n["id"] for n in tree] == ["sec"]
    assert [c["id"] for c in tree[0]["items"]] == ["sec.users"]
    # keep nothing → empty tree (every folder collapses)
    assert build_menu_tree(_menu(), app="demo", keep=lambda item, conn: False) == []


def test_load_and_parse_menus_roundtrip(tmp_path) -> None:
    raw = textwrap.dedent(
        """
        [menus.nomasx1]
        label = "Rights, licenses and SOD"

        [[menus.nomasx1.items]]
        id = "security"
        label = "Security"
        l.fr = "Sécurité"

        [[menus.nomasx1.items]]
        id = "security.users"
        parent = "security"
        label = "Users"
        type = "query"
        target = "security_users_get"
        """
    )
    f = tmp_path / "menus.toml"
    f.write_text(raw)
    m = load_menus(f)
    assert list(m.menus) == ["nomasx1"]
    app = m.menus["nomasx1"]
    assert app.label == "Rights, licenses and SOD" and len(app.items) == 2
    tree = build_menu_tree(app, app="nomasx1", language="fr")
    assert tree[0]["label"] == "Sécurité"
    assert tree[0]["items"][0]["target"] == "security_users_get"
    # a missing file → an empty menu set
    assert load_menus(tmp_path / "nope.toml").menus == {}
    # extra top-level keys / bad shapes rejected
    with pytest.raises(Exception):
        parse_menus({"menus": {"x": {"bogus": 1}}})
    with pytest.raises(Exception):
        parse_menus(tomllib.loads("[menus.x]\nitems = [{ id = 'a', label = 'A', type = 'frame', target = 't' }]"))
