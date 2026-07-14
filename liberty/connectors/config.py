"""TOML schema for ``config/connectors.toml`` — pools and connector definitions.

This is the v2 replacement for v1's ``ly_qry_sql`` / ``ly_api`` / ``ly_api_conn``
metadata tables: executable definitions live in a hot-reloadable file on disk,
not in the database. The result *schema* is still discovered at query time
(``cursor.description``), never stored here.

Example::

    [pools.default]
    url = "postgresql+asyncpg://liberty:liberty@localhost/liberty"

    [connectors.liberty]
    type = "sql"
    pool = "default"

    [[connectors.liberty.queries]]
    name = "users_list"
    sql = "SELECT usr_id, usr_name FROM ly_users WHERE usr_status = :status"
    writable = false
    params = [{ name = "status", default = "ENABLED" }]

    [connectors.github]
    type = "api"
    base_url = "https://api.github.com"
    auth_type = "bearer"
    auth_token = "${GITHUB_TOKEN}"
    default_headers = { Accept = "application/vnd.github+json" }

    [[connectors.github.endpoints]]
    name = "get_repo"
    method = "GET"
    path = "/repos/{{owner}}/{{repo}}"
    params = [{ name = "owner" }, { name = "repo" }]
    response_field = "full_name"
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from liberty.config import substitute_env

# Connector auth configs must reference secrets, never inline them — an
# unresolved ``${NAME}`` becomes the empty string (see :func:`substitute_env`)
# so a missing secret fails loudly at call time rather than using literal text.

# --------------------------------------------------------------------------- #
# Pools
# --------------------------------------------------------------------------- #


class PoolConfig(BaseModel):
    """A named database pool — one SQLAlchemy async engine per entry. (Field docs are in
    ``description=`` so the config-builder UI shows them as form hints; see ``GET /admin/config/schema``.)"""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(description=(
        "SQLAlchemy *async* URL. Pick a **Dialect** to seed a template, then fill in user / host / db. "
        "A special-character password (@ / : / …) goes in the separate `password` field; supports ${ENV} refs."
    ))
    password: str | None = Field(
        default=None,
        description=(
            "DB password kept out of the URL — substituted in (URL-escaped) when the engine connects. "
            "May be an ENC: value (decrypted at runtime via the master key), plain text, or a ${ENV} "
            "reference. Leave blank if the password is already in the URL."
        ),
        json_schema_extra={"format": "password"},
    )
    dialect: str = Field(
        default="",
        description=(
            "SQLAlchemy backend name (postgresql / oracle / sqlite / mysql / mssql / …). Empty → derived from "
            "the URL. Used to pick a query's per-dialect SQL variant."
        ),
        json_schema_extra={"x_enum_ref": "DATASOURCE_TYPE"},
    )
    schemas: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "Schemas"}, description=(
        "Map ``#SCHEMA.<NAME>#`` placeholders in this pool's queries to real schema names. Lets the "
        "same query target dev vs prod (or several schemas under one DB user) without editing SQL."
    ))
    dblinks: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "DB Links"}, description=(
        "Map ``#DBLINK.<NAME>#`` placeholders in this pool's queries to a database-link suffix "
        "(e.g. ``SY = \"@ORCLPROD\"``) — appended to a table name so the same query reads a remote "
        "schema over a DB link in one environment and a local table in another (``…F0092#DBLINK.SY#`` "
        "→ ``…F0092@ORCLPROD``). A token with **no entry or an empty value resolves to nothing** — the "
        "placeholder is simply dropped, so a pool without DB links runs the query locally. Typically "
        "paired with a ``#SCHEMA.<NAME>#`` mapping on the same NAME."
    ))
    pool_size: int = Field(default=5, description="Persistent connections kept open.", json_schema_extra={"x_group": "Pool"})
    max_overflow: int = Field(default=10, description="Extra connections allowed beyond ``pool_size`` under load.", json_schema_extra={"x_group": "Pool"})
    pool_pre_ping: bool = Field(default=True, description="Test connection liveness before handing it out.", json_schema_extra={"x_group": "Pool"})
    pool_recycle: int = Field(default=-1, description="Recycle a connection after this many seconds (-1 = never).", json_schema_extra={"x_group": "Pool"})
    echo: bool = Field(default=False, description="Log every SQL statement (debug only).", json_schema_extra={"x_group": "Pool"})
    max_rows: int | None = Field(default=None, json_schema_extra={"x_group": "Pool"}, description=(
        "Default row cap for SELECTs on this pool. Falls back to 1000. Per-screen / per-request overrides win."
    ))
    arraysize: int | None = Field(default=None, json_schema_extra={"x_group": "Pool"}, description=(
        "Oracle only — rows fetched per DB round-trip (cursor ``arraysize``). The oracledb driver "
        "defaults to 100, which is conservative for large reads; raise it (e.g. 500–1000) to cut "
        "round-trips and speed up big tables. Ignored on non-Oracle pools (asyncpg/Postgres already "
        "batches larger). Blank → the driver default."
    ))
    trim_strings: bool = Field(default=False, json_schema_extra={"x_group": "Pool"}, description=(
        "Strip trailing whitespace from string cells on SELECT. Enable for Oracle pools with "
        "space-padded CHAR / NCHAR columns (JD Edwards is the canonical case)."
    ))
    coalesce_nulls: bool = Field(default=False, json_schema_extra={"x_group": "Pool"}, description=(
        "On INSERT / UPDATE, replace empty binds with type-appropriate defaults (a space for "
        "CHAR-family columns, 0 for numerics). Enable for Oracle pools with NOT-NULL string "
        "columns (Oracle treats ``''`` as NULL)."
    ))
    debug_sql: bool = Field(default=False, json_schema_extra={"x_group": "Pool"}, description=(
        "Log every executed statement on this pool — the resolved SQL (schema placeholders + "
        "filter wrap applied) and the final bound parameters (after trim / coalesce / sequence "
        "resolution), i.e. exactly what reaches the driver. Turn on to debug writes that affect "
        "0 rows; leave off in production (binds may contain sensitive values)."
    ))
    # NB: an earlier iteration carried ``strip_both_columns`` on the pool — moved to the
    # sql_copy step (Step.strip_both_columns) because JDE column names embed a 2-letter
    # table prefix (F0005's right-justified code is ``DRKY``, not ``KY``), so the right
    # column-name list is per-table. The step is where that distinction lives.


# --------------------------------------------------------------------------- #
# Shared bits
# --------------------------------------------------------------------------- #


class ParamDef(BaseModel):
    """A declared parameter — gives a `:name` placeholder a UI label and a default value."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The `:name` placeholder in the SQL / `{{name}}` in the endpoint.")
    label: str | None = Field(default=None, description="Form label for the parameter input (defaults to the name).")
    default: str | None = Field(default=None, description="Pre-filled value; blank means the caller omits it (→ SQL NULL for a query).")


