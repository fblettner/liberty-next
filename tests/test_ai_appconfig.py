"""ai.toml — the assistant material an app ships (starters per app / screen), its merge with
the operator's [ai] starters, the entity rules for the apps installer, and the admin editor."""

from __future__ import annotations

import pytest

from liberty.ai.appconfig import AiFile, load_ai_config, merged_starters, parse_ai_config
from liberty.config_merge import flatten, resolve_path

DATA = {"ai": {"nomajde": {"starters": ["Who has no role?"], "screens": {"f0092": {"starters": ["Users never logged in"]}, "f0093": {}}},
               "nomasx1": {}}}


def test_parse_and_starters_keys() -> None:
    f = parse_ai_config(DATA)
    assert f.starters() == {"nomajde": ["Who has no role?"], "nomajde/f0092": ["Users never logged in"]}
    with pytest.raises(ValueError):
        parse_ai_config({"ai": {"nomajde": {"playbook": "not a field"}}})
    with pytest.raises(ValueError):
        parse_ai_config({"ai": {"nomajde": {"playbooks": {"x": {"title": "no body"}}}}})
    f = parse_ai_config({"ai": {"nomajde": {"playbooks": {"x": {"title": "T", "body": "B"}}}, "nomasx1": {"playbooks": {"y": {"title": "U", "body": "C", "description": "d"}}}}})
    assert [(a, i, p.title) for a, i, p in f.playbooks()] == [("nomajde", "x", "T"), ("nomasx1", "y", "U")]


def test_load_missing_file_is_empty(tmp_path) -> None:
    assert load_ai_config(tmp_path / "ai.toml").starters() == {}
    (tmp_path / "ai.toml").write_text('[ai.shop]\nstarters = ["Revenue this month?"]\n')
    assert load_ai_config(tmp_path / "ai.toml").starters() == {"shop": ["Revenue this month?"]}


def test_operator_starters_win_over_the_bundled_ones() -> None:
    f = parse_ai_config(DATA)
    merged = merged_starters(f, {"*": ["What can you do?"], "nomajde": ["Custom question"], "nomajde/f0092": []})
    assert merged == {"*": ["What can you do?"], "nomajde": ["Custom question"]}
    assert merged_starters(None, {"*": ["x"]}) == {"*": ["x"]}
    assert merged_starters(AiFile(), None) == {}


def test_merge_entities_are_the_app_blocks() -> None:
    flat = flatten(DATA, "ai.toml")
    assert set(flat) == {("ai", "nomajde"), ("ai", "nomasx1")}
    assert flat[("ai", "nomajde")]["starters"] == ["Who has no role?"]
    assert resolve_path("ai", "nomajde", "ai.toml") == (["ai"], "nomajde")


def test_starter_groups_by_key() -> None:
    f = parse_ai_config({"ai": {"shop": {"starters": ["flat"], "groups": [{"label": "Sales", "starters": ["a", "b"]}],
                                         "screens": {"orders": {"groups": [{"label": "Orders", "starters": ["c"]}]}, "items": {"starters": ["d"]}}}}})
    assert f.starter_groups() == {"shop": [{"label": "Sales", "starters": ["a", "b"]}], "shop/orders": [{"label": "Orders", "starters": ["c"]}]}
    assert f.starters() == {"shop": ["flat"], "shop/items": ["d"]}


def test_starters_follow_the_language() -> None:
    from liberty.ai.appconfig import merged_starters, parse_ai_config
    f = parse_ai_config({"ai": {"demo": {
        "starters": ["Who sold the most?"], "groups": [{"label": "Sales", "starters": ["Who sold the most?"]}],
        "screens": {"sales": {"starters": ["Sum this list"]}, "periods": {"starters": ["Which periods are open?"]}},
        "l": {"fr": {"starters": ["Qui a le plus vendu ?"], "groups": [{"label": "Ventes", "starters": ["Qui a le plus vendu ?"]}], "screens": {"sales": {"starters": ["Additionner cette liste"]}}}},
    }}})
    assert f.starters() == {"demo": ["Who sold the most?"], "demo/sales": ["Sum this list"], "demo/periods": ["Which periods are open?"]}
    fr = f.starters("fr-FR")
    assert fr["demo"] == ["Qui a le plus vendu ?"] and fr["demo/sales"] == ["Additionner cette liste"] and fr["demo/periods"] == ["Which periods are open?"]   # untranslated screen keeps English
    assert f.starter_groups("fr")["demo"][0]["label"] == "Ventes" and f.starter_groups("de") == f.starter_groups()
    assert merged_starters(f, {"*": ["Hello"]}, "fr")["demo"] == ["Qui a le plus vendu ?"]
