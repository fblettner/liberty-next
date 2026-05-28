from __future__ import annotations

from pathlib import Path

import pytest

from liberty.theme import (
    DEFAULT_PRESET,
    PRESETS,
    ThemeConfig,
    load_theme,
    parse_theme,
    preset_choices,
    resolve_theme,
)


def test_default_theme_resolves_to_liberty_blue() -> None:
    r = resolve_theme(ThemeConfig())
    assert r["preset"] == DEFAULT_PRESET
    assert r["app_name"] is None
    assert r["vars"]["blue-main"] == "#007AFF"
    # the derived accent family is present
    assert set(r["vars"]) >= {"blue-main", "blue-bg", "blue-bg-hover", "blue-border", "shadow-focus"}


def test_preset_primary_drives_accent() -> None:
    r = resolve_theme(ThemeConfig(preset="ocean"))
    assert r["vars"]["blue-main"] == PRESETS["ocean"]["primary"]


def test_primary_color_overrides_preset_and_derives_rgba() -> None:
    r = resolve_theme(ThemeConfig(preset="ocean", primary_color="#ff8800"))
    v = r["vars"]
    assert v["blue-main"] == "#ff8800"
    assert v["blue-bg"] == "rgba(255,136,0,0.15)"
    assert v["blue-border"] == "rgba(255,136,0,0.35)"


def test_short_hex_expands() -> None:
    r = resolve_theme(ThemeConfig(primary_color="#08f"))
    assert r["vars"]["blue-bg"] == "rgba(0,136,255,0.15)"


def test_bad_hex_falls_back_to_preset_primary() -> None:
    r = resolve_theme(ThemeConfig(preset="violet", primary_color="not-a-colour"))
    assert r["vars"]["blue-main"] == PRESETS["violet"]["primary"]


def test_raw_vars_win_last() -> None:
    r = resolve_theme(ThemeConfig(primary_color="#ff8800", vars={"blue-main": "#123456", "bg-base": "#000"}))
    assert r["vars"]["blue-main"] == "#123456"   # raw override wins over the derived primary
    assert r["vars"]["bg-base"] == "#000"


def test_unknown_preset_falls_back_to_default() -> None:
    r = resolve_theme(ThemeConfig(preset="does-not-exist"))
    assert r["preset"] == DEFAULT_PRESET


def test_preset_choices_carry_primary() -> None:
    choices = preset_choices()
    ids = {c["id"] for c in choices}
    assert "default" in ids and "ocean" in ids
    for c in choices:
        assert c["primary"].startswith("#")


def test_load_missing_file_is_default(tmp_path: Path) -> None:
    cfg = load_theme(tmp_path / "nope.toml").theme
    assert cfg.preset == DEFAULT_PRESET and cfg.app_name is None


def test_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "theme.toml"
    p.write_text('[theme]\npreset = "emerald"\napp_name = "Acme"\nprimary_color = "#10B981"\n')
    cfg = load_theme(p).theme
    assert cfg.preset == "emerald" and cfg.app_name == "Acme" and cfg.primary_color == "#10B981"


def test_parse_rejects_unknown_keys() -> None:
    with pytest.raises(Exception):
        parse_theme({"theme": {"preset": "default", "bogus": 1}})