class FilterDep(BaseModel):
    """A cascading filter — narrow this column's lookup options based on another filter's value.
    Example: pick an Application in one filter, the Role dropdown then shows only that
    application's roles. Only meaningful when this column's rule is LOOKUP."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        description="Another filter-flagged column on the same screen — its current value is the input.",
        json_schema_extra={"x_enum_ref": "DD_ENTRIES"},
    )
    column: str = Field(
        description="Column of the lookup query's result to match the source's value against.",
        json_schema_extra={"x_enum_ref": "DD_ENTRIES"},
    )


class VisibleWhen(BaseModel):
    """Show this column only when a server-filter has a given value. Useful when the same grid
    serves several modes — e.g. show the "From / To date" column only when "Status" is filtered
    to "Active". The column disappears from the grid entirely when the rule doesn't hold.
    Set multiple rules to AND them. ``field`` must be another filter-flagged column on the
    same screen."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        description="The filter column whose value gates this column's visibility.",
        json_schema_extra={"x_enum_ref": "SCREEN_COLUMNS", "x_case": "upper"},
    )
    value: str | list[str] = Field(description="The allowed value, or list of allowed values.")

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "value": self.value}


class DefaultWhen(BaseModel):
    """A conditional forced default — when sibling column ``field`` equals ``value``, THIS column's
    value is set to ``default`` and locked (read-only) on the dialog + grid, so the operator can't
    change a value the row's kind determines (e.g. on f00950: FSSETY='S' → FSDTAI=0; FSSETY='2' →
    FSRUN='N'). Lives on the TARGET column, like ``visible_when``. Reactive — applies on add and
    edit and re-applies when the discriminator changes. UI-only (the write path is unchanged).
    Multiple rules: the FIRST whose condition holds wins."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        description="The sibling column whose value gates this default.",
        json_schema_extra={"x_enum_ref": "SCREEN_COLUMNS", "x_case": "upper"},
    )
    value: str | list[str] = Field(description="The discriminator value, or list of values, that triggers this default.")
    default: str = Field(description="The value forced into this column (and locked) while the condition holds.")
    lock: bool = Field(
        default=True,
        description=(
            "Lock the column (read-only) while this default holds. True (default) = force the value "
            "AND disable the field — the value is system-determined. false = SEED the value when the "
            "condition becomes active but leave the field editable (the operator can override it; a "
            "good fit for a default like ``*ALL`` on a lookup column they may want to narrow)."
        ),
    )

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"field": self.field, "value": self.value, "default": self.default}
        if not self.lock:
            d["lock"] = False  # emit only when overriding the default so existing payloads are unchanged
        return d


class ParamBind(BaseModel):
    """Bind a parameter of a target query — for a lookup combo, an action argument, a row
    context-menu trigger, or a nested-tab filter. Two modes: bind a literal value, or bind
    another column's / field's current value (resolved at call time). Set exactly one of
    ``value`` or ``source``. ``source = "#LOGIN_USER#"`` and ``#SYSDATE#`` are reserved
    built-ins.

    ``default`` is the fallback bound when *source mode* resolves to NULL / empty at call
    time — v2's port of v1's ``ly_act_tasks_params.map_default``. Useful when a workflow
    step needs a sane fallback for an optional input (e.g. JDE F0092 inserts where a blank
    ``UPMJ`` should default to today). Ignored in value mode."""

    model_config = ConfigDict(extra="forbid")

    param: str = Field(
        description="Target parameter name (the ``:placeholder`` on the destination query).",
        # Param + source are column-name references. Normalise to UPPERCASE on save
        # so action chains / nested_table tabs / lookup binds all use one convention.
        # In a lookup_param_binds context the enclosing column/rule's ``rules_values`` picks the
        # lookup → its query's :params (LOOKUP_PARAMS__<lookup id>). Resolves to nothing (free text)
        # in non-lookup contexts (action / nested-table binds) — no enclosing ``rules_values``.
        json_schema_extra={"x_case": "upper", "x_enum_ref_ancestor": {"field": "rules_values", "prefix": "LOOKUP_PARAMS__"}},
    )
    value: str | None = Field(default=None, description="Literal value to bind.")
    source: str | None = Field(
        default=None,
        description="Read the value at call time from a column / form field / chain context path.",
        # Offer the screen's columns as a dropdown (still free-text — a chain step can read a dotted
        # context path like ``step1.first_row.col`` that isn't a column).
        json_schema_extra={"x_enum_ref": "SCREEN_COLUMNS", "x_case": "upper"},
    )
    default: str | None = Field(
        default=None,
        description="Fallback bound when the source resolves to NULL / empty. Ignored in value mode.",
    )


class ReturnBind(BaseModel):
    """Fill a sibling column from a LOOKUP's picked row. When this column's lookup value is
    chosen, the picked row's ``param`` column is written into ``column`` on the SAME row. The
    explicit, per-screen-column replacement for v1's implicit auto-by-dd return-param mapping —
    so the operator says exactly which returned field fills which column. Applies uniformly in
    the dialog form, the grid bulk-edit, and on Excel import (where it fills only when the target
    cell is empty, so an explicit imported value wins)."""

    model_config = ConfigDict(extra="forbid")

    param: str = Field(
        description=(
            "A column returned by the lookup query whose value flows back — one of the lookup's "
            "``return_params`` (the picked row's column with this name)."
        ),
        # The enclosing column/rule's ``rules_values`` picks the lookup → its ``return_params``
        # (LOOKUP_RETURN_PARAMS__<lookup id>), so the dropdown is narrowed to THIS lookup's fields.
        json_schema_extra={"x_enum_ref_ancestor": {"field": "rules_values", "prefix": "LOOKUP_RETURN_PARAMS__"}, "x_case": "upper"},
    )
    column: str = Field(
        description="The screen column on this row to fill with the returned value.",
        json_schema_extra={"x_enum_ref": "SCREEN_COLUMNS", "x_case": "upper"},
    )


class RulesWhen(BaseModel):
    """A conditional RULE override — when sibling column ``field`` equals ``value``, render THIS
    column with ``rules`` / ``rules_values`` instead of the column's base rule. The first matching
    entry wins; no match → the base ``rules``. ``rules = ""`` → plain input (no widget) — e.g. a JDE
    alias row where the value is typed directly, not looked up. Reactive per row / form state, in the
    dialog AND grid. Lives on the TARGET column, like ``default_when`` / ``visible_when``.

    **Per-rule binds.** Each conditional lookup gets its OWN ``lookup_param_binds`` + ``return_binds``
    — NOT the column's shared ones — because two rules on the same discriminator usually need
    different params: on f00950's FSDTAI, FSSETY 6/8 → ``get_form_name`` narrowed by ``OBNM``, while
    FSSETY 2/4 → ``get_data_item`` which must NOT be narrowed by OBNM (it would return nothing). The
    column-level ``lookup_param_binds`` / ``return_binds`` then apply only to the BASE rule (when no
    entry matches). Every referenced lookup is still loaded ONCE per distinct bind value (and cached);
    only the per-cell rule choice is per-row."""

    # x_summary → the editor's list row reads "FSSETY · 2/4 · get_data_item" so two entries on the
    # same discriminator are distinguishable (without it both rows just showed "FSSETY").
    model_config = ConfigDict(extra="forbid", json_schema_extra={"x_summary": ["field", "value", "rules_values"]})

    field: str = Field(
        description="The sibling column whose value selects the rule (e.g. FSSETY).",
        json_schema_extra={"x_enum_ref": "SCREEN_COLUMNS", "x_case": "upper"},
    )
    value: str | list[str] = Field(description="The discriminator value, or list of values, that selects this rule.")
    rules: str | None = Field(
        default=None,
        json_schema_extra={"x_enum_ref": "DICTIONARY_RULES"},
        description="The rule to apply: BOOLEAN / ENUM / LOOKUP / SEQUENCE — or blank for a plain input (no widget).",
    )
    rules_values: str | None = Field(
        default=None,
        json_schema_extra={
            "x_enum_ref_when": {
                "field": "rules",
                "map": {
                    "BOOLEAN": "BOOLEAN_TRUE_VALUES",
                    "ENUM": "ENUM_IDS",
                    "LOOKUP": "LOOKUP_IDS",
                    "SEQUENCE": "SEQUENCE_IDS",
                    "NN": "SEQUENCE_IDS",
                },
            },
        },
        description="The rule's id: ENUM → enum id; LOOKUP → lookup id; SEQUENCE/NN → sequence id; BOOLEAN → true marker.",
    )
    false_value: str | None = Field(
        default=None,
        description=(
            "BOOLEAN-only — value to write when the checkbox is unchecked, for THIS conditional rule "
            "(independent of the column's base false value). Blank infers it from the true value "
            "(Y→N, 1→0, true→false)."
        ),
        json_schema_extra={"x_visible_when": {"field": "rules", "value": "BOOLEAN"}},
    )
    lookup_param_binds: list["ParamBind"] = Field(
        default_factory=list,
        description=(
            "Params bound into THIS rule's LOOKUP query (when ``rules = LOOKUP``) — independent of the "
            "column's base binds and of any sibling rule's. Same shape as ``ColumnHint.lookup_param_binds``."
        ),
    )
    return_binds: list["ReturnBind"] = Field(
        default_factory=list,
        description="Fill sibling columns from THIS rule's LOOKUP pick — independent of the column's base return_binds.",
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field, "value": self.value,
            "rules": self.rules, "rules_values": self.rules_values, "false_value": self.false_value,
            "lookup_param_binds": [b.model_dump(mode="json", exclude_none=True) for b in self.lookup_param_binds],
            "return_binds": [{"param": b.param, "column": b.column} for b in self.return_binds],
        }


class ColumnHint(BaseModel):
    """Display + edit metadata for one column on a screen. Drives both the grid (the table view)
    and the dialog form — set it once, both surfaces use it.

    ``label`` and ``format`` fall back to the shared field dictionary when not set here. The
    dictionary entry is looked up under ``dd`` if set, else the column ``name``; set ``dd = ""``
    to opt out of dictionary lookup entirely.

    The order of the ``columns`` list is the display order in the grid. Columns the read query
    returns but that aren't hinted here keep their discovery order and follow the hinted ones.
    """

    model_config = ConfigDict(extra="forbid")

    # ── General — what this column is + where it writes ──────────────────────────────────
    name: str = Field(
        description="Result column this entry applies to (case-insensitive match).",
        # Column names by v1 convention are UPPERCASE (USR_ID / APPS_ID style).
        # Runtime match is case-insensitive, but normalising on save keeps the
        # saved screens.toml / connectors.toml consistent.
        json_schema_extra={"x_group": "General", "x_case": "upper"},
    )
    dd: str | None = Field(
        default=None,
        description="Inherit label / format / rule from this dictionary entry. Blank uses the column ``name`` as the key; set to ``\"\"`` to opt out.",
        json_schema_extra={"x_group": "General", "x_enum_ref": "DD_ENTRIES", "x_case": "upper"},
    )
    key: bool = Field(
        default=False,
        json_schema_extra={"x_group": "General"},
        description=(
            "Mark this column as part of the row's primary key. Drives Excel-import "
            "update-vs-insert matching and locks the column on edit dialogs."
        ),
    )
    # ── Display — how the column renders in the grid / header ─────────────────────────────
    label: str | None = Field(default=None, json_schema_extra={"x_group": "Display"}, description="Display title in the grid header and the dialog field.")
    hidden: bool = Field(default=False, json_schema_extra={"x_group": "Display"}, description="Hide this column by default. The operator can un-hide it via the grid's Columns menu.")
    format: str | None = Field(
        default=None,
        description="How to render the value (date / number / boolean / currency / …). Overrides the dictionary format.",
        json_schema_extra={"x_group": "Display", "x_enum_ref": "DICTIONARY_TYPE"},
    )
    align: Literal["left", "right", "center"] | None = Field(
        default=None,
        description="Cell alignment (blank auto-picks based on type).",
        json_schema_extra={"x_group": "Display", "x_enum_ref": "COLUMN_ALIGN"},
    )
    width: int | None = Field(default=None, json_schema_extra={"x_group": "Display"}, description="Fixed column width in pixels (blank = auto-size).")
    colspan: int | None = Field(
        default=None,
        json_schema_extra={"x_group": "Display"},
        description="Dialog layout: how many dialog-grid columns this field spans (blank = 1). Only affects the edit dialog, not the grid.",
    )
    # ── Filter — grid filter panel + conditional column visibility ────────────────────────
    filter: bool = Field(default=False, json_schema_extra={"x_group": "Filter"}, description="Show this column in the table's filter panel.")
    filter_from: list[FilterDep] = Field(default_factory=list, json_schema_extra={"x_group": "Filter"}, description="Cascading filters — narrow this column's options based on other filters.")
    visible_when: VisibleWhen | list[VisibleWhen] | None = Field(default=None, json_schema_extra={"x_group": "Filter"}, description="Show this column only when a filter has a specific value. Multiple rules are AND-ed.")
    # ── Rules — the column's widget + conditional rules + lookup config ───────────────────
    rules: str | None = Field(
        default=None,
        json_schema_extra={"x_group": "Rules", "x_enum_ref": "DICTIONARY_RULES"},
        description=(
            "How to render and validate the column's value. BOOLEAN renders a checkbox, ENUM a "
            "dropdown, LOOKUP a searchable picker. Leave blank to inherit from the dictionary "
            "entry. Set here when one screen needs a different widget than the global default."
        ),
    )
    rules_values: str | None = Field(
        default=None,
        json_schema_extra={
            "x_group": "Rules",
            "x_enum_ref_when": {
                "field": "rules",
                "map": {
                    "BOOLEAN": "BOOLEAN_TRUE_VALUES",
                    "ENUM": "ENUM_IDS",
                    "LOOKUP": "LOOKUP_IDS",
                    "SEQUENCE": "SEQUENCE_IDS",
                    "NN": "SEQUENCE_IDS",
                },
            },
        },
        description=(
            "The rule's value: BOOLEAN → the true marker (Y / 1 / true); ENUM → the enum id; "
            "LOOKUP → the lookup id; SEQUENCE / NN → the sequence id."
        ),
    )
    rules_when: list[RulesWhen] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Rules"},
        description=(
            "Conditional rule overrides: when a sibling column has a given value, render THIS column "
            "with a different rule (or none → plain input). Each entry is {field, value, rules, "
            "rules_values}; the first match wins, no match → the base ``rules``. Reactive per row / "
            "form (dialog + grid). Use when one physical column means different things per kind — e.g. "
            "on f00950 FSFRDV is a version LOOKUP for app security but a plain value for an alias. "
            "All referenced lookups load once; only the per-cell choice is per-row."
        ),
    )
    # ── Edit — edit constraints (required / read-only) ────────────────────────────────────
    required: bool = Field(
        default=False,
        json_schema_extra={"x_group": "Edit"},
        description="Operator must fill this column before saving.",
    )
    required_when: list[VisibleWhen] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Edit"},
        description=(
            "Conditional required — required only when every condition holds against the live form "
            "(the dialog). Each entry is {field, value}. Empty → the static ``required`` decides."
        ),
    )
    disabled: bool = Field(
        default=False,
        json_schema_extra={"x_group": "Edit"},
        description="Read-only — the operator sees the value but can't change it.",
    )
    disabled_when: list[VisibleWhen] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Edit"},
        description=(
            "Conditional read-only — locked only when every condition holds against the live form "
            "(the dialog). Each entry is {field, value}. Empty → the static ``disabled`` decides."
        ),
    )
    disable_on_add: bool = Field(
        default=False,
        json_schema_extra={"x_group": "Edit"},
        description=(
            "Read-only when ADDING a new row (still editable when editing an existing one). "
            "Use for a column whose value is system-assigned on create and must not be typed."
        ),
    )
    disable_on_edit: bool = Field(
        default=False,
        json_schema_extra={"x_group": "Edit"},
        description=(
            "Read-only when EDITING an existing row (still editable when adding a new one). "
            "Use for a key whose value must not change after creation — replaces v1's blanket "
            "'lock all keys on edit' with per-column control, so a key that genuinely needs "
            "editing (e.g. on f00950) stays editable. Honoured by BOTH the dialog and the grid "
            "bulk-edit, since it lives on the column."
        ),
    )
    justify: Literal["right_blank", "right_zero", "left"] | None = Field(
        default=None,
        json_schema_extra={"x_group": "Edit"},
        description=(
            "Per-column override of the dictionary's write-side justification (JDE F9210.FRDRUL). "
            "``right_blank`` / ``right_zero`` force right-adjust (space / zero fill); ``left`` forces "
            "left-justified even when the dictionary marks the data item right-adjust — for a code "
            "that's right-justified in one table but trimmed in others (e.g. KY: right-adjust in "
            "F0005's UDC, plain elsewhere). Blank = inherit the dictionary entry's ``justify``."
        ),
    )
    justify_from: str | None = Field(
        default=None,
        json_schema_extra={"x_group": "Edit", "x_enum_ref": "SCREEN_COLUMNS", "x_case": "upper"},
        description=(
            "GENERIC value column: take this column's right/left justification AND width PER ROW from "
            "the data item named in another column's value. F00950's FSFRDV / FSTHDV set this to "
            "``FSDTAI`` — at write time the framework reads FSDTAI (e.g. ``MCU``), looks up that data "
            "item's dictionary ``justify`` + ``size``, and right-justifies the value to that width "
            "(then the column's CHAR padding fills the rest). Replaces a per-query CASE/LPAD."
        ),
    )
    # ── Defaults — pre-fill / conditional forced default / conditional write ──────────────
    default: str | None = Field(
        default=None,
        json_schema_extra={"x_group": "Defaults"},
        description="Pre-fill value when adding a new row.",
    )
    default_when: list[DefaultWhen] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Defaults"},
        description=(
            "Conditional forced defaults: when a sibling column has a given value, set THIS column "
            "to a value and lock it (read-only) on the dialog + grid. Each entry is "
            "{field, value, default}; the first matching rule wins. Reactive (add + edit). Use for "
            "values determined by the row's kind — e.g. FSSETY='S' forces FSDTAI=0."
        ),
    )
    write_when: VisibleWhen | list[VisibleWhen] | None = Field(
        default=None,
        json_schema_extra={"x_group": "Defaults"},
        description=(
            "Write this column only when the condition holds. Opt-in and INDEPENDENT of "
            "``visible_when`` (a hidden column can still be written — e.g. a key resolved by a "
            "lookup). When set and it does NOT hold for the row, the column is written as its "
            "type-neutral value (blank / 0) and its data-dictionary default/rule is suppressed — so "
            "a field that's irrelevant to the row's kind (e.g. a JDE security flag for a security "
            "type that doesn't use it) isn't dirtied by an inherited default. Blank → always written "
            "(unchanged behaviour). Multiple rules are AND-ed; the ``field`` must be a column carried "
            "in the write (the discriminator, e.g. the security type)."
        ),
    )
    # ── Rules (cont.) — LOOKUP-only display/behaviour (shown in the Rules tab) ────────────
    hide_label: bool = Field(
        default=False,
        json_schema_extra={"x_group": "Rules"},
        description=(
            "A LOOKUP / ENUM column normally shows TWO grid columns — the code (ID) and the "
            "resolved label. Set this to show ONLY the code column when the description isn't "
            "needed. The picker dropdown still shows code + label. Default: label shown."
        ),
    )
    lookup_param_binds: list[ParamBind] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Rules"},
        description=(
            "Narrow this column's BASE lookup query by binding extra parameters. Used when the "
            "lookup depends on another field's value — e.g. picking a role narrows by the "
            "row's current application id. When the column has ``rules_when`` entries, each "
            "conditional rule carries its OWN binds — this list applies only to the base rule."
        ),
    )
    return_binds: list[ReturnBind] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Rules"},
        description=(
            "For a LOOKUP column: when a value is picked, fill sibling columns on the same row "
            "from the picked row's returned fields. Each entry maps a lookup ``return_params`` "
            "field → a target column on this screen. Applies in the dialog, the grid bulk-edit, "
            "and on Excel import (import fills only an empty target — an explicit value wins). "
            "Example: on f00950 OBNM's lookup returns SY, bound to fill the product-code column. "
            "With ``rules_when`` entries, each conditional rule carries its own ``return_binds``; "
            "this applies to the base rule."
        ),
    )
    group: str | None = Field(
        default=None,
        json_schema_extra={"x_group": "General", "x_enum_ref": "COLUMN_GROUPS"},
        description=(
            "When set, this column lives on a RELATED 1:1 table (it comes from the read query's "
            "JOIN) and is written back through the screen's matching ``column_groups`` entry on Save "
            "— not the main update query. Blank → the column writes to the main table."
        ),
    )

    @property
    def visible_when_rules(self) -> list[VisibleWhen]:
        """``visible_when`` normalised to a list (a single rule → ``[rule]``; unset → ``[]``)."""
        if self.visible_when is None:
            return []
        return [self.visible_when] if isinstance(self.visible_when, VisibleWhen) else list(self.visible_when)

    @property
    def write_when_rules(self) -> list[VisibleWhen]:
        """``write_when`` normalised to a list (a single rule → ``[rule]``; unset → ``[]``)."""
        if self.write_when is None:
            return []
        return [self.write_when] if isinstance(self.write_when, VisibleWhen) else list(self.write_when)

    @property
    def dictionary_key(self) -> str:
        """The dictionary entry to consult for an un-set ``label``/``format`` (``dd`` or, if
        ``dd`` is the empty string, none — set ``dd = ""`` to opt a column out of the dictionary)."""
        return self.name if self.dd is None else self.dd


# --------------------------------------------------------------------------- #
# SQL connector
# --------------------------------------------------------------------------- #


def _validate_sql_field(v: str | dict[str, str]) -> str | dict[str, str]:
    """Shared validator for the ``sql`` field on :class:`CrudSlot` and :class:`QueryDef` —
    accepts a string (single dialect-independent statement) OR a dict keyed by SQLAlchemy
    backend name. A dict must carry a non-empty ``default`` variant; that's the fallback
    when the runtime pool's dialect isn't explicitly listed."""
    if isinstance(v, dict):
        if "default" not in v:
            raise ValueError("a per-dialect sql map must include a 'default' key")
        if not v.get("default", "").strip():
            raise ValueError("the 'default' sql variant must not be empty")
    return v


class CrudSlot(BaseModel):
    """One CRUD slot inside a :class:`TableDef` — the SQL + executable bits.

    Carries **no metadata of its own**: a table's ``label`` / ``description`` / ``name``
    live on the parent :class:`TableDef`. The slot only knows how to *execute* its
    statement against the pool. Same ``sql`` shape rules as :class:`QueryDef` (single
    string OR per-dialect map with a required ``default``)."""

    model_config = ConfigDict(extra="forbid")

    sql: str | dict[str, str] = Field(
        description=(
            "The SQL statement with ``:name`` placeholders. Use a per-dialect map "
            "(``{ default = \"…\", oracle = \"…\" }``) to ship variants per database "
            "backend; ``default`` is required."
        ),
    )
    writable: bool = Field(
        default=False,
        description=(
            "Allow non-SELECT statements (INSERT / UPDATE / DELETE). Required for any "
            "mutating slot — the runtime defaults this on ``put`` / ``post`` / ``delete`` "
            "slots when the SQL says so, but the explicit flag wins."
        ),
    )
    params: list[ParamDef] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Params"},
        description="Declared parameters — give each ``:name`` placeholder a form label and a default.",
    )

    @field_validator("sql")
    @classmethod
    def _require_default(cls, v: str | dict[str, str]) -> str | dict[str, str]:
        return _validate_sql_field(v)


class TableDef(BaseModel):
    """A first-class CRUD table — one operator-facing entity with its own metadata + up
    to four executable slots (``get`` / ``put`` / ``post`` / ``delete``).

    Replaces the v1-style flat list of CRUD-suffixed queries where ``label`` / ``description``
    were duplicated across each slot. Now there's one canonical home for the table's
    metadata; the slots only carry SQL. Every cross-file reference still uses the
    synthesised name ``<table.name>_<slot>`` (e.g. ``f0092_get``) — the connector builds
    a flat name → query index at load time so callers don't see the shape change."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description=(
            "The table's base name — the operator-visible identifier (e.g. ``f0092``). "
            "Synthetic query names follow ``<name>_<slot>`` (``f0092_get``, ``f0092_put``, …) "
            "and that's what every screen / dictionary / action reference uses."
        ),
    )
    label: str | None = Field(
        default=None,
        json_schema_extra={"x_group": "Advanced"},
        description="Short name shown in listings.",
    )
    description: str | None = Field(
        default=None,
        json_schema_extra={"x_group": "Advanced"},
        description="Longer description of what this table represents.",
    )
    get: CrudSlot | None = Field(
        default=None,
        json_schema_extra={"x_group": "Get"},
        description="The READ slot (SELECT). Synthesised name ``<name>_get``.",
    )
    put: CrudSlot | None = Field(
        default=None,
        json_schema_extra={"x_group": "Put"},
        description="The UPDATE slot. Synthesised name ``<name>_put``.",
    )
    post: CrudSlot | None = Field(
        default=None,
        json_schema_extra={"x_group": "Post"},
        description="The INSERT slot. Synthesised name ``<name>_post``.",
    )
    delete: CrudSlot | None = Field(
        default=None,
        json_schema_extra={"x_group": "Delete"},
        description="The DELETE slot. Synthesised name ``<name>_delete``.",
    )

    def slots(self) -> list[tuple[str, CrudSlot]]:
        """Iterate present slots in CRUD order — used by the connector loader to build
        synthetic ``<name>_<crud>`` entries in its flat query index."""
        out: list[tuple[str, CrudSlot]] = []
        for crud in ("get", "put", "post", "delete"):
            slot = getattr(self, crud)
            if slot is not None:
                out.append((crud, slot))
        return out


