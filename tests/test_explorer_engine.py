"""The aggregate engine on SQLite: joins by cardinality, buckets merged in Python, filters,
period, correlated measures and EXISTS filters, sort / Top N, the row-level scope."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from liberty.auth.scope import DataScope
from liberty.connectors.config import ColumnHint, ConnectorsFile
from liberty.connectors.registry import ConnectorRegistry
from liberty.explorer import parse_explorer
from liberty.explorer.engine import AggregateRequest, ExplorerError, run_aggregate
from liberty.explorer.model import build_model_view
from liberty.screens.config import Screen, ScreensFile

ROWS = [
    # id, company, sold_on, qty, revenue
    (1, "C1", "2026-01-05", 3, 30), (2, "C1", "2026-01-20", 2, 25), (3, "C2", "2026-02-03", 5, 80),
    (4, "C2", "2026-02-28", 1, 10), (5, "C3", "2026-04-10", 4, 44), (6, "C1", "2025-12-31", 7, 70),
]
LINES = [(1, 1, 10), (1, 2, 20), (2, 1, 25), (3, 1, 50), (3, 2, 30), (5, 1, 200)]
COMPANIES = [("C1", "Corp one", "EU"), ("C2", "Corp two", "EU"), ("C3", "Corp three", "US")]

MODEL = {
    "hub": "sales",
    "entities": {
        "sales": {"query": "fact_sales_get", "screen": "sales", "key": ["ID"], "default_date": "SOLD_ON", "measures": {"QTY": "sum", "REVENUE": "sum"}},
        "company": {"query": "dim_company_get", "key": ["COMPANY"]},
        "lines": {"query": "sales_lines_get", "key": ["SALE_ID", "LINE"], "cardinality": "many", "measures": {"AMOUNT": "sum"}},
        "region": {"query": "dim_region_get", "key": ["REGION"]},
    },
    "relations": [
        {"from": "sales", "to": "company", "on": [["COMPANY", "COMPANY"]]},
        {"from": "lines", "to": "sales", "on": [["SALE_ID", "ID"]]},
        {"from": "company", "to": "region", "on": [["REGION", "REGION"]]},
    ],
}


@pytest_asyncio.fixture
async def env(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'x.db'}"
    cfg = ConnectorsFile.model_validate({
        "pools": {"default": {"url": url}},
        "connectors": {"shop": {"type": "sql", "pool": "default", "queries": [
            {"name": "fact_sales_get", "sql": "SELECT id AS ID, company AS COMPANY, sold_on AS SOLD_ON, qty AS QTY, revenue AS REVENUE FROM fact_sales ORDER BY id"},
            {"name": "dim_company_get", "sql": "SELECT company AS COMPANY, name AS NAME, region AS REGION FROM dim_company"},
            {"name": "sales_lines_get", "sql": "SELECT sale_id AS SALE_ID, line AS LINE, amount AS AMOUNT FROM sales_lines"},
            {"name": "dim_region_get", "sql": "SELECT DISTINCT region AS REGION, region || ' zone' AS ZONE FROM dim_company"},
        ]}},
    })
    registry = ConnectorRegistry(cfg)
    engine = registry.pools.engine("default")
    async with engine.begin() as c:
        await c.execute(text("CREATE TABLE fact_sales (id INTEGER PRIMARY KEY, company TEXT, sold_on TEXT, qty INTEGER, revenue NUMERIC)"))
        await c.execute(text("CREATE TABLE dim_company (company TEXT PRIMARY KEY, name TEXT, region TEXT)"))
        await c.execute(text("CREATE TABLE sales_lines (sale_id INTEGER, line INTEGER, amount NUMERIC)"))
        for r in ROWS:
            await c.execute(text("INSERT INTO fact_sales VALUES (:a, :b, :c, :d, :e)"), {"a": r[0], "b": r[1], "c": r[2], "d": r[3], "e": r[4]})
        for r in COMPANIES:
            await c.execute(text("INSERT INTO dim_company VALUES (:a, :b, :c)"), {"a": r[0], "b": r[1], "c": r[2]})
        for r in LINES:
            await c.execute(text("INSERT INTO sales_lines VALUES (:a, :b, :c)"), {"a": r[0], "b": r[1], "c": r[2]})
    screens = ScreensFile(screens={"shop": {"sales": Screen(
        id="sales", label="Sales", read_query="fact_sales_get",
        columns=[ColumnHint(name="SOLD_ON", format="date"), ColumnHint(name="QTY", label="Quantity"), ColumnHint(name="COMPANY", label="Company")],
    )}})
    model = parse_explorer({"explorer": {"shop": MODEL}}).explorer["shop"]
    view = await build_model_view(registry, screens, "shop", model, language="en")
    assert view.warnings == [], view.warnings
    yield registry, screens, view
    await registry.pools.dispose()


async def run(env, scope: DataScope | None = None, **req):
    registry, screens, view = env
    return await run_aggregate(registry, screens, view, AggregateRequest.model_validate(req), scope=scope, debug=True)


@pytest.mark.asyncio
async def test_sum_by_joined_dimension(env) -> None:
    out = await run(env, rows=[{"field": "company.NAME"}], measures=[{"field": "sales.REVENUE", "agg": "sum"}, {"agg": "count"}])
    assert [c["key"] for c in out["columns"]] == ["company.NAME", "sum(sales.REVENUE)", "count(*)"]
    assert out["rows"] == [
        {"company.NAME": "Corp one", "sum(sales.REVENUE)": 125, "count(*)": 3},
        {"company.NAME": "Corp three", "sum(sales.REVENUE)": 44, "count(*)": 1},
        {"company.NAME": "Corp two", "sum(sales.REVENUE)": 90, "count(*)": 2},
    ]
    assert out["groups"] == 3 and out["scanned"] == 3 and not out["truncated"] and "LEFT JOIN" in out["sql"]
    # a two-hop dimension: region through company
    out = await run(env, rows=[{"field": "region.ZONE"}], measures=[{"field": "sales.QTY"}])
    assert out["rows"] == [{"region.ZONE": "EU zone", "sum(sales.QTY)": 18}, {"region.ZONE": "US zone", "sum(sales.QTY)": 4}]
    # labels come from the screen hints
    assert next(c for c in out["columns"] if c["kind"] == "measure")["label"] == "Quantity"


@pytest.mark.asyncio
async def test_buckets_merge_in_python(env) -> None:
    month = await run(env, rows=[{"field": "sales.SOLD_ON", "bucket": "month"}], measures=[{"field": "sales.QTY"}, {"field": "sales.REVENUE", "agg": "avg"}, {"field": "sales.QTY", "agg": "max"}])
    assert [(r["sales.SOLD_ON@month"], r["sum(sales.QTY)"], r["avg(sales.REVENUE)"], r["max(sales.QTY)"]) for r in month["rows"]] == [
        ("2025-12", 7, 70.0, 7), ("2026-01", 5, 27.5, 3), ("2026-02", 6, 45.0, 5), ("2026-04", 4, 44.0, 4),
    ]
    assert month["scanned"] == 6 and month["groups"] == 4          # grouped at day level in SQL, merged here
    quarter = await run(env, rows=[{"field": "sales.SOLD_ON", "bucket": "quarter"}])
    assert [(r["sales.SOLD_ON@quarter"], r["count(*)"]) for r in quarter["rows"]] == [("2025-Q4", 1), ("2026-Q1", 4), ("2026-Q2", 1)]
    week = await run(env, rows=[{"field": "sales.SOLD_ON", "bucket": "week"}])
    assert week["rows"][0]["sales.SOLD_ON@week"] == "2026-W01"       # 2025-12-31 is ISO week 1 of 2026
    year = await run(env, rows=[{"field": "sales.SOLD_ON", "bucket": "year"}, {"field": "sales.COMPANY"}])
    assert year["rows"][:2] == [{"sales.SOLD_ON@year": "2025", "sales.COMPANY": "C1", "count(*)": 1}, {"sales.SOLD_ON@year": "2026", "sales.COMPANY": "C1", "count(*)": 2}]
    with pytest.raises(ExplorerError, match="count_distinct"):
        await run(env, rows=[{"field": "sales.SOLD_ON", "bucket": "month"}], measures=[{"field": "sales.COMPANY", "agg": "count_distinct"}])
    with pytest.raises(ExplorerError, match="not a date"):
        await run(env, rows=[{"field": "sales.COMPANY", "bucket": "month"}])


@pytest.mark.asyncio
async def test_filters_and_period(env) -> None:
    out = await run(env, rows=[{"field": "sales.COMPANY"}], filters={"company.NAME": "two"})
    assert out["rows"] == [{"sales.COMPANY": "C2", "count(*)": 2}]
    out = await run(env, rows=[{"field": "sales.COMPANY"}], filters={"sales.COMPANY": ["C1", "C3"]})
    assert [r["sales.COMPANY"] for r in out["rows"]] == ["C1", "C3"]
    out = await run(env, rows=[{"field": "sales.COMPANY"}], filters={"sales.QTY__from": "3", "sales.QTY__to": 5})
    assert out["rows"] == [{"sales.COMPANY": "C1", "count(*)": 1}, {"sales.COMPANY": "C2", "count(*)": 1}, {"sales.COMPANY": "C3", "count(*)": 1}]
    out = await run(env, rows=[{"field": "sales.COMPANY"}], filters={"company.NAME": "Corp one", "company.NAME__op": "equals"})
    assert out["rows"] == [{"sales.COMPANY": "C1", "count(*)": 3}]
    out = await run(env, filters={"sales.QTY": 4, "sales.QTY__op": "gt"})
    assert out["rows"] == [{"count(*)": 2}]
    out = await run(env, measures=[{"field": "sales.REVENUE"}], period={"field": "sales.SOLD_ON", "from": "2026-01-01", "to": "2026-02-28"})
    assert out["rows"] == [{"sum(sales.REVENUE)": 145}]
    out = await run(env, filters={"company.REGION": None, "company.REGION__op": "empty"})
    assert out["rows"] == [{"count(*)": 0}]
    with pytest.raises(ExplorerError, match="unknown field"):
        await run(env, filters={"sales.NOPE": 1})
    with pytest.raises(ExplorerError, match="operator 'contains' is not valid"):
        await run(env, filters={"sales.QTY": 1, "sales.QTY__op": "contains"})


@pytest.mark.asyncio
async def test_many_side_measure_and_filter_do_not_multiply(env) -> None:
    # lines is many-side and not among the rows: a correlated sub-query per sale, the sale count stays
    out = await run(env, rows=[{"field": "company.NAME"}], measures=[{"field": "lines.AMOUNT"}, {"agg": "count"}, {"field": "lines.AMOUNT", "agg": "avg"}])
    assert out["rows"] == [
        {"company.NAME": "Corp one", "sum(lines.AMOUNT)": 55, "count(*)": 3, "avg(lines.AMOUNT)": 55 / 3},
        {"company.NAME": "Corp three", "sum(lines.AMOUNT)": 200, "count(*)": 1, "avg(lines.AMOUNT)": 200.0},
        {"company.NAME": "Corp two", "sum(lines.AMOUNT)": 80, "count(*)": 2, "avg(lines.AMOUNT)": 40.0},
    ]
    assert "SUM((SELECT SUM(" in out["sql"]
    # an EXISTS filter on lines: sales with a line over 40
    out = await run(env, rows=[{"field": "sales.COMPANY"}], filters={"lines.AMOUNT": 40, "lines.AMOUNT__op": "gt"})
    assert out["rows"] == [{"sales.COMPANY": "C2", "count(*)": 1}, {"sales.COMPANY": "C3", "count(*)": 1}] and "EXISTS" in out["sql"]
    # lines among the rows: joined inline, rows multiply by line (the one allowed many-side source)
    out = await run(env, rows=[{"field": "lines.LINE"}], measures=[{"field": "lines.AMOUNT"}, {"agg": "count"}])
    assert out["rows"] == [{"lines.LINE": 1, "sum(lines.AMOUNT)": 285, "count(*)": 4}, {"lines.LINE": 2, "sum(lines.AMOUNT)": 50, "count(*)": 2}, {"lines.LINE": None, "sum(lines.AMOUNT)": None, "count(*)": 2}]   # nulls last
    # the primary can be the many side, with the hub's measures reached inline
    out = await run(env, entity="lines", rows=[{"field": "sales.COMPANY"}], measures=[{"field": "lines.AMOUNT"}, {"agg": "count"}])
    assert out["rows"] == [{"sales.COMPANY": "C1", "sum(lines.AMOUNT)": 55, "count(*)": 3}, {"sales.COMPANY": "C2", "sum(lines.AMOUNT)": 80, "count(*)": 2}, {"sales.COMPANY": "C3", "sum(lines.AMOUNT)": 200, "count(*)": 1}]
    with pytest.raises(ExplorerError, match="count_distinct on lines.AMOUNT"):
        await run(env, rows=[{"field": "sales.COMPANY"}], measures=[{"field": "lines.AMOUNT", "agg": "count_distinct"}])
    with pytest.raises(ExplorerError, match="not numeric"):
        await run(env, measures=[{"field": "company.NAME", "agg": "sum"}])


@pytest.mark.asyncio
async def test_sort_top_n_and_scope(env) -> None:
    out = await run(env, rows=[{"field": "sales.COMPANY"}], measures=[{"field": "sales.REVENUE"}], sort=[{"key": "sum(sales.REVENUE)", "dir": "desc"}], limit=2)
    assert [r["sales.COMPANY"] for r in out["rows"]] == ["C1", "C2"] and out["truncated"] and out["groups"] == 3
    with pytest.raises(ExplorerError, match="cannot sort"):
        await run(env, rows=[{"field": "sales.COMPANY"}], sort=[{"key": "sales.QTY"}])
    # no rows: the first measure descending is the default sort; no measure at all → count(*)
    out = await run(env)
    assert out["rows"] == [{"count(*)": 6}]
    # the row-level scope applies inside every entity: C1 only sees its own sales and lines
    scope = DataScope.from_claims([[{"column": "COMPANY", "values": ["C1"], "mode": "ignore"}]])
    out = await run(env, scope=scope, rows=[{"field": "company.NAME"}], measures=[{"field": "sales.REVENUE"}, {"field": "lines.AMOUNT"}])
    assert out["rows"] == [{"company.NAME": "Corp one", "sum(sales.REVENUE)": 125, "sum(lines.AMOUNT)": 55}]
    assert "lib_rls" in out["sql"]
