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
    (v1's ``ly_dictionary_l``); ``rules``/``rules_values``/``default`` are carried over
    from v1 verbatim — they're not interpreted by v2 yet."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None          # the default-language display title (v1's dd_label)
    format: str | None = None         # e.g. "date" / "number" / "boolean" / "textarea" (v1's dd_type)
    rules: str | None = None          # v1's dd_rules — pass-through
    rules_values: str | None = None   # v1's dd_rules_values — pass-through
    default: str | None = None        # v1's dd_default — pass-through
    l: dict[str, str] = Field(default_factory=dict)  # language code → translated label

    def label_for(self, language: str | None) -> str | None:
        """The label in *language* if a translation exists, else the default label."""
        if language and self.l:
            return self.l.get(language) or self.label
        return self.label


class DictionarySection(BaseModel):
    """A per-connector group of entries (``[connectors.<name>.entries.*]``)."""

    model_config = ConfigDict(extra="forbid")

    entries: dict[str, DictionaryEntry] = Field(default_factory=dict)


class DictionaryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_language: str = "en"
    entries: dict[str, DictionaryEntry] = Field(default_factory=dict)              # shared / common
    connectors: dict[str, DictionarySection] = Field(default_factory=dict)          # per-connector

    def resolve(self, key: str, language: str | None, *, connector: str | None = None) -> tuple[str | None, str | None]:
        """``(label, format)`` for *key* in *language* — the entry from *connector*'s section if it
        has one, else the top-level (shared) entry; ``(None, None)`` if neither exists."""
        e: DictionaryEntry | None = None
        if connector and connector in self.connectors:
            e = self.connectors[connector].entries.get(key)
        if e is None:
            e = self.entries.get(key)
        if e is None:
            return None, None
        return e.label_for(language), e.format


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
