"""Every field of the forms built from the configuration models has a label in business words and
one short sentence of help (:mod:`liberty.field_labels`) — and the builders receive them."""

from __future__ import annotations

from liberty.actions.config import SharedActionsFile
from liberty.connectors.config import ColumnHint, PoolConfig, SqlConnectorConfig
from liberty.connectors.dictionary import DictionaryEntry, DictionaryFile
from liberty.field_labels import LABELS, MAX_HINT, apply_labels, label_of, problems_in, title_of
from liberty.menus.config import MenusFile
from liberty.screens.config import RunQueryAction, ScreensFile

# The models whose forms the builders draw from the schema (``GET /admin/config/schema`` labels
# exactly these): pools, SQL connectors, the dictionary, menus, screens, shared services.
FORM_ROOTS = [PoolConfig, SqlConnectorConfig, DictionaryFile, MenusFile, ScreensFile, SharedActionsFile]


def test_every_field_of_the_generated_forms_has_a_label_and_a_short_hint() -> None:
    # A field added to one of these models needs its entry: a label a consultant understands
    # ("Dictionary item", not "Dd") and one sentence, without file notation or codes.
    assert problems_in(FORM_ROOTS) == []


def test_the_labels_are_business_words_not_field_names() -> None:
    assert title_of(ColumnHint, "dd") == "Dictionary item"
    assert title_of(ColumnHint, "rules") == "Kind of field"
    assert title_of(ColumnHint, "rules_values") == "List or source"
    assert title_of(ColumnHint, "disable_on_add") == "Read only when adding"
    assert title_of(DictionaryEntry, "rules_values") == "List or source"


def test_a_model_inherits_the_entries_of_its_bases() -> None:
    # every action has an id and a label: said once, on the base class
    assert "id" not in LABELS["RunQueryAction"]
    assert label_of(RunQueryAction, "id") == LABELS["_ActionBase"]["id"]
    assert label_of(RunQueryAction, "query") == LABELS["RunQueryAction"]["query"]
    assert label_of(RunQueryAction, "no_such_field") is None


def test_the_schema_the_builders_receive_carries_them() -> None:
    schema = apply_labels(ScreensFile.model_json_schema(), ScreensFile)
    column = schema["$defs"]["ColumnHint"]["properties"]
    assert column["dd"]["title"] == "Dictionary item"
    assert column["dd"]["description"] == LABELS["ColumnHint"]["dd"][1]
    # what the model itself says is kept, behind "more"
    assert column["dd"]["x_more"].startswith("Inherit label / format / rule from this dictionary entry.")
    assert len(column["write_when"]["description"]) <= MAX_HINT < len(column["write_when"]["x_more"])
    # nothing else of the schema moved: types, groups and pickers are the model's
    assert column["dd"].get("x_enum_ref") == ColumnHint.model_json_schema()["properties"]["dd"].get("x_enum_ref")
    assert set(column) == set(ColumnHint.model_json_schema()["properties"])


def test_no_more_when_the_model_says_nothing_longer() -> None:
    schema = apply_labels(MenusFile.model_json_schema(), MenusFile)
    assert "x_more" not in schema["$defs"]["AppMenu"]["properties"]["label"]     # the model has no description
    assert schema["$defs"]["AppMenu"]["properties"]["label"]["title"] == "Application name"


def test_the_root_model_is_labelled_too() -> None:
    schema = apply_labels(PoolConfig.model_json_schema(), PoolConfig)
    assert schema["properties"]["url"]["title"] == "Address"
    assert schema["properties"]["trim_strings"]["title"] == "Trim trailing spaces"
