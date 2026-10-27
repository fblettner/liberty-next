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
    f = parse_explorer({"explorer": {"nomajde": {"sales": {**MODEL, "entities": {**MODEL["entities"], "sales": {**MODEL["entities"]["sales"], "cardinality": "many"}}}}}})
    m = f.explorer["nomajde"]["sales"]
    assert m.id == "sales" and m.app == "nomajde" and m.entities["company"].id == "company"
    assert f.get("nomajde", "sales") is m and f.get("nomajde", "nope") is None and [(a, i) for a, i, _ in f.items()] == [("nomajde", "sales")]
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
        parse_explorer({"explorer": {"a": {"m": {**MODEL, **patch}}}})


def test_load_missing_file_is_empty(tmp_path) -> None:
    assert load_explorer(tmp_path / "explorer.toml").explorer == {}
    (tmp_path / "explorer.toml").write_text('[explorer.a.m]\nhub = "s"\n[explorer.a.m.entities.s]\nquery = "q"\n', encoding="utf-8")
    assert list(load_explorer(tmp_path / "explorer.toml").explorer["a"]) == ["m"]
    with pytest.raises(ValueError, match="model id"):
        parse_explorer({"explorer": {"a": {"bad id": {"hub": "s", "entities": {"s": {"query": "q"}}}}}})


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


def test_preset_model_defers_completeness_and_table_entities() -> None:
    from liberty.explorer.model import apply_preset, resolve_column, resolve_relations, EntityView
    from liberty.explorer.model import ExplorerField as F
    # with a preset, no hub and no entity are needed at parse time; an entity may be a table
    f = parse_explorer({"explorer": {"jde": {"ab": {"preset": "address_book"}}}})
    assert f.explorer["jde"]["ab"].entities == {}
    m = parse_explorer({"explorer": {"jde": {"ab": {"preset": "address_book", "hub": "f0101", "entities": {"f0101": {"table": "F0101", "schema": "#SCHEMA.DTA#", "measures": {"ABAT1": "count"}}}}}}}).explorer["jde"]["ab"]
    # a declared key wins over the preset's
    _, _, k = apply_preset(parse_explorer({"explorer": {"jde": {"ab": {"preset": "x", "entities": {"f0101": {"table": "F0101", "key": ["ABMCU"]}}}}}}).explorer["jde"]["ab"], {"id": "x", "tables": [{"name": "F0101", "key": ["AN8"]}]})
    assert k == {}
    assert m.entities["f0101"].table_sql == "SELECT * FROM #SCHEMA.DTA#.F0101"
    with pytest.raises(ValueError, match="hub 'x' is not an entity"):
        parse_explorer({"explorer": {"jde": {"ab": {"preset": "address_book", "hub": "x", "entities": {"f0101": {"table": "F0101"}}}}}})
    with pytest.raises(ValueError, match="a query or a table is required"):
        ExplorerEntity(id="x")
    preset = {"id": "address_book", "connector": "jdedwards", "tables": [
        {"schema": "#SCHEMA.DTA#", "name": "F0101", "key": ["AN8"]},
        {"schema": "#SCHEMA.DTA#", "name": "F0111", "join": "LEFT", "on": [{"left": "F0101.AN8", "right": "F0111.AN8"}], "key": ["AN8", "IDLN"]},
        {"schema": "#SCHEMA.DTA#", "name": "F0115", "join": "LEFT", "on": [{"left": "F0111.AN8", "right": "F0115.AN8"}, {"left": "F0111.IDLN", "right": "F0115.IDLN"}], "query": "f0115_get"},
    ]}
    eff, pending, keys = apply_preset(m, preset)
    assert keys == {"f0101": ["AN8"], "f0111": ["AN8", "IDLN"]}      # resolved to columns by materialize_preset
    assert eff.hub == "f0101" and set(eff.entities) == {"f0101", "f0111", "f0115"}
    assert eff.entities["f0101"].measures == {"ABAT1": "count"}            # the declared entity overrides the derived one…
    assert eff.entities["f0101"].connector == "jdedwards" and eff.entities["f0101"].table == "F0101"   # …and inherits what it left unset
    assert eff.entities["f0111"].table == "F0111" and eff.entities["f0111"].cardinality == "many" and eff.entities["f0111"].connector == "jdedwards"
    assert eff.entities["f0115"].query == "f0115_get" and eff.entities["f0115"].table is None
    assert pending == [("f0111", "f0101", [("AN8", "AN8")]), ("f0115", "f0111", [("AN8", "AN8"), ("IDLN", "IDLN")])]
    # data items resolve to physical columns: by name, by dictionary item, or minus the prefix
    fields = [F(key="f0101.ABAN8", entity="f0101", name="ABAN8", label="", type="number", kind="dimension"), F(key="f0101.ABALPH", entity="f0101", name="ABALPH", label="", type="string", kind="dimension", dd="ALPH")]
    assert resolve_column(fields, "AN8", 2) == "ABAN8" and resolve_column(fields, "ALPH", 0) == "ABALPH" and resolve_column(fields, "ABAN8", 2) == "ABAN8" and resolve_column(fields, "NOPE", 2) is None
    views = [
        EntityView(entity=eff.entities["f0101"], fields=fields),
        EntityView(entity=eff.entities["f0111"], fields=[F(key="f0111.WWAN8", entity="f0111", name="WWAN8", label="", type="number", kind="dimension"), F(key="f0111.WWIDLN", entity="f0111", name="WWIDLN", label="", type="number", kind="dimension")]),
        EntityView(entity=eff.entities["f0115"], fields=[F(key="f0115.WPAN8", entity="f0115", name="WPAN8", label="", type="number", kind="dimension")]),
    ]
    rels, warnings = resolve_relations(views, pending, {"f0101": 2, "f0111": 2, "f0115": 2})
    assert [(r.from_, r.to, r.on) for r in rels] == [("f0111", "f0101", [["WWAN8", "ABAN8"]]), ("f0115", "f0111", [["WPAN8", "WWAN8"]])]
    assert warnings == ["preset join f0115.IDLN = f0111.IDLN: column not found on f0115"]


def test_resolve_dictionary_labels_unhinted_columns_through_the_prefix() -> None:
    from liberty.connectors.dictionary import DictionaryEntry, DictionaryFile, DictionarySection
    from liberty.explorer.model import resolve_dictionary
    d = DictionaryFile(default_language="en", entries={"ALPH": DictionaryEntry(label="Alpha Name", format="text")},
                       connectors={"jde": DictionarySection(entries={"AN8": DictionaryEntry(label="Address Number", format="integer"), "ALPH": DictionaryEntry(label="Name (JDE)")})})
    cols = [Column(name="ABAN8", type="NUMBER"), Column(name="ABALPH", type="NCHAR"), Column(name="ABMCU", type="NCHAR"), Column(name="ABTAX", type="NCHAR", label="Tax id", dd="TAX")]
    resolve_dictionary(cols, d, "jde", 2, "en")
    assert (cols[0].label, cols[0].format, cols[0].dd) == ("Address Number", "integer", "AN8")   # the connector's section
    assert (cols[1].label, cols[1].dd) == ("Name (JDE)", "ALPH")                                   # the connector's wins over the shared
    assert cols[2].label is None                                                                   # no entry: left alone
    assert (cols[3].label, cols[3].dd) == ("Tax id", "TAX")                                        # the screen's hint is kept
    resolve_dictionary([c := Column(name="ABALPH", type="NCHAR")], d, "other", 2, "en")
    assert c.label == "Alpha Name"                                                                 # the shared entry
