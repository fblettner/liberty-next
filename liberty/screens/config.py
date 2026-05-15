"""``config/screens.toml`` — screen definitions per app.

A *screen* is the user-facing unit: a table list of rows + an inline dialog form for adding /
editing a row + the CRUD queries that read & write them. One ``[screens.<app>.<screen_id>]``
per screen, where ``<app>`` is the connector name (matching ``menus.toml``'s convention) and
``<screen_id>`` is a stable per-app key (e.g. ``security_users``, ``F0005``).

This collapses v1's chain (``ly_tables`` → ``ly_dialogs`` → ``ly_dlg_frm`` → ``ly_dlg_tab`` →
``ly_dlg_col`` → ``ly_dlg_filters``) into one Pydantic shape — the screen owns its dialog
directly, the dialog owns its tabs, each tab owns its fields, and each field can declare
parameter bindings (``ParamBind``) for its lookup query. Same shape for actions and the row
context menu (slices 4 and 6) — the framework has one parameter-binding mechanism.

A screen's dialog is optional: a screen with no dialog still works as a read-only / grid-edit
table (the existing TableView batch-edit flow). Dialogs land in slice 2; this module just
captures the data model and lets the migration emit it.

Example::

    [screens.nomasx1.security_users]
    label = "Users"
    description = "Security - Users"
    connector = "nomasx1"
    read_query = "security_users_get"
    update_query = "security_users_put"
    insert_query = "security_users_post"
    delete_query = "security_users_delete"
    audit = true     # v1's tbl_audit — wires AUD_<table> writes (slice 5)

    [screens.nomasx1.security_users.dialog]
    title = "User"

    [[screens.nomasx1.security_users.dialog.tabs]]
    id = "general"
    label = "General"

    [[screens.nomasx1.security_users.dialog.tabs.fields]]
    name = "USR_ID"
    dd = "USR_ID"

    [[screens.nomasx1.security_users.dialog.tabs.fields]]
    name = "USR_ROLE_ID"
    dd = "ROL_ID"

    [[screens.nomasx1.security_users.dialog.tabs.fields.lookup_param_binds]]
    param = "ROL_APPS_ID"
    source = "USR_APPS_ID"   # bind from another field on the same form at submit time
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParamBind(BaseModel):
    """Bind one ``:placeholder`` parameter of a target query — for a lookup combo on a dialog
    field, an action's argument, a row context-menu trigger, or any future query/API call.
    v2's port of v1's ``ly_dlg_filters`` (the name was historical — the table was reused for
    every kind of parameter passing). Two modes (exactly one of ``value`` / ``source`` set in
    practice, but both may be blank during edits)::

        {param = "SY", value = "01"}                  # literal binding
        {param = "ROL_APPS_ID", source = "USR_APPS_ID"} # dynamic — read at call time
    """

    model_config = ConfigDict(extra="forbid")

    param: str = Field(description="The target query's :placeholder name to bind.")
    value: str | None = Field(default=None, description="Literal value to bind (mode A).")
    source: str | None = Field(
        default=None,
        description=(
            "Name of the column / form-field whose current value to bind (mode B). Resolved "
            "against the row (table context), the form (dialog context), or the firing event's "
            "context (action / row menu). Reserved built-ins start with ``#`` — e.g. ``#LOGIN_USER#``, "
            "``#SYSDATE#`` — will be wired in slice 4."
        ),
    )


class FieldCondition(BaseModel):
    """One per-field predicate evaluated against the dialog's current form state (v2's port of
    v1's ``ly_cdn_params``). Mirrors :class:`liberty.connectors.config.VisibleWhen` in shape but
    the evaluation context is the *form*, not server filters: ``field`` names another field on
    the same dialog (its v2 name, matching ``ScreenField.name``), and the predicate holds when
    that field's current form value equals ``value`` (or is in ``value`` when it's a list). A
    list of these AND-s: every predicate must hold for the parent rule to fire. An empty list
    on a ``ScreenField.visible_when`` / ``required_when`` / ``disabled_when`` is "no condition"
    (= the static ``hidden`` / ``required`` / ``disabled`` flag decides). v1's other operators
    (NOT_EQUAL, LIKE, …) aren't representable; the migrator skips them with a warning."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="The other dialog field whose live value gates this rule.")
    value: str | list[str] = Field(description="The expected value, or list of values (any one matches).")


