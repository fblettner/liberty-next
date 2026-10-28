"""Route-level tests for the admin write paths that had none (A7.1): find-usages /
find-dependencies / build-package → import-package round trip / clone-with-deps /
delete-with-deps / config move / the API-connector test harness / apply-proposal / the
change-package routes / nomaflow retention / the shared-actions catalog / the
``X-Liberty-Pool`` multi-pool header.

Every route also gets its auth gate pinned (401 anonymous, 403 non-superuser) — these are
the operator-facing write paths with the biggest blast radius, and the engines behind
them were tested while the HTTP layer was not (that's how the import-package upload
stayed broken for months).
"""

from __future__ import annotations

import asyncio
import json
import io
import sqlite3
import textwrap
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import (
    ActionSettings,
    AISettings,
    AppSettings,
    AuthSettings,
    ChartSettings,
    ConnectorSettings,
    CryptoSettings,
    DashboardSettings,
    JobsSettings,
    MenuSettings,
    ScreenSettings,
    Settings,
    ThemeSettings,
)
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "route-tests-secret-long-enough-for-hs256"


def _write(p: Path, body: str) -> None:
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def _seed_auth(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.get_or_create_role("reader", permissions=["sql:foo:*", "api:*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("reader", password="readerpw", roles=["reader"])
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def env(tmp_path: Path):
    """A small but complete config tree on two SQLite pools: connector ``foo`` (default pool,
    may also run on ``alt``), a screen + chart + dashboard + menu + dictionary scope + a
    shared action, nomaflow on the default pool (scheduler off)."""
    default_db = tmp_path / "default.db"
    alt_db = tmp_path / "alt.db"
    with sqlite3.connect(alt_db) as c:  # a marker table that exists ONLY on the alt pool
        c.execute("CREATE TABLE marker (x INTEGER)")
    db_url = f"sqlite+aiosqlite:///{default_db}"
    _seed_auth(db_url)
    _write(tmp_path / "connectors.toml", f"""
        [pools.default]
        url = "{db_url}"
        [pools.alt]
        url = "sqlite+aiosqlite:///{alt_db}"

        [connectors.foo]
        type = "sql"
        pool = "default"
        pools = ["alt"]

        [[connectors.foo.queries]]
        name = "users_get"
        sql = "SELECT 1 AS id, 'ann' AS usr_name"

        [[connectors.foo.queries]]
        name = "users_put"
        sql = "UPDATE sqlite_master SET name = :USR_NAME WHERE 1 = 0"
        writable = true

        [[connectors.foo.queries]]
        name = "markers_get"
        sql = "SELECT count(*) AS n FROM sqlite_master WHERE name = 'marker'"

        [connectors.foo2]
        type = "sql"
        pool = "default"

        [[connectors.foo2.queries]]
        name = "other_get"
        sql = "SELECT 2 AS two"
    """)
    _write(tmp_path / "dictionary.toml", """
        default_language = "en"
        [connectors.foo.entries.USR_NAME]
        label = "Username"
    """)
    _write(tmp_path / "screens.toml", """
        [screens.foo.users]
        connector = "foo"
        read_query = "users_get"
        update_query = "users_put"
        [[screens.foo.users.columns]]
        name = "usr_name"
        dd = "USR_NAME"
    """)
    _write(tmp_path / "menus.toml", """
        [menus.foo]
        label = "Foo"
        [[menus.foo.items]]
        id = "u"
        label = "Users"
        type = "query"
        connector = "foo"
        target = "users_get"
    """)
    _write(tmp_path / "charts.toml", """
        [charts.foo.users_chart]
        label = "Users"
        query = "users_get"
        [charts.foo.users_chart.spec]
        type = "bar"
        x = "usr_name"
        y = ["id"]
    """)
    _write(tmp_path / "dashboards.toml", """
        [dashboards.foo.overview]
        label = "Overview"
        [[dashboards.foo.overview.widgets]]
        type = "chart"
        chart = "users_chart"
    """)
    _write(tmp_path / "actions.toml", """
        [actions.ping]
        label = "Ping"
        [[actions.ping.steps]]
        id = "q"
        type = "run_query"
        connector = "foo"
        query = "users_get"
    """)
    (tmp_path / "jobs.toml").write_text("")
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=tmp_path / "connectors.toml"),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        actions=ActionSettings(config_path=tmp_path / "actions.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        theme=ThemeSettings(config_path=tmp_path / "theme.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
        jobs=JobsSettings(config_path=tmp_path / "jobs.toml", enabled=False),
        crypto=CryptoSettings(master_key="route-tests-master-key"),
    )
    return create_app(settings), tmp_path


def _h(client: TestClient, user: str) -> dict[str, str]:
    tok = client.post("/auth/login", json={"username": user, "password": f"{user}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


# ── auth gates ──────────────────────────────────────────────────────────────────────


GATED_POSTS = [
    ("/admin/find-usages", {"kind": "query", "name": "users_get", "scope": "foo"}),
    ("/admin/find-dependencies", {"seeds": [{"kind": "screen", "name": "users", "scope": "foo"}]}),
    ("/admin/build-package", {"seeds": [{"kind": "screen", "name": "users", "scope": "foo"}]}),
    ("/admin/clone-with-deps", {"kind": "screen", "name": "users", "scope": "foo", "new_name": "x"}),
    ("/admin/delete-with-deps/preview", {"kind": "screen", "name": "users", "scope": "foo"}),
    ("/admin/delete-with-deps", {"kind": "screen", "name": "users", "scope": "foo"}),
    ("/admin/config/move", {"kind": "query", "name": "users_get", "from_connector": "foo", "to_connector": "foo2"}),
    ("/admin/config/api/test", {"config": {"type": "api", "base_url": "http://x", "auth_type": "none"}}),
    ("/admin/config/apply-proposal", {"kind": "dictionary_entries", "payload": {"scope": "foo", "entries": {}}}),
    ("/admin/changesets", {"application": "foo", "name": "p"}),
    ("/admin/changesets/apply", {"bundle": {}}),
    ("/admin/jobs/retention/sweep", None),
]


@pytest.mark.parametrize("path,body", GATED_POSTS, ids=[p for p, _ in GATED_POSTS])
def test_admin_write_routes_are_superuser_only(env, path, body) -> None:
    app, _ = env
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers=_h(client, "reader")).status_code == 403


def test_admin_read_routes_are_superuser_only(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        for path in ("/admin/changesets", "/admin/jobs/retention"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers=_h(client, "reader")).status_code == 403


# ── usages / dependencies / packages ───────────────────────────────────────────────


def test_find_usages_lists_every_reference_to_a_query(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/find-usages", json={"kind": "query", "name": "users_get", "scope": "foo"}, headers=_h(client, "admin"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == len(body["usages"]) >= 3  # screen + menu item + chart (+ action)
        text = str(body["usages"])
        assert "users" in text and "users_chart" in text


def test_find_usages_rejects_unknown_kind(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/find-usages", json={"kind": "nope", "name": "x"}, headers=_h(client, "admin"))
        assert r.status_code == 400


def test_find_dependencies_walks_the_closure(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/find-dependencies", json={"seeds": [{"kind": "dashboard", "name": "overview", "scope": "foo"}]},
                        headers=_h(client, "admin"))
        assert r.status_code == 200, r.text
        text = str(r.json())
        # dashboard → chart → query → connector
        assert "users_chart" in text and "users_get" in text


def test_build_package_then_import_round_trips(env) -> None:
    app, tmp = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/build-package", json={"seeds": [{"kind": "screen", "name": "users", "scope": "foo"}]}, headers=h)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/zip")
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert "screens.toml" in names
        # Delete the screen, then import the package back → the screen is restored.
        d = client.post("/admin/delete-with-deps", json={"kind": "screen", "name": "users", "scope": "foo"}, headers=h)
        assert d.status_code == 200, d.text
        assert "users" not in app.state.screens.screens.get("foo", {})
        imp = client.post("/admin/import-package?strategy=overwrite", headers=h,
                          files={"package": ("p.zip", r.content, "application/zip")})
        assert imp.status_code == 200, imp.text
        report = imp.json()["report"]
        assert report["reloaded"] is True and not any(f["errors"] for f in report["files"])
        assert "users" in app.state.screens.screens["foo"]


def test_package_is_built_from_the_files_and_previews_identical(env) -> None:
    """C11 1a: the package carries what the files say (no model defaults), a connector left out of the
    selection still ships the queries the screen uses, and previewing it here changes nothing."""
    import tomllib
    app, tmp = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        seeds = [{"kind": "screen", "name": "users", "scope": "foo"}]
        deps = client.post("/admin/find-dependencies", json={"seeds": seeds}, headers=h).json()["deps"]
        include = [{"kind": d["kind"], "scope": d["scope"], "name": d["name"]} for d in deps if d["kind"] not in ("connector", "pool")]
        r = client.post("/admin/build-package", json={"seeds": seeds, "include": include}, headers=h)
        assert r.status_code == 200, r.text
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        screens = tomllib.loads(zf.read("screens.toml").decode())
        assert screens == {"screens": {"foo": {"users": tomllib.loads((tmp / "screens.toml").read_text())["screens"]["foo"]["users"]}}}
        conns = tomllib.loads(zf.read("connectors.toml").decode())
        assert set(conns["connectors"]["foo"]) == {"queries"}            # items only, no header
        assert {q["name"] for q in conns["connectors"]["foo"]["queries"]} == {"users_get", "users_put"}
        assert json.loads(zf.read("manifest.json"))["source"]["version"]
        before = (tmp / "connectors.toml").read_text()
        prev = client.post("/admin/import-package?strategy=overwrite&dry_run=true", headers=h,
                           files={"package": ("p.zip", r.content, "application/zip")})
        assert prev.status_code == 200, prev.text
        rep = prev.json()["report"]
        assert rep["dry_run"] is True and rep["reloaded"] is False and not rep["warnings"]
        assert rep["summary"]["identical"] >= 4 and rep["summary"]["new"] == 0 and rep["summary"]["changed"] == 0
        assert (tmp / "connectors.toml").read_text() == before


# ── clone / delete with dependencies ───────────────────────────────────────────────


def test_clone_with_deps_forks_the_screen_and_its_queries(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/clone-with-deps", headers=h, json={
            "kind": "screen", "name": "users", "scope": "foo", "new_name": "users_copy",
            "suffix": "copy", "options": {"clone_queries": True},
        })
        assert r.status_code == 200, r.text
        assert "users_copy" in app.state.screens.screens["foo"]
        cloned = app.state.screens.screens["foo"]["users_copy"]
        # the base name takes the suffix, the CRUD slot stays: users_copy_get / users_copy_put
        assert cloned.read_query == "users_copy_get" and cloned.update_query == "users_copy_put"
        names = {q["name"] for q in client.get("/api/connectors/foo", headers=h).json()["queries"]}
        assert {"users_get", "users_copy_get", "users_copy_put"} <= names


def test_clone_with_deps_rejects_unknown_kind(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/clone-with-deps", headers=_h(client, "admin"),
                        json={"kind": "menu", "name": "x", "new_name": "y"})
        assert r.status_code == 400


def test_delete_with_deps_preserves_queries_still_in_use(env) -> None:
    """Clone ``users`` (sharing nothing), then delete the clone with ``delete_queries``: its own
    cloned queries go, the original ``users_get`` (still used by the original screen, the menu
    and the chart) stays."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.post("/admin/clone-with-deps", headers=h, json={
            "kind": "screen", "name": "users", "scope": "foo", "new_name": "users_copy",
            "suffix": "copy", "options": {"clone_queries": True},
        }).status_code == 200
        preview = client.post("/admin/delete-with-deps/preview", headers=h,
                              json={"kind": "screen", "name": "users_copy", "scope": "foo", "options": {"delete_queries": True}})
        assert preview.status_code == 200, preview.text
        plan = preview.json()
        assert {q["name"] for q in plan["delete_queries"]} == {"users_copy_get", "users_copy_put"}
        assert plan["preserved_queries"] == []
        d = client.post("/admin/delete-with-deps", headers=h,
                        json={"kind": "screen", "name": "users_copy", "scope": "foo", "options": {"delete_queries": True}})
        assert d.status_code == 200, d.text
        assert "users_copy" not in app.state.screens.screens["foo"]
        names = {q["name"] for q in client.get("/api/connectors/foo", headers=h).json()["queries"]}
        assert "users_copy_get" not in names and "users_get" in names


# ── move between connectors ────────────────────────────────────────────────────────


def test_move_query_between_connectors(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/config/move", headers=h,
                        json={"kind": "query", "name": "markers_get", "from_connector": "foo", "to_connector": "foo2"})
        assert r.status_code == 200, r.text
        assert client.post("/admin/reload", headers=h).status_code == 200  # move doesn't reload by itself
        foo = {q["name"] for q in client.get("/api/connectors/foo", headers=h).json()["queries"]}
        foo2 = {q["name"] for q in client.get("/api/connectors/foo2", headers=h).json()["queries"]}
        assert "markers_get" not in foo and "markers_get" in foo2


def test_move_rejects_unknown_target_connector(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/config/move", headers=_h(client, "admin"),
                        json={"kind": "query", "name": "markers_get", "from_connector": "foo", "to_connector": "nope"})
        assert r.status_code == 422


# ── API connector test harness ─────────────────────────────────────────────────────


def test_api_connector_test_harness_with_no_auth_succeeds_without_network(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/config/api/test", headers=_h(client, "admin"),
                        json={"config": {"type": "api", "base_url": "http://127.0.0.1:9", "auth_type": "none"}})
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), dict)


def test_api_connector_test_harness_rejects_an_invalid_config(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/config/api/test", headers=_h(client, "admin"),
                        json={"config": {"type": "api", "auth_type": "no-such-auth"}})
        assert r.status_code in (400, 422)


# ── AI proposal apply ──────────────────────────────────────────────────────────────


def test_apply_proposal_merges_dictionary_entries_and_reloads(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/config/apply-proposal", headers=h, json={
            "kind": "dictionary_entries",
            "payload": {"scope": "foo", "entries": {"USR_EMAIL": {"label": "E-mail", "format": "text"}}},
        })
        assert r.status_code == 200, r.text
        assert r.json()["applied"] is True
        entries = client.get("/admin/config/dictionary/parsed", headers=h).json()["dictionary"]["connectors"]["foo"]["entries"]
        assert entries["USR_EMAIL"]["label"] == "E-mail"


def test_apply_proposal_rejects_unknown_kind_and_bad_payload(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.post("/admin/config/apply-proposal", headers=h, json={"kind": "nope", "payload": {}}).status_code == 400
        r = client.post("/admin/config/apply-proposal", headers=h, json={"kind": "dictionary_entries", "payload": {"scope": "foo"}})
        assert r.status_code == 422


# ── change packages ────────────────────────────────────────────────────────────────


def test_changeset_lifecycle_create_submit_reject_delete(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.get("/admin/changesets", headers=h).status_code == 200
        r = client.post("/admin/changesets", headers=h, json={"application": "foo", "name": "parked work"})
        assert r.status_code == 200, r.text
        pkg = r.json(); pid = pkg["id"]
        assert pkg["entries"] == []
        assert client.get(f"/admin/changesets/{pid}", headers=h).status_code == 200
        # An empty package can't enter the approval flow — an invalid transition is a 409, not a 500.
        sub = client.post(f"/admin/changesets/{pid}/submit", headers=h)
        assert sub.status_code == 409, sub.text
        assert client.post(f"/admin/changesets/{pid}/reject", headers=h).status_code == 409  # not pending either
        assert client.delete(f"/admin/changesets/{pid}", headers=h).status_code == 200
        assert client.get(f"/admin/changesets/{pid}", headers=h).status_code == 404


def test_changeset_create_validates_input(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        assert client.post("/admin/changesets", headers=_h(client, "admin"), json={"application": " ", "name": ""}).status_code == 422


def test_changeset_apply_rejects_a_foreign_bundle(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.post("/admin/changesets/apply", headers=h, json={"bundle": {"format": "something-else"}}).status_code == 400
        assert client.post("/admin/changesets/apply", headers=h, json={"bundle": {"format": "liberty-changeset/1"}}).status_code == 400


# ── nomaflow retention ─────────────────────────────────────────────────────────────


def test_retention_status_and_manual_sweep(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        st = client.get("/admin/jobs/retention", headers=h)
        assert st.status_code == 200, st.text
        assert "policy" in st.json() and st.json()["last_sweep"] is None
        sw = client.post("/admin/jobs/retention/sweep", headers=h)
        assert sw.status_code == 200, sw.text
        assert client.get("/admin/jobs/retention", headers=h).json()["last_sweep"] is not None


# ── shared actions catalog ─────────────────────────────────────────────────────────


def test_shared_actions_catalog(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        assert client.get("/api/actions").status_code == 401
        h = _h(client, "reader")  # any authenticated user
        ids = {a["id"] for a in client.get("/api/actions", headers=h).json()["actions"]}
        assert ids == {"ping"}
        assert client.get("/api/actions/ping", headers=h).json()["steps"][0]["query"] == "users_get"
        assert client.get("/api/actions/nope", headers=h).status_code == 404


# ── X-Liberty-Pool: multi-pool connectors ──────────────────────────────────────────


def test_x_liberty_pool_rebinds_a_multi_pool_connector(env) -> None:
    """``markers_get`` counts a table that exists only on the ``alt`` pool: 0 on the default
    pool, 1 when the header picks ``alt``; an unknown pool name is ignored (default)."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "reader")
        base = client.get("/api/sql/foo/markers_get", headers=h)
        assert base.status_code == 200, base.text
        assert base.json()["rows"][0]["n"] == 0
        alt = client.get("/api/sql/foo/markers_get", headers={**h, "X-Liberty-Pool": "alt"})
        assert alt.status_code == 200, alt.text
        assert alt.json()["rows"][0]["n"] == 1
        nope = client.get("/api/sql/foo/markers_get", headers={**h, "X-Liberty-Pool": "nope"})
        assert nope.status_code == 200 and nope.json()["rows"][0]["n"] == 0
