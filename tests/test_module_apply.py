"""Apply a module (catalogue v2): the plan from live columns, then the configuration written —
on SQLite tables shaped like JD Edwards ones (prefixed columns: ABAN8 on F0101, WWAN8 on F0111)."""

from __future__ import annotations

import asyncio
import textwrap
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, MenuSettings, ScreenSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app
from liberty.module_apply import build_plan, resolve_prefix
from liberty.modules import Module, ModuleScreen

MODULE = textwrap.dedent("""
    [[modules]]
    id = "address_book"
    label = "Address Book"
    connector = "jdedwards"
    app = "nomajde"
    menu = { label = "Address Book", icon = "users", l = { fr = "Carnet d'adresses" } }

    [[modules.screens]]
    id = "f0101"
    table = "F0101"
    label = "Address Book"
    key = ["AN8"]
    columns = ["AN8", "ALPH"]
    [[modules.screens.tabs]]
    label = "General"
    fields = ["AN8", "ALPH", "AT1", "NOPE"]
    [[modules.screens.details]]
    screen = "f0111"
    label = "Who's Who"
    l = { fr = "Contacts" }
    on = ["AN8"]

    [[modules.screens]]
    id = "f0111"
    table = "F0111"
    label = "Who's Who"
    key = ["AN8", "IDLN"]
    """)


def _seed(db_url: str) -> None:
    async def go() -> None:
        engine = create_async_engine(db_url)
        async with engine.begin() as c:
            await c.execute(text("CREATE TABLE F0101 (ABAN8 INTEGER PRIMARY KEY, ABALPH TEXT, ABAT1 TEXT)"))
            await c.execute(text("CREATE TABLE F0111 (WWAN8 INTEGER, WWIDLN INTEGER, WWMLNM TEXT, PRIMARY KEY (WWAN8, WWIDLN))"))
            await c.execute(text("INSERT INTO F0101 VALUES (1, 'Acme', 'C'), (2, 'Globex', 'V')"))
            await c.execute(text("INSERT INTO F0111 VALUES (1, 0, 'Acme'), (1, 1, 'Jane'), (2, 0, 'Globex')"))
        await engine.dispose()
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.create_user("admin", password="adminpw", is_superuser=True)
            await svc.create_user("reader", password="readerpw")
        await pools.dispose()
    asyncio.run(go())


