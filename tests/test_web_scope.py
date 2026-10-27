"""End to end: a TOML role with a data scope, a user with attributes, the token carries the
resolved scope, GET / POST / stream /api/sql are filtered, writes outside the scope are 403,
superusers are unrestricted."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.auth.authstore import AuthFile, AuthRole, AuthUser, save_auth
from liberty.auth.password import hash_password
from liberty.auth.scope import ScopeRule
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, ScreenSettings, Settings
from liberty.main import create_app

JWT = "scope-tests-secret-long-enough-for-hs256"


@pytest.fixture
def app(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'data.db'}"

    async def seed() -> None:
        eng = create_async_engine(url)
        async with eng.begin() as c:
            await c.execute(text("CREATE TABLE sales (id INTEGER PRIMARY KEY, company TEXT, amount INTEGER)"))
            for i, co in enumerate(["A", "A", "B", "C"], 1):
                await c.execute(text("INSERT INTO sales VALUES (:i, :c, :a)"), {"i": i, "c": co, "a": i * 10})
        await eng.dispose()
    asyncio.run(seed())
    (tmp_path / "connectors.toml").write_text(f'''
[pools.default]
url = "{url}"
[connectors.shop]
type = "sql"
pool = "default"
[[connectors.shop.queries]]
name = "sales_get"
sql = "SELECT id, company, amount FROM sales ORDER BY id"
[[connectors.shop.queries]]
name = "sales_put"
sql = "UPDATE sales SET amount = :AMOUNT WHERE id = :ID_ORIGINAL"
writable = true
''')
    (tmp_path / "screens.toml").write_text('''
[screens.shop.sales]
connector = "shop"
read_query = "sales_get"
update_query = "sales_put"
[[screens.shop.sales.columns]]
name = "company"
dd = "COMPANY_ID"
''')
    auth = AuthFile(
        roles={
            "admin": AuthRole(permissions=["*"]),
            "sales": AuthRole(permissions=["sql:shop:*"], scope=[ScopeRule(dd="COMPANY_ID", source="#USER.company#", mode="deny")]),
        },
        users={
            "root": AuthUser(password_hash=hash_password("rootpw"), roles=["admin"], superuser=True),
            "ann": AuthUser(password_hash=hash_password("annpw"), roles=["sales"], attributes={"company": ["A"]}),
            "bob": AuthUser(password_hash=hash_password("bobpw"), roles=["sales"], attributes={"company": ["B", "C"]}),
        },
    )
    save_auth(tmp_path / "auth.toml", auth)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=tmp_path / "connectors.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        auth=AuthSettings(backend="toml", toml_path=tmp_path / "auth.toml", jwt_secret=JWT),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def _login(client: TestClient, user: str, pw: str) -> dict[str, str]:
    r = client.post("/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_scoped_users_see_their_rows_only(app) -> None:
    with TestClient(app) as c:
        ann, bob, root = _login(c, "ann", "annpw"), _login(c, "bob", "bobpw"), _login(c, "root", "rootpw")
        ids = lambda h: [r["id"] for r in c.get("/api/sql/shop/sales_get", headers=h).json()["rows"]]  # noqa: E731
        assert ids(ann) == [1, 2] and ids(bob) == [3, 4] and ids(root) == [1, 2, 3, 4]
        # the token carries the resolved scope and the attributes; /auth/me shows them
        me = c.get("/auth/me", headers=ann).json()
        assert me["attributes"] == {"company": ["A"]}
        assert me["scope"] == [[{"column": None, "dd": "COMPANY_ID", "values": ["A"], "mode": "deny"}]]
        # POST (same route, body params) and the NDJSON stream are filtered the same way
        assert [r["id"] for r in c.post("/api/sql/shop/sales_get", json={}, headers=ann).json()["rows"]] == [1, 2]
        lines = [json.loads(ln) for ln in c.get("/api/sql/shop/sales_get?_stream=1", headers=bob).text.splitlines() if ln]
        streamed = [r["id"] for ev in lines if ev["kind"] == "rows" for r in ev["rows"]]
        assert streamed == [3, 4]


def test_write_outside_the_scope_is_403(app) -> None:
    with TestClient(app) as c:
        ann = _login(c, "ann", "annpw")
        ok = c.post("/api/sql/shop/sales_put", json={"ID": 1, "ID_ORIGINAL": 1, "AMOUNT": 99, "COMPANY": "A", "COMPANY_ORIGINAL": "A"}, headers=ann)
        assert ok.status_code == 200, ok.text
        no = c.post("/api/sql/shop/sales_put", json={"ID": 3, "ID_ORIGINAL": 3, "AMOUNT": 99, "COMPANY": "B", "COMPANY_ORIGINAL": "B"}, headers=ann)
        assert no.status_code == 403 and "data scope" in no.json()["detail"]
        # deny mode: a write that doesn't even carry the scoped column is refused
        no = c.post("/api/sql/shop/sales_put", json={"ID": 1, "ID_ORIGINAL": 1, "AMOUNT": 5}, headers=ann)
        assert no.status_code == 403
