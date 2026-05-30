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

# ``ParamBind`` lives in connectors.config (no upstream deps) so v2 has one single mechanism for
# every parameter binding — lookups, actions, row menus, nested-tab read filters, cascading
# filters on ``ColumnHint.lookup_param_binds``. Re-exported here for back-compat: external code
# that does ``from liberty.screens import ParamBind`` keeps working.
from liberty.connectors.config import ColumnHint, ParamBind


class FieldCondition(BaseModel):
    """A condition based on another field's current value on the same dialog. Used by
    ``visible_when`` / ``required_when`` / ``disabled_when`` to make the dialog react as the
    operator types. Add multiple rules to AND them — all must hold for the parent to fire."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="The other dialog field whose live value gates this rule.")
    value: str | list[str] = Field(description="The expected value, or list of values (any one matches).")


class ScreenField(BaseModel):
    """A field on a dialog tab — references a column from the screen's ``columns`` list by
    ``name`` and adds placement (``colspan``) + per-dialog overrides.

    Most fields don't need any overrides: the column already defines the label, format, rule,
    default, lookup binds, required, and disabled. The field just says "show this column on
    this tab, spanning N grid cells". Use the override flags only when a specific dialog needs
    different behaviour — e.g. "this column is normally required but for this particular
    dialog it's optional".

    Conditional rules (``visible_when`` / ``required_when`` / ``disabled_when``) fire against
    the dialog's live form state — show / require / lock this field only when another field
    has a given value."""

    # ``extra = "ignore"`` so old screens.toml files keep loading. Operators re-migrate via
    # ``liberty-migrate screen`` to move legacy per-field metadata onto Screen.columns.
    model_config = ConfigDict(extra="ignore")

    name: str = Field(
        description="The screen column to render here (matches Screen.columns[].name).",
        # ScreenField.name references a Screen.columns[].name entry. Normalise to
        # UPPERCASE on save so the dialog field list stays consistent with the
        # column list (the column-name match is case-insensitive at runtime, but
        # mixed casing in the saved TOML is operator-confusing).
        json_schema_extra={"x_case": "upper"},
    )
    hidden: bool | None = Field(
        default=None,
        description="Hide this field on this dialog. Leave blank to inherit from the column.",
    )
    disabled: bool | None = Field(
        default=None,
        description="Make this field read-only on this dialog. Leave blank to inherit from the column.",
    )
    required: bool | None = Field(
        default=None,
        description="Require a value on this dialog. Leave blank to inherit from the column.",
    )
    colspan: int | None = Field(default=None, description="How many tab-grid columns this field spans.")
    visible_when: list[FieldCondition] = Field(
        default_factory=list,
        description=(
            "Show the field only when every rule matches the live form state. Leave empty to "
            "fall back to the static ``hidden`` flag."
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
    """Fields every tab kind shares — title + per-mode hide flags + translations + the per-tab
    action buttons. The discriminator ``type`` selects the variant: ``form`` (the default — a
    grid of fields), ``nested_form`` (an editable child-record form embedded inline),
    ``nested_table`` (a related-rows TableView embedded inline). v1's ``tab_disable_add`` /
    ``tab_disable_edit`` are captured as ``hide_on_add`` / ``hide_on_edit``."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable id within the dialog (used as the tab's key in builders).")
    label: str | None = Field(default=None, description="Default-language tab title.")
    l: dict[str, str] = Field(default_factory=dict, description="Per-language label overrides.")
    hide_on_add: bool = Field(default=False, description="Hide this tab when adding a new row.")
    hide_on_edit: bool = Field(default=False, description="Hide this tab when editing an existing row.")
    actions: list["Action"] = Field(
        default_factory=list,
        description=(
            "Action buttons that show on this tab — run a query, call an API endpoint, navigate "
            "to another screen, show a notification, refresh the screen, etc."
        ),
    )


class FormTab(_ScreenTabBase):
    """A grid of editable fields — the original tab kind. ``cols`` is the CSS grid column count
    (v1's tab_cols). Backward-compatible: a tab in screens.toml without an explicit ``type``
    field is treated as ``"form"`` (see :func:`parse_screens`)."""

    type: Literal["form"] = "form"
    cols: int | None = Field(default=None, description="How many columns the tab's grid spans.")
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
    cols: int | None = Field(default=None, description="How many columns the nested form's grid spans.")
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