class QueryDef(BaseModel):
    """A standalone named SQL query — one of the three "single SQL per entity" kinds:

    * **custom** queries (``[[connectors.X.queries]]``) — anything the operator wired by
      hand that doesn't fit the CRUD-table shape;
    * **sequence** queries (``[[connectors.X.sequences]]``) — value sources for a
      dictionary SEQUENCE rule;
    * **lookup** queries (``[[connectors.X.lookups]]``) — value sources for a dictionary
      LOOKUP rule.

    The section a query sits in *is* its type (no ``type`` field on the entry — that was
    the v1-Phase-3 shape we deprecated). Same ``sql`` rules as :class:`CrudSlot`: a single
    string OR a per-dialect map with a required ``default``.

    Per-screen display + behaviour (``columns`` hints, ``auto_load``, ``audit_table`` …)
    lives on the matching :class:`liberty.screens.config.Screen` and is threaded into the
    SQL connector by the route layer at execute time — same as Phase 3."""

    # ``extra = "ignore"`` (not "forbid") so a connectors.toml written under the v1-Phase-3
    # flat-queries shape keeps loading — the migration in :func:`parse_connectors` already
    # lifted such entries into the right section, but a hand-edited file with a stray
    # ``type`` field on a custom query parses without surfacing the deprecated field as a
    # validation error.
    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="Unique name within the connector. The permission string is ``sql:<connector>:<name>``.")
    sql: str | dict[str, str] = Field(description="The SQL statement with ``:name`` placeholders. Use a per-dialect map (``{ default = \"…\", oracle = \"…\" }``) to ship variants per database backend; ``default`` is required.")
    writable: bool = Field(default=False, description="Allow non-SELECT statements (INSERT / UPDATE / DELETE). Required for any mutating query.")
    params: list[ParamDef] = Field(default_factory=list, json_schema_extra={"x_group": "Params"}, description="Declared parameters — give each ``:name`` placeholder a form label and a default.")
    label: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="Short name shown in listings.")
    description: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="Longer description of what this query returns.")

    @field_validator("sql")
    @classmethod
    def _require_default(cls, v: str | dict[str, str]) -> str | dict[str, str]:
        return _validate_sql_field(v)

    def sql_for(self, dialect: str | None) -> str:
        """The SQL to run on a pool of *dialect* (falls back to ``default``)."""
        if isinstance(self.sql, str):
            return self.sql
        return self.sql.get(dialect or "", self.sql["default"])

    @property
    def default_sql(self) -> str:
        """The dialect-independent variant — used for statement-type / bind-param introspection."""
        return self.sql if isinstance(self.sql, str) else self.sql["default"]

    @property
    def dialects(self) -> list[str]:
        return ["default"] if isinstance(self.sql, str) else list(self.sql)


