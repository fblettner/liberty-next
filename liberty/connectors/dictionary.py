"""The shared **field dictionary** — ``config/dictionary.toml``.

v1's ``ly_dictionary`` defined a field once (``dd_id`` → label, type, rules, default)
and reused it across every table/form within an *app* (each app had its own); ``ly_dictionary_l``
carried the per-language labels. v2 keeps that idea as a config file: a connector query's
``columns`` hints *reference* a dictionary entry (``{ name = "USR_NAME", dd = "USR_NAME" }`` —
and a bare ``{ name = "USR_NAME" }`` looks the entry up under the column name), and the SQL
connector resolves the label/format at result time (a per-column ``label``/``format`` on the hint
still overrides, like v1's ``col_label``/``col_type``). v1's per-app isolation maps to **per-connector
sections** (``[connectors.<name>.entries.*]``) — a ``[connectors.nomasx1]`` query consults
``[connectors.nomasx1.entries.*]`` first, then the top-level ``[entries.*]`` (a shared/common pool),
so two migrated apps can't clash on a ``dd_id``. The migration emits this from ``ly_dictionary``
(+ ``ly_dictionary_l``); ``liberty-migrate dictionary --connector <name>`` nests under that connector.

Example::

    default_language = "en"

    # shared — consulted by every connector as a fallback
    [entries.AUDIT_DATE]
    label = "Audit Date"

    # the nomasx1 app's dictionary
    [connectors.nomasx1.entries.USR_NAME]
    label  = "User Name"
    format = "text"
    [connectors.nomasx1.entries.USR_NAME.l]
    fr = "Nom d'utilisateur"

    [connectors.nomasx1.entries.USR_STATUS]
    label  = "Status"
    format = "boolean"
    [connectors.nomasx1.entries.USR_STATUS.l]
    fr = "Statut"
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DictionaryEntry(BaseModel):
    """One shared field definition. ``l`` maps a language code → translated label
    (v1's ``ly_dictionary_l``); ``rules`` / ``rules_values`` / ``default`` are v1's
    ``dd_rules`` family — carried verbatim. The SQL connector *does* interpret the
    display-relevant ones (``BOOLEAN`` / ``ENUM`` / ``LOOKUP`` — see :meth:`DictionaryFile.resolve_rule`)
    and emits them as a ``Column.rule``; the form-layer ones (``SEQUENCE`` / ``SYSDATE`` / ``LOGIN`` /
    ``PASSWORD`` / ``CURRENT_DATE``) are pass-through until Phase 6."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, description="Display title for this field.")
    format: str | None = Field(
        default=None,
        description="How to render the cell — pick a built-in (date / number / boolean / …) or type a custom format.",
        json_schema_extra={"x_enum_ref": "DICTIONARY_TYPE"},
    )
    rules: str | None = Field(
        default=None,
        description="Display rule. BOOLEAN renders ✓/✗, ENUM shows a label per code, LOOKUP resolves a code to a label via a query, SEQUENCE / NN auto-fill on insert, PASSWORD masks input.",
        json_schema_extra={"x_group": "Rule", "x_enum_ref": "DICTIONARY_RULES"},
    )
    rules_values: str | None = Field(
        default=None,
        description="The rule's argument — true-value for BOOLEAN, enum id for ENUM, lookup id for LOOKUP, sequence id for SEQUENCE / NN. The dropdown changes based on the chosen rule.",
        json_schema_extra={
            "x_group": "Rule",
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
    )
    false_value: str | None = Field(
        default=None,
        description=(
            "BOOLEAN-only — value to write when the checkbox is unchecked. Blank infers it from "
            "the true value (``Y``→``N``, ``1``→``0``, ``true``→``false``); anything else sends NULL."
        ),
        json_schema_extra={"x_group": "Rule"},
    )
    default: str | None = Field(
        default=None,
        description="Initial value when a new row is added.",
        json_schema_extra={"x_group": "Rule"},
    )
    lookup_params: dict[str, str] = Field(
        default_factory=dict,
        title="Lookup params",
        description=(
            "Static values bound to the LOOKUP query's parameters — needed when the query has "
            "``:param`` placeholders (a JDE UDC query usually needs SY / RT to return rows). "
            "Keys auto-suggest the params declared on the picked lookup."
        ),
        json_schema_extra={"x_group": "Rule", "x_key_enum_ref": "CURRENT_LOOKUP_PARAMS"},
    )
    l: dict[str, str] = Field(
        default_factory=dict,
        title="Translations",
        description="Per-language label overrides.",
        json_schema_extra={"x_group": "Translations", "x_key_enum_ref": "SUPPORTED_LANGUAGES"},
    )
    # Upgrade-safety flag — see Screen.override for the full story. Default False.
    override: bool = Field(
        default=False,
        description=(
            "Mark this dictionary entry as a customer customisation that an upgrade-package "
            "import should NOT overwrite. Default False — operators opt in after editing."
        ),
        json_schema_extra={"x_group": "Advanced"},
    )

    def label_for(self, language: str | None) -> str | None:
        """The label in *language* if a translation exists, else the default label."""
        if language and self.l:
            return self.l.get(language) or self.label
        return self.label


class EnumValue(BaseModel):
    """One member of an ``[enums.*]`` set — a code (``value``) and its display label
    (with optional per-language overrides). v1's ``ly_enum_val`` + ``ly_enum_val_l``."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(description="The code stored in the cell (e.g. ``JDE``).")
    label: str | None = Field(default=None, description="What to show in place of the code (e.g. ``JD Edwards``).")
    l: dict[str, str] = Field(
        default_factory=dict,
        title="Translations",
        description="Per-language label overrides.",
        json_schema_extra={"x_group": "Translations", "x_key_enum_ref": "SUPPORTED_LANGUAGES"},
    )

    def label_for(self, language: str | None) -> str:
        if language and self.l:
            return self.l.get(language) or self.label or self.value
        return self.label or self.value


class EnumDef(BaseModel):
    """A fixed set of code → label pairs (v1's ``ly_enum`` + ``ly_enum_val``)."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, description="Short name for this enum set.")
    values: list[EnumValue] = Field(
        default_factory=list,
        description="The code → label pairs.",
        json_schema_extra={"x_group": "Values"},
    )
    # Upgrade-safety flag — see Screen.override.
    override: bool = Field(
        default=False,
        description="Mark as customer override; upgrade-package imports skip this on overwrite.",
        json_schema_extra={"x_group": "Advanced"},
    )


class LookupDef(BaseModel):
    """A reference to a *query* whose rows resolve a cell's value to a human label
    (v1's ``ly_lookup`` — ``lkp_query_id``/``lkp_dd_id``/``lkp_dd_label``). The frontend
    fetches the query once and uses the named columns as a ``{value: label}`` map."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, description="Short description of this lookup.")
    connector: str | None = Field(
        default=None,
        description="Connector hosting the lookup query. Blank uses the connector that references this lookup.",
        json_schema_extra={"x_group": "Target", "x_enum_ref": "CONNECTOR_NAMES"},
    )
    query: str = Field(
        description="The query that produces the code → label rows.",
        json_schema_extra={"x_group": "Target", "x_enum_ref": "LOOKUP_QUERIES"},
    )
    value: str = Field(
        description="Result column matching the cell's value (the code).",
        json_schema_extra={"x_group": "Target", "x_enum_ref": "LOOKUP_DD_FIELDS"},
    )
    label: str = Field(
        description="Result column to display instead of the code.",
        json_schema_extra={"x_group": "Target", "x_enum_ref": "LOOKUP_DD_FIELDS"},
    )
    group: str | None = Field(
        default=None,
        description="Optional grouping key (not currently used).",
        json_schema_extra={"x_group": "Advanced"},
    )
    params: list[str] = Field(
        default_factory=list,
        title="Params",
        description=(
            "Parameter names the lookup query expects. Each entry that uses this lookup "
            "binds values via ``DictionaryEntry.lookup_params``."
        ),
        json_schema_extra={"x_group": "Target"},
    )
    return_params: list[str] = Field(
        default_factory=list,
        title="Return params",
        description=(
            "Extra fields written back to the form when the user picks a row. Each name is a "
            "dictionary entry key; the picked row's column with that name fills the matching field."
        ),
        json_schema_extra={"x_group": "Target"},
    )
    # Upgrade-safety flag — see Screen.override.
    override: bool = Field(
        default=False,
        description="Mark as customer override; upgrade-package imports skip this on overwrite.",
        json_schema_extra={"x_group": "Advanced"},
    )


class SequenceDef(BaseModel):
    """A named "next number" source — the query that generates the next ID for a column.
    The SQL connector fires the sequence inside the INSERT transaction when:
      * a dictionary entry with ``rules = "SEQUENCE"`` / ``"NN"`` references this id, OR
      * this sequence's ``dd_id`` matches a bound column on the INSERT (independent of the
        column's own rule — so a column that's both a LOOKUP target *and* gets an auto-ID
        still picks up the next number).

    The query typically reads ``SELECT COALESCE(MAX(<col>), 0) + 1 FROM <table>`` narrowed
    by any row context (the WHERE binds against the in-flight INSERT row)."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, description="Short description of this sequence.")
    connector: str | None = Field(
        default=None,
        description="Connector hosting the sequence query. Blank uses the connector that fires it.",
        json_schema_extra={"x_group": "Target", "x_enum_ref": "CONNECTOR_NAMES"},
    )
    query: str = Field(
        description="The query that returns the next number (typically ``SELECT COALESCE(MAX(col),0)+1 FROM table``).",
        json_schema_extra={"x_group": "Target", "x_enum_ref": "LOOKUP_QUERIES"},
    )
    dd_id: str | None = Field(
        default=None,
        description=(
            "Auto-fire this sequence for any column whose dictionary entry has this key — even "
            "if the entry's rule isn't SEQUENCE. Blank limits firing to entries with "
            "``rules = SEQUENCE`` pointing at this id."
        ),
        json_schema_extra={"x_group": "Target", "x_enum_ref": "DD_ENTRIES"},
    )
    params: list[str] = Field(
        default_factory=list,
        title="Params",
        description=(
            "Parameter names the sequence query expects. Values come from the in-flight INSERT "
            "row automatically — listing them here is documentation."
        ),
        json_schema_extra={"x_group": "Target"},
    )
    # Upgrade-safety flag — see Screen.override.
    override: bool = Field(
        default=False,
        description="Mark as customer override; upgrade-package imports skip this on overwrite.",
        json_schema_extra={"x_group": "Advanced"},
    )


class DictionarySection(BaseModel):
    """A per-connector group of entries / enums / lookups / sequences (``[connectors.<name>.…]``)."""

    model_config = ConfigDict(extra="forbid")

    entries: dict[str, DictionaryEntry] = Field(default_factory=dict)
    enums: dict[str, EnumDef] = Field(default_factory=dict)
    lookups: dict[str, LookupDef] = Field(default_factory=dict)
    sequences: dict[str, SequenceDef] = Field(default_factory=dict)


class DictionaryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_language: str = "en"
    entries: dict[str, DictionaryEntry] = Field(default_factory=dict)              # shared / common
    enums: dict[str, EnumDef] = Field(default_factory=dict)
    lookups: dict[str, LookupDef] = Field(default_factory=dict)
    sequences: dict[str, SequenceDef] = Field(default_factory=dict)
    connectors: dict[str, DictionarySection] = Field(default_factory=dict)          # per-connector
    # Operator overrides for the bundled `liberty/framework_enums.py` registry that powers the
    # builder UI's dropdowns (DICTIONARY_TYPE / DATASOURCE_TYPE / HTTP_METHOD / …). An entry here
    # *replaces* the bundled set for that id — let the operator add a new "Datasource Type" value
    # without a code change. The merge happens at /admin/config/schema time.
    framework_enums: dict[str, EnumDef] = Field(default_factory=dict)

    def find_entry(self, key: str, *, connector: str | None = None) -> DictionaryEntry | None:
        """The :class:`DictionaryEntry` for *key* — *connector*'s section first, then the shared pool."""
        if connector and connector in self.connectors:
            e = self.connectors[connector].entries.get(key)
            if e is not None:
                return e
        return self.entries.get(key)

    def _find_enum(self, eid: str, *, connector: str | None = None) -> EnumDef | None:
        if connector and connector in self.connectors:
            e = self.connectors[connector].enums.get(eid)
            if e is not None:
                return e
        return self.enums.get(eid)

    def _find_lookup(self, lid: str, *, connector: str | None = None) -> LookupDef | None:
        if connector and connector in self.connectors:
            lk = self.connectors[connector].lookups.get(lid)
            if lk is not None:
                return lk
        return self.lookups.get(lid)

    def find_sequence(self, sid: str, *, connector: str | None = None) -> SequenceDef | None:
        """The :class:`SequenceDef` for *sid* — *connector*'s section first, then the shared pool.
        Used by the SQL connector to resolve a ``rules = "SEQUENCE"`` entry's ``rules_values``
        (the sequence id) to the actual next-number query at INSERT time."""
        if connector and connector in self.connectors:
            s = self.connectors[connector].sequences.get(sid)
            if s is not None:
                return s
        return self.sequences.get(sid)

    def find_sequence_by_dd_id(self, dd_id: str, *, connector: str | None = None) -> SequenceDef | None:
        """Find any sequence whose ``dd_id`` matches *dd_id* — used at INSERT time to auto-fire
        a sequence for a column regardless of its dictionary entry's own rule. Returns the first
        match (v1's ``ly_sequence`` has at most one row per ``seq_dd_id`` per app). Searches the
        per-connector scope first, then shared."""
        key = dd_id.upper() if dd_id else ""
        if not key:
            return None
        if connector and connector in self.connectors:
            for s in self.connectors[connector].sequences.values():
                if (s.dd_id or "").upper() == key:
                    return s
        for s in self.sequences.values():
            if (s.dd_id or "").upper() == key:
                return s
        return None

    def resolve(self, key: str, language: str | None, *, connector: str | None = None) -> tuple[str | None, str | None]:
        """``(label, format)`` for *key* in *language* — *connector*'s section first, then shared;
        ``(None, None)`` if neither has it."""
        e = self.find_entry(key, connector=connector)
        return (e.label_for(language), e.format) if e is not None else (None, None)

    def resolve_rule(
        self, entry: DictionaryEntry, *, connector: str | None = None, language: str | None = None
    ) -> dict[str, Any] | None:
        """The *entry*'s display rule resolved into a wire-ready dict, or ``None`` if the rule
        isn't display-relevant (or the referenced enum/lookup is missing). Three shapes:

        * ``{"kind": "boolean", "true_value": "Y", "false_value": "N"}`` — values equal to
          ``true_value`` display as "yes"; else "no" (nulls stay null). ``true_value`` defaults
          to ``"Y"`` when unset; ``false_value`` is explicit (``DictionaryEntry.false_value``)
          when set, else inferred via :func:`infer_false_value`, else omitted entirely (the
          frontend then sends SQL NULL on uncheck, the v1 default).
        * ``{"kind": "enum", "values": [{"value": "JDE", "label": "JD Edwards"}, …]}`` — the enum's
          members, with each label resolved in *language*; the frontend renders the matching label.
        * ``{"kind": "lookup", "connector": "nomasx1", "query": "security_roles_get",
          "value": "ROL_ID", "label": "ROL_NAME"}`` — a *reference* (the frontend fetches the query
          once and uses the named columns as a ``{value: label}`` map).
        """
        rule = (entry.rules or "").strip().upper()
        if rule == "BOOLEAN":
            true_value = entry.rules_values or "Y"
            wire: dict[str, Any] = {"kind": "boolean", "true_value": true_value}
            # Always surface the false value — explicit when the operator set it; inferred from
            # the true value otherwise (Y→N, 1→0, true→false, "01"→null). The frontend reads
            # this to know what to send on uncheck; the SQL connector substitutes any NULL
            # bind on a BOOLEAN-ruled column with it as a safety net (the dialog might send
            # null if it doesn't know the rule yet, e.g. a freshly-loaded screen).
            fv = entry.false_value
            if fv is None:
                fv = infer_false_value(true_value)
            if fv is not None:
                wire["false_value"] = fv
            return wire
        if rule == "ENUM":
            ed = self._find_enum(entry.rules_values or "", connector=connector)
            if ed is None:
                return None
            return {
                "kind": "enum",
                "values": [{"value": v.value, "label": v.label_for(language)} for v in ed.values],
            }
        if rule == "LOOKUP":
            lk = self._find_lookup(entry.rules_values or "", connector=connector)
            if lk is None:
                return None
            wire: dict[str, Any] = {
                "kind": "lookup",
                "connector": lk.connector or connector,
                "query": lk.query,
                "value": lk.value,
                "label": lk.label,
            }
            # Pass through the entry's static parameter bindings (v1's ly_dictionary_filters).
            # Without these the lookup query may return nothing (UDC queries need SY/RT to know
            # which UDC table to read).
            if entry.lookup_params:
                wire["params"] = dict(entry.lookup_params)
            # ``return_params`` (v1's ly_lkp_params with ``lkp_dir = 'OUT'``) — extra dd_ids to
            # write back from the picked row to other form fields / grid cells. Surfaced on the
            # wire so the frontend's lookup-pick handler can populate sibling fields.
            if lk.return_params:
                wire["return_params"] = list(lk.return_params)
            # ``params`` (the lookup's declared query params) double as the **key columns** that
            # disambiguate a non-unique ``value``: e.g. SECURITY_USERS has the same USR_ID across
            # apps, so USR_ID is only unique per USR_APPS_ID. The grid uses these to resolve the
            # right label per row (match the row's same-named column), so the operator doesn't have
            # to add a ``filter_from`` to every lookup column — it's automatic from the lookup def.
            if lk.params:
                wire["key_columns"] = list(lk.params)
            return wire
        # Form-layer auto-fill rules — v1's `dd_rules` shapes the dialog runtime can act on at
        # *open time* (add mode only). Each maps to a built-in source the frontend resolves
        # against its installed providers (auth username, JS clock). The matching source ids
        # mirror the action-runner ``#NAME#`` built-ins so a future widget-side reuse stays
        # consistent. PASSWORD is intentionally NOT here — the masking is driven by the
        # ``format = "password"`` flag, not the rule; treating it as auto_fill would seed the
        # column with the user's password, which is the bug we already fixed elsewhere.
        # SEQUENCE / NN are server-side (SQLConnector._resolve_sequences fires the sequence
        # query inside the INSERT transaction); auto-filling the form would mean firing the
        # sequence on every Open and reverting on Cancel — not worth the complexity.
        if rule in ("SYSDATE", "CURRENT_DATE"):
            return {"kind": "auto_fill", "source": "current_date"}
        if rule == "LOGIN":
            return {"kind": "auto_fill", "source": "login_user"}
        return None


def infer_false_value(true_value: str | None) -> str | None:
    """Best-effort inference of a BOOLEAN rule's false value from its true value. Mirrors v1's
    common pairs — operator overrides via ``DictionaryEntry.false_value``. Returns ``None`` when
    no obvious counterpart exists (the field then writes SQL NULL on uncheck, the v1 default).

    Inferred pairs:

    * ``"Y"`` / ``"y"`` → ``"N"`` / ``"n"`` (case preserved)
    * ``"1"`` → ``"0"`` (numeric flag)
    * ``"true"`` / ``"True"`` / ``"TRUE"`` → ``"false"`` / ``"False"`` / ``"FALSE"`` (case preserved)
    * everything else (``"01"``, ``"A"``, ``"YES"``, …) → ``None`` (no auto-counterpart; the
      operator sets ``false_value`` explicitly if the DB needs one)
    """
    if not true_value:
        return None
    if true_value == "Y":
        return "N"
    if true_value == "y":
        return "n"
    if true_value == "1":
        return "0"
    if true_value == "true":
        return "false"
    if true_value == "True":
        return "False"
    if true_value == "TRUE":
        return "FALSE"
    return None


def parse_dictionary(data: dict[str, Any]) -> DictionaryFile:
    """Validate a raw TOML dict into a :class:`DictionaryFile`."""
    return DictionaryFile.model_validate(data)


def load_dictionary(path: Path | str) -> DictionaryFile:
    """Load and validate ``dictionary.toml``. A missing file yields an empty dictionary."""
    path = Path(path)
    if not path.exists():
        return DictionaryFile()
    with path.open("rb") as fh:
        return parse_dictionary(tomllib.load(fh))