class PromptField(BaseModel):
    """One input field on the *prompt dialog* shown before an action with ``prompt_fields`` fires —
    v2's port of v1's ``ly_act_params``. The action declares the inputs it needs from the user (a
    JDE "Create Role" workflow needs AUUSER / JOBN / MUSE / PID / UPMJ); the runtime opens a small
    sub-dialog, collects the values, and threads them into the chain's resolution context — every
    later ``ParamBind`` whose ``source`` matches a prompt field's ``name`` reads from the prompt
    instead of the parent form / row.

    Shape mirrors :class:`ScreenField` (so the migrator and the builder can reuse the same widgets),
    but with two practical differences:

    * **No backing column** — ``name`` is the prompt's *own* key (becomes the ParamBind source
      target); the widget comes from ``dd``/``format`` rather than a read-result column. The
      backend's screens-API resolves ``dd`` against the shared dictionary and attaches the
      resulting display rule (BOOLEAN / ENUM / LOOKUP) so the frontend renders the right combo.
    * **No grid context** — the prompt dialog is its own modal; ``colspan`` still controls the
      grid spread inside the prompt.

    Conditional rules (``visible_when`` / ``required_when`` / ``disabled_when``) evaluate against
    *the prompt's own form state*, not the parent dialog's — so a JDE param shown only when
    another JDE param is "AC" stays consistent inside the sub-dialog."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description=(
            "The prompt field's internal key. Acts as the ParamBind ``source`` target so a sibling "
            "task in the same chain can read it (e.g. ``{param: 'muse', source: 'MUSE'}``)."
        ),
    )
    dd: str | None = Field(
        default=None,
        description="Dictionary entry override (blank → looked up under ``name``).",
    )
    label: str | None = Field(default=None, description="Display label on the prompt dialog (overrides the dictionary).")
    format: str | None = Field(
        default=None,
        description=(
            "Display format override — only used when ``dd`` is unset or the entry has no "
            "``format``. Same values the dictionary uses (``text`` / ``number`` / ``date`` / "
            "``password`` / …)."
        ),
    )
    hidden: bool = Field(default=False, description="Hide this prompt field by default (paired with a ``visible_when`` rule).")
    disabled: bool = Field(default=False, description="Render the prompt field read-only (only meaningful with a ``default``).")
    required: bool = Field(default=False, description="Prompt cannot be confirmed without a value.")
    colspan: int | None = Field(default=None, description="How many columns of the prompt grid this field spans.")
    default: str | None = Field(default=None, description="Pre-fill the prompt with this value.")
    lookup_param_binds: list[ParamBind] = Field(
        default_factory=list,
        description=(
            "Parameters bound to this field's LOOKUP query (when the field's dd resolves to "
            "LOOKUP). ``source`` reads another prompt field on the same dialog; ``value`` is "
            "a literal."
        ),
    )
    visible_when: list[FieldCondition] = Field(
        default_factory=list,
        description="Conditional visibility, evaluated against the prompt dialog's own live state.",
    )
    required_when: list[FieldCondition] = Field(
        default_factory=list,
        description="Conditional required, evaluated against the prompt dialog's own live state.",
    )
    disabled_when: list[FieldCondition] = Field(
        default_factory=list,
        description="Conditional read-only, evaluated against the prompt dialog's own live state.",
    )


class _ActionBase(BaseModel):
    """Fields shared by every action variant. The ``type`` discriminator selects the variant."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable id within the screen.")
    label: str | None = Field(default=None, description="Display label (button caption / log line).")
    stop_on_error: bool = Field(
        default=True,
        description="When this action raises, abort the rest of the action list. Set false to make this action best-effort.",
    )


class _PromptableMixin(BaseModel):
    """Shared by the three ParamBind-bearing action variants (``run_query``, ``call_api``,
    ``navigate``) — declares the *prompt-before-fire* dialog. When ``prompt_fields`` is non-empty
    the runtime opens a small sub-dialog *before* this action fires, collects the operator's input,
    and merges the values into the chain's resolution context (so this action's own ``param_binds``
    *and* every later action in the same chain can refer to a prompt field via
    ``source: "<NAME>"``). Cancelling the prompt aborts the chain — the runtime treats it as a soft
    cancel, no error surfaced; same convention as :class:`ConfirmAction`."""

    prompt_fields: list[PromptField] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Prompt"},
        description=(
            "Inputs to collect from the operator before this action fires. Empty fires the "
            "action immediately."
        ),
    )
    prompt_title: str | None = Field(
        default=None,
        json_schema_extra={"x_group": "Prompt"},
        description="Title of the prompt sub-dialog (falls back to the action's ``label``).",
    )
    prompt_l: dict[str, str] = Field(
        default_factory=dict,
        json_schema_extra={"x_group": "Prompt"},
        description="Per-language overrides for ``prompt_title``: ``{language_code: translated}``.",
    )
    prompt_cols: int | None = Field(
        default=None,
        json_schema_extra={"x_group": "Prompt"},
        description="CSS grid column count for the prompt dialog's fields (default: 2).",
    )
    prompt_submit_label: str | None = Field(
        default=None,
        json_schema_extra={"x_group": "Prompt"},
        description="Caption on the prompt's primary button (default: the action's ``label`` or i18n ``common.confirm``).",
    )


