"""Unit tests for ``liberty.screens.config`` — the Pydantic shape used to round-trip
``screens.toml`` (Phase 6 slice 1)."""
from __future__ import annotations

import textwrap
import tomllib

import pytest

from liberty.screens import (
    FieldCondition,
    NotifyAction,
    ParamBind,
    RefreshAction,
    RunQueryAction,
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


def test_action_discriminated_union_round_trips() -> None:
    """Each action variant validates via its ``type`` literal — Pydantic picks the right subclass.
    Cross-variant fields (e.g. ``query`` on a notify action) are rejected (``extra="forbid"``)."""
    # run_query: the multi-table FormsDialog workhorse — required `query`, optional connector +
    # param_binds. ParamBind shape carries through (param + value-or-source).
    rq = RunQueryAction(
        id="write_apps_jde", label="Write apps_jde", query="apps_jde_post",
        param_binds=[ParamBind(param="APP_ID", source="apps_id")],
    )
    assert rq.type == "run_query" and rq.stop_on_error is True
    assert rq.param_binds[0].source == "apps_id"
    # notify (`message` required, `tone` literal-restricted) — and refresh (no extra fields)
    NotifyAction(id="ok", message="Saved related rows.", tone="ok")
    RefreshAction(id="reload")
    # Bad: invalid tone literal
    with pytest.raises(Exception):
        NotifyAction(id="x", message="y", tone="bogus")  # type: ignore[arg-type]
    # Bad: extra key on a variant
    with pytest.raises(Exception):
        RunQueryAction(id="x", query="q", message="oops")  # type: ignore[call-arg]
    # Round-trip a full dialog with on_save through ScreensFile — the discriminator picks each
    # variant by its `type` literal. parse_screens injects the screen id from its dict key.
    raw = {
        "screens": {
            "myapp": {
                "settings_applications": {
                    "read_query": "apps_get",
                    "update_query": "apps_put",
                    "dialog": {
                        "tabs": [{"id": "general", "fields": [{"name": "APP_ID"}]}],
                        "on_save": [
                            {"id": "write_apps_jde", "type": "run_query", "query": "apps_jde_post",
                             "param_binds": [{"param": "APP_ID", "source": "apps_id"}]},
                            {"id": "write_apps_ldap", "type": "run_query", "query": "apps_ldap_post"},
                            {"id": "notify_ok", "type": "notify", "message": "Saved.", "tone": "ok"},
                            {"id": "reload", "type": "refresh"},
                        ],
                    },
                },
            },
        },
    }
    sf = parse_screens(raw)
    actions = sf.screens["myapp"]["settings_applications"].dialog.on_save  # type: ignore[union-attr]
    assert [a.type for a in actions] == ["run_query", "run_query", "notify", "refresh"]
    # First action's binds round-trip — same ParamBind shape used elsewhere.
    rq0 = actions[0]
    assert rq0.type == "run_query" and rq0.query == "apps_jde_post"  # type: ignore[union-attr]
    assert rq0.param_binds == [ParamBind(param="APP_ID", source="apps_id")]  # type: ignore[union-attr]


def test_field_condition_and_per_field_rules() -> None:
    """``FieldCondition`` accepts a single value or a list (any matches). A field carries up to
    three lists — ``visible_when`` / ``required_when`` / ``disabled_when`` — each AND-ed; an
    empty list (the default) means "no condition, fall back to the static flag"."""
    # single literal
    FieldCondition(field="KIND", value="PRODUCT")
    # list (membership) — what the migrator emits
    cond = FieldCondition(field="KIND", value=["PRODUCT", "SERVICE"])
    assert cond.value == ["PRODUCT", "SERVICE"]
    # required arguments / extras rejected
    with pytest.raises(Exception):
        FieldCondition(value="x")  # type: ignore[call-arg]
    with pytest.raises(Exception):
        FieldCondition(field="X", value="y", typo=1)  # type: ignore[call-arg]
    # Field plumbing: the three lists default to empty and round-trip when set.
    f = ScreenField(
        name="ITM_PRICE", required=True,
        visible_when=[FieldCondition(field="KIND", value=["PRODUCT", "SERVICE"])],
        required_when=[FieldCondition(field="TIER", value="PRO")],
        disabled_when=[FieldCondition(field="LOCKED", value="Y")],
    )
    assert [c.value for c in f.visible_when] == [["PRODUCT", "SERVICE"]]
    assert f.required_when[0].field == "TIER"
    assert f.disabled_when[0].value == "Y"
    # Defaults: empty lists when nothing is set.
    bare = ScreenField(name="X")
    assert bare.visible_when == [] and bare.required_when == [] and bare.disabled_when == []


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
