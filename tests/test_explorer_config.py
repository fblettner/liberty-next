"""``explorer.toml`` schema + the field classification (:mod:`liberty.explorer`)."""

from __future__ import annotations

import pytest

from liberty.connectors.sql import Column
from liberty.explorer import ExplorerEntity, load_explorer, parse_explorer
from liberty.explorer.model import fields_from_columns, type_class

MODEL = {
    "hub": "sales",
    "entities": {
        "sales": {"query": "fact_sales_get", "cardinality": "many", "key": ["ID"], "default_date": "SALE_DATE", "measures": {"QTY": "sum", "REVENUE": "sum"}},
        "company": {"query": "dim_company_get", "key": ["COMPANY"], "label": "Company"},
        "region": {"query": "dim_region_get", "key": ["REGION"]},
    },
    "relations": [
        {"from": "sales", "to": "company", "on": [["COMPANY", "COMPANY"]]},
        {"from": "company", "to": "region", "on": [["REGION", "REGION"]]},
    ],
}


def test_parse_injects_ids_and_forces_hub_cardinality() -> None:
    f = parse_explorer({"explorer": {"nomajde": {**MODEL, "entities": {**MODEL["entities"], "sales": {**MODEL["entities"]["sales"], "cardinality": "many"}}}}})
    m = f.explorer["nomajde"]
    assert m.id == "nomajde" and m.entities["company"].id == "company"
    assert m.entities["sales"].cardinality == "one"        # the hub is "one" by definition
    assert m.entities["company"].cardinality == "one"
    assert [(r.from_, r.to) for r in m.path_to_hub("region")] == [("company", "region"), ("sales", "company")]
    assert m.path_to_hub("sales") == []
    assert m.reachable("sales") == {"sales", "company", "region"}
    # relations dump with their `from` alias
    assert m.model_dump(by_alias=True)["relations"][0]["from"] == "sales"


@pytest.mark.parametrize("patch, message", [
    ({"hub": "nope"}, "hub 'nope' is not an entity"),
    ({"relations": [{"from": "sales", "to": "ghost", "on": [["A", "A"]]}]}, "unknown entity 'ghost'"),
    ({"relations": [{"from": "sales", "to": "company", "on": []}]}, "at least one column pair"),
    ({"relations": [{"from": "sales", "to": "company", "on": [["A"]]}]}, "expected [from_column, to_column]"),
    ({"relations": [{"from": "sales", "to": "company", "on": [["A", "A"]]}]}, "not related to the hub 'sales': ['region']"),
    ({"relations": [{"from": "sales", "to": "sales", "on": [["A", "A"]]}]}, "cannot relate to itself"),
    ({"entities": {**MODEL["entities"], "bad id": {"query": "q"}}, "relations": []}, "letters, digits"),
])
def test_validation_errors(patch, message) -> None:
    with pytest.raises(ValueError, match=__import__("re").escape(message)):
        parse_explorer({"explorer": {"a": {**MODEL, **patch}}})


def test_load_missing_file_is_empty(tmp_path) -> None:
    assert load_explorer(tmp_path / "explorer.toml").explorer == {}
    (tmp_path / "explorer.toml").write_text('[explorer.a]\nhub = "s"\n[explorer.a.entities.s]\nquery = "q"\n', encoding="utf-8")
    assert list(load_explorer(tmp_path / "explorer.toml").explorer) == ["a"]


def test_type_class() -> None:
    assert type_class(Column(name="A", type="INTEGER")) == "number"
    assert type_class(Column(name="A", type="numeric")) == "number"
    assert type_class(Column(name="A", type="VARCHAR", format="currency")) == "number"
    assert type_class(Column(name="A", type="NUMBER", format="jdedate")) == "date"
    assert type_class(Column(name="A", type="timestamp")) == "datetime"
    assert type_class(Column(name="A", type="DATE")) == "date"
    assert type_class(Column(name="A", type="VARCHAR", rule={"kind": "boolean", "true_value": "Y"})) == "boolean"
    assert type_class(Column(name="A", type="NUMBER", format="jdetime")) == "string"
    assert type_class(Column(name="A", type=None)) == "string"
    # no driver type (SQLite): the sampled value decides
    from datetime import date, datetime
    from decimal import Decimal
    assert type_class(Column(name="A", type=None), 3) == "number" and type_class(Column(name="A", type=None), Decimal("1.5")) == "number"
    assert type_class(Column(name="A", type=None), date(2026, 1, 1)) == "date" and type_class(Column(name="A", type=None), datetime(2026, 1, 1)) == "datetime"
    assert type_class(Column(name="A", type=None), True) == "boolean" and type_class(Column(name="A", type=None), "x") == "string"


def test_fields_from_columns_classifies_and_warns() -> None:
    e = ExplorerEntity(id="sales", query="q", key=["ID"], default_date="SALE_DATE", measures={"QTY": "sum", "NAME": "avg", "GHOST": "sum"}, hidden=["ID"])
    cols = [
        Column(name="ID", type="INTEGER"),
        Column(name="QTY", type="INTEGER", label="Quantity", dd="QTY"),
        Column(name="NAME", type="VARCHAR"),
        Column(name="SALE_DATE", type="DATE"),
        Column(name="SECRET", type="VARCHAR", hidden=True),
        Column(name="STATUS", type="VARCHAR", rule={"kind": "enum", "values": [{"value": "O", "label": "Open"}]}),
    ]
    fields, warnings = fields_from_columns(e, cols)
    by = {f.name: f for f in fields}
    assert by["QTY"].kind == "measure" and by["QTY"].aggregate == "sum" and by["QTY"].label == "Quantity" and by["QTY"].key == "sales.QTY"
    assert by["NAME"].kind == "dimension" and by["NAME"].aggregate is None
    assert by["ID"].hidden and by["SECRET"].hidden and not by["QTY"].hidden
    assert by["SALE_DATE"].type == "date" and by["STATUS"].rule["kind"] == "enum"
    assert any("GHOST" in w for w in warnings) and any("NAME" in w and "typed string" in w for w in warnings)
    assert by["QTY"].to_dict()["dd"] == "QTY" and "hidden" not in by["QTY"].to_dict()
    # Postgres returns lowercase names: the field keeps the uppercase convention, the config matches
    fields, _ = fields_from_columns(ExplorerEntity(id="s", query="q", measures={"QTY": "sum"}, hidden=["id"]), [Column(name="id", type="int4"), Column(name="qty", type="int4", label="Quantity")])
    assert [(f.key, f.name, f.kind, f.hidden) for f in fields] == [("s.ID", "ID", "dimension", True), ("s.QTY", "QTY", "measure", False)]