class RunQueryAction(_PromptableMixin, _ActionBase):
    """Execute a connector query (the most common action — v1's ``ly_act_tasks evt_type='QUERY'``).
    ``param_binds`` resolves at call time against the firing context (the dialog form's live values,
    the row for a row-menu action, or the chain context inside a :class:`ChainAction`): same
    :class:`ParamBind` shape used for lookups.

    The v2 form of v1's ``FormsDialog``: a screen whose main ``update_query`` writes one table can
    list extra ``RunQueryAction``s on its ``dialog.on_save`` to write related tables that share a
    PK (e.g. NOMASX1's ``settings_applications`` → apps + apps_jde + apps_ldap).

    When ``bind_result = true`` the action's rows land in the chain context under this step's
    ``id`` as ``{rows, first_row, success}`` so later steps in the same :class:`ChainAction` can
    bind from them via a ``ParamBind {source: '<id>.first_row.COL'}`` (v2's port of v1's
    ``TASK_<id>.RESULTS[0].COL`` references)."""

    type: Literal["run_query"] = "run_query"
    connector: str | None = Field(
        default=None,
        description="Connector hosting the query. Blank uses the screen's connector.",
    )
    query: str = Field(description="Connector query to run.")
    param_binds: list[ParamBind] = Field(
        default_factory=list,
        description="Bind the query's ``:placeholder`` parameters from the firing context.",
    )
    bind_result: bool = Field(
        default=False,
        description=(
            "Capture this query's rows into the chain context so later steps in the same "
            "Chain can reference them via ``source: '<step_id>.first_row.<col>'``."
        ),
    )


class CallApiAction(_PromptableMixin, _ActionBase):
    """Call an API endpoint on a configured API connector — v1's ``evt_type='API'``. The
    endpoint's own ``{{placeholder}}`` template wins; ``param_binds`` is for the *query string /
    body* parameters the endpoint declares as bindable.

    Same ``bind_result`` semantics as :class:`RunQueryAction` — the response items land in the
    chain context under this step's ``id`` so later steps can bind from them."""

    type: Literal["call_api"] = "call_api"
    connector: str = Field(description="API connector to call (must be type = ``api``).")
    endpoint: str = Field(description="Endpoint name on that connector.")
    param_binds: list[ParamBind] = Field(
        default_factory=list,
        description="Bind values into the endpoint's placeholders from the firing context.",
    )
    bind_result: bool = Field(
        default=False,
        description="Capture the API response into the chain context so later steps can reference it.",
    )


class NavigateAction(_PromptableMixin, _ActionBase):
    """Open another TableView (v1's "drill into another table" pattern) — the row-menu staple.
    ``to`` names the target query (matching a ``QueryDef.name`` on ``connector``); the URL the
    runtime opens is ``/sql/{connector}/{to}`` with ``param_binds`` resolved against the firing
    context and forwarded as ``?<param>=<value>`` query-string entries. The destination's
    TableView seeds its param form from those — so a row-menu item "View this user's roles"
    becomes ``{to: "roles_get", connector: "nomasx1", param_binds: [{param: "USR_ID", source:
    "usr_id"}]}`` and ends up opening ``/sql/nomasx1/roles_get?USR_ID=<the-clicked-user-id>``."""

    type: Literal["navigate"] = "navigate"
    to: str = Field(description="Target query name (the destination URL is ``/sql/<connector>/<to>``).")
    connector: str | None = Field(
        default=None,
        description="Connector hosting the target query. Blank uses the firing screen's connector.",
    )
    param_binds: list[ParamBind] = Field(
        default_factory=list,
        description="Forwarded as ``?<param>=<value>`` query-string entries. The target's filter panel seeds from these.",
    )


