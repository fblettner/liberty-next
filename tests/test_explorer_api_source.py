"""The Explorer over an API entity (``liberty.explorer.api_source``): the rows of an endpoint
filtered, grouped, measured, sorted and paged in Python — the same answers as the SQL engine's."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from liberty.explorer import api_source as src
from liberty.explorer.config import ExplorerEntity, ExplorerModel
from liberty.explorer.engine import AggregateRequest, DetailRequest, ExplorerError
from liberty.explorer.model import EntityView, ExplorerField, ModelView


def _view() -> ModelView:
    entity = ExplorerEntity(id="orders", connector="shopapi", query="orders_list", key=["ID"])
    other = ExplorerEntity(id="customers", connector="shop", query="customers")
    model = ExplorerModel(id="sales", app="shop", hub="orders", entities={"orders": entity, "customers": other},
                          relations=[{"from": "orders", "to": "customers", "on": [["ID", "ID"]]}])
    f = lambda name, typ, kind="dimension", **kw: ExplorerField(key=f"orders.{name}", entity="orders", name=name, label=name.title(), type=typ, kind=kind, **kw)  # noqa: E731
    fields = [f("ID", "number"), f("COUNTRY", "string"), f("STATUS", "string", rule={"kind": "enum", "values": []}), f("AMOUNT", "number", "measure", decimals=2),
              f("ORDERED", "date"), f("NOTE", "string", hidden=True)]
    cust = [ExplorerField(key="customers.NAME", entity="customers", name="NAME", label="Name", type="string", kind="dimension")]
    return ModelView(model=model, app="shop", entities=[EntityView(entity=model.entities["orders"], fields=fields), EntityView(entity=model.entities["customers"], fields=cust)])


# the endpoint's rows, as an API hands them: its own key case, padded text, numbers as text, a missing value
ROWS: list[dict[str, Any]] = [
    {"id": 1, "country": "FR ", "status": "O", "amount": "100.50", "ordered": "2026-01-15", "note": "a"},
    {"id": 2, "country": "FR", "status": "C", "amount": 50, "ordered": "2026-01-20", "note": None},
    {"id": 3, "country": "de", "status": "O", "amount": 200, "ordered": "2026-02-03", "note": ""},
    {"id": 4, "country": "DE", "status": "O", "amount": None, "ordered": "2026-02-10", "note": "x"},
    {"id": 5, "country": None, "status": "C", "amount": 10, "ordered": None, "note": "y"},
]


def test_group_and_measure() -> None:
    req = AggregateRequest(entity="orders", rows=[{"field": "orders.STATUS"}],
                           measures=[{"field": "orders.AMOUNT", "agg": "sum"}, {"field": "orders.AMOUNT", "agg": "avg"}, {"agg": "count"},
                                     {"field": "orders.AMOUNT", "agg": "count"}, {"field": "orders.COUNTRY", "agg": "count_distinct"},
                                     {"field": "orders.AMOUNT", "agg": "max"}])
    out = src.aggregate_rows(_view(), req, ROWS)
    assert out["mode"] == "aggregate" and out["groups"] == 2 and out["scanned"] == 5 and not out["truncated"]
    by = {r["orders.STATUS"]: r for r in out["rows"]}
    assert by["O"]["sum(orders.AMOUNT)"] == 300.5 and by["O"]["avg(orders.AMOUNT)"] == 150.25          # the missing amount is not averaged in
    assert by["O"]["count(*)"] == 3 and by["O"]["count(orders.AMOUNT)"] == 2 and by["O"]["max(orders.AMOUNT)"] == 200
    assert by["O"]["count_distinct(orders.COUNTRY)"] == 3                                               # "FR ", "de", "DE": trimmed, the case kept
    assert by["C"]["sum(orders.AMOUNT)"] == 60 and [c["key"] for c in out["columns"]][:2] == ["orders.STATUS", "sum(orders.AMOUNT)"]
    assert out["columns"][0]["rule"] == {"kind": "enum", "values": []} and out["columns"][1]["decimals"] == 2


def test_buckets_filters_period_sort_and_limit() -> None:
    view = _view()
    req = AggregateRequest(entity="orders", rows=[{"field": "orders.ORDERED", "bucket": "month"}], measures=[{"field": "orders.AMOUNT", "agg": "sum"}])
    out = src.aggregate_rows(view, req, ROWS)
    assert [(r["orders.ORDERED@month"], r["sum(orders.AMOUNT)"]) for r in out["rows"]] == [("2026-01", 150.5), ("2026-02", 200), (None, 10)]   # nulls last
    # filters: text without regard to case and padding, a number range, a list; the period keeps January
    f = lambda **bag: [r["orders.COUNTRY"] for r in src.aggregate_rows(view, AggregateRequest(entity="orders", rows=[{"field": "orders.COUNTRY"}], filters=bag), ROWS)["rows"]]  # noqa: E731
    assert f(**{"orders.COUNTRY": "fr", "orders.COUNTRY__op": "equals"}) == ["FR"]
    assert f(**{"orders.COUNTRY": "d"}) == ["DE", "de"]                                               # contains by default on text
    assert f(**{"orders.AMOUNT__from": 60, "orders.AMOUNT__to": 150}) == ["FR"]
    assert f(**{"orders.ID": [3, 5]}) == ["de", None]
    assert f(**{"orders.COUNTRY__op": "empty", "orders.COUNTRY": ""}) == [None]
    jan = AggregateRequest(entity="orders", measures=[{"agg": "count"}], period={"field": "orders.ORDERED", "from": "2026-01-01", "to": "2026-01-31"})
    assert src.aggregate_rows(view, jan, ROWS)["rows"] == [{"count(*)": 2}]
    top = AggregateRequest(entity="orders", rows=[{"field": "orders.COUNTRY"}], measures=[{"field": "orders.AMOUNT", "agg": "sum"}],
                           sort=[{"key": "sum(orders.AMOUNT)", "dir": "desc"}], limit=1)
    out = src.aggregate_rows(view, top, ROWS)
    assert out["rows"] == [{"orders.COUNTRY": "de", "sum(orders.AMOUNT)": 200}] and out["truncated"] and out["groups"] == 4
    # nothing matches and no dimension: one line of zeros, as a SELECT without GROUP BY answers
    none = AggregateRequest(entity="orders", measures=[{"agg": "count"}, {"field": "orders.AMOUNT", "agg": "sum"}], filters={"orders.ID": 999})
    assert src.aggregate_rows(view, none, ROWS)["rows"] == [{"count(*)": 0, "sum(orders.AMOUNT)": None}]


def test_what_an_api_entity_refuses() -> None:
    view = _view()
    with pytest.raises(ExplorerError, match="stands alone"):
        src.aggregate_rows(view, AggregateRequest(entity="orders", rows=[{"field": "customers.NAME"}]), ROWS)
    with pytest.raises(ExplorerError, match="calculated field"):
        src.aggregate_rows(view, AggregateRequest(entity="orders", calculated=[{"name": "x", "expr": "AMOUNT * 2"}], measures=[{"agg": "count"}]), ROWS)
    with pytest.raises(ExplorerError, match="not numeric"):
        src.aggregate_rows(view, AggregateRequest(entity="orders", measures=[{"field": "orders.COUNTRY", "agg": "sum"}]), ROWS)
    with pytest.raises(ExplorerError, match="not valid on"):
        src.aggregate_rows(view, AggregateRequest(entity="orders", measures=[{"agg": "count"}], filters={"orders.AMOUNT": "1", "orders.AMOUNT__op": "contains"}), ROWS)
    with pytest.raises(ExplorerError, match="unknown field"):
        src.aggregate_rows(view, AggregateRequest(entity="orders", rows=[{"field": "orders.NOPE"}]), ROWS)


def test_detail_filtered_drilled_sorted_and_paged() -> None:
    view = _view()
    out = src.detail_rows(view, DetailRequest(entity="orders", page_size=2), ROWS)
    assert out["mode"] == "detail" and out["total"] == 5 and out["page_size"] == 2
    assert [c["key"] for c in out["columns"]] == ["orders.ID", "orders.COUNTRY", "orders.STATUS", "orders.AMOUNT", "orders.ORDERED"]   # the hidden one stays out
    assert [r["orders.ID"] for r in out["rows"]] == [1, 2]                                             # by the key, first page
    assert [r["orders.ID"] for r in src.detail_rows(view, DetailRequest(entity="orders", page=3, page_size=2), ROWS)["rows"]] == [5]
    drilled = DetailRequest(entity="orders", columns=["orders.ID", "orders.AMOUNT"], drill=[{"field": "orders.STATUS", "value": "O"}],
                            sort=[{"key": "orders.AMOUNT", "dir": "desc"}])
    out = src.detail_rows(view, drilled, ROWS)
    assert out["total"] == 3 and [r["orders.ID"] for r in out["rows"]] == [3, 1, 4]                    # the missing amount last
    month = DetailRequest(entity="orders", columns=["orders.ID"], drill=[{"field": "orders.ORDERED", "bucket": "month", "value": "2026-02"}])
    assert [r["orders.ID"] for r in src.detail_rows(view, month, ROWS)["rows"]] == [3, 4]


def test_matches() -> None:
    text = ExplorerField(key="e.T", entity="e", name="T", label="T", type="string", kind="dimension")
    num = ExplorerField(key="e.N", entity="e", name="N", label="N", type="number", kind="measure")
    day = ExplorerField(key="e.D", entity="e", name="D", label="D", type="date", kind="dimension")
    assert src.matches(text, "startsWith", "al", " Alpha ") and src.matches(text, "in", ["x", "ALPHA"], "alpha") and not src.matches(text, "equals", "a", None)
    assert src.matches(text, "empty", None, "  ") and src.matches(text, "notEmpty", None, "x")
    assert src.matches(num, "gt", "10", "1,250.5") and src.matches(num, "between", 1, 5) is False and not src.matches(num, "equals", 1, "abc")
    assert src.matches(day, "le", "2026-01-31", "2026-01-31T10:00:00") and src.matches(day, "gt", "2026-01-31", date(2026, 2, 1))
    assert not src.matches(day, "equals", "2026-01-31", "not a date")


@pytest.mark.asyncio
async def test_the_engine_hands_an_api_entity_over(monkeypatch) -> None:
    from liberty.explorer import engine

    view = _view()
    monkeypatch.setattr(src, "api_entity", lambda connectors, app, entity: entity.id == "orders")

    sent: list[dict[str, Any]] = []

    async def read(connectors: Any, screens: Any, app: str, entity: Any, **kw: Any):
        assert entity.query == "orders_list" and kw.get("scope") == "the caller's scope"
        sent.append(kw.get("values") or {})
        return [], ROWS, True

    # the endpoint takes STATUS and a limit: an "equals" on STATUS is sent to it, the rest is not
    from types import SimpleNamespace
    endpoint = SimpleNamespace(params=[SimpleNamespace(name="status"), SimpleNamespace(name="MAX")], limit_param="MAX")
    monkeypatch.setattr(src, "api_connector", lambda connectors, name: object())
    monkeypatch.setattr(src, "rows_endpoint", lambda conn, name: endpoint)

    monkeypatch.setattr(src, "read_entity", read)
    out = await engine.run_aggregate(object(), None, view, AggregateRequest(entity="orders", measures=[{"agg": "count"}]), scope="the caller's scope", debug=True)
    assert out["rows"] == [{"count(*)": 5}] and out["scan_truncated"] is True and "read through the API: shopapi.orders_list" in out["sql"]
    out = await engine.run_detail(object(), None, view, DetailRequest(entity="orders", columns=["orders.ID"], page_size=2), scope="the caller's scope")
    assert out["total"] == 5 and len(out["rows"]) == 2
    assert sent == [{}, {}]
    filtered = AggregateRequest(entity="orders", measures=[{"agg": "count"}],
                                filters={"orders.STATUS": "O", "orders.STATUS__op": "equals", "orders.COUNTRY": "fr", "orders.ID": [1, 2]})
    out = await engine.run_aggregate(object(), None, view, filtered, scope="the caller's scope")
    assert sent[-1] == {"status": "O"} and out["rows"] == [{"count(*)": 1}]            # sent to the endpoint AND applied to its rows
    await engine.run_detail(object(), None, view, DetailRequest(entity="orders", columns=["orders.ID"], drill=[{"field": "orders.STATUS", "value": "C"}]), scope="the caller's scope")
    assert sent[-1] == {"status": "C"}                                                 # a drill's cell too
