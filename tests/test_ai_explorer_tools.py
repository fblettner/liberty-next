"""explorer_models / explorer_fields / explorer_query: the Data Explorer in the assistant's
hands — built on the engine test's SQLite model, through a fake ``app.state``."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from liberty.ai.explorer_tools import build_explorer_tools
from liberty.connectors.config import ColumnHint, ConnectorsFile
from liberty.connectors.registry import ConnectorRegistry
from liberty.explorer import parse_explorer
from liberty.explorer.model import ModelCache
from liberty.screens.config import Screen, ScreensFile
from tests.test_explorer_engine import COMPANIES, MODEL, ROWS


@pytest_asyncio.fixture
async def tools(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'x.db'}"
    cfg = ConnectorsFile.model_validate({
        "pools": {"default": {"url": url}},
        "connectors": {"shop": {"type": "sql", "pool": "default", "queries": [
            {"name": "fact_sales_get", "sql": "SELECT id AS ID, company AS COMPANY, sold_on AS SOLD_ON, qty AS QTY, revenue AS REVENUE FROM fact_sales ORDER BY id"},
            {"name": "dim_company_get", "sql": "SELECT company AS COMPANY, name AS NAME, region AS REGION FROM dim_company"},
            {"name": "sales_lines_get", "sql": "SELECT sale_id AS SALE_ID, line AS LINE, amount AS AMOUNT FROM sales_lines"},
            {"name": "dim_region_get", "sql": "SELECT DISTINCT region AS REGION, region || ' zone' AS ZONE FROM dim_company"},
        ]}, "other": {"type": "sql", "pool": "default"}},
    })
    registry = ConnectorRegistry(cfg)
    async with registry.pools.engine("default").begin() as c:
        await c.execute(text("CREATE TABLE fact_sales (id INTEGER PRIMARY KEY, company TEXT, sold_on TEXT, qty INTEGER, revenue NUMERIC)"))
        await c.execute(text("CREATE TABLE dim_company (company TEXT PRIMARY KEY, name TEXT, region TEXT)"))
        await c.execute(text("CREATE TABLE sales_lines (sale_id INTEGER, line INTEGER, amount NUMERIC)"))
        for r in ROWS:
            await c.execute(text("INSERT INTO fact_sales VALUES (:a, :b, :c, :d, :e)"), {"a": r[0], "b": r[1], "c": r[2], "d": r[3], "e": r[4]})
        for r in COMPANIES:
            await c.execute(text("INSERT INTO dim_company VALUES (:a, :b, :c)"), {"a": r[0], "b": r[1], "c": r[2]})
    screens = ScreensFile(screens={"shop": {"sales": Screen(id="sales", label="Sales", read_query="fact_sales_get", columns=[ColumnHint(name="SOLD_ON", format="date"), ColumnHint(name="QTY", label="Quantity")])}})
    explorer = parse_explorer({"explorer": {"shop": {"sales": {**MODEL, "label": "Shop sales", "analyses": {"by_company": {"label": "By company", "definition": {"entity": "sales", "view": "table", "rows": [], "measures": []}}}}}, "other": {"x": {"hub": "x", "entities": {"x": {"query": "fact_sales_get", "connector": "shop"}}}}}})
    st = SimpleNamespace(connectors=registry, screens=screens, explorer=explorer, explorer_cache=ModelCache())
    yield {t.name: t for t in build_explorer_tools(lambda: st)}, {t.name: t for t in build_explorer_tools(lambda: st, allowed=["shop"])}
    await registry.pools.dispose()


@pytest.mark.asyncio
async def test_models_fields_and_query(tools) -> None:
    all_tools, shop_only = tools
    models = all_tools["explorer_models"].func()["models"]
    assert [(m["app"], m["model"]) for m in models] == [("shop", "sales"), ("other", "x")]
    m = models[0]
    assert m["label"] == "Shop sales" and m["hub"] == "sales" and m["analyses"] == [{"id": "by_company", "label": "By company"}]
    assert {e["id"]: e["measures"] for e in m["entities"]}["sales"] == ["QTY", "REVENUE"]
    assert [m["model"] for m in shop_only["explorer_models"].func()["models"]] == ["sales"]
    assert all_tools["explorer_models"].func(app="other")["models"][0]["model"] == "x"

    f = await all_tools["explorer_fields"].func("shop", "sales", entity="sales")
    by = {x["key"]: x for x in f["entities"][0]["fields"]}
    assert f["hub"] == "sales" and by["sales.REVENUE"]["kind"] == "measure" and by["sales.QTY"]["label"] == "Quantity" and by["sales.SOLD_ON"]["type"] == "date"
    assert [e["entity"] for e in (await all_tools["explorer_fields"].func("shop", "sales"))["entities"]] == ["sales", "company", "lines", "region"]

    r = await all_tools["explorer_query"].func(
        "shop", "sales", rows=[{"field": "company.NAME"}], measures=[{"field": "sales.REVENUE", "agg": "sum"}],
        sort=[{"key": "sum(sales.REVENUE)", "dir": "desc"}], limit=2,
    )
    assert [c["key"] for c in r["columns"]] == ["company.NAME", "sum(sales.REVENUE)"] and r["columns"][1]["aggregate"] == "sum"
    assert [(row["company.NAME"], row["sum(sales.REVENUE)"]) for row in r["rows"]] == [("Corp one", 125.0), ("Corp two", 90.0)]
    assert r["groups"] == 3 and r["truncated"] is True
    # a period and a month bucket
    r = await all_tools["explorer_query"].func(
        "shop", "sales", rows=[{"field": "sales.SOLD_ON", "bucket": "month"}], measures=[{"agg": "count"}],
        period={"field": "sales.SOLD_ON", "from": "2026-01-01", "to": "2026-02-28"},
    )
    dim = r["columns"][0]["key"]
    assert {row[dim]: row["count(*)"] for row in r["rows"]} == {"2026-01": 2, "2026-02": 2}
    # an unknown model, a model outside the allowed apps
    with pytest.raises(ValueError, match="no explorer model"):
        await all_tools["explorer_query"].func("shop", "ghost")
    with pytest.raises(ValueError, match="no explorer model"):
        await shop_only["explorer_fields"].func("other", "x")


def test_tools_are_registered_in_the_assistant() -> None:
    from liberty.ai.assistant import build_assistant
    from liberty.config import AISettings
    st = SimpleNamespace(connectors=ConnectorRegistry(ConnectorsFile.model_validate({"pools": {}, "connectors": {}})), explorer=None, explorer_cache=ModelCache())
    a = build_assistant(AISettings(enabled=True, api_key=""), st.connectors, state=lambda: st)
    names = set(a.tools.names())
    assert {"explorer_models", "explorer_fields", "explorer_query"} <= names
    b = build_assistant(AISettings(enabled=True, api_key="", explorer_tools=False), st.connectors, state=lambda: st)
    assert "explorer_query" not in b.tools.names()