class SetFieldAction(_ActionBase):
    """Change the value of a field on the current form (only meaningful from a dialog context).
    ``target`` is the destination field; ``value`` is a literal or ``source`` reads from another
    field's current value (the same ParamBind value-vs-source dichotomy)."""

    type: Literal["set_field"] = "set_field"
    target: str = Field(description="Field name to write into.")
    value: str | None = Field(default=None, description="Literal value to set.")
    source: str | None = Field(
        default=None,
        description="Read the value at call time from another field, the chain context, or a ``#BUILTIN#``.",
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


# ── workflow steps (v1's named-action shape, ported as inline action variants) ──────────────
# A v1 ``ly_actions`` row is one logical button that runs N tasks with shared context
# (``allParams.current = {INPUT, TASK_<id>: {RESULTS: …}}``) and forks via IF / iterates via
# LOOP / returns values to the caller via RETURN. v2 brings the same shape inline on screens —
# one :class:`ChainAction` button whose ``steps`` list runs sequentially, with the chain
# context resolving ``ParamBind {source: '<step_id>.first_row.COL'}`` references against each
# step's recorded result. Shared / cross-screen actions are a later slice (Slice D in
# CLAUDE.md); for now every chain lives where it's used.


class Condition(BaseModel):
    """Predicate evaluated by :class:`IfAction`. v2's port of v1's ``ly_conditions`` — but
    flatter: a single ``source`` path resolved against the chain context (or form field),
    compared to ``value`` with ``operator``. The migration falls back to ``operator = 'truthy'``
    + a warning when v1's condition can't be expressed in this shape (multi-clause OR / AND
    predicates the v2 builder will grow in a later slice)."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        description=(
            "What to compare. Pick a form field, a chain-context path (``INPUT.<name>`` / "
            "``<step_id>.first_row.<col>``), or a ``#BUILTIN#``."
        ),
    )
    operator: Literal[
        "equals", "not_equals", "has_rows", "no_rows", "truthy", "falsy",
        "greater_than", "less_than",
    ] = Field(
        default="truthy",
        description=(
            "``equals`` / ``not_equals`` compare to ``value``. ``has_rows`` / ``no_rows`` test "
            "the array length. ``truthy`` / ``falsy`` is a boolean cast. ``greater_than`` / "
            "``less_than`` compare numerically."
        ),
    )
    value: str | None = Field(
        default=None,
        description="The value to compare against (used by equals / not_equals / greater_than / less_than).",
    )


class ChainAction(_PromptableMixin, _ActionBase):
    """One button (or hook entry) that fires a *sequence of steps* sharing a single context.
    v2's form of a v1 named-action workflow (``ly_actions`` + N rows in ``ly_act_tasks``).

    The chain context accumulates as the chain runs:

    ::

        {
            INPUT:       { /* prompt_fields values + caller-passed params */ },
            <step_id>:   { rows: [...], first_row: {...}, success: bool },
            loop:        { /* current LoopAction iteration's element fields */ },
        }

    Steps reference values via ``ParamBind {source: '<dotted.path>'}`` — see :class:`Condition`
    for the path syntax. Existing form-field bindings (no dots) keep working — the runtime tries
    the chain context first, then falls back to the firing context (dialog form / clicked row).

    ``prompt_fields`` from :class:`_PromptableMixin` ride on the *chain*: the operator's inputs
    land in the chain context under ``INPUT.<name>`` once, fired before the first step. Steps
    don't carry their own prompt_fields (the runner won't show a sub-dialog mid-chain) — that
    keeps the UX predictable."""

    type: Literal["chain"] = "chain"
    steps: list["Action"] = Field(
        default_factory=list,
        description="Ordered sub-steps. May include If / Loop for nested control flow.",
    )


class IfAction(_ActionBase):
    """Conditional branching inside a :class:`ChainAction`. Evaluates :attr:`condition` against
    the chain context, then walks :attr:`then_steps` (when true) or :attr:`else_steps` (when
    false). Either branch may be empty — a one-armed IF is fine.

    v2's port of v1's ``evt_type='IF'`` tasks (+ ``evt_brc_true`` / ``evt_brc_false`` branch
    membership). The v1 model used labeled jumps (``currentBranch.current`` + ``evt_brc_id``
    filtering); v2 inlines each branch's steps for a cleaner read."""

    type: Literal["if"] = "if"
    condition: Condition = Field(description="Predicate evaluated against the chain context.")
    then_steps: list["Action"] = Field(default_factory=list)
    else_steps: list["Action"] = Field(default_factory=list)


class LoopAction(_ActionBase):
    """Iterate over an array resolved from the chain context, running :attr:`steps` once per
    element. The current element is bound under ``loop`` for the duration of the iteration — a
    step inside the loop reads it via ``ParamBind {source: 'loop.<field>'}``.

    v2's port of v1's ``evt_loop='Y'`` task modifier. v1 only allowed looping on QUERY tasks
    over a single source (``evt_loop_array``); v2 makes LOOP a block with arbitrary nested
    steps (still typed Actions)."""

    type: Literal["loop"] = "loop"
    source: str = Field(
        description=(
            "Path to an array in the chain context (e.g. ``select_workbench.rows``). The body "
            "runs once per element, with the element bound under ``loop``."
        ),
    )
    steps: list["Action"] = Field(default_factory=list, description="Body of the loop — runs once per element.")


class ReturnAction(_ActionBase):
    """Bind chain-context values back into the **caller's** form fields. v2's port of v1's
    ``evt_type='RETURN'`` task: after a workflow looked up a value (e.g. a SELECT that found
    the user's email), this step writes it into the dialog's matching field. Useful for
    "look up + populate" workflows."""

    type: Literal["return"] = "return"
    bindings: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "``{form_field: chain_context_path}`` — each entry reads the chain context and writes "
            "it into the dialog's matching field. Only meaningful when the chain fires from a dialog."
        ),
    )


# Discriminated union — every Action variant carries a ``type`` literal; Pydantic picks the right
# subclass when validating a raw dict (so screens.toml's `[[on_save]] type = "run_query"` validates).
Action = Annotated[
    Union[
        RunQueryAction, CallApiAction, NavigateAction, SetFieldAction,
        ConfirmAction, NotifyAction, RefreshAction,
        ChainAction, IfAction, LoopAction, ReturnAction,
    ],
    Field(discriminator="type"),
]

