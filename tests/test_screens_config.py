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
    """ScreenField carries placement (``colspan``) + per-dialog override flags (``hidden`` /
    ``disabled`` / ``required``) + the conditional rule lists, PLUS optional self-contained display
    metadata (``dd`` / ``label`` / ``format`` / ``rules`` / ``rules_values`` / ``default`` /
    ``lookup_param_binds``). The MAIN screen's fields inherit the display metadata from the matching
    ``Screen.columns`` (set it there), but a NESTED form's fields — which reference a table with no
    column-hint layer — carry their own ``dd`` so the dictionary resolves their rule/label/format."""
    f = ScreenField(name="USR_ROLE_ID", hidden=False, disabled=True, required=True, colspan=2)
    assert f.name == "USR_ROLE_ID" and f.required is True and f.colspan == 2 and f.disabled is True
    # `name` is mandatory
    with pytest.raises(Exception):
        ScreenField()  # type: ignore[call-arg]
    # Field-level display metadata is KEPT (a nested-form field links its dd here, since it has no
    # Screen.columns layer to inherit from).
    withdd = ScreenField.model_validate({
        "name": "JDE_SY", "dd": "SY", "label": "System", "rules": "LOOKUP",
        "rules_values": "get_sy", "default": "920", "format": "text",
        "lookup_param_binds": [{"param": "X", "source": "Y"}],
    })
    assert withdd.dd == "SY" and withdd.rules == "LOOKUP" and withdd.rules_values == "get_sy"
    assert withdd.lookup_param_binds[0].source == "Y"
    # A layout-only field omits all of it — stays terse on dump.
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
    ``nested_form`` (an editable child-record form inline), ``nested_table`` (a related-rows
    TableView inline). The discriminator routes a raw dict to the right variant."""
    nf = NestedFormTab(
        id="jdedwards", label="JD Edwards",
        read_query="settings_jdedwards_get", update_query="settings_jdedwards_put",
        insert_query="settings_jdedwards_post",
        fields=[ScreenField(name="JDE_SY"), ScreenField(name="JDE_DTA")],
        param_binds=[ParamBind(param="APPS_ID", source="APPS_ID")],
    )
    assert nf.type == "nested_form" and nf.read_query == "settings_jdedwards_get"
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
    # Field-level ``dd`` + ``lookup_param_binds`` are KEPT — a field links its own dictionary entry
    # (the main screen usually inherits via Screen.columns, but nested-form fields rely on this).
    field = tab.fields[1]
    assert field.name == "USR_ROLE_ID"
    assert field.dd == "ROL_ID"
    assert field.lookup_param_binds[0].source == "USR_APPS_ID"


def test_parse_screens_with_nested_tab_kinds() -> None:
    """A dialog with mixed tab kinds: a plain ``form`` tab, an inline ``nested_form`` tab
    (an editable child record), and an inline ``nested_table`` tab (a related-rows TableView).
    Each variant validates via its discriminator + parses its variant-specific fields."""
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
            read_query = "settings_jdedwards_get"
            update_query = "settings_jdedwards_put"
            insert_query = "settings_jdedwards_post"
            hide_on_add = true
            param_binds = [{ param = "APPS_ID", source = "APPS_ID" }]

            [[screens.nomasx1.settings_applications.dialog.tabs.fields]]
            name = "JDE_SY"

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
    assert tabs[1].read_query == "settings_jdedwards_get"
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
                            # No type but read_query → nested_form
                            {
                                "id": "jd_edwards",
                                "read_query": "settings_jdedwards_get",
                                "update_query": "settings_jdedwards_put",
                                "fields": [{"name": "JDE_SY"}],
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
    assert tabs[1].read_query == "settings_jdedwards_get"
    assert tabs[1].update_query == "settings_jdedwards_put"
    assert tabs[1].param_binds[0].source == "APPS_ID"
    assert isinstance(tabs[2], NestedTableTab)
    assert tabs[2].screen == "settings_activity_log"


def test_nested_form_reference_mode() -> None:
    """A ``nested_form`` can REFERENCE an existing screen (``form_screen``) instead of inlining its
    queries + fields. The reference is unambiguous across the type-stripped round-trip: a tab with
    ``screen`` is a nested_table, one with ``form_screen`` (or ``read_query``) is a nested_form."""
    raw = {
        "screens": {
            "nomasx1": {
                "settings_applications": {
                    "read_query": "settings_applications_get",
                    "dialog": {
                        "tabs": [
                            # Reference mode: no type, no read_query — just ``form_screen``.
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
    assert tab.read_query == ""                       # inherited from the referenced screen at runtime
    assert tab.param_binds[0].source == "APPS_ID"


def test_form_tab_embeds_nested_forms() -> None:
    """A ``form`` tab can carry ``nested_forms`` — embedded child forms saved alongside the main
    table in one pass. Each is a full NestedFormTab (inline queries or a form_screen reference)."""
    sf = parse_screens({
        "screens": {"nomajde": {"f0092": {
            "read_query": "f0092_get",
            "dialog": {"tabs": [{
                "id": "main", "type": "form",
                "fields": [{"name": "ULUSER"}],
                "nested_forms": [{
                    "id": "sec", "label": "F00926",
                    "read_query": "f00926_get", "insert_query": "f00926_post",
                    "fields": [{"name": "SECUSER"}],
                    "param_binds": [{"param": "USER", "source": "ULUSER"}],
                }],
            }]},
        }}},
    })
    tab = sf.screens["nomajde"]["f0092"].dialog.tabs[0]  # type: ignore[union-attr]
    assert isinstance(tab, FormTab)
    assert len(tab.nested_forms) == 1
    nf = tab.nested_forms[0]
    assert nf.type == "nested_form" and nf.read_query == "f00926_get"
    assert nf.param_binds[0].source == "ULUSER"


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
