"""``/api/explorer`` + the admin round trip — a SQLite model with a fact and a dimension."""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, ExplorerSettings, ScreenSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-explorer-test-secret"


def _connectors_toml(db_url: str) -> str:
    return textwrap.dedent(f"""
        [pools.default]
        url = "{db_url}"

        [connectors.shop]
        type = "sql"
        pool = "default"
        [[connectors.shop.queries]]
        name = "fact_sales_get"
        sql = "SELECT id AS ID, company AS COMPANY, sale_date AS SALE_DATE, qty AS QTY, revenue AS REVENUE FROM fact_sales"
        [[connectors.shop.queries]]
        name = "dim_company_get"
        sql = "SELECT company AS COMPANY, name AS NAME, region AS REGION FROM dim_company"
        [[connectors.shop.queries]]
        name = "broken_get"
        sql = "SELECT nope FROM missing_table"
        """)


EXPLORER_TOML = textwrap.dedent("""
    [explorer.shop]
    label = "Shop"
    hub = "sales"

    [explorer.shop.entities.sales]
    query = "fact_sales_get"
    screen = "sales"
    key = ["ID"]
    default_date = "SALE_DATE"
    measures = { QTY = "sum", REVENUE = "sum", COMPANY = "sum" }
    hidden = ["ID"]

    [explorer.shop.entities.company]
    label = "Company"
    query = "dim_company_get"
    key = ["COMPANY"]

    [[explorer.shop.relations]]
    from = "sales"
    to = "company"
    on = [["COMPANY", "COMPANY"]]

    [explorer.other]
    hub = "x"
    [explorer.other.entities.x]
    connector = "shop"
    query = "broken_get"
    """)

SCREENS_TOML = textwrap.dedent("""
    [screens.shop.sales]
    label = "Sales"
    read_query = "fact_sales_get"
    [[screens.shop.sales.columns]]
    name = "QTY"
    label = "Quantity"
    [[screens.shop.sales.columns]]
    name = "SALE_DATE"
    format = "date"
    """)


