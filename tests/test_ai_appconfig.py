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
        parse_ai_config({"ai": {"nomajde": {"playbook": "not yet"}}})


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