def _crud_slot_to_querydef(name: str, slot: CrudSlot, *, label: str | None, description: str | None, crud: str) -> QueryDef:
    """Lift a :class:`CrudSlot` into a flat :class:`QueryDef` for the connector's unified
    name → query index. ``writable`` defaults to True on the mutating slots when the slot
    doesn't say so explicitly; the runtime's statement-type guard already rejects bad
    INSERT/UPDATE/DELETE without ``writable``, so this is a no-op safety net."""
    writable = slot.writable or (crud != "get")
    return QueryDef(
        name=name,
        sql=slot.sql,
        writable=writable,
        params=list(slot.params),
        label=label,
        description=description,
    )


class SqlConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["sql"]
    pool: str = Field(
        default="default", description="Default pool this connector's queries run on.",
        json_schema_extra={"x_enum_ref": "POOL_NAMES"},  # pick from configured pools (combobox)
    )
    pools: list[str] = Field(
        default_factory=list,
        description=(
            "Additional pools this connector may run against at runtime (multi-environment: "
            "one connector, many JDE/DB instances). A request/job/UI can select any pool in "
            "``[pool] + pools`` via the ``X-Liberty-Pool`` header / a step's ``source_pool`` — "
            "the queries + screens stay shared, only the target DB changes. Empty ⇒ this "
            "connector is single-pool (``pool`` only), behaving exactly as before."
        ),
        json_schema_extra={"x_enum_ref": "POOL_NAMES"},  # each entry picks from configured pools
    )
    licensed: bool = Field(default=False, description="Require a valid [license] key. Without one this connector isn't loaded.")
    max_rows: int | None = Field(default=None, description="Default SELECT row cap. Falls back to the pool's, then 1000. A per-screen / per-request cap takes precedence.")
    show_in_switcher: bool = Field(
        default=True,
        description="Show this connector in the top app switcher.",
    )
    home: str | None = Field(
        default=None,
        description="Landing menu item id when this app is picked.",
        json_schema_extra={"x_enum_ref": "MENU_HOME_ITEMS"},
    )
    tables: list[TableDef] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Tables"},
        description=(
            "CRUD tables exposed by this connector — each entry owns one canonical "
            "``label`` / ``description`` and up to four executable slots (get / put / "
            "post / delete). Synthesised query names follow ``<table_name>_<slot>``."
        ),
    )
    queries: list[QueryDef] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Queries"},
        description="Custom standalone queries — anything that doesn't fit the CRUD-table shape.",
    )
    sequences: list[QueryDef] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Sequences"},
        description=(
            "Sequence queries — value sources for dictionary SEQUENCE rules. Each is "
            "addressed by its own ``name``."
        ),
    )
    lookups: list[QueryDef] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Lookups"},
        description=(
            "Lookup queries — value sources for dictionary LOOKUP rules. Each is "
            "addressed by its own ``name``."
        ),
    )

    def iter_named_queries(self) -> "list[tuple[str, QueryDef]]":
        """The unified name → QueryDef index used by the SQL connector at load time.

        Order: tables (each slot synthesised as ``<table.name>_<slot>``), then
        ``queries`` (custom), then ``sequences``, then ``lookups``. Duplicate names
        raise :class:`ValueError` — the caller treats it as a config error."""
        out: list[tuple[str, QueryDef]] = []
        seen: set[str] = set()
        def _push(name: str, q: QueryDef) -> None:
            if name in seen:
                raise ValueError(
                    f"duplicate query name {name!r} — a CRUD slot synthesised name "
                    f"collides with a sibling query in the same connector; rename one."
                )
            seen.add(name)
            out.append((name, q))
        for tbl in self.tables:
            for crud, slot in tbl.slots():
                qname = f"{tbl.name}_{crud}"
                _push(qname, _crud_slot_to_querydef(
                    qname, slot, label=tbl.label, description=tbl.description, crud=crud,
                ))
        for q in self.queries:
            _push(q.name, q)
        for q in self.sequences:
            _push(q.name, q)
        for q in self.lookups:
            _push(q.name, q)
        return out


