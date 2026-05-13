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

    label: str | None = Field(default=None, description="Default-language display title (v1's dd_label).")
    format: str | None = Field(
        default=None,
        description="Display format hint — e.g. 'date' / 'number' / 'boolean' / 'textarea' (v1's dd_type). The frontend uses it to render the cell. Free-text — values come from the DICTIONARY_TYPE framework enum but a custom value (numeric / decimal / …) is still accepted.",
        json_schema_extra={"x_enum_ref": "DICTIONARY_TYPE"},
    )
    rules: str | None = Field(
        default=None,
        description="Display rule (v1's dd_rules) — BOOLEAN / ENUM / LOOKUP show a ✓/✗ / label / lookup-resolved label in the grid. SEQUENCE / SYSDATE / LOGIN / PASSWORD / CURRENT_DATE are form-layer (Phase 6).",
        json_schema_extra={"x_group": "Rule", "x_enum_ref": "DICTIONARY_RULES"},
    )
    rules_values: str | None = Field(
        default=None,
        description="The rule's argument — true-value for BOOLEAN (default 'Y'), enum id for ENUM, lookup id for LOOKUP.",
        json_schema_extra={"x_group": "Rule"},
    )
    default: str | None = Field(
        default=None,
        description="Default value (form-layer, pass-through — v1's dd_default).",
        json_schema_extra={"x_group": "Rule"},
    )
    l: dict[str, str] = Field(
        default_factory=dict,
        description="Per-language overrides for the label: {language_code: translated_label} (v1's ly_dictionary_l).",
        json_schema_extra={"x_group": "Translations"},
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

    value: str = Field(description="Code as it appears in the cell (e.g. 'JDE').")
    label: str | None = Field(default=None, description="Default-language label (e.g. 'JD Edwards').")
    l: dict[str, str] = Field(
        default_factory=dict,
        description="Per-language label overrides: {language_code: translated_label}.",
        json_schema_extra={"x_group": "Translations"},
    )

    def label_for(self, language: str | None) -> str:
        if language and self.l:
            return self.l.get(language) or self.label or self.value
        return self.label or self.value


class EnumDef(BaseModel):
    """A fixed set of code → label pairs (v1's ``ly_enum`` + ``ly_enum_val``)."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, description="Display name for the enum set (informational).")
    values: list[EnumValue] = Field(
        default_factory=list,
        description="Members of the enum — each row is one {value, label, l?} pair.",
        json_schema_extra={"x_group": "Values"},
    )


class LookupDef(BaseModel):
    """A reference to a *query* whose rows resolve a cell's value to a human label
    (v1's ``ly_lookup`` — ``lkp_query_id``/``lkp_dd_id``/``lkp_dd_label``). The frontend
    fetches the query once and uses the named columns as a ``{value: label}`` map."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, description="Display name / description (informational).")
    connector: str | None = Field(
        default=None,
        description="Connector the lookup query lives on. Blank → the asking connector (the one the lookup is referenced from).",
        json_schema_extra={"x_group": "Target"},
    )
    query: str = Field(
        description="The v2 query name (the *read* migrated name, e.g. 'security_roles_get').",
        json_schema_extra={"x_group": "Target"},
    )
    value: str = Field(
        description="The result column whose value matches the cell.",
        json_schema_extra={"x_group": "Target"},
    )
    label: str = Field(
        description="The result column whose value to display in place of the code.",
        json_schema_extra={"x_group": "Target"},
    )
    group: str | None = Field(
        default=None,
        description="Optional secondary key (v1's lkp_dd_group — not used yet).",
        json_schema_extra={"x_group": "Advanced"},
    )


class DictionarySection(BaseModel):
    """A per-connector group of entries / enums / lookups (``[connectors.<name>.…]``)."""

    model_config = ConfigDict(extra="forbid")

    entries: dict[str, DictionaryEntry] = Field(default_factory=dict)
    enums: dict[str, EnumDef] = Field(default_factory=dict)
    lookups: dict[str, LookupDef] = Field(default_factory=dict)


class DictionaryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_language: str = "en"
    entries: dict[str, DictionaryEntry] = Field(default_factory=dict)              # shared / common
    enums: dict[str, EnumDef] = Field(default_factory=dict)
    lookups: dict[str, LookupDef] = Field(default_factory=dict)
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

        * ``{"kind": "boolean", "true_value": "Y"}`` — values equal to ``true_value`` display as
          "yes"; else "no" (nulls stay null). ``true_value`` defaults to ``"Y"`` when unset.
        * ``{"kind": "enum", "values": [{"value": "JDE", "label": "JD Edwards"}, …]}`` — the enum's
          members, with each label resolved in *language*; the frontend renders the matching label.
        * ``{"kind": "lookup", "connector": "nomasx1", "query": "security_roles_get",
          "value": "ROL_ID", "label": "ROL_NAME"}`` — a *reference* (the frontend fetches the query
          once and uses the named columns as a ``{value: label}`` map).
        """
        rule = (entry.rules or "").strip().upper()
        if rule == "BOOLEAN":
            return {"kind": "boolean", "true_value": (entry.rules_values or "Y")}
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
            return {
                "kind": "lookup",
                "connector": lk.connector or connector,
                "query": lk.query,
                "value": lk.value,
                "label": lk.label,
            }
        return None  # the form-layer rules (SEQUENCE/SYSDATE/LOGIN/PASSWORD/…) — not a display transform


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