# ``_ScreenTabBase`` declares ``actions: list["Action"]`` as a forward reference (Action is
# defined here, after the tab classes). Rebuild every tab variant so Pydantic resolves the
# union before any model_validate call. ChainAction / IfAction / LoopAction also carry forward
# references to the same union — rebuild them so their nested step schemas resolve.
FormTab.model_rebuild()
NestedFormTab.model_rebuild()
NestedTableTab.model_rebuild()
ChainAction.model_rebuild()
IfAction.model_rebuild()
LoopAction.model_rebuild()


class SheetSpec(BaseModel):
    """One sheet inside a workbook export — a named tab whose rows come from a connector query.

    The query runs once per workbook (per ``split_value`` produced by the parent screen's
    ``WorkbookExport.split_by``). Operator binds the split value into the sheet's
    ``:placeholder`` params via :class:`ParamBind` — typically ``source = "split_value"``,
    so a single declared param ``:DEP_GROUP`` becomes the running group key. The same
    ``param_binds`` shape used elsewhere keeps the editor + runtime consistent.

    Two layout modes, controlled by ``split_by``:

    * **Single sheet** (``split_by`` blank): the query's rows fill one worksheet, name from
      ``name`` (with ``{{split_value}}`` resolved against the workbook's group key).
    * **Fan-out** (``split_by = "<column>"``): the query's rows are partitioned in-memory by
      that column → one worksheet per distinct value. ``name`` may reference
      ``{{sheet_value}}`` for the partition value. Same single DB roundtrip — the partition
      is purely client-side. This is v2's port of v1's ``tbl_sheet`` (per-app sheets within
      a per-group xlsx)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description=(
            "Sheet name inside the xlsx. Supports ``{{split_value}}`` (the workbook's group key) "
            "and ``{{sheet_value}}`` (the sheet partition value, only when ``split_by`` is set) "
            "substitution. Excel truncates names past 31 characters and rejects some special "
            "characters; the exporter sanitises automatically."
        ),
    )
    connector: str | None = Field(
        default=None,
        description="Connector that hosts the sheet's query. Blank uses the screen's connector.",
    )
    query: str = Field(description="Query that produces the sheet's rows.")
    split_by: str | None = Field(
        default=None,
        description=(
            "Optional: column on the sheet's query whose distinct values produce one worksheet "
            "each (v1's ``tbl_sheet``). Blank produces a single worksheet. The query still runs "
            "once; partitioning is done client-side in declaration / first-seen order."
        ),
    )
    param_binds: list[ParamBind] = Field(
        default_factory=list,
        description=(
            "Bind values into the query's ``:placeholder`` params. ``source = \"split_value\"`` "
            "is the current workbook's group key (the value of the parent's ``split_by`` column); "
            "literal ``value`` is also accepted."
        ),
    )


class WorkbookExport(BaseModel):
    """Configure multi-file / multi-sheet xlsx export for this screen — v2's port of v1's
    ``ly_tables.tbl_workbook`` / ``tbl_sheet`` (Phase 9). Triggered from the TableView's
    ``Export workbooks`` button; the backend ``POST /api/screens/{app}/{id}/export``
    streams a single ``.xlsx`` or a ``.zip`` of multiple xlsx files.

    Two flavours, controlled by ``split_by``:

    * **Single workbook** (``split_by`` blank): one xlsx file containing every sheet, each
      sheet running its own query without splitting.
    * **Per-group workbooks** (``split_by = "<column>"``): the screen's read query runs first
      to collect the distinct values of that column. One xlsx is produced per distinct
      value; inside it, each :class:`SheetSpec` query runs with ``"split_value"`` bound to
      the current group key.

    Example — the ``ldap_apps_get`` screen exports one file per ``DEP_GROUP``, each file
    holding an "Apps" sheet (rows of ``ldap_apps_get`` filtered to that group) plus an
    "LDAP Users" sheet (rows of a sibling ``ldap_users_get`` filtered the same way).
    """

    model_config = ConfigDict(extra="forbid")

    split_by: str | None = Field(
        default=None,
        description=(
            "Column on the screen's read query whose distinct values produce one xlsx file each. "
            "Blank produces a single xlsx file. Sheets bind to the current group via "
            "``source = \"split_value\"`` on their ``param_binds``."
        ),
    )
    sheets: list[SheetSpec] = Field(
        default_factory=list,
        description="One sheet per entry, in display order inside the xlsx.",
    )
    file_name_template: str | None = Field(
        default=None,
        description=(
            "How to name each xlsx file. ``{{split_value}}`` is the current group key; "
            "``{{screen}}`` is the screen id. Blank defaults to ``<screen>_<split_value>.xlsx``."
        ),
    )
    archive_name: str | None = Field(
        default=None,
        description=(
            "Name of the .zip downloaded when several workbooks are produced. Blank defaults to "
            "``<screen>.zip``."
        ),
    )


class ScreenDialog(BaseModel):
    """The form shown when the user adds / edits a row of this screen. Optional — a screen
    with no dialog renders as a read-only / grid-edit table."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, description="Dialog title (falls back to the screen's label).")
    tabs: list[ScreenTab] = Field(default_factory=list, description="Tabs, in display order. At least one.")
    # ── lifecycle hooks ────────────────────────────────────────────────────────────────────
    # v1 only had on-save and on-delete events; v2 extends to a fuller dialog lifecycle so the
    # operator can wire side-effects at the right moment without faking them into on_save.
    # All four are optional + ordered lists of Action; the runner applies the same switch
    # (run_query / call_api / notify / refresh / navigate / set_field / confirm) and stops on
    # the first error unless an action sets ``stop_on_error = false``.
    on_load: list[Action] = Field(
        default_factory=list,
        description=(
            "Runs immediately after the dialog opens and the row's data has been loaded — for "
            "edit mode, after the row is fetched; for add mode, after default values are seeded. "
            "Useful for: refreshing a lookup, prefetching related rows, hitting a webhook to "
            "log the open. ParamBinds resolve against the just-loaded form state."
        ),
    )
    on_save: list[Action] = Field(
        default_factory=list,
        description=(
            "Runs after the dialog's main update / insert succeeds. Each action's ParamBinds "
            "resolve against the form's live state. Use this for multi-table writes — chain "
            "extra ``run_query`` actions to update related tables on the same PK."
        ),
    )
    on_cancel: list[Action] = Field(
        default_factory=list,
        description=(
            "Runs when the user closes the dialog *without* saving (Cancel button or click-outside). "
            "Useful for cleanup — release a lock, drop a draft row, log abandoned edits. The "
            "actions fire *before* the dialog actually closes; an error blocks the close (so the "
            "user sees the failure and can retry)."
        ),
    )


class ScreenTreeview(BaseModel):
    """Parent / child hierarchy config — declares a screen's read query as a tree.
    When set, the TableView surfaces a third view toggle (Tree) alongside Table /
    Chart; rows are folded into a recursive node tree where each node's children
    are the rows whose ``parent`` column value equals this row's ``child`` column
    value. Rows with an unresolvable parent (null / empty / not present in the
    result set) become tree roots — robust for filtered / narrowed queries that
    return a partial tree.

    v1 port of ``ly_tbl_treeview`` (tbl_tree_type='hierarchy'); v1's other tree
    type — group-by — is covered by v2's :attr:`Screen.initial_group_by`, no
    separate config needed for that one.

    Example for ``security_menus`` (one node per (root, parent, child) edge):

        [screens.nomasx1.security_menus.treeview]
        parent = "menu_parent_id"
        child  = "menu_child_id"
        label  = "menu_id"
    """

    model_config = ConfigDict(extra="forbid")

    parent: str = Field(
        description=(
            "Column carrying each row's PARENT id. The tree walker links a "
            "row UP by matching this against another row's ``child`` value. "
            "An empty / null / unresolvable value makes the row a root."
        ),
        json_schema_extra={"x_enum_ref": "SCREEN_COLUMNS"},
    )
    child: str = Field(
        description=(
            "Column carrying each row's OWN id — what other rows' ``parent`` "
            "values match against. Must be unique enough that two rows don't "
            "collapse into the same tree node (typically the same column the "
            "table's primary key uses)."
        ),
        json_schema_extra={"x_enum_ref": "SCREEN_COLUMNS"},
    )
    label: str = Field(
        description=(
            "Display text for each node. **Two forms**:\n"
            "* a bare column name — ``menu_id`` renders the row's ``menu_id`` value;\n"
            "* a template with ``{column}`` placeholders — "
            "  ``{menu_label} ({menu_object})`` substitutes each column's value and "
            "  renders the surrounding literal text verbatim. Useful for menu trees "
            "  where the operator wants the human label *and* the JDE object id on "
            "  the same line. Empty placeholders render as empty strings; unknown "
            "  column names render as the literal ``{...}`` so the operator notices."
        ),
        json_schema_extra={"x_enum_ref": "SCREEN_COLUMNS"},
    )
    order_by: list[str] = Field(
        default_factory=list,
        description=(
            "Sibling sort order — column name(s) the tree sorts children by, "
            "in priority order. Empty (default) falls back to alphabetic on "
            "``label``. Example for ``security_menus``: ``[\"MENU_SEQ_UKID\"]`` "
            "preserves JDE's authoring sequence (menu items render in the order "
            "the JDE developer placed them, not alphabetically). Numeric values "
            "sort numerically, strings lexicographically (per-column auto-detect)."
        ),
        json_schema_extra={"x_enum_ref": "SCREEN_COLUMNS"},
    )


class Screen(BaseModel):
    """A screen — list + dialog. Keyed by ``id`` within the app's screens map."""

    # ``extra = "ignore"`` (not "forbid") so a screens.toml written before Phase 3 still loads.
    # Removed legacy keys (``audit: bool``, ``max_rows`` was never on Screen, etc. — and the
    # whole legacy field shape from before Phase 2) are silently dropped on parse; operators
    # re-migrate at their pace via ``liberty-migrate screen`` to repopulate ``audit_table``,
    # ``max_rows``, ``key_columns``, and the per-screen column hints.
    model_config = ConfigDict(extra="ignore")

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
    columns: list[ColumnHint] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Columns"},
        description=(
            "The columns on this screen — display title, format, filtering, edit rules. Drives "
            "both the grid view and the dialog form. Add one entry per column the read query "
            "returns that needs explicit configuration; columns not listed here keep the query's "
            "discovered name and the dictionary's defaults."
        ),
    )
    auto_load: bool = Field(default=False, description="Run the read query as soon as the screen opens, with no Run click.")
    audit_table: str | None = Field(
        default=None,
        description=(
            "Mirror every successful write on this screen into this audit table — convention is "
            "``AUD_<TABLE>``. Each row gets three extra columns: AUD_ACTION (INSERT/UPDATE/DELETE), "
            "AUD_USER (the caller's username), AUD_DATE (server timestamp). Blank = no auditing. "
            "v1 only let you toggle audit on/off (the name was hardcoded); v2 lets you name the "
            "table so several screens can share one (e.g. all security screens → ``AUD_SECURITY``)."
        ),
        json_schema_extra={"x_placeholder": "e.g. AUD_USERS — leave blank to disable"},
    )
    max_rows: int | None = Field(
        default=None,
        description="Cap how many rows this screen's read query returns. Blank = use the connector's / pool's default.",
    )
    key_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Result columns that uniquely identify a row — also derivable per-column by ticking "
            "``key`` on the column hint (the visual designer's Columns tab uses that). When this "
            "list is empty, the runtime falls back to the column hints whose ``key`` is true. "
            "Set this explicitly only when you want to override the column-derived list."
        ),
    )
    editable: bool = Field(default=True, description="Allow inline grid editing on this screen.")
    uploadable: bool = Field(default=False, description="Show the Excel / CSV import button on this screen.")
    initial_group_by: list[str] = Field(
        default_factory=list,
        description=(
            "Default tanstack-table grouping — column name(s) the grid groups by on first "
            "open (operators can still ungroup / regroup from the Group control). One name "
            "groups by that single column; multiple names nest groups in order. Example: "
            "``[\"CLA_MODULE\"]`` on the last_refresh screen → audit rows fold under their "
            "module (DATABASE / SECURITY / LICENSE / …) so an operator sees per-module "
            "refresh status at a glance instead of a flat row dump."
        ),
        json_schema_extra={"x_enum_ref": "SCREEN_COLUMNS"},
    )
    treeview: ScreenTreeview | None = Field(
        default=None,
        description=(
            "Parent / child hierarchy — when set, the TableView gains a Tree view toggle "
            "alongside Table / Chart. The runtime walks rows via ``parent`` → ``child`` "
            "edges to build a recursive node tree; rows with an unresolvable parent become "
            "roots. Use for screens like ``security_menus`` (menu_parent_id → menu_child_id) "
            "where the natural shape is a tree, not a flat list. See :class:`ScreenTreeview`."
        ),
    )
    chart_id: str | None = Field(
        default=None,
        description=(
            "Saved chart that pre-fills the Chart view tab when this screen opens. Operators "
            "save charts from the TableView's Chart toggle (the floppy icon writes to "
            "``charts.toml``); pointing a screen at one of those ids means the Chart toggle "
            "renders with the saved spec instead of the empty default + a localStorage seed. "
            "Blank = no default (the TableView falls back to its session-local localStorage "
            "spec, the pre-Phase-F behaviour). Validated to exist at config-load time."
        ),
        json_schema_extra={"x_enum_ref": "CHART_IDS"},
    )
    export: WorkbookExport | None = Field(
        default=None,
        description=(
            "Multi-file / multi-sheet xlsx export config — v2's port of v1's "
            "``tbl_workbook`` / ``tbl_sheet``. When set, the TableView gains an "
            "``Export workbooks`` button that streams one xlsx (or a .zip of several) from "
            "``POST /api/screens/{app}/{id}/export``. Blank hides the button."
        ),
    )
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
    # ── row-level lifecycle hooks ──────────────────────────────────────────────────────────
    # Fire after a row is mutated — whether via dialog Save (insert/update mode) or via inline
    # batch edit (the grid's Save button). v2's port of v1's ``ly_evt_cpt`` FormsTable events
    # (evt 2 = row insert, evt 3 = row delete). The hooks are screen-level (not dialog-level)
    # because the same mutation can happen from either path; this single hook fires for both.
    # ParamBinds resolve against the *new row's* values for on_insert / on_update, or the
    # *deleted row's* values for on_delete.
    on_insert: list[Action] = Field(
        default_factory=list,
        description="Runs after a row is inserted — via dialog Save in add mode, or via the inline grid's Save.",
    )
    on_update: list[Action] = Field(
        default_factory=list,
        description="Runs after a row is updated — via dialog Save in edit mode, or via the inline grid's Save.",
    )
    on_delete: list[Action] = Field(
        default_factory=list,
        description="Runs after a row is deleted — via the dialog's Delete button, or via the inline grid.",
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
    # Row-click → SPA route. The escape hatch for screens that drill into a hand-written React
    # page rather than another Screen dialog — needed when the destination shows things SQL can't
    # easily render (live-streamed logs, custom charts, multi-source merges). The template uses
    # ``{column_name}`` placeholders that resolve against the clicked row; values are URL-encoded
    # so columns with ``/`` or ``?`` don't break the route. Wins over ``row_click_screen`` when
    # both are set, since the operator opting into a route is the more specific intent.
    #
    # The first concrete use is ``screens.nomaflow.runs`` → ``/nomaflow/runs/{id}`` (React
    # ``RunDetail.tsx``), which polls live log lines from the in-memory ring buffer — something
    # a SQL-backed Logs screen can't do (``nomaflow_run_logs`` is one TEXT blob written once at
    # run finalize, so live tailing only works through the API, not via a query).
    row_click_route: str | None = Field(
        default=None,
        description=(
            "SPA route opened on row click instead of a sibling-screen dialog. Use "
            "``{column_name}`` placeholders to interpolate the clicked row's columns "
            "(URL-encoded). Example: ``/nomaflow/runs/{id}``. Wins over ``row_click_screen``."
        ),
    )

    # ── upgrade-safety flag ────────────────────────────────────────────────────────────────
    # When true, this screen survives an ``overwrite`` import (POST /admin/import-package
    # strategy=overwrite). Customers tick this on screens they've forked or hand-edited so
    # a vendor-shipped package can't clobber their customisation. The base version is still
    # imported under its own id; this protects the operator's local fork. See the long
    # comment in liberty/web/package_import.py for the apply semantics. Default False —
    # operators opt in.
    override: bool = Field(
        default=False,
        description=(
            "Mark this screen as a customer customisation that an upgrade-package import "
            "should NOT overwrite. Use after cloning the vendor's base screen + editing — "
            "subsequent imports of the same package leave this fork alone. Doesn't affect "
            "the runtime; only the import-package endpoint reads it."
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
        # row_click_route placeholders must reference real columns on this screen — catch the
        # typo at config-load time instead of at the user's first click (silent no-op route).
        if self.row_click_route:
            import re
            placeholders = set(re.findall(r"\{([^{}]+)\}", self.row_click_route))
            if placeholders:
                known = {c.name for c in self.columns}
                missing = placeholders - known
                if missing:
                    raise ValueError(
                        f"screen {self.id!r}: row_click_route references unknown column(s) "
                        f"{sorted(missing)!r} — known: {sorted(known)!r}"
                    )
        return self

    def effective_key_columns(self) -> list[str]:
        """The row-identifying columns for this screen. Returns ``key_columns`` when set,
        else the names of every column hint with ``key=True`` (in column-hint order). The
        visual designer's Columns tab is where operators flip ``key`` per column; the General
        tab's ``key_columns`` field stays as an explicit override for less-common cases."""
        if self.key_columns:
            return list(self.key_columns)
        return [c.name for c in self.columns if c.key]


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
            # to be present to pick a branch. **Variant-specific keys also infer the kind**:
            # a tab carrying ``screen`` resolves to nested_table, one carrying ``read_query``
            # (without ``screen``) resolves to nested_form. This is the safety net for the PUT
            # round-trip via /admin/config/screens/parsed — the GET strips the Literal
            # discriminator (it matches its default and exclude_defaults drops it), so without
            # this inference a saved nested tab would re-validate as FormTab and reject its
            # extra keys (read_query / update_query / screen / param_binds).
            dialog = screen.get("dialog")
            if isinstance(dialog, dict):
                tabs = dialog.get("tabs")
                if isinstance(tabs, list):
                    for tab in tabs:
                        if isinstance(tab, dict) and not tab.get("type"):
                            if isinstance(tab.get("screen"), str):
                                tab["type"] = "nested_table"
                            elif isinstance(tab.get("read_query"), str):
                                tab["type"] = "nested_form"
                            else:
                                tab["type"] = "form"
    return ScreensFile.model_validate(data)


def load_screens(path: Path | str) -> ScreensFile:
    """Load and validate ``screens.toml``. A missing file yields an empty screens set."""
    path = Path(path)
    if not path.exists():
        return ScreensFile()
    with path.open("rb") as fh:
        return parse_screens(tomllib.load(fh))