# --------------------------------------------------------------------------- #
# API connector
# --------------------------------------------------------------------------- #

AuthType = Literal["none", "basic", "bearer", "api_key", "oauth2"]


class EndpointDef(BaseModel):
    """A named HTTP endpoint relative to the connector's ``base_url``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Unique name within the connector. The permission string is ``api:<connector>:<name>``.")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = Field(
        default="GET",
        description="HTTP method.",
        json_schema_extra={"x_enum_ref": "HTTP_METHOD"},
    )
    path: str = Field(default="", description="Path appended to the connector's base URL. Supports ``{{placeholder}}`` substitution. Use an absolute URL to bypass the base URL.")
    headers: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "Headers"}, description="Per-endpoint headers (merged over the connector's defaults). Values support ``{{placeholders}}``.")
    query_params: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "Headers"}, description="Query-string parameters. Values support ``{{placeholders}}``.")
    body: str | None = Field(default=None, description="Request body template with ``{{placeholders}}``. For multipart: ``name=value`` (text) or ``name=@path;filename=X;contentType=Y`` (file).")
    content_type: str = Field(default="application/json", description="Content-Type of the request body.")
    response_field: str | None = Field(default=None, json_schema_extra={"x_group": "Response"}, description="Dot-path into the JSON response to extract (e.g. ``data.0.id``). Blank returns the whole response.")
    response_map: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "Response"}, description="Extract several fields from the response: ``{output_name = dot.path}``.")
    params: list[ParamDef] = Field(default_factory=list, json_schema_extra={"x_group": "Params"}, description="Declared parameters for the ``{{placeholders}}``.")
    label: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="Short name shown in listings.")
    description: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="Longer description of what this endpoint does.")


class ApiConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["api"]
    licensed: bool = Field(default=False, description="Gate this connector behind a valid [license] key — without one (the open framework) it isn't loaded.")
    show_in_switcher: bool = Field(
        default=True,
        description="Show this connector in the top app switcher.",
    )
    home: str | None = Field(
        default=None,
        description="Landing menu item id when this app is picked.",
        json_schema_extra={"x_enum_ref": "MENU_HOME_ITEMS"},
    )
    base_url: str = Field(description="Base URL endpoints are relative to, e.g. https://api.example.com. Supports ${ENV} refs. (Leave blank only if every endpoint uses an absolute path.)")
    auth_type: AuthType = Field(
        default="none",
        description="Authentication scheme. Pick the right one and only the fields it needs will appear.",
        json_schema_extra={"x_enum_ref": "AUTH_TYPE"},
    )
    # Auth fields are scoped to the matching ``auth_type`` via ``x_visible_when`` so the form
    # only shows what's actually used. Switching auth_type doesn't drop the stored values —
    # they stay in the model so flipping back to a previous mode restores them.
    auth_username: str | None = Field(
        default=None,
        description="Username for the request. Also available as the ``{{username}}`` placeholder.",
        json_schema_extra={"x_group": "Auth", "x_visible_when": {"field": "auth_type", "value": ["basic", "oauth2"]}},
    )
    auth_password: str | None = Field(
        default=None,
        description="Password for the request. Also available as ``{{password}}``. May be an ``ENC:`` value (decrypted at runtime).",
        json_schema_extra={"x_group": "Auth", "format": "password", "x_visible_when": {"field": "auth_type", "value": ["basic", "oauth2"]}},
    )
    auth_token: str | None = Field(
        default=None,
        description="Static bearer token / API key. Also available as ``{{token}}``. May be an ``ENC:`` value.",
        json_schema_extra={"x_group": "Auth", "format": "password", "x_visible_when": {"field": "auth_type", "value": ["bearer", "api_key"]}},
    )
    auth_api_key_header: str = Field(
        default="X-Api-Key",
        description="Header name to carry the API key (e.g. ``X-Api-Key``, ``Authorization``).",
        json_schema_extra={"x_group": "Auth", "x_visible_when": {"field": "auth_type", "value": "api_key"}},
    )
    auth_token_endpoint: str | None = Field(
        default=None,
        description="Token-endpoint URL the connector POSTs to fetch a fresh token.",
        json_schema_extra={"x_group": "Auth", "x_visible_when": {"field": "auth_type", "value": "oauth2"}},
    )
    auth_token_field: str | None = Field(
        default=None,
        description="Dot-path to the token in the token-endpoint response (e.g. ``access_token`` or ``userInfo.token``).",
        json_schema_extra={"x_group": "Auth", "x_visible_when": {"field": "auth_type", "value": "oauth2"}},
    )
    auth_token_body: str | None = Field(
        default=None,
        description="Request body posted to the token endpoint. Supports ``{{username}}`` / ``{{password}}`` placeholders.",
        json_schema_extra={"x_group": "Auth", "x_visible_when": {"field": "auth_type", "value": "oauth2"}},
    )
    auth_token_content_type: str = Field(
        default="application/json",
        description="Content-Type of the token-request body (``application/json`` or ``application/x-www-form-urlencoded``).",
        json_schema_extra={"x_group": "Auth", "x_visible_when": {"field": "auth_type", "value": "oauth2"}},
    )
    auth_token_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Extra headers sent on the token request (e.g. a client-id header).",
        json_schema_extra={"x_group": "Auth", "x_visible_when": {"field": "auth_type", "value": "oauth2"}},
    )
    auth_token_ttl: int = Field(
        default=3300,
        description="How long (seconds) to cache a fetched token before refreshing. Default 3300 = 55 min.",
        json_schema_extra={"x_group": "Auth", "x_visible_when": {"field": "auth_type", "value": "oauth2"}},
    )
    default_headers: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "Transport"}, description="Headers sent on every request from this connector.")
    timeout: float = Field(default=30.0, json_schema_extra={"x_group": "Transport"}, description="Per-request timeout in seconds.")
    verify_ssl: bool = Field(default=True, json_schema_extra={"x_group": "Transport"}, description="Verify the server's TLS certificate (disable only for dev / self-signed).")
    endpoints: list[EndpointDef] = Field(default_factory=list, json_schema_extra={"x_group": "Endpoints"}, description="The named HTTP endpoints this connector exposes.")


ConnectorConfig = Annotated[
    Union[SqlConnectorConfig, ApiConnectorConfig],
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------- #
# Top-level file
# --------------------------------------------------------------------------- #


class ConnectorsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pools: dict[str, PoolConfig] = Field(default_factory=dict)
    connectors: dict[str, ConnectorConfig] = Field(default_factory=dict)


_CRUD_SUFFIXES = ("_get", "_put", "_post", "_delete")


def _classify_legacy_query(q: dict[str, Any]) -> str:
    """Pick a section name (``tables`` / ``queries`` / ``sequences`` / ``lookups``) for
    one legacy flat-shape query entry. ``type`` field wins when set; otherwise we fall
    back to a name-suffix guess (``_get``/``_put``/``_post``/``_delete`` → table, else
    custom). ``custom`` maps to ``queries`` (the new section name for standalone SQL)."""
    raw_type = q.get("type")
    if isinstance(raw_type, str):
        t = raw_type.strip().lower()
        if t == "table": return "tables"
        if t == "sequence": return "sequences"
        if t == "lookup": return "lookups"
        if t == "custom": return "queries"
        # Unknown type (e.g. the historical AI-scaffold bug emitted ``select``) falls
        # through to the suffix guess — no warning at this layer; the loader caller can
        # log if they care.
    name = q.get("name") or ""
    if isinstance(name, str) and name.lower().endswith(_CRUD_SUFFIXES):
        return "tables"
    return "queries"


def _split_legacy_queries(connector_name: str, flat: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Lift a flat ``[[connectors.X.queries]]`` list (Phase-3 shape, with a per-query
    ``type`` field) into the four-section shape the new models expect. Groups CRUD-suffixed
    entries by base into :class:`TableDef`-shaped dicts; promotes the GET slot's label /
    description onto the table (falls back to the first available slot). Drops the
    ``type`` field everywhere — sections are implicit now.

    Used by :func:`parse_connectors` on legacy files; also re-used by the admin save
    endpoint as a defensive boundary against a stale client sending the old shape."""
    tables_by_base: dict[str, dict[str, Any]] = {}
    customs: list[dict[str, Any]] = []
    sequences: list[dict[str, Any]] = []
    lookups: list[dict[str, Any]] = []
    for raw in flat:
        if not isinstance(raw, dict):
            # Anything that isn't a dict is a config error; let validation surface it.
            customs.append(raw)  # type: ignore[arg-type]
            continue
        q = dict(raw)
        section = _classify_legacy_query(q)
        q.pop("type", None)  # implicit now
        if section == "tables":
            name = str(q.get("name") or "")
            # Strip the CRUD suffix to find the base. Names without one shouldn't reach
            # here (classifier would have routed them to ``queries``) but guard anyway.
            base, crud = name, "get"
            for suf in _CRUD_SUFFIXES:
                if name.lower().endswith(suf):
                    base = name[: -len(suf)]
                    crud = suf[1:]   # "_get" → "get"
                    break
            tbl = tables_by_base.setdefault(base, {"name": base})
            # Promote label / description onto the table from the GET slot first, else
            # the first available slot. Existing values win — once promoted, later slots
            # don't overwrite.
            for meta_key in ("label", "description"):
                if meta_key in q:
                    if crud == "get" or meta_key not in tbl:
                        tbl[meta_key] = q.pop(meta_key)
                    else:
                        q.pop(meta_key, None)
            # The slot body is everything else (sql / writable / params).
            slot: dict[str, Any] = {}
            for k in ("sql", "writable", "params"):
                if k in q:
                    slot[k] = q[k]
            tbl[crud] = slot
        elif section == "sequences":
            sequences.append(q)
        elif section == "lookups":
            lookups.append(q)
        else:
            customs.append(q)
    return {
        "tables": list(tables_by_base.values()),
        "queries": customs,
        "sequences": sequences,
        "lookups": lookups,
    }


