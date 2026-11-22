"""The predefined roles the applications ship (roles.toml): the file, the route that lists them, their
place in an application install, and the rule they rest on — a person's roles add up."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth import implied
from liberty.auth.db import AuthDatabase
from liberty.auth.implied import MenuAccess
from liberty.auth.principal import Principal
from liberty.auth.role_templates import load_role_templates, parse_role_templates
from liberty.auth.service import AuthService
from liberty.config import AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.config_merge import flatten, merge_file
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

_ROLES = """
[roles.shop_sales]
app = "shop"
label = "Sales manager"
description = "Orders and customers: read and change."
l = { fr = { label = "Responsable des ventes", description = "Commandes et clients : lecture et modification." } }
permissions = ["menu:shop:*", "edit:shop:sales", "ai:chat"]

[roles.shop_stock]
app = "shop"
label = "Stock keeper"
permissions = ["menu:shop:*", "edit:shop:warehouse"]
"""


def test_the_file_is_read_with_its_texts_per_language(tmp_path) -> None:
    (tmp_path / "roles.toml").write_text(_ROLES)
    cat = load_role_templates(tmp_path / "roles.toml")
    assert list(cat.roles) == ["shop_sales", "shop_stock"]
    sales = cat.roles["shop_sales"]
    assert sales.text("label", "fr") == "Responsable des ventes" and sales.text("label", "fr-CA") == "Responsable des ventes"
    assert sales.text("label", "de") == "Sales manager" and sales.text("label", None) == "Sales manager"
    assert cat.roles["shop_stock"].text("description", "fr") is None
    assert sales.permissions == ["menu:shop:*", "edit:shop:sales", "ai:chat"]


def test_no_file_is_an_empty_catalogue(tmp_path) -> None:
    assert load_role_templates(tmp_path / "roles.toml").roles == {}


def test_a_predefined_role_only_gives() -> None:
    with pytest.raises(ValueError, match="only gives"):
        parse_role_templates({"roles": {"x": {"label": "X", "permissions": ["menu:shop:*", "!menu:shop:stock"]}}})


def test_two_roles_held_by_one_person_add_up() -> None:
    """A security officer who is also the licence manager: each role changes its own folder and
    reads the rest; together they change both."""
    access = MenuAccess()
    access.add("shop", "orders", ("orders", "sales"), ["sql:shop:orders_get"], ["sql:shop:orders_put"])
    access.add("shop", "stock", ("stock", "warehouse"), ["sql:shop:stock_get"], ["sql:shop:stock_put"])
    access.add("shop", "prices", ("prices", "settings"), ["sql:shop:prices_get"], ["sql:shop:prices_put"])
    cat = parse_role_templates({"roles": {
        "sales": {"label": "Sales", "permissions": ["menu:shop:*", "edit:shop:sales"]},
        "stock": {"label": "Stock", "permissions": ["menu:shop:*", "edit:shop:warehouse"]},
    }})
    implied.set_source(lambda: access)
    try:
        def held(*roles: str) -> Principal:
            return Principal(id="1", username="u", permissions=tuple(sorted({p for r in roles for p in cat.roles[r].permissions})))

        one = held("sales")
        assert one.has_permission("sql:shop:orders_put") and one.has_permission("sql:shop:stock_get") and not one.has_permission("sql:shop:stock_put")
        both = held("sales", "stock")
        assert both.has_permission("sql:shop:orders_put") and both.has_permission("sql:shop:stock_put")
        assert both.has_permission("sql:shop:prices_get") and not both.has_permission("sql:shop:prices_put")
    finally:
        implied.set_source(None)


def test_an_install_adds_and_refreshes_the_shipped_roles_and_keeps_the_customers_own() -> None:
    shipped_v1 = {"roles": {"a": {"label": "A", "permissions": ["menu:shop:*"]}}}
    shipped_v2 = {"roles": {"a": {"label": "A", "permissions": ["menu:shop:*", "ai:chat"]}, "b": {"label": "B", "permissions": []}}}
    live = {"roles": {"a": {"label": "A", "permissions": ["menu:shop:*"]}, "mine": {"label": "Mine", "permissions": ["ai:chat"]}}}
    merged, _report = merge_file("roles.toml", shipped_v2, live, shipped_v1)
    assert merged["roles"]["a"]["permissions"] == ["menu:shop:*", "ai:chat"]      # untouched since shipped: refreshed
    assert "b" in merged["roles"] and merged["roles"]["mine"]["label"] == "Mine"
    assert sorted(flatten(merged, "roles.toml")) == [("role_template", "a"), ("role_template", "b"), ("role_template", "mine")]


# ── the route ─────────────────────────────────────────────────────────────────────────────

def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.get_or_create_role("shop_stock", permissions=["menu:shop:warehouse"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("alice", password="alicepw")
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    (tmp_path / "connectors.toml").write_text(f'[pools.default]\nurl = "{db_url}"\n')
    (tmp_path / "roles.toml").write_text(_ROLES)
    _seed(db_url)
    return create_app(Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        auth=AuthSettings(backend="db", jwt_secret="role-templates-test-secret", pool="default", roles_path=Path(tmp_path / "roles.toml")),
    ))


def _h(client: TestClient, username: str, **extra: str) -> dict[str, str]:
    tok = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}", **extra}


def test_the_route_lists_them_in_the_callers_language_and_says_which_exist(app) -> None:
    with TestClient(app) as client:
        assert client.get("/admin/access/role-templates", headers=_h(client, "alice")).status_code == 403
        got = client.get("/admin/access/role-templates", headers=_h(client, "admin", **{"X-Liberty-Lang": "fr"})).json()["templates"]
        assert [(t["id"], t["label"], t["exists"]) for t in got] == [
            ("shop_sales", "Responsable des ventes", False), ("shop_stock", "Stock keeper", True)]
        assert got[0]["permissions"] == ["menu:shop:*", "edit:shop:sales", "ai:chat"] and got[0]["app"] == "shop"
        # picking one is the ordinary upsert: the role is then the customer's
        h = _h(client, "admin")
        saved = client.put("/admin/access/roles/shop_sales", json={"permissions": got[0]["permissions"], "description": got[0]["description"]}, headers=h)
        assert saved.status_code == 200
        again = client.get("/admin/access/role-templates", headers=h).json()["templates"]
        assert [t["exists"] for t in again] == [True, True]


def test_a_catalogue_that_cannot_be_read_is_said(app, tmp_path) -> None:
    (tmp_path / "roles.toml").write_text('[roles.bad]\nlabel = "Bad"\npermissions = ["!menu:shop:x"]\n')
    with TestClient(app) as client:
        r = client.get("/admin/access/role-templates", headers=_h(client, "admin"))
        assert r.status_code == 422 and "only gives" in r.json()["detail"]
