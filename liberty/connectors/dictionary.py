"""The shared **field dictionary** — ``config/dictionary.toml``.

v1's ``ly_dictionary`` defined a field once (``dd_id`` → label, type, rules, default)
and reused it across every table/form; ``ly_dictionary_l`` carried the per-language
labels. v2 keeps that idea as a config file: a connector query's ``columns`` hints
*reference* a dictionary entry (``{ name = "USR_NAME", dd = "USR_NAME" }`` — and a
bare ``{ name = "USR_NAME" }`` looks the entry up under the column name), and the SQL
connector resolves the label/format from the dictionary at result time (a per-column
``label``/``format`` on the hint still overrides, like v1's ``col_label``/``col_type``).
The migration emits this file from ``ly_dictionary`` (+ ``ly_dictionary_l``).

Example::

    default_language = "en"

    [entries.USR_NAME]
    label  = "User Name"
    format = "text"
    [entries.USR_NAME.l]
    fr = "Nom d'utilisateur"

    [entries.USR_STATUS]
    label  = "Status"
    format = "boolean"
    [entries.USR_STATUS.l]
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


class DictionaryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_language: str = "en"
    entries: dict[str, DictionaryEntry] = Field(default_factory=dict)

    def resolve(self, key: str, language: str | None) -> tuple[str | None, str | None]:
        """``(label, format)`` for *key* in *language* (``(None, None)`` if there's no such entry)."""
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