def _migrate_legacy_shape(data: dict[str, Any]) -> dict[str, Any]:
    """In-memory pre-pass: detect connectors using the legacy flat-queries shape
    (no ``tables`` / ``sequences`` / ``lookups`` sections, ``queries`` carries a ``type``
    field per entry) and rewrite them to the new sectioned shape *before* validation. A
    connector that already declares any of the new section keys is treated as new-shape
    and left alone — the migration is one-way and idempotent.

    Always returns a fresh dict; never mutates the caller's input."""
    connectors = data.get("connectors") or {}
    if not isinstance(connectors, dict) or not connectors:
        return data
    new_connectors: dict[str, Any] = {}
    changed = False
    for name, conn in connectors.items():
        if not isinstance(conn, dict) or conn.get("type") != "sql":
            new_connectors[name] = conn
            continue
        already_new = any(k in conn for k in ("tables", "sequences", "lookups"))
        flat = conn.get("queries")
        # The new shape ALSO uses ``queries`` (for custom standalone queries) — only
        # call this "legacy" when the flat-list entries have a ``type`` field on them
        # OR when no other section is present and the queries look CRUD-named.
        needs_migration = False
        if isinstance(flat, list) and not already_new:
            needs_migration = any(
                isinstance(q, dict) and "type" in q for q in flat
            ) or any(
                isinstance(q, dict)
                and isinstance(q.get("name"), str)
                and q["name"].lower().endswith(_CRUD_SUFFIXES)
                for q in flat
            )
        if not needs_migration:
            new_connectors[name] = conn
            continue
        # Migrate this connector in memory. Preserve every non-``queries`` field.
        rest = {k: v for k, v in conn.items() if k != "queries"}
        split = _split_legacy_queries(name, list(flat or []))
        new_conn: dict[str, Any] = dict(rest)
        for section in ("tables", "queries", "sequences", "lookups"):
            entries = split.get(section) or []
            if entries:
                new_conn[section] = entries
        new_connectors[name] = new_conn
        changed = True
    if not changed:
        return data
    out = dict(data)
    out["connectors"] = new_connectors
    return out


def parse_connectors(data: dict[str, Any], *, env: dict[str, str] | None = None) -> ConnectorsFile:
    """Validate a raw TOML dict into a :class:`ConnectorsFile` (after env substitution).

    Auto-migrates the legacy flat ``[[connectors.X.queries]]`` shape (per-query ``type``
    field, CRUD slots as siblings) into the new sectioned shape (``tables`` / ``queries``
    / ``sequences`` / ``lookups``). The migration is in-memory only — operators still
    have to save through the admin endpoint to rewrite the file on disk in the new
    shape."""
    return ConnectorsFile.model_validate(_migrate_legacy_shape(substitute_env(data, env=env)))


def load_connectors_file(
    path: Path | str, *, env: dict[str, str] | None = None
) -> ConnectorsFile:
    """Load and validate ``connectors.toml``. A missing file yields an empty config."""
    path = Path(path)
    if not path.exists():
        return ConnectorsFile()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return parse_connectors(data, env=env)
