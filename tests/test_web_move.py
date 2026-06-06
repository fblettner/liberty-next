"""Move a query / lookup / table between connectors — pure-function tests on
:func:`liberty.web.move.move_query` against a tmp_path of TOML files. Each test asserts both the
connectors.toml relocation AND the cross-file reference rewrite (or the manual-ref report when a
screen's shared connector can't be flipped)."""

from __future__ import annotations

import textwrap
import tomllib
from pathlib import Path

import pytest

from liberty.web.move import MoveError, move_query


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def _paths(tmp: Path) -> dict[str, Path]:
    return {k: tmp / f"{k}.toml" for k in ("connectors", "screens", "menus", "dictionary", "charts", "dashboards", "actions")}


def _load(p: Path) -> dict:
    return tomllib.loads(p.read_text(encoding="utf-8"))


def _base_connectors(tmp: Path) -> Path:
    """nomajde owns a lookup + a couple of queries + a table; jdedwards is the (existing) target."""
    p = tmp / "connectors.toml"
    _write(p, """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        [connectors.nomajde]
        type = "sql"
        pool = "default"

        [[connectors.nomajde.lookups]]
        name = "get_product_code_get"
        sql = "SELECT DRKY AS ky, DRDL01 AS dl01 FROM F0005"

        [[connectors.nomajde.queries]]
        name = "users_get"
        sql = "SELECT 1 AS id"

        [[connectors.nomajde.queries]]
        name = "users_put"
        sql = "UPDATE users SET name = :name WHERE id = :id"
        writable = true

        [[connectors.nomajde.tables]]
        name = "f0004"
        [connectors.nomajde.tables.get]
        sql = "SELECT 1 AS id"

        [connectors.jdedwards]
        type = "sql"
        pool = "default"
    """)
    return p


def test_move_lookup_relocates_and_flips_dictionary_and_menu(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _base_connectors(tmp_path)
    _write(paths["dictionary"], """
        [lookups.prod]
        connector = "nomajde"
        query = "get_product_code_get"
        value = "KY"
        label = "DL01"
    """)
    _write(paths["menus"], """
        [menus.nomajde]
        label = "JDE"

        [[menus.nomajde.items]]
        id = "p"
        label = "Products"
        type = "query"
        connector = "nomajde"
        target = "get_product_code_get"
    """)

    result = move_query(
        "lookup", "get_product_code_get", "nomajde", "jdedwards",
        connectors_path=paths["connectors"], screens_path=paths["screens"],
        menus_path=paths["menus"], dictionary_path=paths["dictionary"],
    )

    conns = _load(paths["connectors"])["connectors"]
    assert not conns["nomajde"].get("lookups")  # left nomajde
    assert any(lk["name"] == "get_product_code_get" for lk in conns["jdedwards"]["lookups"])  # arrived
    assert _load(paths["dictionary"])["lookups"]["prod"]["connector"] == "jdedwards"
    assert _load(paths["menus"])["menus"]["nomajde"]["items"][0]["connector"] == "jdedwards"
    assert not result.manual_refs
    assert result.total_refs() >= 3


def test_move_query_flips_dedicated_screen_but_reports_shared_screen(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _base_connectors(tmp_path)
    _write(paths["screens"], """
        # dedicated: reads only users_get → safe to flip
        [screens.nomajde.dedicated]
        connector = "nomajde"
        read_query = "users_get"

        # shared: also updates users_put (which stays in nomajde) → can't flip, must report
        [screens.nomajde.shared]
        connector = "nomajde"
        read_query = "users_get"
        update_query = "users_put"
    """)

    result = move_query(
        "query", "users_get", "nomajde", "jdedwards",
        connectors_path=paths["connectors"], screens_path=paths["screens"],
        menus_path=paths["menus"], dictionary_path=paths["dictionary"],
    )

    screens = _load(paths["screens"])["screens"]["nomajde"]
    assert screens["dedicated"]["connector"] == "jdedwards"   # flipped
    assert screens["shared"]["connector"] == "nomajde"        # left alone
    assert any("shared" in m.where for m in result.manual_refs)
    assert all("dedicated" not in m.where for m in result.manual_refs)
    # the moved query is gone from nomajde, present on jdedwards
    conns = _load(paths["connectors"])["connectors"]
    assert {q["name"] for q in conns["nomajde"]["queries"]} == {"users_put"}
    assert {q["name"] for q in conns["jdedwards"]["queries"]} == {"users_get"}


def test_move_table_relocates_entry_and_flips_screen_using_a_crud_slot(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _base_connectors(tmp_path)
    _write(paths["screens"], """
        [screens.nomajde.f0004]
        connector = "nomajde"
        read_query = "f0004_get"
    """)
    result = move_query(
        "table", "f0004", "nomajde", "jdedwards",
        connectors_path=paths["connectors"], screens_path=paths["screens"],
        menus_path=paths["menus"], dictionary_path=paths["dictionary"],
    )
    conns = _load(paths["connectors"])["connectors"]
    assert not conns["nomajde"].get("tables")
    assert any(t["name"] == "f0004" for t in conns["jdedwards"]["tables"])
    assert _load(paths["screens"])["screens"]["nomajde"]["f0004"]["connector"] == "jdedwards"
    assert not result.manual_refs


def test_move_rejects_collision_and_missing(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    p = _base_connectors(tmp_path)
    # give jdedwards a colliding lookup name
    body = p.read_text(encoding="utf-8").replace(
        '[connectors.jdedwards]\ntype = "sql"\npool = "default"\n',
        '[connectors.jdedwards]\ntype = "sql"\npool = "default"\n\n'
        '[[connectors.jdedwards.lookups]]\nname = "get_product_code_get"\nsql = "SELECT 1"\n',
    )
    p.write_text(body, encoding="utf-8")
    with pytest.raises(MoveError, match="already exists"):
        move_query("lookup", "get_product_code_get", "nomajde", "jdedwards",
                   connectors_path=paths["connectors"], screens_path=paths["screens"],
                   menus_path=paths["menus"], dictionary_path=paths["dictionary"])

    with pytest.raises(MoveError, match="not found"):
        move_query("lookup", "no_such_lookup", "nomajde", "jdedwards",
                   connectors_path=paths["connectors"], screens_path=paths["screens"],
                   menus_path=paths["menus"], dictionary_path=paths["dictionary"])


def test_move_rejects_same_connector_and_missing_target(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _base_connectors(tmp_path)
    with pytest.raises(MoveError, match="same"):
        move_query("lookup", "get_product_code_get", "nomajde", "nomajde",
                   connectors_path=paths["connectors"], screens_path=paths["screens"],
                   menus_path=paths["menus"], dictionary_path=paths["dictionary"])
    with pytest.raises(MoveError, match="does not exist"):
        move_query("lookup", "get_product_code_get", "nomajde", "ghost",
                   connectors_path=paths["connectors"], screens_path=paths["screens"],
                   menus_path=paths["menus"], dictionary_path=paths["dictionary"])
