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
from typing import Any

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


class ScreenTab(BaseModel):
    """One tab in a dialog. ``cols`` is v1's per-tab grid width (number of columns) — the
    frontend lays the fields out in a CSS grid that wide. v1's tab_disable_add /
    tab_disable_edit are captured as ``hide_on_add`` / ``hide_on_edit`` so the migration
    keeps fidelity; the runtime checks them in slice 2."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable id within the dialog (used as the tab's key in builders).")
    label: str | None = Field(default=None, description="Default-language tab title.")
    l: dict[str, str] = Field(default_factory=dict, description="Per-language overrides: {language_code: translated_label}.")
    cols: int | None = Field(default=None, description="CSS grid column count for this tab's fields (v1's tab_cols).")
    hide_on_add: bool = Field(default=False, description="Hide this tab when *adding* a row (v1's tab_disable_add='Y').")
    hide_on_edit: bool = Field(default=False, description="Hide this tab when *editing* a row (v1's tab_disable_edit='Y').")
    fields: list[ScreenField] = Field(default_factory=list, description="Fields shown on this tab, in display order.")


class ScreenDialog(BaseModel):
    """The form shown when the user adds / edits a row of this screen. Optional — a screen
    with no dialog renders as a read-only / grid-edit table."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, description="Dialog title (falls back to the screen's label).")
    tabs: list[ScreenTab] = Field(default_factory=list, description="Tabs, in display order. At least one.")


class ScreenAction(BaseModel):
    """Placeholder for an action / event handler — fully shaped in slice 4. For now we capture
    just the id + label so screens can carry actions through migration round-trips without the
    runtime having to interpret them yet. Slice 4 will add ``trigger`` (on_load / on_save /
    toolbar / row_menu / …), the action body (run_query / call_api / set_field / …), and a
    ``param_binds: list[ParamBind]`` for feeding arguments at call time."""

    model_config = ConfigDict(extra="allow")  # forward-compat — slice 4 adds more fields

    id: str = Field(description="Stable id within the screen.")
    label: str | None = Field(default=None, description="Button / menu label.")


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
    actions: list[ScreenAction] = Field(
        default_factory=list,
        description="Toolbar buttons and event handlers (slice 4).",
    )
    row_menu: list[ScreenAction] = Field(
        default_factory=list,
        description="Right-click menu entries on a row (slice 6).",
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
    auto-set from its key when missing (most TOML by-hand authors won't repeat it)."""
    # Inject `id` from the dict key when an entry omits it — the validator above then
    # enforces that an explicit `id` matches its key.
    apps = data.get("screens") or {}
    for app_name, screens in apps.items():
        if isinstance(screens, dict):
            for sid, screen in screens.items():
                if isinstance(screen, dict) and not screen.get("id"):
                    screen["id"] = sid
    return ScreensFile.model_validate(data)


def load_screens(path: Path | str) -> ScreensFile:
    """Load and validate ``screens.toml``. A missing file yields an empty screens set."""
    path = Path(path)
    if not path.exists():
        return ScreensFile()
    with path.open("rb") as fh:
        return parse_screens(tomllib.load(fh))