@pytest.fixture
def env(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(textwrap.dedent(f"""
        # the JDE connector — this comment survives an apply (tomlkit)
        [pools.default]
        url = "{db_url}"

        [connectors.jdedwards]
        type = "sql"
        pool = "default"
        [[connectors.jdedwards.queries]]
        name = "ping_get"
        sql = "SELECT 1 AS ONE"
        """))
    (tmp_path / "screens.toml").write_text("")
    (tmp_path / "menus.toml").write_text('[menus.nomajde]\nlabel = "JDE"\n[[menus.nomajde.items]]\nid = "jobs"\nlabel = "Jobs"\n')
    (tmp_path / "presets").mkdir()
    (tmp_path / "presets" / "jde.toml").write_text(MODULE)
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=tmp_path / "connectors.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        auth=AuthSettings(backend="db", jwt_secret="module-apply-test-secret", pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings), tmp_path


def _h(client: TestClient, user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {client.post('/auth/login', json={'username': user, 'password': f'{user}pw'}).json()['access_token']}"}


def test_prefix_resolution() -> None:
    s = ModuleScreen(id="f0111", table="F0111", label="W", key=["AN8", "IDLN"])
    assert resolve_prefix(s, ["WWAN8", "WWIDLN", "WWMLNM"]) == "WW"
    assert resolve_prefix(s, ["WWAN8", "WXIDLN"]) is None                 # no prefix has every key column
    assert resolve_prefix(s, ["AN8", "IDLN"]) == ""                        # a table without prefix
    assert resolve_prefix(s.model_copy(update={"prefix": "ZZ"}), ["WWAN8"]) == "ZZ"


def test_plan_reports_missing_and_existing() -> None:
    m = Module.model_validate(tomllib.loads(MODULE)["modules"][0])
    live = {"F0101": ["ABAN8", "ABALPH", "ABAT1"], "F0111": ["WWAN8", "WWIDLN", "WWMLNM"]}
    plan = build_plan(m, live, existing_tables=set(), existing_screens=set(), existing_menu_ids=set())
    assert plan.errors == []
    ab, ww = plan.screens
    assert (ab.prefix, ab.missing, ab.columns) == ("AB", ["NOPE"], 3)
    assert ww.columns == 3                                                 # no columns listed → every column
    screen = plan.screen_defs["f0101"]
    assert screen["read_only"] is True and screen["read_query"] == "f0101_get"
    assert [c["name"] for c in screen["columns"]] == ["ABAN8", "ABALPH", "ABAT1"]
    assert "hidden" not in screen["columns"][2]                           # dialog only: kept for the dialog's field
    assert screen["views"] == [{"name": "Default", "default": True, "columns": ["ABAN8", "ABALPH"]}]   # the grid
    general, who = screen["dialog"]["tabs"]
    assert [f["name"] for f in general["fields"]] == ["ABAN8", "ABALPH", "ABAT1"]
    assert who == {"id": "f0111", "label": "Who's Who", "type": "nested_table", "screen": "f0111", "hide_on_add": True,
                   "param_binds": [{"param": "WWAN8", "source": "ABAN8"}], "l": {"fr": "Contacts"}}
    child = {c["name"]: c for c in plan.screen_defs["f0111"]["columns"]}
    assert child["WWAN8"] == {"name": "WWAN8", "dd": "AN8", "key": True, "filter": True, "hidden": True}
    assert "SELECT\n    ABAN8,\n    ABALPH,\n    ABAT1\nFROM\n  F0101\nORDER BY\n  ABAN8" == plan.tables[0]["get"]["sql"]
    assert [d["id"] for d in plan.menu_defs] == ["address_book", "f0101"]
    # what exists is kept: no table, no screen, no menu entry written again
    again = build_plan(m, live, existing_tables={"f0101", "f0111"}, existing_screens={"f0101", "f0111"},
                       existing_menu_ids={"address_book", "f0101"})
    assert again.tables == [] and again.screen_defs == {} and again.menu_defs == []
    assert [s.status for s in again.screens] == ["exists", "exists"]
    # a table not found / a key column missing block the apply
    bad = build_plan(m, {"F0101": None, "F0111": ["WWAN8"]}, existing_tables=set(), existing_screens=set(), existing_menu_ids=set())
    assert len(bad.errors) == 2 and bad.screen_defs == {}
    # replace: the module's own entries rewritten
    rep = build_plan(m, live, existing_tables={"f0101", "f0111"}, existing_screens={"f0101", "f0111"},
                     existing_menu_ids={"address_book", "f0101"}, replace=True)
    assert [s.status for s in rep.screens] == ["replace", "replace"] and set(rep.screen_defs) == {"f0101", "f0111"}
    assert len(rep.tables) == 2 and [d["id"] for d in rep.menu_defs] == ["address_book", "f0101"]


LOOKUP_MODULE = textwrap.dedent("""
    [[modules]]
    id = "ab"
    label = "AB"
    connector = "jdedwards"
    app = "nomajde"
    menu = { label = "AB" }

    [[modules.screens]]
    id = "f0101"
    table = "F0101"
    label = "Address Book"
    key = ["AN8"]
    columns = ["AN8", "ALPH"]
    [[modules.screens.details]]
    screen = "f0401"
    label = "Supplier"
    cardinality = "one"
    on = ["AN8"]

    [[modules.screens]]
    id = "f0401"
    table = "F0401"
    label = "Supplier Master"
    key = ["AN8"]
    menu = true
    columns = ["AN8", "ALPH", "CRRP"]
    filters = ["ALPH", "ALKY"]
    [[modules.screens.joins]]
    table = "F0101"
    on = ["AN8"]
    columns = ["ALPH", "ALKY"]
    [[modules.screens.row_menu]]
    id = "open_in_jde"
    type = "open_url"
    label = "Open in JDE"
    url = "https://jde.example.com/jde/ShortcutLauncher?OID=P04012&FormDSData=| |{AN8}|#LOGIN_USER#|{STEP.X}"
    """)


def test_one_to_one_detail_and_a_joined_lookup() -> None:
    m = Module.model_validate(tomllib.loads(LOOKUP_MODULE)["modules"][0])
    live = {"F0101": ["ABAN8", "ABALPH", "ABALKY"], "F0401": ["A6AN8", "A6CRRP"]}
    plan = build_plan(m, live, existing_tables=set(), existing_screens=set(), existing_menu_ids=set())
    assert plan.errors == []
    supplier_tab = plan.screen_defs["f0101"]["dialog"]["tabs"][-1]
    assert supplier_tab["type"] == "nested_form" and supplier_tab["form_screen"] == "f0401"
    assert supplier_tab["param_binds"] == [{"param": "A6AN8", "source": "ABAN8"}]
    sup = plan.screen_defs["f0401"]
    # the joined name and reference in the grid (after the listed columns), the link shown on a menu screen
    assert [c["name"] for c in sup["columns"]] == ["A6AN8", "ABALPH", "A6CRRP", "ABALKY"]
    an8 = sup["columns"][0]
    assert an8["filter"] is True and "hidden" not in an8
    # the filter panel's columns, a joined one among them (filtered in the database)
    assert [c["name"] for c in sup["columns"] if c.get("filter")] == ["A6AN8", "ABALPH", "ABALKY"]
    with pytest.raises(ValueError, match="filters"):
        Module.model_validate(tomllib.loads(LOOKUP_MODULE.replace('filters = ["ALPH", "ALKY"]', 'filters = ["NOPE"]'))["modules"][0])
    sql = plan.tables[1]["get"]["sql"]
    assert "SELECT\n    T0.A6AN8,\n    T1.ABALPH,\n    T0.A6CRRP,\n    T1.ABALKY" in sql
    assert "FROM\n  F0401 T0\nLEFT JOIN\n  F0101 T1\n  ON T0.A6AN8 = T1.ABAN8\nORDER BY\n  T0.A6AN8" in sql
    # a one-to-one detail must link on the child's whole key
    bad = LOOKUP_MODULE.replace('key = ["AN8"]\nmenu = true', 'key = ["AN8", "CRRP"]\nmenu = true')
    with pytest.raises(ValueError, match="one-to-one"):
        Module.model_validate(tomllib.loads(bad)["modules"][0])
    # the row menu: an open_url's data items become the physical columns, the rest left as it is
    [act] = sup["row_menu"]
    assert act["url"] == "https://jde.example.com/jde/ShortcutLauncher?OID=P04012&FormDSData=| |{A6AN8}|#LOGIN_USER#|{STEP.X}"
    with pytest.raises(ValueError, match="open_url"):
        Module.model_validate(tomllib.loads(LOOKUP_MODULE.replace('url = "https://jde', 'url = "javascript:x//jde'))["modules"][0])
    # a joined table not found is an error
    assert build_plan(m, {**live, "F0101": None}, existing_tables=set(), existing_screens=set(), existing_menu_ids=set()).errors


def test_apply_module_end_to_end(env) -> None:
    app, tmp_path = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.get("/admin/assistant/modules", headers=_h(client, "reader")).status_code == 403
        [m] = client.get("/admin/assistant/modules", headers=h).json()["modules"]
        assert m["id"] == "address_book" and [s["id"] for s in m["screens"]] == ["f0101", "f0111"]
        assert client.post("/admin/assistant/modules/nope/preview", headers=h).status_code == 404
        preview = client.post("/admin/assistant/modules/address_book/preview", headers=h).json()
        assert preview["errors"] == [] and [s["prefix"] for s in preview["screens"]] == ["AB", "WW"]
        assert preview["new"] == {"queries": ["f0101_get", "f0111_get"], "screens": ["f0101", "f0111"], "menu_items": ["address_book", "f0101"]}
        assert not (tmp_path / "screens.toml").read_text()                 # the preview writes nothing

        r = client.post("/admin/assistant/modules/address_book/apply", headers=h)
        assert r.status_code == 200, r.text
        conn_text = (tmp_path / "connectors.toml").read_text()
        assert "this comment survives" in conn_text and 'name = "f0101"' in conn_text
        screens = tomllib.loads((tmp_path / "screens.toml").read_text())["screens"]["nomajde"]
        assert set(screens) == {"f0101", "f0111"}
        items = tomllib.loads((tmp_path / "menus.toml").read_text())["menus"]["nomajde"]["items"]
        assert [i["id"] for i in items] == ["jobs", "address_book", "f0101"] and items[2]["parent"] == "address_book"
        # live: the master reads, the detail filtered on the parent's key (the nested tab's bind)
        rows = client.get("/api/sql/jdedwards/f0101_get", headers=h).json()["rows"]
        assert [r["ABALPH"] for r in rows] == ["Acme", "Globex"]
        detail = client.get("/api/sql/jdedwards/f0111_get", params={"WWAN8": 1}, headers=h).json()["rows"]
        assert [r["WWMLNM"] for r in detail] == ["Acme", "Jane"]
        assert client.get("/api/screens", headers=h).status_code == 200

        # applying again creates nothing and changes no file
        before = {p: (tmp_path / p).read_text() for p in ("connectors.toml", "screens.toml", "menus.toml")}
        again = client.post("/admin/assistant/modules/address_book/apply", headers=h).json()
        assert again["new"] == {"queries": [], "screens": [], "menu_items": []}
        assert before == {p: (tmp_path / p).read_text() for p in before}


def test_a_module_with_an_unknown_table_writes_nothing(env) -> None:
    app, tmp_path = env
    (tmp_path / "presets" / "jde.toml").write_text(MODULE.replace('table = "F0111"', 'table = "F9999"'))
    with TestClient(app) as client:
        r = client.post("/admin/assistant/modules/address_book/apply", headers=_h(client, "admin"))
        assert r.status_code == 422 and "F9999" in str(r.json())
        assert not (tmp_path / "screens.toml").read_text() and "f0101" not in (tmp_path / "connectors.toml").read_text()