def _seed(db_url: str) -> None:
    async def go() -> None:
        engine = create_async_engine(db_url)
        async with engine.begin() as c:
            await c.execute(text("CREATE TABLE fact_sales (id INTEGER PRIMARY KEY, company TEXT, sale_date DATE, qty INTEGER, revenue NUMERIC)"))
            await c.execute(text("CREATE TABLE dim_company (company TEXT PRIMARY KEY, name TEXT, region TEXT)"))
            await c.execute(text("INSERT INTO fact_sales VALUES (1, 'C1', '2026-01-05', 3, 30.5)"))
            await c.execute(text("INSERT INTO dim_company VALUES ('C1', 'Corp one', 'EU')"))
        await engine.dispose()
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.get_or_create_role("analyst", permissions=["explorer:shop", "sql:shop:*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("analyst", password="analystpw", roles=["analyst"])
            await svc.create_user("nobody", password="nobodypw")
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def env(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_connectors_toml(db_url))
    (tmp_path / "explorer.toml").write_text(EXPLORER_TOML)
    (tmp_path / "screens.toml").write_text(SCREENS_TOML)
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        explorer=ExplorerSettings(config_path=tmp_path / "explorer.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings), tmp_path


def _h(client: TestClient, username: str) -> dict[str, str]:
    tok = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_list_models_by_permission(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        assert client.get("/api/explorer").status_code == 401
        admin = client.get("/api/explorer", headers=_h(client, "admin")).json()["models"]
        assert [m["app"] for m in admin] == ["shop", "other"] and admin[0]["label"] == "Shop" and admin[0]["entities"] == 2
        assert [m["app"] for m in client.get("/api/explorer", headers=_h(client, "analyst")).json()["models"]] == ["shop"]
        assert client.get("/api/explorer", headers=_h(client, "nobody")).json()["models"] == []


def test_model_fields_discovered_with_hints(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.get("/api/explorer/shop/model", headers=_h(client, "analyst"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["hub"] == "sales" and body["relations"] == [{"from": "sales", "to": "company", "on": [["COMPANY", "COMPANY"]]}]
        sales, company = body["entities"]
        assert sales["id"] == "sales" and sales["cardinality"] == "one" and sales["default_date"] == "SALE_DATE"
        by = {f["name"]: f for f in sales["fields"]}
        assert "ID" not in by                                     # hidden
        assert by["QTY"] == {"key": "sales.QTY", "entity": "sales", "name": "QTY", "label": "Quantity", "type": "number", "kind": "measure", "aggregate": "sum"}
        assert by["SALE_DATE"]["type"] == "date" and by["SALE_DATE"]["format"] == "date"
        assert by["COMPANY"]["kind"] == "dimension"               # declared a measure, but text
        assert by["REVENUE"]["kind"] == "measure" and by["REVENUE"]["type"] == "number"
        assert company["label"] == "Company" and [f["key"] for f in company["fields"]] == ["company.COMPANY", "company.NAME", "company.REGION"]
        assert any("COMPANY" in w and "typed string" in w for w in body["warnings"])
        # permission: nobody → 403; unknown app (with permission) → 404
        assert client.get("/api/explorer/shop/model", headers=_h(client, "nobody")).status_code == 403
        assert client.get("/api/explorer/ghost/model", headers=_h(client, "admin")).status_code == 404


def test_model_with_a_broken_entity_reports_it(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        body = client.get("/api/explorer/other/model", headers=_h(client, "admin")).json()
        assert body["entities"][0]["fields"] == [] and "broken_get" in body["entities"][0]["error"]
        assert body["warnings"] and "broken_get" in body["warnings"][0]


def test_model_cache_and_refresh(env) -> None:
    app, tmp_path = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        first = client.get("/api/explorer/shop/model", headers=h).json()
        assert app.state.explorer_cache._views  # cached
        # editing the file + reload empties the cache and the new label is seen
        (tmp_path / "explorer.toml").write_text(EXPLORER_TOML.replace('label = "Shop"', 'label = "Shop v2"'))
        client.post("/admin/reload", headers=h)
        second = client.get("/api/explorer/shop/model", headers=h).json()
        assert first["label"] == "Shop" and second["label"] == "Shop v2"
        assert client.get("/api/explorer/shop/model?refresh=1", headers=h).json()["label"] == "Shop v2"


def test_admin_parsed_round_trip(env) -> None:
    app, tmp_path = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = client.get("/admin/config/explorer/parsed", headers=h).json()
        shop = body["explorer"]["shop"]
        assert shop["hub"] == "sales" and shop["entities"]["sales"]["measures"]["QTY"] == "sum"
        assert shop["relations"][0]["from"] == "sales" and "id" not in shop["entities"]["sales"]
        shop["entities"]["company"]["label"] = "Companies"
        shop["entities"]["region"] = {"query": "dim_company_get", "key": ["REGION"]}
        shop["relations"].append({"from": "company", "to": "region", "on": [["REGION", "REGION"]]})
        r = client.put("/admin/config/explorer/parsed", json={"explorer": body["explorer"]}, headers=h)
        assert r.status_code == 200, r.text
        text_ = (tmp_path / "explorer.toml").read_text()
        assert 'label = "Companies"' in text_ and 'from = "company"' in text_
        client.post("/admin/reload", headers=h)
        back = client.get("/admin/config/explorer/parsed", headers=h).json()["explorer"]["shop"]
        assert back["entities"]["company"]["label"] == "Companies" and len(back["relations"]) == 2
        # an invalid model is refused and the file untouched
        bad = {"explorer": {"shop": {**shop, "hub": "ghost"}}}
        assert client.put("/admin/config/explorer/parsed", json=bad, headers=h).status_code == 422
        assert client.get("/admin/config/explorer/parsed", headers=_h(client, "analyst")).status_code == 403
        assert "explorer" in client.get("/admin/config/schema", headers=h).json()
        assert client.get("/info", headers=h).json()["explorer"]["apps"] == ["shop", "other"]