class ScreenField(BaseModel):
    """One field on a dialog tab. Maps to a column of the screen's read query (by ``name``) —
    same convention as ``ColumnHint.name``. ``dd`` overrides the dictionary entry lookup
    (defaults to ``name`` when unset).

    **Conditional rules** (slice 3): ``visible_when`` / ``required_when`` / ``disabled_when``
    each take a list of :class:`FieldCondition` predicates evaluated against the dialog's live
    form state. When the list is non-empty *and* every predicate holds, the rule fires (the
    field shows / is required / is read-only); the static ``hidden`` / ``required`` /
    ``disabled`` flags act as the fallback when the corresponding ``*_when`` list is empty.
    v1's ``col_cdn_id`` migrates into ``visible_when``; the ``required_when`` / ``disabled_when``
    paths have no v1 source mass-migrated yet — operators set them via the builder.
    (``default_when`` waits for the form-rule slice — v1's SEQUENCE / SYSDATE / LOGIN / CURRENT_DATE
    derived defaults live in the same territory.)"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The result column this field reads from / writes to.")
    dd: str | None = Field(
        default=None,
        description="Dictionary entry override (blank → looked up under ``name``).",
    )
    label: str | None = Field(default=None, description="Display label (overrides the dictionary).")
    hidden: bool = Field(default=False, description="Hide this field from the dialog by default.")
    disabled: bool = Field(default=False, description="Render the field read-only (v1's col_disabled).")
    required: bool = Field(default=False, description="Field is required for save (v1's col_required).")
    colspan: int | None = Field(default=None, description="How many columns of the tab's grid this field spans (v1's col_colspan).")
    default: str | None = Field(default=None, description="Pre-fill value on a new row (v1's col_default).")
    lookup_param_binds: list[ParamBind] = Field(
        default_factory=list,
        description=(
            "Parameter bindings for this field's *lookup* query (when the field's dd resolves "
            "to a LOOKUP rule). Same shape used by actions/menus. v1's ly_dlg_filters."
        ),
    )
    visible_when: list[FieldCondition] = Field(
        default_factory=list,
        description=(
            "Conditional visibility (v2's port of v1's ``col_cdn_id``). When non-empty, every "
            "predicate must hold against the form's live state for the field to render; "
            "otherwise the static ``hidden`` flag decides."
        ),
    )
    required_when: list[FieldCondition] = Field(
        default_factory=list,
        description=(
            "Conditional ``required``. When non-empty, every predicate must hold against the "
            "live form state for the field to be required; otherwise ``required`` decides."
        ),
    )
    disabled_when: list[FieldCondition] = Field(
        default_factory=list,
        description=(
            "Conditional read-only. When non-empty, every predicate must hold against the live "
            "form state for the field to be locked; otherwise ``disabled`` decides."
        ),
    )


class _ScreenTabBase(BaseModel):
    """Fields every tab kind shares — title + per-mode hide flags + translations. The
    discriminator ``type`` selects the variant: ``form`` (the default — a grid of fields),
    ``nested_form`` (an editable child-record form embedded inline), ``nested_table`` (a
    related-rows TableView embedded inline). v1's ``tab_disable_add`` / ``tab_disable_edit``
    are captured as ``hide_on_add`` / ``hide_on_edit`` so the migration keeps fidelity."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable id within the dialog (used as the tab's key in builders).")
    label: str | None = Field(default=None, description="Default-language tab title.")
    l: dict[str, str] = Field(default_factory=dict, description="Per-language overrides: {language_code: translated_label}.")
    hide_on_add: bool = Field(default=False, description="Hide this tab when *adding* a row (v1's tab_disable_add='Y').")
    hide_on_edit: bool = Field(default=False, description="Hide this tab when *editing* a row (v1's tab_disable_edit='Y').")


