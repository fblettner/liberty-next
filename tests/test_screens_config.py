"""Unit tests for ``liberty.screens.config`` — the Pydantic shape used to round-trip
``screens.toml`` (Phase 6 slice 1)."""
from __future__ import annotations

import textwrap
import tomllib

import pytest

from liberty.screens import (
    CallApiAction,
    FieldCondition,
    FormTab,
    NavigateAction,
    NestedFormTab,
    NestedTableTab,
    NotifyAction,
    ParamBind,
    PromptField,
    RefreshAction,
    RunQueryAction,
    Screen,
    ScreenDialog,
    ScreenField,
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
    """ScreenField is a pure REFERENCE to a ``Screen.columns`` entry: ``name`` (required) +
    ``colspan`` (layout). Every behaviour (label / format / rule / default / binds, hidden /
    disabled / required, the conditional *_when rules) lives on the matching column — there are
    no per-dialog overrides. Stale override keys from before unification are silently dropped."""
    f = ScreenField(name="USR_ROLE_ID", colspan=2)
    assert f.name == "USR_ROLE_ID" and f.colspan == 2
    # `name` is mandatory
    with pytest.raises(Exception):
        ScreenField()  # type: ignore[call-arg]
    # Leftover per-field override keys (label / rules / hidden / lookup_param_binds / …) from a
    # pre-unification screens.toml load cleanly (extra="ignore") and are dropped — the column is
    # authoritative, so a layout-only field stays terse on dump.
    loaded = ScreenField.model_validate({
        "name": "JDE_SY", "dd": "SY", "label": "System", "rules": "LOOKUP",
        "hidden": True, "lookup_param_binds": [{"param": "X", "source": "Y"}], "colspan": 2,
    })
    assert loaded.model_dump(exclude_defaults=True) == {"name": "JDE_SY", "colspan": 2}
    assert ScreenField(name="USR_ROLE_ID").model_dump(exclude_defaults=True) == {"name": "USR_ROLE_ID"}


def test_screen_row_click_route_validates_placeholders() -> None:
    """``row_click_route`` template placeholders (``{name}``) must reference real
    columns on the screen. Catching this at config-load time turns a silent
    misclick (empty URL segment, then a bewildered 404) into a startup-time
    config error with a clear message."""
    from liberty.connectors.config import ColumnHint
    # OK: every placeholder names a real column hint.
    Screen(
        id="runs", read_query="list_runs",
        columns=[ColumnHint(name="id"), ColumnHint(name="job_id")],
        row_click_route="/nomaflow/runs/{id}",
    )
    # Multiple placeholders on the same route — all must resolve.
    Screen(
        id="composite", read_query="q",
        columns=[ColumnHint(name="a"), ColumnHint(name="b")],
        row_click_route="/x/{a}/y/{b}",
    )
    # Typo: ``{ix}`` doesn't match column ``id`` — surfaces at validation time.
    with pytest.raises(Exception) as exc:
        Screen(
            id="runs", read_query="list_runs",
            columns=[ColumnHint(name="id")],
            row_click_route="/nomaflow/runs/{ix}",
        )
    assert "ix" in str(exc.value)
    # No placeholders at all — a literal route is fine (no column constraint).
    Screen(
        id="literal", read_query="q",
        columns=[ColumnHint(name="id")],
        row_click_route="/static/path",
    )


def test_screen_dialog_and_tab() -> None:
    """A dialog owns its tabs, each tab owns its fields. Duplicate tab ids in the same
    dialog are caught at the Screen level (the dialog's parent)."""
    tab = FormTab(id="general", label="General", cols=2, l={"fr": "Général"},
                  fields=[ScreenField(name="USR_ID")])
    dlg = ScreenDialog(title="User", tabs=[tab])
    Screen(id="security_users", read_query="users_get", dialog=dlg)
    # duplicate tab ids — Screen's model_validator catches it.
    bad_dlg = ScreenDialog(tabs=[FormTab(id="general"), FormTab(id="general")])
    with pytest.raises(Exception):
        Screen(id="x", read_query="q", dialog=bad_dlg)


def test_nested_tab_variants() -> None:
    """The discriminated union covers three tab kinds: ``form`` (default field grid),
    ``nested_form`` (reuse another screen's form inline, reference-only), ``nested_table``
    (a related-rows TableView inline). The discriminator routes a raw dict to the right variant."""
    nf = NestedFormTab(
        id="jdedwards", label="JD Edwards",
        form_screen="settings_jdedwards",
        param_binds=[ParamBind(param="APPS_ID", source="APPS_ID")],
    )
    assert nf.type == "nested_form" and nf.form_screen == "settings_jdedwards"
    nt = NestedTableTab(
        id="activity_log", screen="settings_activity_log",
        param_binds=[ParamBind(param="ACL_APPS_ID", source="APPS_ID")],
    )
    assert nt.type == "nested_table" and nt.screen == "settings_activity_log"
    # The union resolves via the ``type`` discriminator. A dialog of mixed tab kinds round-trips.
    dlg = ScreenDialog(tabs=[
        FormTab(id="general", fields=[ScreenField(name="APPS_ID")]),
        nf, nt,
    ])
    s = Screen(id="settings_applications", read_query="settings_applications_get", dialog=dlg)
    assert [t.type for t in s.dialog.tabs] == ["form", "nested_form", "nested_table"]


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


def test_prompt_field_shape_and_round_trip() -> None:
    """``PromptField`` mirrors ``ScreenField`` (same ``dd``/``label``/``required``/``colspan``/
    ``default``/``lookup_param_binds``/conditional rules) but stands on its own — no backing
    column. ``name`` is required (becomes the ParamBind source target). Conditional rules carry
    through. ``extra='forbid'`` keeps the shape tight."""
    pf = PromptField(
        name="MUSE",
        dd="USR_ID",
        label="User",
        format="text",
        required=True,
        colspan=2,
        default="ANON",
        lookup_param_binds=[ParamBind(param="POOL", value="JDE")],
        visible_when=[FieldCondition(field="TYPE", value="USER")],
    )
    assert pf.name == "MUSE" and pf.required and pf.colspan == 2
    assert pf.lookup_param_binds[0].value == "JDE"
    assert pf.visible_when[0].field == "TYPE"
    # name required
    with pytest.raises(Exception):
        PromptField()  # type: ignore[call-arg]
    # extra fields rejected — keeps the wire shape tight
    with pytest.raises(Exception):
        PromptField(name="X", oops=True)  # type: ignore[call-arg]


def test_promptable_actions_carry_prompt_fields() -> None:
    """The three ParamBind-bearing variants (``run_query`` / ``call_api`` / ``navigate``) accept
    a ``prompt_fields`` list, an optional ``prompt_title`` / ``prompt_l`` / ``prompt_cols`` /
    ``prompt_submit_label``. Non-ParamBind-bearing variants (notify / refresh / confirm / set_field)
    don't — ``extra='forbid'`` rejects them."""
    rq = RunQueryAction(
        id="create_role", label="Create Role", query="roles_post",
        prompt_title="New role", prompt_cols=2,
        prompt_l={"fr": "Nouveau rôle"},
        prompt_fields=[PromptField(name="MUSE", required=True), PromptField(name="UPMJ")],
        param_binds=[ParamBind(param="muse", source="MUSE")],
    )
    assert [pf.name for pf in rq.prompt_fields] == ["MUSE", "UPMJ"]
    assert rq.prompt_title == "New role" and rq.prompt_l == {"fr": "Nouveau rôle"}
    # call_api + navigate carry the same mixin
    ca = CallApiAction(id="x", connector="srv", endpoint="ping", prompt_fields=[PromptField(name="A")])
    nav = NavigateAction(id="y", to="users_get", prompt_fields=[PromptField(name="B")])
    assert ca.prompt_fields[0].name == "A" and nav.prompt_fields[0].name == "B"
    # notify / refresh / etc. reject prompt_fields (no mixin) — keeps stub variants clean
    with pytest.raises(Exception):
        NotifyAction(id="n", message="m", prompt_fields=[])  # type: ignore[call-arg]
    with pytest.raises(Exception):
        RefreshAction(id="r", prompt_fields=[])  # type: ignore[call-arg]
    # Round-trip through ScreensFile so the discriminated union resolves correctly via TOML.
    raw = {
        "screens": {
            "njde": {
                "users_screen": {
                    "read_query": "users_get",
                    "actions": [
                        {
                            "id": "create_user", "type": "run_query", "query": "users_post",
                            "prompt_title": "New user",
                            "prompt_fields": [{"name": "USR_ID", "required": True}, {"name": "EMAIL"}],
                            "param_binds": [{"param": "usr_id", "source": "USR_ID"}],
                        },
                    ],
                },
            },
        },
    }
    sf = parse_screens(raw)
    a = sf.screens["njde"]["users_screen"].actions[0]
    assert a.type == "run_query" and a.prompt_title == "New user"
    assert [pf.name for pf in a.prompt_fields] == ["USR_ID", "EMAIL"]  # type: ignore[union-attr]
    assert a.prompt_fields[0].required is True  # type: ignore[union-attr]


def test_field_condition_and_prompt_field_rules() -> None:
    """``FieldCondition`` accepts a single value or a list (any matches). It still drives the
    *prompt dialog*'s conditional rules (``PromptField`` carries up to three lists —
    ``visible_when`` / ``required_when`` / ``disabled_when``); the screen dialog's own conditional
    rules live on the column now, not the field."""
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
    # PromptField plumbing: the three lists default to empty and round-trip when set.
    pf = PromptField(
        name="ITM_PRICE", required=True,
        visible_when=[FieldCondition(field="KIND", value=["PRODUCT", "SERVICE"])],
        required_when=[FieldCondition(field="TIER", value="PRO")],
        disabled_when=[FieldCondition(field="LOCKED", value="Y")],
    )
    assert [c.value for c in pf.visible_when] == [["PRODUCT", "SERVICE"]]
    assert pf.required_when[0].field == "TIER"
    assert pf.disabled_when[0].value == "Y"
    # A dialog field, by contrast, drops any conditional keys — the column owns them now.
    bare = ScreenField.model_validate({"name": "X", "visible_when": [{"field": "K", "value": "P"}]})
    assert bare.model_dump(exclude_defaults=True) == {"name": "X"}


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
            colspan = 2
            dd = "ROL_ID"
            """
        )
    )
    sf = parse_screens(raw)
    s = sf.screens["nomasx1"]["security_users"]
    assert s.id == "security_users"  # injected from the key
    assert s.read_query == "users_get"
    # Phase 3 — ``audit: bool`` is gone; ``audit_table: str | None`` replaces it. Old
    # screens.toml files with ``audit = true`` parse cleanly (extra="ignore") but the bool
    # is silently dropped; re-migration repopulates ``audit_table``.
    assert s.dialog is not None and s.dialog.title == "User"
    tab = s.dialog.tabs[0]
    # The tab dict had no ``type`` → ``parse_screens`` defaulted it to "form" so the
    # discriminated union resolves cleanly. Backward compat for every screens.toml file
    # written before the nested-tab variants were added.
    assert isinstance(tab, FormTab) and tab.type == "form"
    # A field is a pure reference: ``name`` + ``colspan``. A stale per-field ``dd`` override loads
    # (extra="ignore") but is dropped — the column owns the dictionary link now.
    field = tab.fields[1]
    assert field.name == "USR_ROLE_ID" and field.colspan == 2
    assert field.model_dump(exclude_defaults=True) == {"name": "USR_ROLE_ID", "colspan": 2}


def test_parse_screens_with_nested_tab_kinds() -> None:
    """A dialog with mixed tab kinds: a plain ``form`` tab, a ``nested_form`` tab (reuse another
    screen's form, reference-only via ``form_screen``), and a ``nested_table`` tab (a related-rows
    TableView). Each variant validates via its discriminator + parses its variant-specific fields."""
    raw = tomllib.loads(
        textwrap.dedent(
            """
            [screens.nomasx1.settings_applications]
            label = "Applications"
            read_query = "settings_applications_get"

            [screens.nomasx1.settings_applications.dialog]

            [[screens.nomasx1.settings_applications.dialog.tabs]]
            id = "general"
            label = "General"

            [[screens.nomasx1.settings_applications.dialog.tabs.fields]]
            name = "APPS_ID"

            [[screens.nomasx1.settings_applications.dialog.tabs]]
            type = "nested_form"
            id = "jdedwards"
            label = "JD Edwards"
            form_screen = "settings_jdedwards"
            hide_on_add = true
            param_binds = [{ param = "APPS_ID", source = "APPS_ID" }]

            [[screens.nomasx1.settings_applications.dialog.tabs]]
            type = "nested_table"
            id = "activity_log"
            label = "Activity Log"
            screen = "settings_activity_log"
            hide_on_add = true
            param_binds = [{ param = "ACL_APPS_ID", source = "APPS_ID" }]
            """
        )
    )
    sf = parse_screens(raw)
    tabs = sf.screens["nomasx1"]["settings_applications"].dialog.tabs
    assert [t.type for t in tabs] == ["form", "nested_form", "nested_table"]
    assert isinstance(tabs[1], NestedFormTab)
    assert tabs[1].form_screen == "settings_jdedwards"
    assert tabs[1].param_binds[0].source == "APPS_ID"
    assert isinstance(tabs[2], NestedTableTab)
    assert tabs[2].screen == "settings_activity_log"


def test_parse_screens_infers_nested_tab_type_from_keys() -> None:
    """Round-trip safety: when the ``type`` discriminator is missing on a tab dict (the
    ``/admin/config/screens/parsed`` GET strips it because ``exclude_defaults=True`` finds the
    Literal value matches its default), variant-specific keys tell ``parse_screens`` what kind
    the tab really is. A tab carrying ``screen`` is a nested_table; one carrying ``read_query``
    (without ``screen``) is a nested_form; otherwise plain form. Without this inference the
    next PUT would re-validate every nested tab as FormTab and reject its extra keys (422)."""
    raw = {
        "screens": {
            "nomasx1": {
                "settings_applications": {
                    "read_query": "settings_applications_get",
                    "dialog": {
                        "tabs": [
                            # No type, no nested-specific keys → form
                            {"id": "general", "fields": [{"name": "APPS_ID"}]},
                            # No type but form_screen → nested_form (reference-only)
                            {
                                "id": "jd_edwards",
                                "form_screen": "settings_jdedwards",
                                "param_binds": [{"param": "APPS_ID", "source": "APPS_ID"}],
                            },
                            # No type but screen → nested_table
                            {
                                "id": "activity_log",
                                "screen": "settings_activity_log",
                                "param_binds": [{"param": "ACL_APPS_ID", "source": "APPS_ID"}],
                            },
                        ],
                    },
                },
            },
        },
    }
    sf = parse_screens(raw)
    tabs = sf.screens["nomasx1"]["settings_applications"].dialog.tabs  # type: ignore[union-attr]
    assert isinstance(tabs[0], FormTab) and tabs[0].id == "general"
    assert isinstance(tabs[1], NestedFormTab)
    assert tabs[1].form_screen == "settings_jdedwards"
    assert tabs[1].param_binds[0].source == "APPS_ID"
    assert isinstance(tabs[2], NestedTableTab)
    assert tabs[2].screen == "settings_activity_log"


def test_nested_form_reference_mode() -> None:
    """A ``nested_form`` is REFERENCE-ONLY — it reuses an existing screen's form (``form_screen``);
    that screen owns the queries + fields, so the tab carries only the screen + ``param_binds``. The
    reference is unambiguous across the type-stripped round-trip: a tab with ``screen`` is a
    nested_table, one with ``form_screen`` is a nested_form."""
    raw = {
        "screens": {
            "nomasx1": {
                "settings_applications": {
                    "read_query": "settings_applications_get",
                    "dialog": {
                        "tabs": [
                            {
                                "id": "jd_edwards",
                                "form_screen": "settings_jdedwards",
                                "param_binds": [{"param": "APPS_ID", "source": "APPS_ID"}],
                            },
                        ],
                    },
                },
            },
        },
    }
    sf = parse_screens(raw)
    tab = sf.screens["nomasx1"]["settings_applications"].dialog.tabs[0]  # type: ignore[union-attr]
    assert isinstance(tab, NestedFormTab)
    assert tab.form_screen == "settings_jdedwards"
    assert tab.param_binds[0].source == "APPS_ID"
    # A nested_form with no source screen is rejected — ``form_screen`` is required.
    with pytest.raises(Exception):
        NestedFormTab(id="bad")  # type: ignore[call-arg]


def test_column_groups_and_column_group_ref() -> None:
    """A screen can declare related 1:1 write-back ``column_groups``; a column joins one via its
    ``group`` field. (The read query JOINs the related table; the save splits writes per table.)"""
    sf = parse_screens({
        "screens": {"nomajde": {"f0092": {
            "read_query": "f0092_get", "update_query": "f0092_put",
            "columns": [
                {"name": "ULUSER", "key": True},
                {"name": "ABALPH", "group": "addr"},
            ],
            "column_groups": [{
                "id": "addr", "label": "Address Book",
                "update_query": "f0101_put", "insert_query": "f0101_post",
                "key_columns": ["ABAN8"],
                "param_binds": [{"param": "ABAN8", "source": "ULUSER"}],
            }],
        }}},
    })
    s = sf.screens["nomajde"]["f0092"]
    assert [c.name for c in s.columns if c.group == "addr"] == ["ABALPH"]
    grp = s.column_groups[0]
    assert grp.id == "addr" and grp.update_query == "f0101_put" and grp.key_columns == ["ABAN8"]
    assert grp.param_binds[0].source == "ULUSER"
    # A column with no group writes to the main table.
    assert next(c for c in s.columns if c.name == "ULUSER").group is None


def test_call_plugin_action_round_trips() -> None:
    """``call_plugin`` is a first-class action (sibling of run_query / call_api): the discriminator
    resolves it, its ``callable`` carries the PLUGIN_CALLABLES picker hint, and param_binds + the
    promptable mixin fields parse."""
    import json

    from liberty.screens.config import Action
    from pydantic import TypeAdapter

    a = TypeAdapter(Action).validate_python({
        "type": "call_plugin", "id": "remerge", "label": "Re-merge security",
        "callable": "nomajde.security:j_remerge_security",
        "param_binds": [{"param": "role_id", "source": "AUUSER"}],
        "bind_result": True,
    })
    assert type(a).__name__ == "CallPluginAction"
    assert a.callable == "nomajde.security:j_remerge_security"
    assert a.param_binds[0].param == "role_id" and a.bind_result is True
    # The editor's callable dropdown is schema-driven via the PLUGIN_CALLABLES enum ref.
    schema = TypeAdapter(Action).json_schema()
    cp = next(s for s in schema["$defs"].values() if s.get("title") == "CallPluginAction")
    assert cp["properties"]["callable"].get("x_enum_ref") == "PLUGIN_CALLABLES"
    # round-trips through a JSON dump unchanged
    assert json.loads(a.model_dump_json())["type"] == "call_plugin"


def test_column_group_per_column_picker_is_schema_driven() -> None:
    """The per-column ``group`` dropdown in the Columns-tab editor is schema-driven — it resolves
    its options from the ``COLUMN_GROUPS`` enum the ScreenEditor injects (the screen's defined group
    ids). Guards that wiring so the operator picks a group instead of hand-typing its id. (The group
    EDITOR's own connector / query / bind dropdowns are the dedicated ColumnGroupsEditor component,
    not schema enums, so only this one field carries an ``x_enum_ref``.)"""
    defs = ScreensFile.model_json_schema()["$defs"]
    assert defs["ColumnHint"]["properties"]["group"].get("x_enum_ref") == "COLUMN_GROUPS"


def test_column_groups_roundtrip_through_toml(tmp_path) -> None:
    """A screen authored with ``column_groups`` + a grouped column survives a load → dump → load
    cycle — the shape the editor writes back is parsed identically on the next open."""
    f = tmp_path / "screens.toml"
    f.write_text(
        textwrap.dedent(
            """
            [screens.nomajde.f0092]
            read_query = "f0092_get"
            update_query = "f0092_put"

            [[screens.nomajde.f0092.columns]]
            name = "ULUSER"
            key = true

            [[screens.nomajde.f0092.columns]]
            name = "ABALPH"
            group = "addr"

            [[screens.nomajde.f0092.column_groups]]
            id = "addr"
            label = "Address Book"
            update_query = "f0101_put"
            insert_query = "f0101_post"
            delete_query = "f0101_delete"
            key_columns = ["ABAN8"]

            [[screens.nomajde.f0092.column_groups.param_binds]]
            param = "ABAN8"
            source = "ULUSER"
            """
        )
    )
    s = load_screens(f).screens["nomajde"]["f0092"]
    # Re-dump the parsed model (what the editor's Save serialises) and parse it again.
    redumped = parse_screens({"screens": {"nomajde": {"f0092": s.model_dump(exclude_defaults=True)}}})
    s2 = redumped.screens["nomajde"]["f0092"]
    grp = s2.column_groups[0]
    assert grp.id == "addr" and grp.update_query == "f0101_put" and grp.insert_query == "f0101_post"
    assert grp.delete_query == "f0101_delete"
    assert grp.key_columns == ["ABAN8"] and grp.param_binds[0].source == "ULUSER"
    assert next(c for c in s2.columns if c.name == "ABALPH").group == "addr"


def test_nested_form_without_source_rejected() -> None:
    """A nested_form with neither ``form_screen`` nor ``read_query`` would render empty — reject it."""
    with pytest.raises(Exception):
        parse_screens({
            "screens": {"nomasx1": {"s": {
                "read_query": "s_get",
                "dialog": {"tabs": [{"id": "bad", "type": "nested_form", "fields": [{"name": "X"}]}]},
            }}},
        })


def test_parse_screens_id_mismatch_rejected() -> None:
    """An explicit ``id`` field that doesn't match its dict key is a config bug — fail loudly."""
    with pytest.raises(Exception):
        parse_screens({"screens": {"nomasx1": {"users": {"id": "people", "read_query": "q"}}}})


def test_screen_views_roundtrip() -> None:
    """Named shared views parse with their columns / sort / group_by / page_size + default flag."""
    sf = parse_screens({"screens": {"nomasx1": {"s": {
        "read_query": "q",
        "views": [
            {"name": "Wide", "default": True, "columns": ["A", "B"],
             "sort": [{"column": "A", "desc": True}], "group_by": ["B"], "page_size": 100},
            {"name": "Narrow", "columns": ["A"]},
        ],
    }}}})
    s = sf.screens["nomasx1"]["s"]
    assert [v.name for v in s.views] == ["Wide", "Narrow"]
    assert s.views[0].default is True and s.views[0].sort[0].column == "A" and s.views[0].sort[0].desc is True
    assert s.views[0].page_size == 100 and s.views[1].default is False


def test_screen_views_reject_two_defaults() -> None:
    with pytest.raises(Exception):
        parse_screens({"screens": {"nomasx1": {"s": {
            "read_query": "q",
            "views": [{"name": "A", "default": True}, {"name": "B", "default": True}],
        }}}})


def test_screen_views_reject_duplicate_name() -> None:
    with pytest.raises(Exception):
        parse_screens({"screens": {"nomasx1": {"s": {
            "read_query": "q",
            "views": [{"name": "Dup"}, {"name": "Dup"}],
        }}}})


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
