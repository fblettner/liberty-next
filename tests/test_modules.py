"""Module catalogues (catalogue v2): the format, its validation and the loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from liberty.modules import ModuleError, find_module, load_modules

MODULE = """
[[modules]]
id = "address_book"
label = "Address Book"
group = "JDE"
connector = "jdedwards"
app = "nomajde"
schema = "#SCHEMA.DTA#"
menu = { label = "Address Book", icon = "users", l = { fr = "Carnet d'adresses" } }

[[modules.screens]]
id = "f0101"
table = "F0101"
label = "Address Book"
key = ["AN8"]
columns = ["AN8", "ALPH", "AT1"]
[[modules.screens.tabs]]
label = "General"
fields = ["AN8", "ALKY", "ALPH"]
[[modules.screens.details]]
screen = "f0111"
label = "Who's Who"
on = ["AN8"]

[[modules.screens]]
id = "f0111"
table = "F0111"
label = "Who's Who"
key = ["AN8", "IDLN"]
[[modules.screens.details]]
screen = "f0115"
label = "Phones"
on = ["AN8", { parent = "IDLN", child = "IDLN" }]

[[modules.screens]]
id = "f0115"
table = "F0115"
label = "Phones"
prefix = "WP"
key = ["AN8", "IDLN", "RCK7"]
"""


def _write(tmp: Path, body: str, name: str = "jde.toml") -> Path:
    (tmp / name).write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp


def test_a_module_loads_with_its_graph(tmp_path: Path) -> None:
    [m] = load_modules(_write(tmp_path, MODULE))
    assert m.id == "address_book" and m.menu.l == {"fr": "Carnet d'adresses"}
    ab, ww, wp = m.screens
    assert m.screen_schema(ab) == "#SCHEMA.DTA#" and wp.prefix == "WP"
    assert [(p.parent, p.child) for p in ww.details[0].on] == [("AN8", "AN8"), ("IDLN", "IDLN")]
    assert m.detail_ids() == {"f0111", "f0115"}
    assert [s.id for s in m.menu_screens()] == ["f0101"]          # details get no menu item of their own
    assert ab.items() == ["AN8", "ALPH", "AT1", "ALKY"]
    assert find_module(tmp_path, "address_book") is not None and find_module(tmp_path, "nope") is None
    assert load_modules(tmp_path, "other") == [] and load_modules(tmp_path / "missing") == []


def test_presets_and_modules_share_a_file(tmp_path: Path) -> None:
    from liberty.presets import load_presets
    _write(tmp_path, '[[presets]]\nid = "p"\nlabel = "P"\n[[presets.tables]]\nname = "F0101"\n' + MODULE)
    assert [p["id"] for p in load_presets(tmp_path)] == ["p"]
    assert [m.id for m in load_modules(tmp_path)] == ["address_book"]


@pytest.mark.parametrize("change, message", [
    (('screen = "f0111"', 'screen = "f9999"'), "not a screen of the module"),
    (('on = ["AN8"]', 'on = ["ALPH"]'), "not in its key"),
    (('id = "f0115"', 'id = "f0111"'), "listed twice"),
    (('key = ["AN8"]\ncolumns', 'key = []\ncolumns'), "key"),
    (('columns = ["AN8", "ALPH", "AT1"]', 'columns = ["AN8", "an8"]'), "not data items"),
    (('label = "Phones"\nprefix', 'label = "Phones"\nwrites = true\nprefix'), "writes"),   # display first: no write option
])
def test_invalid_modules_are_refused_with_their_name(tmp_path: Path, change: tuple[str, str], message: str) -> None:
    body = MODULE.replace(*change, 1)
    assert body != MODULE
    with pytest.raises(ModuleError, match=message) as exc:
        load_modules(_write(tmp_path, body))
    assert "address_book" in str(exc.value) and "jde.toml" in str(exc.value)


def test_detail_cycles_and_duplicate_modules_are_refused(tmp_path: Path) -> None:
    cyclic = MODULE + '[[modules.screens.details]]\nscreen = "f0101"\nlabel = "Back"\non = ["AN8"]\n'
    with pytest.raises(ModuleError, match="cycle"):
        load_modules(_write(tmp_path, cyclic))
    (tmp_path / "jde.toml").unlink()
    _write(tmp_path, MODULE, "a.toml")
    _write(tmp_path, MODULE, "b.toml")
    with pytest.raises(ModuleError, match="also in"):
        load_modules(tmp_path)