class FormTab(_ScreenTabBase):
    """A grid of editable fields — the original tab kind. ``cols`` is the CSS grid column count
    (v1's tab_cols). Backward-compatible: a tab in screens.toml without an explicit ``type``
    field is treated as ``"form"`` (see :func:`parse_screens`)."""

    type: Literal["form"] = "form"
    cols: int | None = Field(default=None, description="CSS grid column count for this tab's fields (v1's tab_cols).")
    fields: list[ScreenField] = Field(default_factory=list, description="Fields shown on this tab, in display order.")


class NestedFormTab(_ScreenTabBase):
    """A child-record form embedded inline in this tab — v2's port of v1's "FormsDialog inside
    a FormsDialog". The parent's PK is bound into the nested ``read_query`` via ``param_binds``;
    if a row comes back the nested form renders in edit mode (saving fires ``update_query``); if
    not, the user is editing a new related record (saving fires ``insert_query``). All three
    queries live on ``connector`` (default: the parent screen's effective connector).

    Used for cases like SETTINGS_APPLICATIONS' "JD Edwards" / "LDAP" tabs in v1: same APPS_ID,
    extra columns on related tables. The parent screen's ``update_query`` writes its own table;
    each nested tab takes care of its own."""

    type: Literal["nested_form"] = "nested_form"
    connector: str | None = Field(
        default=None,
        description="Connector hosting the nested CRUD queries; blank → the parent screen's effective connector.",
    )
    read_query: str = Field(description="Reads the linked row (typically returning 0 or 1 rows after the bind narrows it).")
    update_query: str | None = Field(default=None, description="Writes edits when a linked row already exists.")
    insert_query: str | None = Field(default=None, description="Writes a new linked row when none existed.")
    cols: int | None = Field(default=None, description="CSS grid column count for the nested fields (v1's tab_cols).")
    fields: list[ScreenField] = Field(default_factory=list, description="Fields shown in the nested form, in display order.")
    param_binds: list[ParamBind] = Field(
        default_factory=list,
        description=(
            "Bind parent values into the nested read/update/insert queries. Typically the parent's "
            "PK column → the nested query's matching ``:param``. Resolved at call time against the "
            "parent dialog's live form state (same shape as ``ScreenField.lookup_param_binds``)."
        ),
    )


class NestedTableTab(_ScreenTabBase):
    """A related-rows TableView embedded inline in this tab — v2's port of v1's "FormsTable
    inside a FormsDialog". References another v2 screen by id (``screen``) — the nested
    TableView re-uses that screen's read query + column hints + dialog + actions, so e.g.
    a parent SETTINGS_APPLICATIONS' "Activity Log" tab points at ``settings_activity_log``
    (already a standalone screen) and gets the full TableView pipeline for free.

    The parent's PK (or any other column) is bound into the nested screen's read query via
    ``param_binds`` — the activity-log rows narrow to the current application's APPS_ID."""

    type: Literal["nested_table"] = "nested_table"
    screen: str = Field(description="ID of the v2 screen to render as a TableView in this tab.")
    connector: str | None = Field(
        default=None,
        description="Connector that hosts the referenced screen; blank → the parent screen's effective connector.",
    )
    param_binds: list[ParamBind] = Field(
        default_factory=list,
        description=(
            "Bind parent values into the nested screen's read query (e.g. parent's APPS_ID → "
            "nested ``ACL_APPS_ID``). Resolved at render time against the parent dialog's form state."
        ),
    )


# Discriminator union of tab kinds. Pydantic picks the right subclass on the ``type`` field;
# a tab dict missing ``type`` is patched to ``"form"`` by :func:`parse_screens` so existing
# screens.toml files keep validating. Ungrouped: deliberately re-emit through the public
# alias ``ScreenTab`` so external code (the migration, tests, the OpenAPI exporter) still
# imports one name.
ScreenTab = Annotated[Union[FormTab, NestedFormTab, NestedTableTab], Field(discriminator="type")]


