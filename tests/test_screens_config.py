"""Unit tests for ``liberty.screens.config`` — the Pydantic shape used to round-trip
``screens.toml`` (Phase 6 slice 1)."""
from __future__ import annotations

import textwrap
import tomllib

import pytest

from liberty.screens import (
    ParamBind,
    Screen,
    ScreenDialog,
    ScreenField,
    ScreenTab,
    ScreensFile,
    load_screens,
    parse_screens,
)


def test_param_bind_either_mode() -> None:
    """ParamBind models both v1 ly_dlg_filters flavours: a literal ``value`` (mode A) or a
    column / form-field ``source`` (mode B). Both modes accept blank fields during edits."""
    # mode A — literal binding
    pb = ParamBind(param="SY", value="01")
    assert pb.param == "SY" and pb.value == "01" and pb.source is None
    # mode B — dynamic, read at call time
    pb = ParamBind(param="ROL_APPS_ID", source="USR_APPS_ID")
    assert pb.value is None and pb.source == "USR_APPS_ID"
    # built-in reserved sources (start with `#`) are accepted as plain strings — wired in slice 4.
    assert ParamBind(param="USR", source="#LOGIN_USER#").source == "#LOGIN_USER#"
    # extras rejected
    with pytest.raises(Exception):
        ParamBind(param="x", typo=1)  # type: ignore[call-arg]


def test_screen_field_shape() -> None:
    """A field with the optional knobs the migration carries over."""
    f = ScreenField(
        name="USR_ROLE_ID", dd="ROL_ID", label="Role", hidden=False, disabled=True,
        required=True, colspan=2, default="ADMIN",
        lookup_param_binds=[ParamBind(param="ROL_APPS_ID", source="USR_APPS_ID")],
    )
    assert f.dd == "ROL_ID" and f.required is True and f.colspan == 2
    assert f.lookup_param_binds[0].source == "USR_APPS_ID"
    # `name` is mandatory; extra keys rejected
    with pytest.raises(Exception):
        ScreenField()  # type: ignore[call-arg]
    with pytest.raises(Exception):
        ScreenField(name="X", bogus=1)  # type: ignore[call-arg]


def test_screen_dialog_and_tab() -> None:
    """A dialog owns its tabs, each tab owns its fields. Duplicate tab ids in the same
    dialog are caught at the Screen level (the dialog's parent)."""
    tab = ScreenTab(id="general", label="General", cols=2, l={"fr": "Général"},
                    fields=[ScreenField(name="USR_ID")])
    dlg = ScreenDialog(title="User", tabs=[tab])
    Screen(id="security_users", read_query="users_get", dialog=dlg)
    # duplicate tab ids — Screen's model_validator catches it.
    bad_dlg = ScreenDialog(tabs=[ScreenTab(id="general"), ScreenTab(id="general")])
    with pytest.raises(Exception):
        Screen(id="x", read_query="q", dialog=bad_dlg)


def test_parse_screens_injects_id_from_key() -> None:
    """``parse_screens`` injects each screen's ``id`` from the dict key — most operators
    won't repeat it in TOML — and the validator enforces that an explicit id matches its key."""
    raw = tomllib.loads(
        textwrap.dedent(
            """
            [screens.nomasx1.security_users]
            label = "Users"
            read_query = "users_get"
            update_query = "users_put"
            audit = true

            [screens.nomasx1.security_users.dialog]
            title = "User"

            [[screens.nomasx1.security_users.dialog.tabs]]
            id = "general"
            label = "General"

            [[screens.nomasx1.security_users.dialog.tabs.fields]]
            name = "USR_ID"

            [[screens.nomasx1.security_users.dialog.tabs.fields]]
            name = "USR_ROLE_ID"
            dd = "ROL_ID"

            [[screens.nomasx1.security_users.dialog.tabs.fields.lookup_param_binds]]
            param = "ROL_APPS_ID"
            source = "USR_APPS_ID"
            """
        )
    )
    sf = parse_screens(raw)
    s = sf.screens["nomasx1"]["security_users"]
    assert s.id == "security_users"  # injected from the key
    assert s.read_query == "users_get" and s.audit is True
    assert s.dialog is not None and s.dialog.title == "User"
    field = s.dialog.tabs[0].fields[1]
    assert field.dd == "ROL_ID"
    assert field.lookup_param_binds[0].source == "USR_APPS_ID"


def test_parse_screens_id_mismatch_rejected() -> None:
    """An explicit ``id`` field that doesn't match its dict key is a config bug — fail loudly."""
    with pytest.raises(Exception):
        parse_screens({"screens": {"nomasx1": {"users": {"id": "people", "read_query": "q"}}}})


def test_load_screens_missing_file_yields_empty(tmp_path) -> None:
    assert load_screens(tmp_path / "nope.toml") == ScreensFile()


def test_load_screens_roundtrip(tmp_path) -> None:
    f = tmp_path / "screens.toml"
    f.write_text(
        textwrap.dedent(
            """
            [screens.nomasx1.security_users]
            read_query = "users_get"
            """
        )
    )
    sf = load_screens(f)
    assert list(sf.screens) == ["nomasx1"]
    assert list(sf.screens["nomasx1"]) == ["security_users"]