class _ActionBase(BaseModel):
    """Fields shared by every action variant. The ``type`` discriminator selects the variant."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable id within the screen.")
    label: str | None = Field(default=None, description="Display label (button caption / log line).")
    stop_on_error: bool = Field(
        default=True,
        description="When this action raises, abort the rest of the action list. Set false to make this action best-effort.",
    )


class RunQueryAction(_ActionBase):
    """Execute a connector query (the most common action — v1's ``ly_act_tasks evt_type='QUERY'``).
    ``param_binds`` resolves at call time against the firing context (the dialog form's live values,
    or the row for a row-menu action): same :class:`ParamBind` shape used for lookups.

    The v2 form of v1's ``FormsDialog``: a screen whose main ``update_query`` writes one table can
    list extra ``RunQueryAction``s on its ``dialog.on_save`` to write related tables that share a
    PK (e.g. NOMASX1's ``settings_applications`` → apps + apps_jde + apps_ldap)."""

    type: Literal["run_query"] = "run_query"
    connector: str | None = Field(
        default=None,
        description="Connector the query lives on; blank → the screen's effective connector (or app name).",
    )
    query: str = Field(description="Name of the connector query to run (e.g. ``apps_jde_post``).")
    param_binds: list[ParamBind] = Field(
        default_factory=list,
        description="Parameter bindings — same shape as ``ScreenField.lookup_param_binds`` / row menu binds.",
    )


class CallApiAction(_ActionBase):
    """Call an API endpoint on a configured API connector — v1's ``evt_type='API'``. The
    endpoint's own ``{{placeholder}}`` template wins; ``param_binds`` is for the *query string /
    body* parameters the endpoint declares as bindable."""

    type: Literal["call_api"] = "call_api"
    connector: str = Field(description="API connector name (must be of ``type = \"api\"``).")
    endpoint: str = Field(description="Endpoint name on that connector.")
    param_binds: list[ParamBind] = Field(default_factory=list)


class NavigateAction(_ActionBase):
    """Open another TableView (v1's "drill into another table" pattern) — the row-menu staple.
    ``to`` names the target query (matching a ``QueryDef.name`` on ``connector``); the URL the
    runtime opens is ``/sql/{connector}/{to}`` with ``param_binds`` resolved against the firing
    context and forwarded as ``?<param>=<value>`` query-string entries. The destination's
    TableView seeds its param form from those — so a row-menu item "View this user's roles"
    becomes ``{to: "roles_get", connector: "nomasx1", param_binds: [{param: "USR_ID", source:
    "usr_id"}]}`` and ends up opening ``/sql/nomasx1/roles_get?USR_ID=<the-clicked-user-id>``."""

    type: Literal["navigate"] = "navigate"
    to: str = Field(description="Target query name on ``connector`` (e.g. ``roles_get``) — the destination's URL is ``/sql/{connector}/{to}``.")
    connector: str | None = Field(
        default=None,
        description="Connector that holds the target query; blank → the firing screen's effective connector.",
    )
    param_binds: list[ParamBind] = Field(default_factory=list)


class SetFieldAction(_ActionBase):
    """Change the value of a field on the current form (only meaningful from a dialog context).
    ``target`` is the destination field; ``value`` is a literal or ``source`` reads from another
    field's current value (the same ParamBind value-vs-source dichotomy)."""

    type: Literal["set_field"] = "set_field"
    target: str = Field(description="Field name to write into (matches ``ScreenField.name``).")
    value: str | None = Field(default=None, description="Literal value (mode A).")
    source: str | None = Field(
        default=None,
        description="Source field name to read from at call time (mode B). Reserved built-ins start with ``#``.",
    )


class ConfirmAction(_ActionBase):
    """Prompt the user for confirmation before continuing. The action list pauses on this entry; if
    the user cancels, the rest of the list is skipped (an inline `confirm` is best-effort — see
    ``stop_on_error``)."""

    type: Literal["confirm"] = "confirm"
    message: str = Field(description="Prompt text shown to the user.")
    confirm_label: str | None = Field(default=None)
    cancel_label: str | None = Field(default=None)


class NotifyAction(_ActionBase):
    """Surface a toast / banner. Cheap, side-effect-free — useful after a run_query writes to log
    "Saved related rows: 3" or to flag an unusual response."""

    type: Literal["notify"] = "notify"
    message: str = Field(description="Text shown to the user.")
    tone: Literal["info", "ok", "warn", "error"] = Field(default="info")


class RefreshAction(_ActionBase):
    """Re-run the screen's read query so the table picks up changes. Common closer for an
    on_save chain that wrote to several tables."""

    type: Literal["refresh"] = "refresh"


# Discriminated union — every Action variant carries a ``type`` literal; Pydantic picks the right
# subclass when validating a raw dict (so screens.toml's `[[on_save]] type = "run_query"` validates).
Action = Annotated[
    Union[
        RunQueryAction, CallApiAction, NavigateAction, SetFieldAction,
        ConfirmAction, NotifyAction, RefreshAction,
    ],
    Field(discriminator="type"),
]


class ScreenDialog(BaseModel):
    """The form shown when the user adds / edits a row of this screen. Optional — a screen
    with no dialog renders as a read-only / grid-edit table."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, description="Dialog title (falls back to the screen's label).")
    tabs: list[ScreenTab] = Field(default_factory=list, description="Tabs, in display order. At least one.")
    on_save: list[Action] = Field(
        default_factory=list,
        description=(
            "Actions to run sequentially *after* the dialog's main update_query / insert_query "
            "succeeds. Each action's ParamBinds resolve against the form's live state. Stops on "
            "the first failure unless an action sets ``stop_on_error = false``. v2's port of v1's "
            "``ly_act_tasks`` for the form-save flow — multi-table writes (FormsDialog) land here."
        ),
    )


class Screen(BaseModel):
    """A screen — list + dialog. Keyed by ``id`` within the app's screens map."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable screen id within the app (e.g. ``security_users``, ``F0005``).")
    label: str | None = Field(default=None, description="Short label shown in the menu / list.")
    description: str | None = Field(default=None, description="Longer description shown as the page title.")
    connector: str | None = Field(
        default=None,
        description="Connector the read / write queries live on. Blank → the app's own connector.",
    )
    read_query: str = Field(description="The list / read query name (returns the rows shown in the table).")
    update_query: str | None = Field(default=None, description="Writable query for edits — usually ``<base>_put``.")
    insert_query: str | None = Field(default=None, description="Writable query for inserts — usually ``<base>_post``.")
    delete_query: str | None = Field(default=None, description="Writable query for deletes — usually ``<base>_delete``.")
    auto_load: bool = Field(default=False, description="Run the read query on screen open (v1's tbl_auto_load).")
    audit: bool = Field(default=False, description="Stamp AUD_<table> on every write (v1's tbl_audit). Wired in slice 5.")
    editable: bool = Field(default=True, description="Allow inline grid editing (v1's tbl_editable).")
    uploadable: bool = Field(default=False, description="Show the Excel/CSV import button (v1's tbl_uploadable).")
    dialog: ScreenDialog | None = Field(default=None, description="Form for adding / editing a row — optional.")
    actions: list[Action] = Field(
        default_factory=list,
        description=(
            "Toolbar buttons on the screen (above the table) — each fires its action list when "
            "clicked. ParamBinds resolve against the currently-selected row (or empty when none). "
            "Common pattern: a ``run_query`` to run a report, then ``refresh`` to reload the grid."
        ),
    )
    row_menu: list[Action] = Field(
        default_factory=list,
        description="Right-click menu entries on a row (slice 6) — uses the same action shape.",
    )
    # Row-click → sibling-screen dialog. v2's port of v1's "FormsDialog inside a ly_ctxmenus"
    # pattern: a screen that itself has no ``tbl_frm_id`` but whose context menu carried a single
    # FormsDialog entry (the conventional "Display Properties" / "Edit details" action). The
    # migrator promotes that entry to a row-click target — the row's PK columns bind into the
    # named screen's read_query, the resulting row opens in that screen's dialog as a modal.
    # The redundant ctx-menu entry is dropped in the same migration step. Set together: id, the
    # optional connector override (blank → parent screen's), and the bind list (parent-row column
    # → target read-query :param).
    row_click_screen: str | None = Field(
        default=None,
        description=(
            "ID of a sibling v2 screen to open as a row-click dialog when *this* screen has "
            "no own ``dialog``. The target screen's read_query is fetched narrowed by "
            "``row_click_binds``, then opened in its dialog as a modal."
        ),
    )
    row_click_connector: str | None = Field(
        default=None,
        description="Connector hosting the target screen; blank → this screen's effective connector.",
    )
    row_click_binds: list[ParamBind] = Field(
        default_factory=list,
        description=(
            "Bind the clicked row's columns into the target screen's read_query. ``source`` reads "
            "a column on this screen's row; ``value`` is a literal. Same shape as field / action / "
            "row-menu binds."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> Screen:
        # Dialog tab ids must be unique within the dialog.
        if self.dialog:
            seen: set[str] = set()
            for tab in self.dialog.tabs:
                if tab.id in seen:
                    raise ValueError(f"screen {self.id!r}: duplicate dialog tab id {tab.id!r}")
                seen.add(tab.id)
        return self


class ScreensFile(BaseModel):
    """The top-level shape of ``screens.toml``. One ``[screens.<app>]`` per app, then one
    ``[screens.<app>.<screen_id>]`` per screen — flat dict-of-dict, keyed by id at both
    levels (matches how ``menus.toml`` is laid out)."""

    model_config = ConfigDict(extra="forbid")

    screens: dict[str, dict[str, Screen]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> ScreensFile:
        for app, screens in self.screens.items():
            for sid, screen in screens.items():
                # Pydantic sets the dict key as ``id`` automatically when we ``model_validate`` —
                # but operators editing the file by hand can mis-key one. Mismatched id is
                # confusing later; force the convention now.
                if screen.id and screen.id != sid:
                    raise ValueError(
                        f"screen {app!r}/{sid!r}: ``id`` field is {screen.id!r}, must match its key",
                    )
        return self


def parse_screens(data: dict[str, Any]) -> ScreensFile:
    """Validate a raw TOML dict into a :class:`ScreensFile`. Each inner screen's ``id`` is
    auto-set from its key when missing (most TOML by-hand authors won't repeat it). Each
    dialog tab missing the ``type`` discriminator defaults to ``"form"`` so screens.toml
    files written before the nested-tab variants were added keep validating."""
    apps = data.get("screens") or {}
    for app_name, screens in apps.items():
        if not isinstance(screens, dict):
            continue
        for sid, screen in screens.items():
            if not isinstance(screen, dict):
                continue
            # Inject `id` from the dict key when an entry omits it — the validator above then
            # enforces that an explicit `id` matches its key.
            if not screen.get("id"):
                screen["id"] = sid
            # Default tab kind = "form" for backward compat with the pre-nested-tab schema —
            # everything before this commit was a flat field grid, and TOML by-hand authors
            # routinely omit defaults. Pydantic's discriminated-union resolver needs the key
            # to be present to pick a branch.
            dialog = screen.get("dialog")
            if isinstance(dialog, dict):
                tabs = dialog.get("tabs")
                if isinstance(tabs, list):
                    for tab in tabs:
                        if isinstance(tab, dict) and not tab.get("type"):
                            tab["type"] = "form"
    return ScreensFile.model_validate(data)


def load_screens(path: Path | str) -> ScreensFile:
    """Load and validate ``screens.toml``. A missing file yields an empty screens set."""
    path = Path(path)
    if not path.exists():
        return ScreensFile()
    with path.open("rb") as fh:
        return parse_screens(tomllib.load(fh))
