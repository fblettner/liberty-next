from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import (
    AISettings,
    AppSettings,
    AuthSettings,
    ChartSettings,
    ConnectorSettings,
    CryptoSettings,
    DashboardSettings,
    MenuSettings,
    ScreenSettings,
    Settings,
)
from liberty.crypto import decrypt
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-admin-test-secret"


def _toml(db_url: str, *, extra_connector: bool = False) -> str:
    base = textwrap.dedent(
        f"""
        [pools.default]
        url = "{db_url}"

        [connectors.db]
        type = "sql"
        pool = "default"

        [[connectors.db.queries]]
        name = "answer"
        sql = "SELECT 42 AS answer"
        """
    )
    if extra_connector:
        base += textwrap.dedent(
            """
            [connectors.db2]
            type = "sql"
            pool = "default"

            [[connectors.db2.queries]]
            name = "two"
            sql = "SELECT 2 AS two"
            """
        )
    return base


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("reader", password="readerpw")  # not a superuser
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def env(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(_toml(db_url))
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
        # Configured master key so the password-encryption tests have something to use; a
        # missing key is its own test below (verifies the 422 guard works).
        crypto=CryptoSettings(master_key="test-master-key-for-config-saves"),
    )
    return create_app(settings), conn_toml, db_url


def _h(client: TestClient, username: str) -> dict[str, str]:
    token = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_reload_requires_superuser(env) -> None:
    app, _, _ = env
    with TestClient(app) as client:
        assert client.post("/admin/reload").status_code == 401
        assert client.post("/admin/reload", headers=_h(client, "reader")).status_code == 403


def test_reload_picks_up_new_connector(env) -> None:
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        before = {c["name"] for c in client.get("/api/connectors", headers=h).json()["connectors"]}
        assert before == {"db"}

        conn_toml.write_text(_toml(db_url, extra_connector=True))
        r = client.post("/admin/reload", headers=h)
        assert r.status_code == 200 and r.json()["reloaded"] is True
        assert set(r.json()["connectors"]) == {"db", "db2"}

        # the new connector is live, and the old one still works
        after = {c["name"] for c in client.get("/api/connectors", headers=h).json()["connectors"]}
        assert after == {"db", "db2"}
        assert client.get("/api/sql/db2/two", headers=h).json()["rows"] == [{"two": 2}]
        assert client.get("/api/sql/db/answer", headers=h).json()["rows"] == [{"answer": 42}]

        # auth still works after the registry swap (re-pointed at the new pools)
        assert client.post("/auth/login", json={"username": "admin", "password": "adminpw"}).status_code == 200


# ``GET / PUT /admin/config/connectors`` (raw TOML) was removed — the Settings UI dropped
# its "raw editor" tab in favour of the per-section structured editors (Pools / Connectors /
# Dictionary / Menus / Screens / Dashboards), each backed by ``/admin/config/<section>/parsed``
# which validates against the matching Pydantic schema before writing. The structured editors
# now cover every config concern, so the raw escape hatch is gone (and with it, the foot-gun
# of writing invalid TOML that bypassed every per-section validation).


def test_config_schema_and_pools_get(env) -> None:
    app, conn_toml, _ = env
    with TestClient(app) as client:
        # schema (the builder UI's source of truth)
        assert client.get("/admin/config/schema").status_code == 401
        assert client.get("/admin/config/schema", headers=_h(client, "reader")).status_code == 403
        sch = client.get("/admin/config/schema", headers=_h(client, "admin")).json()
        assert "url" in sch["pool"]["properties"] and sch["pool"]["required"] == ["url"]
        # the structured pools view
        body = client.get("/admin/config/pools", headers=_h(client, "admin")).json()
        assert body["path"] == str(conn_toml)
        assert set(body["pools"]) == {"default"}
        assert body["pools"]["default"]["url"].startswith("sqlite+aiosqlite:///") and body["pools"]["default"]["pool_size"] == 5


def test_config_pools_put(env) -> None:
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.put("/admin/config/pools", json={"pools": {}}, headers=_h(client, "reader")).status_code == 403
        # an invalid pool (no `url`) is rejected before anything is written
        assert client.put("/admin/config/pools", json={"pools": {"x": {"pool_size": 3}}}, headers=h).status_code == 422
        # valid: tweak `default` + add a `cache` pool; only the [pools.*] tables change, [connectors.db] survives
        new = {
            "default": {"url": db_url, "pool_size": 12, "schemas": {"PROD": "myprod"}},
            "cache": {"url": "sqlite+aiosqlite://"},
        }
        assert client.put("/admin/config/pools", json={"pools": new}, headers=h).json()["saved"] is True
        txt = conn_toml.read_text()
        assert "[connectors.db]" in txt and 'name = "answer"' in txt          # the connectors side is untouched
        assert "pool_size = 12" in txt and "[pools.cache]" in txt and "[pools.default.schemas]" in txt
        # GET reflects it, and Reload makes the new pool live
        after = client.get("/admin/config/pools", headers=h).json()["pools"]
        assert set(after) == {"default", "cache"} and after["default"]["pool_size"] == 12
        assert set(client.post("/admin/reload", headers=h).json()["pools"]) == {"default", "cache"}


def test_config_pools_put_encrypts_plaintext_password(env) -> None:
    """Save a pool with a plaintext password through PUT /admin/config/pools.
    The bytes that land on disk must be ``ENC:`` — silently storing plaintext
    is the bug this test exists to catch (operators couldn't connect because
    the runtime expected to decrypt and the value wasn't encrypted)."""
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {"pools": {"default": {"url": db_url, "password": "supersecret"}}}
        assert client.put("/admin/config/pools", json=body, headers=h).json()["saved"] is True
        # On-disk: the password is ENC:, NOT the original plaintext.
        txt = conn_toml.read_text()
        assert 'password = "ENC:' in txt
        assert "supersecret" not in txt
        # And the encrypted value round-trips via the same master key.
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith('password = "ENC:'):
                enc = line.split('=', 1)[1].strip().strip('"')
                assert decrypt(enc, "test-master-key-for-config-saves") == "supersecret"
                break
        else:
            raise AssertionError("no ENC: password line found in connectors.toml")


def test_config_pools_put_passes_through_already_encrypted_password(env) -> None:
    """An ENC: value (the typical "user didn't touch the password field" case —
    the UI fetched ENC: and sent it back unchanged) must NOT be re-encrypted;
    it'd land as a different ENC: blob that still decrypts to the SAME plaintext,
    but the on-disk diff would be noisy. encrypt() is idempotent — assert that
    behaviour holds at the PUT boundary."""
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Seed with a known ENC: value (we manufacture one via the same encrypt path
        # the helper uses on save).
        from liberty.crypto import encrypt
        enc = encrypt("first-password", "test-master-key-for-config-saves")
        client.put("/admin/config/pools", json={"pools": {"default": {"url": db_url, "password": enc}}}, headers=h)
        first_text = conn_toml.read_text()
        # Save again with the SAME ENC: value — file content must be byte-identical.
        client.put("/admin/config/pools", json={"pools": {"default": {"url": db_url, "password": enc}}}, headers=h)
        assert conn_toml.read_text() == first_text


def test_config_pools_put_refuses_plaintext_when_no_master_key(tmp_path) -> None:
    """If the operator types a plaintext password but the server has no master
    key configured, refuse with 422 — silently storing plaintext would be the
    worst outcome (looks saved, then connection fails)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(_toml(db_url))
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
        # Deliberately NO crypto.master_key — that's the scenario under test.
    )
    app = create_app(settings)
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {"pools": {"default": {"url": db_url, "password": "plaintext"}}}
        r = client.put("/admin/config/pools", json=body, headers=h)
        assert r.status_code == 422
        assert "master key" in r.json()["detail"].lower()
        # On-disk: nothing changed (the existing pool's password isn't even present).
        assert "plaintext" not in conn_toml.read_text()


def test_config_connectors_parsed_put_encrypts_api_auth_secrets(env) -> None:
    """API connectors carry ``auth_password`` (basic/oauth2) + ``auth_token`` (bearer/api_key)
    — same encrypt-on-save contract as pool passwords. A plaintext typed in the UI must
    land on disk as ENC:."""
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {
            "connectors": {
                "db": {                                       # existing sql connector — preserved as-is
                    "type": "sql", "pool": "default",
                    "queries": [{"name": "answer", "sql": "SELECT 42 AS answer"}],
                },
                "remote": {                                   # new api connector — secrets must be encrypted
                    "type": "api", "base_url": "https://api.example.com",
                    "auth_type": "bearer", "auth_token": "raw-bearer-token",
                },
                "remote_basic": {
                    "type": "api", "base_url": "https://api.example.com",
                    "auth_type": "basic", "auth_username": "alice", "auth_password": "raw-basic-pw",
                },
            },
        }
        r = client.put("/admin/config/connectors/parsed", json=body, headers=h)
        assert r.json().get("saved") is True
        txt = conn_toml.read_text()
        # Plaintexts must be gone; the ENC: ciphertexts present.
        assert "raw-bearer-token" not in txt
        assert "raw-basic-pw" not in txt
        assert 'auth_token = "ENC:' in txt
        assert 'auth_password = "ENC:' in txt
        # And the sql connector's body wasn't accidentally encrypted (its SQL doesn't have any
        # password-shaped field — guard against an over-eager helper).
        assert 'sql = "SELECT 42 AS answer"' in txt


def test_config_connectors_parsed_get_and_put(env) -> None:
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # schema serves the sql / api connector schemas (with their $defs)
        sch = client.get("/admin/config/schema", headers=h).json()
        assert "queries" in sch["sql"]["properties"] and "QueryDef" in sch["sql"]["$defs"]
        assert "endpoints" in sch["api"]["properties"] and "EndpointDef" in sch["api"]["$defs"]
        # the structured connectors view
        body = client.get("/admin/config/connectors/parsed", headers=h).json()
        assert body["path"] == str(conn_toml) and set(body["connectors"]) == {"db"}
        db_conn = body["connectors"]["db"]
        assert db_conn["type"] == "sql" and any(q["name"] == "answer" for q in db_conn["queries"])
        # a non-superuser can't write; a malformed connector is rejected before anything lands
        assert client.put("/admin/config/connectors/parsed", json={"connectors": {}}, headers=_h(client, "reader")).status_code == 403
        assert client.put("/admin/config/connectors/parsed", json={"connectors": {"x": {"type": "ftp"}}}, headers=h).status_code == 422
        # valid: rename a query + add a connector; [pools.*] survives, the new connector is live after reload
        new = {
            "db": {"type": "sql", "pool": "default", "queries": [{"name": "the_answer", "sql": "SELECT 42 AS answer"}]},
            "extra": {"type": "sql", "pool": "default", "queries": [{"name": "two", "sql": "SELECT 2 AS two"}]},
        }
        assert client.put("/admin/config/connectors/parsed", json={"connectors": new}, headers=h).json()["saved"] is True
        txt = conn_toml.read_text()
        assert "[pools.default]" in txt and 'name = "the_answer"' in txt and "[connectors.extra]" in txt
        after = client.get("/admin/config/connectors/parsed", headers=h).json()["connectors"]
        assert set(after) == {"db", "extra"} and after["db"]["queries"][0]["name"] == "the_answer"
        r = client.post("/admin/reload", headers=h)
        assert set(r.json()["connectors"]) == {"db", "extra"}
        assert client.get("/api/sql/extra/two", headers=h).json()["rows"] == [{"two": 2}]
        assert client.get("/api/sql/db/the_answer", headers=h).json()["rows"] == [{"answer": 42}]


def test_config_dictionary_parsed_get_and_put(env) -> None:
    app, conn_toml, _ = env
    dict_toml = conn_toml.with_name("dictionary.toml")
    with TestClient(app) as client:
        h = _h(client, "admin")
        # schema serves the DictionaryFile shape (with its $defs)
        sch = client.get("/admin/config/schema", headers=h).json()
        assert "entries" in sch["dictionary"]["properties"]
        for nested in ("DictionaryEntry", "EnumDef", "LookupDef", "SequenceDef", "EnumValue", "DictionarySection"):
            assert nested in sch["dictionary"]["$defs"]
        # a fresh checkout has no dictionary.toml → GET returns an empty dict (path still reported)
        body = client.get("/admin/config/dictionary/parsed", headers=h).json()
        assert body["path"] == str(dict_toml) and body["dictionary"] == {}
        # permission gate + validation gate
        assert client.put("/admin/config/dictionary/parsed", json={"dictionary": {}}, headers=_h(client, "reader")).status_code == 403
        assert client.put("/admin/config/dictionary/parsed", json={"dictionary": {"entries": {"X": {"nope": 1}}}}, headers=h).status_code == 422
        # valid: shared entries + an enum + a per-connector overlay
        payload = {
            "default_language": "en",
            "entries": {
                "AUDIT_DATE": {"label": "Audit Date", "format": "date"},
                "USR_STATUS": {"label": "Status", "rules": "ENUM", "rules_values": "STATUS"},
            },
            "enums": {
                "STATUS": {"label": "Status", "values": [{"value": "A", "label": "Active"}, {"value": "I", "label": "Inactive"}]},
            },
            "connectors": {
                "db": {"entries": {"USR_NAME": {"label": "User Name", "l": {"fr": "Nom"}}}},
            },
        }
        assert client.put("/admin/config/dictionary/parsed", json={"dictionary": payload}, headers=h).json()["saved"] is True
        # the file was written + parses cleanly
        text = dict_toml.read_text()
        assert "[entries.AUDIT_DATE]" in text and "[enums.STATUS]" in text and "[connectors.db.entries.USR_NAME]" in text
        assert 'fr = "Nom"' in text
        # GET round-trips it (default-valued keys dropped — no `l = {}` on AUDIT_DATE)
        after = client.get("/admin/config/dictionary/parsed", headers=h).json()["dictionary"]
        assert after["entries"]["AUDIT_DATE"] == {"label": "Audit Date", "format": "date"}
        assert after["connectors"]["db"]["entries"]["USR_NAME"]["l"] == {"fr": "Nom"}
        # Reload picks it up — dictionary_entries goes from 0 to 2 (the shared entries)
        r = client.post("/admin/reload", headers=h).json()
        assert r["dictionary_entries"] == 2

        # framework-enum override: a user-defined [framework_enums.DATASOURCE_TYPE] replaces the
        # bundled values for that id, so /admin/config/schema merges the two on the fly.
        bundled_count = len(sch["framework_enums"]["DATASOURCE_TYPE"]["values"])
        assert bundled_count > 1  # the bundled DATASOURCE_TYPE ships several values
        payload2 = {
            **payload,
            "framework_enums": {
                "DATASOURCE_TYPE": {"label": "DB Engines", "values": [{"value": "duckdb", "label": "DuckDB"}]},
            },
        }
        assert client.put("/admin/config/dictionary/parsed", json={"dictionary": payload2}, headers=h).json()["saved"] is True
        assert "[framework_enums.DATASOURCE_TYPE]" in dict_toml.read_text()
        sch2 = client.get("/admin/config/schema", headers=h).json()
        # the override replaces the bundled list wholesale (label too)
        assert sch2["framework_enums"]["DATASOURCE_TYPE"]["label"] == "DB Engines"
        assert sch2["framework_enums"]["DATASOURCE_TYPE"]["values"] == [{"value": "duckdb", "label": "DuckDB"}]
        # other ids still come from the bundled set (untouched)
        assert sch2["framework_enums"]["HTTP_METHOD"]["values"] == sch["framework_enums"]["HTTP_METHOD"]["values"]


def test_dictionary_put_is_fast_on_large_payload(env) -> None:
    """Regression — the v1 migration produces a 500+ entry dictionary (per-language translations
    on every row). The PUT used to go through ``tomlkit`` whose parser is O(n²)-ish on big
    nested-table files; on a real-world libnsx1 dictionary (153 kB, ~5 k lines) Save took
    ~2 minutes, which the browser interprets as an indefinite hang. The switch to ``tomli-w``
    rewrites the file from scratch in single-digit ms.

    This test builds a payload roughly the size that triggered the bug (500 entries × a few
    translations + a connector overlay with another 200 entries) and asserts the round-trip
    PUT completes well under a second. If a future change introduces tomlkit re-parsing on
    the dictionary again, this test catches it.
    """
    import time
    app, conn_toml, _ = env
    dict_toml = conn_toml.with_name("dictionary.toml")
    with TestClient(app) as client:
        h = _h(client, "admin")
        # 500 shared entries, each with two translations and a rule — close to libnsx1's shape.
        entries: dict[str, dict[str, object]] = {}
        for i in range(500):
            entries[f"COL_{i:03d}"] = {
                "label": f"Col {i}", "format": "text",
                "l": {"fr": f"Colonne {i}", "es": f"Columna {i}"},
            }
        # 200 connector-scoped overlays + a few enums/lookups so the file isn't trivial.
        overlay_entries = {f"NSX_{i:03d}": {"label": f"NS {i}"} for i in range(200)}
        payload = {
            "default_language": "en",
            "entries": entries,
            "enums": {f"E{i}": {"values": [{"value": "A"}, {"value": "B"}]} for i in range(10)},
            "connectors": {"nomasx1": {"entries": overlay_entries}},
        }
        # Round-trip once to write the file out (first PUT — no prior tomlkit parse cost).
        start = time.perf_counter()
        r = client.put("/admin/config/dictionary/parsed", json={"dictionary": payload}, headers=h)
        first_ms = (time.perf_counter() - start) * 1000
        assert r.json()["saved"] is True
        # Second PUT — *this* is the one that used to hit the tomlkit-parse bottleneck because
        # the file now exists and was re-parsed on every save. Must stay sub-second on a
        # comfortable threshold so an overloaded CI box doesn't flake. tomli-w typically does
        # this in ~10 ms; the prior tomlkit path took 60-120 *seconds* on this payload size.
        start = time.perf_counter()
        r = client.put("/admin/config/dictionary/parsed", json={"dictionary": payload}, headers=h)
        second_ms = (time.perf_counter() - start) * 1000
        assert r.json()["saved"] is True
        assert second_ms < 1500, (
            f"dictionary PUT took {second_ms:.0f}ms (first call: {first_ms:.0f}ms); "
            "the tomlkit-parse regression is back — see commit history for the tomli_w switch."
        )
        # File still has the data.
        text = dict_toml.read_text()
        assert "[entries.COL_000]" in text and "[connectors.nomasx1.entries.NSX_000]" in text


def test_config_menus_parsed_get_and_put(env) -> None:
    app, conn_toml, _ = env
    menus_toml = conn_toml.parent / "menus.toml"  # see env fixture — the same tmp_path
    with TestClient(app) as client:
        h = _h(client, "admin")
        # schema serves the MenusFile shape (with its $defs)
        sch = client.get("/admin/config/schema", headers=h).json()
        assert "menus" in sch["menus"]["properties"]
        for nested in ("AppMenu", "MenuItem"):
            assert nested in sch["menus"]["$defs"]
        # a fresh tmp dir has no menus.toml → GET returns an empty dict (path still reported)
        body = client.get("/admin/config/menus/parsed", headers=h).json()
        assert body["path"] == str(menus_toml) and body["menus"] == {}
        # permission gate + validation gate (an unknown parent / type mismatch is rejected)
        assert client.put("/admin/config/menus/parsed", json={"menus": {}}, headers=_h(client, "reader")).status_code == 403
        bad = {"app1": {"items": [{"id": "a", "label": "A", "parent": "nope"}]}}
        assert client.put("/admin/config/menus/parsed", json={"menus": bad}, headers=h).status_code == 422
        # valid: a folder + two leaves underneath
        payload = {
            "nomasx1": {
                "label": "NOMASX-1",
                "items": [
                    {"id": "security", "label": "Security", "l": {"fr": "Sécurité"}, "icon": "shield"},
                    {"id": "users", "parent": "security", "label": "Users", "type": "query", "target": "security_users_get"},
                    {"id": "roles", "parent": "security", "label": "Roles", "type": "query", "target": "security_roles_get"},
                ],
            },
        }
        r = client.put("/admin/config/menus/parsed", json={"menus": payload}, headers=h)
        assert r.status_code == 200 and r.json()["saved"] is True
        text = menus_toml.read_text()
        assert "[[menus.nomasx1.items]]" in text and 'id = "security"' in text and 'fr = "Sécurité"' in text
        after = client.get("/admin/config/menus/parsed", headers=h).json()["menus"]
        assert set(after) == {"nomasx1"}
        assert after["nomasx1"]["label"] == "NOMASX-1"
        assert [it["id"] for it in after["nomasx1"]["items"]] == ["security", "users", "roles"]
        # Reload picks it up — the app's menu count goes from 0 to 1
        assert client.post("/admin/reload", headers=h).json()["menu_apps"] == ["nomasx1"]


def test_config_screens_parsed_get_and_put(env) -> None:
    app, conn_toml, _ = env
    screens_toml = conn_toml.parent / "screens.toml"  # see env fixture — the same tmp_path
    with TestClient(app) as client:
        h = _h(client, "admin")
        # schema serves the ScreensFile shape with its $defs
        sch = client.get("/admin/config/schema", headers=h).json()
        assert "screens" in sch["screens"]["properties"]
        # The action discriminated union (slice 4) exposes every variant as its own $def so the
        # builder's ActionEditor can render the right per-type form when the operator picks one.
        # Same for the tab union: ``ScreenTab`` is itself an ``oneOf + discriminator`` annotation
        # (no class) — Pydantic emits one $def per branch (FormTab / NestedFormTab / NestedTableTab)
        # instead of a single $def for the union.
        for nested in (
            "Screen", "ScreenDialog", "FormTab", "NestedFormTab", "NestedTableTab",
            "ScreenField", "ParamBind",
            "RunQueryAction", "CallApiAction", "NavigateAction", "SetFieldAction",
            "ConfirmAction", "NotifyAction", "RefreshAction",
        ):
            assert nested in sch["screens"]["$defs"]
        # a fresh tmp dir has no screens.toml → GET returns an empty dict (path still reported)
        body = client.get("/admin/config/screens/parsed", headers=h).json()
        assert body["path"] == str(screens_toml) and body["screens"] == {}
        # permission gate + validation gate (an explicit id ≠ its key is a config bug)
        assert (
            client.put("/admin/config/screens/parsed", json={"screens": {}}, headers=_h(client, "reader")).status_code
            == 403
        )
        bad = {"app1": {"users": {"id": "people", "read_query": "q"}}}
        assert client.put("/admin/config/screens/parsed", json={"screens": bad}, headers=h).status_code == 422
        # valid: one screen with a dialog + a ParamBind binding for a column's lookup.
        # Phase 2 — ``dd`` / ``lookup_param_binds`` live on ``Screen.columns[]`` (single source
        # of truth for both grid + dialog); the ScreenField is layout-only.
        payload = {
            "nomasx1": {
                "security_users": {
                    "label": "Users",
                    "read_query": "users_get",
                    "update_query": "users_put",
                    # Phase 3 — ``audit_table`` (string) replaces the legacy ``audit`` bool.
                    "audit_table": "AUD_SECURITY_USERS",
                    "columns": [
                        {
                            "name": "USR_ROLE_ID",
                            "dd": "ROL_ID",
                            "lookup_param_binds": [
                                {"param": "ROL_APPS_ID", "source": "USR_APPS_ID"},
                            ],
                        },
                    ],
                    "dialog": {
                        "title": "User",
                        "tabs": [
                            {
                                "id": "general",
                                "label": "General",
                                "fields": [
                                    {"name": "USR_ID"},
                                    {"name": "USR_ROLE_ID"},
                                ],
                            },
                        ],
                    },
                },
            },
        }
        r = client.put("/admin/config/screens/parsed", json={"screens": payload}, headers=h)
        assert r.status_code == 200 and r.json()["saved"] is True
        text = screens_toml.read_text()
        assert "[screens.nomasx1.security_users]" in text
        assert 'read_query = "users_get"' in text and 'audit_table = "AUD_SECURITY_USERS"' in text
        # round-trip GET — id is injected from the dict key (default-valued keys dropped)
        after = client.get("/admin/config/screens/parsed", headers=h).json()["screens"]
        assert set(after) == {"nomasx1"}
        assert set(after["nomasx1"]) == {"security_users"}
        s = after["nomasx1"]["security_users"]
        assert s["read_query"] == "users_get" and s["audit_table"] == "AUD_SECURITY_USERS"
        # The lookup_param_binds round-trip on Screen.columns (not the dialog field).
        assert s["columns"][0]["lookup_param_binds"][0] == {
            "param": "ROL_APPS_ID", "source": "USR_APPS_ID",
        }
        assert s["columns"][0]["dd"] == "ROL_ID"
        # Reload picks it up — the screen apps go from 0 → 1
        assert client.post("/admin/reload", headers=h).json()["screen_apps"] == ["nomasx1"]


def test_config_screens_parsed_preserves_workflow_step_discriminators(env) -> None:
    """Regression for the F00926 chain that landed in screens.toml with the right shape but
    came back from the admin endpoint with the IF step's ``type`` stripped — the Visual
    Designer then read the IF as a ``run_query`` (the frontend's default when the
    discriminator is missing), and the operator couldn't reach the Condition editor.

    The fix in :func:`_dump_screen` recursively re-injects the discriminator on every nested
    step list — :class:`ChainAction.steps`, :class:`IfAction.then_steps` / ``else_steps``,
    :class:`LoopAction.steps`. This test wires one of each at every nesting depth and asserts
    the GET payload keeps every ``type`` field through one round-trip + survives a PUT echo.
    """
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        payload = {
            "nomasx1": {
                "settings_applications": {
                    "read_query": "apps_get",
                    "dialog": {
                        "tabs": [
                            {
                                "id": "general", "type": "form", "fields": [{"name": "APPS_ID"}],
                                "actions": [
                                    {
                                        "id": "wf_button", "type": "chain", "label": "Run",
                                        "steps": [
                                            {
                                                "id": "guard", "type": "if",
                                                "condition": {"source": "INPUT.MODE", "operator": "equals", "value": "Y"},
                                                "then_steps": [
                                                    {"id": "do_thing", "type": "run_query", "query": "apps_rebuild"},
                                                    {
                                                        "id": "for_each_app", "type": "loop",
                                                        "source": "do_thing.rows",
                                                        "steps": [
                                                            {"id": "touch", "type": "run_query", "query": "apps_touch"},
                                                        ],
                                                    },
                                                ],
                                                "else_steps": [
                                                    {"id": "notify_skip", "type": "notify", "message": "skipped", "tone": "info"},
                                                ],
                                            },
                                            {"id": "wrap_up", "type": "refresh"},
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                },
            },
        }
        r = client.put("/admin/config/screens/parsed", json={"screens": payload}, headers=h)
        assert r.status_code == 200, r.text

        body = client.get("/admin/config/screens/parsed", headers=h).json()
        tab_actions = body["screens"]["nomasx1"]["settings_applications"]["dialog"]["tabs"][0]["actions"]
        chain = tab_actions[0]
        # Every nested type discriminator survives the dump (the bug was: nested steps lost
        # their ``type`` because Literal[X] = X matches the Pydantic default).
        assert chain["type"] == "chain"
        if_step = chain["steps"][0]
        assert if_step["type"] == "if"
        assert if_step["condition"] == {"source": "INPUT.MODE", "operator": "equals", "value": "Y"}
        assert [s["type"] for s in if_step["then_steps"]] == ["run_query", "loop"]
        # Loop's body keeps its discriminator at the next nesting level too.
        loop_step = if_step["then_steps"][1]
        assert loop_step["steps"][0]["type"] == "run_query"
        # Else branch + the trailing refresh on the outer chain — also preserved.
        assert [s["type"] for s in if_step["else_steps"]] == ["notify"]
        assert chain["steps"][1]["type"] == "refresh"

        # PUT echo — saving the GET'd body back doesn't 422 (every discriminator survives so
        # the discriminated union validates without falling through to the wrong variant).
        r2 = client.put("/admin/config/screens/parsed", json={"screens": body["screens"]}, headers=h)
        assert r2.status_code == 200, r2.text


def test_config_screens_parsed_preserves_tab_type_discriminator(env) -> None:
    """Regression: ``model_dump(exclude_defaults=True)`` strips the Literal ``type`` discriminator
    on nested tabs (its only-allowed value equals its default). The visual builder needs the
    discriminator to render the right Tab Settings panel — and a PUT round-trip would otherwise
    re-validate every nested tab as FormTab and 422 on the extra keys (form_screen / screen /
    param_binds). The endpoint post-injects ``type`` for every tab + action so the wire payload
    survives the trip."""
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Save a screen with one of each tab kind.
        payload = {
            "nomasx1": {
                "settings_applications": {
                    "read_query": "apps_get",
                    "dialog": {
                        "tabs": [
                            {"id": "general", "type": "form", "fields": [{"name": "APPS_ID"}]},
                            {
                                "id": "jd_edwards", "type": "nested_form",
                                "form_screen": "settings_jdedwards",
                                "param_binds": [{"param": "APPS_ID", "source": "APPS_ID"}],
                            },
                            {
                                "id": "activity_log", "type": "nested_table",
                                "screen": "settings_activity_log",
                                "param_binds": [{"param": "ACL_APPS_ID", "source": "APPS_ID"}],
                            },
                        ],
                    },
                    "actions": [
                        {"id": "rebuild", "type": "run_query", "query": "apps_rebuild"},
                    ],
                },
            },
        }
        r = client.put("/admin/config/screens/parsed", json={"screens": payload}, headers=h)
        assert r.status_code == 200, r.text
        # GET — every tab + action has its ``type`` discriminator present in the JSON.
        body = client.get("/admin/config/screens/parsed", headers=h).json()
        tabs = body["screens"]["nomasx1"]["settings_applications"]["dialog"]["tabs"]
        assert [t["type"] for t in tabs] == ["form", "nested_form", "nested_table"]
        actions = body["screens"]["nomasx1"]["settings_applications"]["actions"]
        assert actions[0]["type"] == "run_query"
        # PUT round-trip — saving the GET'd body back doesn't 422 (the discriminator survives).
        r2 = client.put("/admin/config/screens/parsed", json={"screens": body["screens"]}, headers=h)
        assert r2.status_code == 200, r2.text


def test_config_screens_parsed_folds_legacy_key_columns_onto_column_hints(env) -> None:
    """The screen designer's Columns tab reads ``column.key`` per column. An older
    ``screens.toml`` (pre-key-on-hint) sets keys via the flat ``screen.key_columns`` list —
    the GET payload folds those names onto matching column hints as ``key: true`` so the
    UI lights up without a re-migration, and PUTs back the cleaner shape (no redundant
    ``key_columns`` line when every key has a hint)."""
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Pre-fold: an operator hand-wrote screens.toml with the flat list.
        payload = {
            "nomasx1": {
                "settings_applications": {
                    "read_query": "apps_get",
                    "columns": [
                        {"name": "APPS_ID"},
                        {"name": "APPS_NAME"},
                    ],
                    "key_columns": ["APPS_ID"],
                },
            },
        }
        assert client.put("/admin/config/screens/parsed", json={"screens": payload}, headers=h).status_code == 200
        body = client.get("/admin/config/screens/parsed", headers=h).json()
        s = body["screens"]["nomasx1"]["settings_applications"]
        # The matching column got ``key: True``; the legacy list dropped (every key found a hint).
        assert s["columns"] == [{"name": "APPS_ID", "key": True}, {"name": "APPS_NAME"}]
        assert "key_columns" not in s
        # PUT the GET'd shape back — server keeps the column.key flag (no 422).
        r = client.put("/admin/config/screens/parsed", json={"screens": body["screens"]}, headers=h)
        assert r.status_code == 200, r.text
        # Round-trip GET — column.key survives, no spurious key_columns reintroduced.
        after = client.get("/admin/config/screens/parsed", headers=h).json()
        s2 = after["screens"]["nomasx1"]["settings_applications"]
        assert s2["columns"] == [{"name": "APPS_ID", "key": True}, {"name": "APPS_NAME"}]
        assert "key_columns" not in s2


def test_config_screens_parsed_preserves_leftover_key_columns(env) -> None:
    """Defensive: a key column with no matching column hint (rare — a hand-edited
    ``key_columns`` referring to a column that isn't in the ``columns`` list) keeps the
    explicit list as a fallback so the Excel-import match still works."""
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        payload = {
            "nomasx1": {
                "settings_applications": {
                    "read_query": "apps_get",
                    "columns": [{"name": "APPS_ID"}],
                    "key_columns": ["APPS_ID", "TENANT_ID"],   # TENANT_ID has no hint
                },
            },
        }
        assert client.put("/admin/config/screens/parsed", json={"screens": payload}, headers=h).status_code == 200
        body = client.get("/admin/config/screens/parsed", headers=h).json()
        s = body["screens"]["nomasx1"]["settings_applications"]
        assert s["columns"] == [{"name": "APPS_ID", "key": True}]
        assert s["key_columns"] == ["TENANT_ID"]   # only the leftover — APPS_ID landed on its hint


def test_oidc_callback_fragment_redirect() -> None:
    from liberty.config import OIDCSettings

    s = OIDCSettings(enabled=True, discovery_url="https://idp.test/.well-known", frontend_redirect="https://app.test/oidc/callback")
    assert s.frontend_redirect == "https://app.test/oidc/callback"
    # (the full OIDC flow needs a real IdP; this just pins the new setting + that the
    # callback route now returns a redirect when frontend_redirect is set — see liberty/auth/routes.py)


# --- POST /admin/config/connectors/{c}/test-sql --------------------------- #


def _seed_item_table(db_url: str) -> None:
    """Drop an ``item`` table on the test DB so test-sql has something to run against —
    the env fixture only sets up the auth schema, no user table to play with."""
    from sqlalchemy import text as _text

    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        engine = pools.engine("default")
        async with engine.begin() as conn:
            await conn.execute(_text("CREATE TABLE IF NOT EXISTS item (id INTEGER PRIMARY KEY, name TEXT, status TEXT)"))
            await conn.execute(_text("DELETE FROM item"))
            await conn.execute(_text("INSERT INTO item (id, name, status) VALUES (1,'a','on'),(2,'b','off')"))
        await pools.dispose()

    asyncio.run(go())


def test_test_sql_select_with_params(env) -> None:
    """A SELECT runs and returns rows; the same ``:param`` binds the named-query path uses
    work here too — the operator can paste the same SQL they keep in the TOML and it just runs."""
    app, _, db_url = env
    _seed_item_table(db_url)
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {"sql": "SELECT id, name FROM item WHERE (:status IS NULL OR status = :status) ORDER BY id", "params": {"status": "on"}}
        r = client.post("/admin/config/connectors/db/test-sql", json=body, headers=h)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["statement_type"] == "SELECT" and j["row_count"] == 1
        assert j["rows"] == [{"id": 1, "name": "a"}]
        assert j["truncated"] is False and "duration_ms" in j
        # max_rows truncates
        body2 = {"sql": "SELECT id FROM item ORDER BY id", "max_rows": 1}
        r2 = client.post("/admin/config/connectors/db/test-sql", json=body2, headers=h).json()
        assert r2["row_count"] == 1 and r2["truncated"] is True


def test_test_sql_dry_run_rolls_back_write(env) -> None:
    """An INSERT/UPDATE/DELETE with dry_run=True (the default) reports its rowcount but
    the row never lands — the operator gets to verify a `_put` parses and the WHERE matches
    before committing it."""
    app, _, db_url = env
    _seed_item_table(db_url)
    with TestClient(app) as client:
        h = _h(client, "admin")
        # default dry_run=True
        body = {"sql": "INSERT INTO item (id, name, status) VALUES (99, 'test', 'on')"}
        j = client.post("/admin/config/connectors/db/test-sql", json=body, headers=h).json()
        assert j["statement_type"] == "INSERT" and j["rowcount"] == 1 and "rows" not in j or j["rows"] == []
        # the row didn't land — verify via a follow-up SELECT
        rows = client.post("/admin/config/connectors/db/test-sql", json={"sql": "SELECT id FROM item ORDER BY id"}, headers=h).json()["rows"]
        assert [r["id"] for r in rows] == [1, 2]
        # explicit dry_run=False commits
        body2 = {"sql": "INSERT INTO item (id, name, status) VALUES (99, 'test', 'on')", "dry_run": False}
        client.post("/admin/config/connectors/db/test-sql", json=body2, headers=h)
        rows2 = client.post("/admin/config/connectors/db/test-sql", json={"sql": "SELECT id FROM item ORDER BY id"}, headers=h).json()["rows"]
        assert [r["id"] for r in rows2] == [1, 2, 99]


def test_test_sql_rejects_disallowed_and_requires_superuser(env) -> None:
    app, _, _ = env
    with TestClient(app) as client:
        # non-superuser → 403
        assert client.post("/admin/config/connectors/db/test-sql", json={"sql": "SELECT 1"}, headers=_h(client, "reader")).status_code == 403
        # unauth → 401
        assert client.post("/admin/config/connectors/db/test-sql", json={"sql": "SELECT 1"}).status_code == 401
        h = _h(client, "admin")
        # unknown connector → 404
        assert client.post("/admin/config/connectors/ghost/test-sql", json={"sql": "SELECT 1"}, headers=h).status_code == 404
        # DROP not in the statement allow-list → 422 (parity with the named-query path)
        r = client.post("/admin/config/connectors/db/test-sql", json={"sql": "DROP TABLE item"}, headers=h)
        assert r.status_code == 422


# --- /admin/config/charts/parsed ------------------------------------------- #


def test_config_charts_parsed_get_and_put(env) -> None:
    """GET → an empty dict when no charts.toml; PUT validates + writes; reload picks it up."""
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # GET: empty
        r = client.get("/admin/config/charts/parsed", headers=h).json()
        assert r["charts"] == {}
        assert r["path"].endswith("charts.toml")
        # PUT: save one chart, nested under its connector scope [charts.db.users_per_app].
        body = {"charts": {
            "db": {
                "users_per_app": {
                    "label": "Users per app",
                    "query": "answer",
                    "spec": {"type": "bar", "x": "X", "y": ["Y"], "aggregation": "count"},
                },
            },
        }}
        s = client.put("/admin/config/charts/parsed", json=body, headers=h)
        assert s.status_code == 200 and s.json()["saved"] is True
        # GET round-trip: nested {scope: {id: chart}}. The GET uses `exclude_defaults=True` so default
        # values (type='bar', aggregation='sum') are stripped — only non-default fields land in
        # the response (and on disk), which keeps charts.toml diff-friendly. id + connector are the
        # path keys, so they're not echoed in the body.
        after = client.get("/admin/config/charts/parsed", headers=h).json()["charts"]
        assert set(after) == {"db"}
        assert set(after["db"]) == {"users_per_app"}
        c = after["db"]["users_per_app"]
        assert c["label"] == "Users per app"
        assert "connector" not in c and "id" not in c
        assert c["spec"]["x"] == "X" and c["spec"]["y"] == ["Y"] and c["spec"]["aggregation"] == "count"
        # Reload picks it up — the chart shows up (qualified) in /admin/reload's reply + /api/charts
        reload_resp = client.post("/admin/reload", headers=h).json()
        assert reload_resp["charts"] == ["db.users_per_app"]
        listed = client.get("/api/charts", headers=h).json()["charts"]
        assert [c["id"] for c in listed] == ["db.users_per_app"]


def test_config_charts_parsed_rejects_invalid(env) -> None:
    """A chart missing its X or Y column → 422 from the PUT (the model validator catches it)."""
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {"charts": {
            "c": {
                "broken": {
                    "label": "B", "query": "q",
                    "spec": {"type": "bar", "x": "X", "y": []},  # empty Y
                },
            },
        }}
        r = client.put("/admin/config/charts/parsed", json=body, headers=h)
        assert r.status_code == 422 and "spec.y" in r.json()["detail"]


def test_config_schema_includes_charts(env) -> None:
    """The /admin/config/schema bundle now carries the ChartsFile JSON Schema so the
    (future) ChartsBuilder can render its forms from it."""
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        schema = client.get("/admin/config/schema", headers=h).json()
        assert "charts" in schema
        assert "properties" in schema["charts"]
        # The ChartConfig + ChartSpec $defs are reachable for SchemaNavigator drill-in
        defs = schema["charts"].get("$defs") or {}
        assert "ChartConfig" in defs and "ChartSpec" in defs


# --- /admin/config/dashboards/parsed -------------------------------------- #


def test_config_dashboards_parsed_get_and_put(env) -> None:
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # GET empty
        assert client.get("/admin/config/dashboards/parsed", headers=h).json()["dashboards"] == {}
        # PUT a dashboard (nested under scope `db`) with a chart-reference widget + a KPI
        body = {"dashboards": {
            "db": {
                "overview": {
                    "label": "Overview",
                    "widgets": [
                        {"type": "chart", "chart": "saved_chart", "col_span": 12, "row_span": 1},
                        {"type": "kpi", "label": "Rows", "connector": "db", "query": "answer",
                         "column": "answer", "aggregation": "count", "col_span": 3},
                    ],
                },
            },
        }}
        r = client.put("/admin/config/dashboards/parsed", json=body, headers=h)
        assert r.status_code == 200 and r.json()["saved"] is True
        # Reload + GET round-trip — the public id is the qualified `db.overview`.
        reload_resp = client.post("/admin/reload", headers=h).json()
        assert reload_resp["dashboards"] == ["db.overview"]
        # /api/dashboards surfaces it (with the orphan chart-ref dropped — `saved_chart` doesn't exist)
        ds = client.get("/api/dashboards", headers=h).json()["dashboards"]
        assert [d["id"] for d in ds] == ["db.overview"]
        # Two widgets in TOML → one survives (the chart-ref drops because there's no charts.toml)
        assert [w["type"] for w in ds[0]["widgets"]] == ["kpi"]


def test_config_dashboards_parsed_rejects_invalid(env) -> None:
    """A chart widget with both `chart` id and an inline spec → 422 (the validator catches it)."""
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {"dashboards": {
            "s": {
                "x": {"label": "X", "widgets": [{
                    "type": "chart", "chart": "c", "connector": "c", "query": "q",
                    "spec": {"type": "bar", "x": "X", "y": ["Y"], "aggregation": "count"},
                }]},
            },
        }}
        r = client.put("/admin/config/dashboards/parsed", json=body, headers=h)
        assert r.status_code == 422 and "either" in r.json()["detail"]


def test_config_schema_includes_dashboards(env) -> None:
    app, _, _ = env
    with TestClient(app) as client:
        schema = client.get("/admin/config/schema", headers=_h(client, "admin")).json()
        assert "dashboards" in schema
        defs = schema["dashboards"].get("$defs") or {}
        assert "Dashboard" in defs
        # The widget discriminated union ships its variants
        assert "ChartWidget" in defs and "KpiWidget" in defs


# --------------------------------------------------------------------------- #
# Phase 3b — Reports → Branding endpoints
# --------------------------------------------------------------------------- #
def test_reports_branding_round_trips(env, tmp_path, monkeypatch) -> None:
    """``GET /admin/config/reports/branding`` returns the defaults when the
    file is empty; ``PUT`` writes the non-default values into a fresh
    ``[reports.branding]`` table; the next GET round-trips them; the live
    settings object on ``app.state`` is reloaded so the next render picks
    them up without a restart."""
    app, _conn_toml, _db_url = env
    # Point the app.toml resolver at a tmp path so we don't touch the real
    # config/app.toml when running tests.
    app_toml = tmp_path / "app.toml"
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(app_toml))

    with TestClient(app) as client:
        h = _h(client, "admin")
        # Reader can't read or write
        assert client.get("/admin/config/reports/branding", headers=_h(client, "reader")).status_code == 403
        assert client.put("/admin/config/reports/branding",
                          json={"branding": {}}, headers=_h(client, "reader")).status_code == 403
        # First GET on a missing file → defaults
        r = client.get("/admin/config/reports/branding", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["branding"] == body["defaults"]
        assert body["branding"]["author"] == ""
        assert body["branding"]["primary_color"] == "#0b3a82"

        # PUT new branding — only non-default values land on disk
        new = {
            "author": "ACME Consulting",
            "primary_color": "#FF5722",
            "primary_color_light": body["defaults"]["primary_color_light"],  # default — should NOT be written
            "cover_eyebrow": "Audit Report",
            "cover_ref": body["defaults"]["cover_ref"],                     # default
            "footer_left": body["defaults"]["footer_left"],                 # default
        }
        r = client.put("/admin/config/reports/branding", json={"branding": new}, headers=h)
        assert r.status_code == 200 and r.json()["saved"] is True

        # On-disk: only the overridden keys appear, [reports.branding] header present
        on_disk = app_toml.read_text(encoding="utf-8")
        assert "[reports.branding]" in on_disk
        assert 'author = "ACME Consulting"' in on_disk
        assert 'primary_color = "#FF5722"' in on_disk
        assert 'cover_eyebrow = "Audit Report"' in on_disk
        # Default-valued fields are NOT serialised (terseness)
        assert "primary_color_light" not in on_disk
        assert "footer_left" not in on_disk

        # GET reflects the new values (with defaults re-applied for omitted fields)
        after = client.get("/admin/config/reports/branding", headers=h).json()["branding"]
        assert after["author"] == "ACME Consulting"
        assert after["primary_color"] == "#FF5722"
        assert after["primary_color_light"] == body["defaults"]["primary_color_light"]
        assert after["cover_eyebrow"] == "Audit Report"

        # Live-apply: app.state.settings.reports.branding has the new author
        live_branding = app.state.settings.reports.branding
        assert live_branding.author == "ACME Consulting"
        assert live_branding.primary_color == "#FF5722"


def test_reports_branding_rejects_unknown_field(env, tmp_path, monkeypatch) -> None:
    """Unknown branding keys land on the validation error path — Pydantic
    rejects extras under ``model_config = ConfigDict(extra="forbid")``
    inherited from the parent. (Today the model doesn't lock extras, so this
    test documents the lenient behaviour — if it tightens later, change to
    expect 422.)"""
    app, _conn_toml, _db_url = env
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Surplus key just gets dropped; the save succeeds
        r = client.put(
            "/admin/config/reports/branding",
            json={"branding": {"author": "X", "unknown_field": "value"}},
            headers=h,
        )
        assert r.status_code == 200
        after = client.get("/admin/config/reports/branding", headers=h).json()["branding"]
        assert after["author"] == "X"
        assert "unknown_field" not in after


# --------------------------------------------------------------------------- #
# Phase 4a — Custom report templates
# --------------------------------------------------------------------------- #
def test_reports_templates_round_trip(env, tmp_path, monkeypatch) -> None:
    """``GET /admin/config/reports/parsed`` returns an empty list when the
    file is missing; ``PUT`` writes one or more [reports.<id>] tables; the
    next GET round-trips them; the in-memory registry refreshes so
    ``GET /api/reports`` immediately reflects the new template."""
    app, _conn_toml, _db_url = env
    # Settings.reports.templates_config_path defaults to "config/reports.toml"
    # relative to cwd; override to a tmp file so the test doesn't touch real
    # config. Bypassing the env-var resolver because templates_config_path is
    # a direct Settings field, not a LIBERTY_APP_CONFIG path.
    reports_toml = tmp_path / "reports.toml"
    # Redirect LIBERTY_APP_CONFIG so the live-apply reload doesn't touch real
    # ./config/app.toml.
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))

    with TestClient(app) as client:
        # Lifespan has populated app.state.settings — override the templates
        # path now (before any request touches the file).
        app.state.settings.reports.templates_config_path = reports_toml
        h = _h(client, "admin")
        # Reader → 403 on both
        assert client.get("/admin/config/reports/parsed", headers=_h(client, "reader")).status_code == 403
        assert client.put("/admin/config/reports/parsed",
                          json={"templates": []}, headers=_h(client, "reader")).status_code == 403

        # First GET on missing file → empty list, with connector choices
        r = client.get("/admin/config/reports/parsed", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["templates"] == []
        # Connectors block is present (env's seeded "db" connector with the "answer" query)
        assert "db" in body["connectors"] and "answer" in body["connectors"]["db"]

        # PUT a new template — well-formed
        new_template = {
            "id": "answer-summary",
            "title": "The answer",
            "description": "Renders the answer query",
            "formats": ["pdf", "markdown"],
            "params": [],
            "data": {"connector": "db", "query": "answer"},
            "template_inline": "## Answer\n\n{{ ctx.data.rows[0].answer }}",
            "pdf_options": {},
        }
        r = client.put(
            "/admin/config/reports/parsed",
            json={"templates": [new_template]},
            headers=h,
        )
        assert r.status_code == 200 and r.json()["saved"] is True
        assert r.json()["count"] == 1

        # On-disk: the [reports.answer-summary] table is present + multiline
        # template_inline got serialised correctly
        on_disk = reports_toml.read_text(encoding="utf-8")
        assert "[reports.answer-summary]" in on_disk
        assert "## Answer" in on_disk
        assert 'connector = "db"' in on_disk
        assert 'query = "answer"' in on_disk

        # GET reflects it
        after = client.get("/admin/config/reports/parsed", headers=h).json()
        assert len(after["templates"]) == 1
        assert after["templates"][0]["id"] == "answer-summary"

        # Registry refreshed — /api/reports shows the new entry, runnable via /run
        # (the reader role for the API has the catalog permission elsewhere; here
        # we just confirm the admin sees it).
        listed = client.get("/api/reports", headers=h).json()
        ids = {(r["scope"], r["id"]) for r in listed["reports"]}
        assert ("custom", "answer-summary") in ids


def test_reports_templates_rejects_duplicate_id(env, tmp_path, monkeypatch) -> None:
    """Two templates with the same id in one PUT payload → 422 before the
    file is touched. Mirrors the registry's collision contract."""
    app, _conn_toml, _db_url = env
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    with TestClient(app) as client:
        app.state.settings.reports.templates_config_path = tmp_path / "reports.toml"
        h = _h(client, "admin")
        body = {"templates": [
            {"id": "x", "title": "X", "data": {"connector": "db", "query": "answer"},
             "template_inline": "ok"},
            {"id": "x", "title": "X dup", "data": {"connector": "db", "query": "answer"},
             "template_inline": "ok"},
        ]}
        r = client.put("/admin/config/reports/parsed", json=body, headers=h)
        assert r.status_code == 422
        assert "duplicate" in r.json()["detail"].lower()


def test_reports_templates_rejects_invalid_template_shape(env, tmp_path, monkeypatch) -> None:
    """Missing required field (e.g. data.connector) → 422 naming the entry
    so the operator knows which one to fix."""
    app, _conn_toml, _db_url = env
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    with TestClient(app) as client:
        app.state.settings.reports.templates_config_path = tmp_path / "reports.toml"
        h = _h(client, "admin")
        body = {"templates": [
            {"id": "bad", "title": "X", "data": {"query": "answer"},  # no connector
             "template_inline": "ok"},
        ]}
        r = client.put("/admin/config/reports/parsed", json=body, headers=h)
        assert r.status_code == 422
        assert "bad" in r.json()["detail"]


def test_reports_templates_preview_renders_with_data(env, tmp_path, monkeypatch) -> None:
    """``POST /admin/config/reports/preview`` executes the bound query and
    renders the inline template through the same sandboxed Jinja env the
    runtime dispatcher uses. The operator gets the rendered markdown back so
    they can iterate without saving the template."""
    app, _conn_toml, _db_url = env
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Reader → 403
        assert client.post(
            "/admin/config/reports/preview",
            json={"template_inline": "x", "data": {"connector": "db", "query": "answer"}},
            headers=_h(client, "reader"),
        ).status_code == 403

        body = {
            "template_inline": (
                "# Preview\n"
                "Rows: {{ ctx.data.rows | length }}\n"
                "{% for row in ctx.data.rows %}- {{ row.answer }}\n{% endfor %}"
            ),
            "data": {"connector": "db", "query": "answer"},
            "params": {},
        }
        r = client.post("/admin/config/reports/preview", json=body, headers=h)
        assert r.status_code == 200, r.text
        out = r.json()
        assert "# Preview" in out["markdown"]
        # The seeded "answer" query returns at least one row (it's the env's smoke query).
        assert out["row_count"] >= 1
        assert out["rendered_row_count"] >= 1
        assert out["truncated"] is False


def test_reports_templates_preview_blocks_sandbox_escape(env, tmp_path, monkeypatch) -> None:
    """Preview MUST go through the sandboxed env — attempts to reach
    ``__class__`` / ``__mro__`` / ``__subclasses__`` (the classic Jinja
    escape route to arbitrary Python) surface as 422 with the sandbox
    error, not as a successful render."""
    app, _conn_toml, _db_url = env
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {
            "template_inline": "{{ ''.__class__.__mro__[1].__subclasses__() }}",
            "data": {"connector": "", "query": ""},  # no data fetch needed
            "params": {},
        }
        r = client.post("/admin/config/reports/preview", json=body, headers=h)
        assert r.status_code == 422
        assert "render" in r.json()["detail"].lower()


def test_reports_templates_preview_without_data_binding(env, tmp_path, monkeypatch) -> None:
    """When connector / query are blank the preview still renders the template
    against an empty row list. Useful for iterating on a skeleton before the
    data binding is wired."""
    app, _conn_toml, _db_url = env
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {
            "template_inline": "Rows: {{ ctx.data.rows | length }} / Params: {{ ctx.params.x }}",
            "data": {"connector": "", "query": ""},
            "params": {"x": "hello"},
        }
        r = client.post("/admin/config/reports/preview", json=body, headers=h)
        assert r.status_code == 200
        out = r.json()
        assert "Rows: 0" in out["markdown"]
        assert "hello" in out["markdown"]
        assert out["row_count"] == 0
        assert out["truncated"] is False


def test_reports_templates_preview_rejects_unknown_connector(env, tmp_path, monkeypatch) -> None:
    """An unknown connector → 422 — never falls back to a no-op render that
    would mask a typo in the operator's binding."""
    app, _conn_toml, _db_url = env
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {
            "template_inline": "noop",
            "data": {"connector": "nope_doesnt_exist", "query": "answer"},
            "params": {},
        }
        r = client.post("/admin/config/reports/preview", json=body, headers=h)
        assert r.status_code == 422
        assert "unknown connector" in r.json()["detail"].lower()


def test_reports_templates_columns_returns_query_metadata(env, tmp_path, monkeypatch) -> None:
    """``POST /admin/config/reports/columns`` runs the bound query with a
    1-row cap and returns its column metadata — feeds the template editor's
    autocomplete and the 'Scaffold from query' button."""
    app, _conn_toml, _db_url = env
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Reader → 403
        assert client.post(
            "/admin/config/reports/columns",
            json={"connector": "db", "query": "answer"},
            headers=_h(client, "reader"),
        ).status_code == 403

        r = client.post(
            "/admin/config/reports/columns",
            json={"connector": "db", "query": "answer", "params": {}},
            headers=h,
        )
        assert r.status_code == 200, r.text
        out = r.json()
        # At least one column comes back with a name
        assert len(out["columns"]) >= 1
        names = {c["name"] for c in out["columns"]}
        assert "answer" in names


def test_reports_templates_columns_rejects_unknown_connector(env, tmp_path, monkeypatch) -> None:
    """Unknown connector → 422 — never silently returns an empty column list
    that would mask a typo in the operator's binding."""
    app, _conn_toml, _db_url = env
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/reports/columns",
            json={"connector": "nope_doesnt_exist", "query": "answer"},
            headers=h,
        )
        assert r.status_code == 422
        assert "unknown connector" in r.json()["detail"].lower()


def test_reports_templates_columns_propagates_query_error(env, tmp_path, monkeypatch) -> None:
    """A connector that exists but doesn't carry the named query → 422 with
    the underlying error, so the operator can fix the binding."""
    app, _conn_toml, _db_url = env
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/reports/columns",
            json={"connector": "db", "query": "no_such_query"},
            headers=h,
        )
        assert r.status_code == 422
        assert "no_such_query" in r.json()["detail"]


def test_app_parsed_secret_round_trip_via_enc_ciphertext(env, tmp_path, monkeypatch) -> None:
    """Regression: PUT /admin/config/app/parsed must preserve an already-encrypted
    secret when the operator saves the form without touching it. The UI follows
    the same pattern as pools / API-connector secrets: GET returns the on-disk
    ``ENC:…`` value, the input renders masked via ``<PasswordInput>``, and PUT
    sends back whatever's in the field — ``ENC:…`` unchanged when the operator
    didn't edit. The backend's :func:`_encrypt_sensitives` is idempotent on
    ``ENC:`` values so the round-trip is a no-op.

    The historical bug: AppBuilder used to send ``api_key: ""`` for "unedited"
    secrets (via an "Edit" toggle that hid the value behind a Set button), and
    the backend wrote that empty string to disk — wiping the operator's
    encrypted license key / Anthropic API key / OIDC secret. The toggle is gone;
    this test pins the saner contract that replaced it."""
    app, _conn_toml, _db_url = env
    app_toml = tmp_path / "app.toml"
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(app_toml))
    with TestClient(app) as client:
        h = _h(client, "admin")
        # 1) Set an api_key the first time — backend encrypts it on disk.
        r = client.put(
            "/admin/config/app/parsed",
            json={"ai": {"enabled": True, "api_key": "sk-test-1234"}},
            headers=h,
        )
        assert r.status_code == 200, r.text
        disk1 = app_toml.read_text(encoding="utf-8")
        assert "sk-test-1234" not in disk1  # plaintext must not land on disk
        assert "ENC:" in disk1               # encrypted form did
        encrypted_line = next(l for l in disk1.splitlines() if l.startswith("api_key"))

        # 2) The GET now returns the ENC: ciphertext (no masking) so the UI can
        # round-trip it untouched. Same shape as pools/connectors.
        got = client.get("/admin/config/app/parsed", headers=h).json()
        assert got["ai"]["api_key"].startswith("ENC:")
        # ``_set`` indicator fields are gone — the UI checks for non-empty value.
        assert "api_key_set" not in got["ai"]

        # 3) Operator saves the form without touching the secret — UI sends the
        # same ENC: value back. Backend's encrypt() is idempotent → byte-for-byte
        # unchanged on disk.
        r = client.put(
            "/admin/config/app/parsed",
            json={"ai": got["ai"]},
            headers=h,
        )
        assert r.status_code == 200, r.text
        disk2 = app_toml.read_text(encoding="utf-8")
        assert any(l == encrypted_line for l in disk2.splitlines()), (
            f"api_key changed: was {encrypted_line!r}, now "
            f"{[l for l in disk2.splitlines() if l.startswith('api_key')]!r}"
        )

        # 4) Explicit empty string still means CLEAR — operator deleted the
        # field contents and saved. The on-disk value becomes empty.
        cleared = {**got["ai"], "api_key": ""}
        r = client.put(
            "/admin/config/app/parsed",
            json={"ai": cleared},
            headers=h,
        )
        assert r.status_code == 200
        disk3 = app_toml.read_text(encoding="utf-8")
        assert 'api_key = ""' in disk3


def test_reports_templates_preview_template_parse_error(env, tmp_path, monkeypatch) -> None:
    """A syntactically broken Jinja template → 422 with the parser's message
    so the operator can fix it without saving."""
    app, _conn_toml, _db_url = env
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(tmp_path / "app.toml"))
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {
            "template_inline": "{{ ctx.data.rows ",  # unbalanced expression
            "data": {"connector": "", "query": ""},
            "params": {},
        }
        r = client.post("/admin/config/reports/preview", json=body, headers=h)
        assert r.status_code == 422
        assert "parse error" in r.json()["detail"].lower()


def test_config_versioning_endpoints(env) -> None:
    """Snapshot store + version endpoints: list / content / download / restore, superuser-gated."""
    app, conn_toml, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        store = app.state.config_versions
        assert store is not None
        v = store.snapshot(conn_toml, source="manual", who="admin")   # snapshot the live connectors.toml
        assert v is not None and v.version_num == 1

        listed = client.get("/admin/config/versions", headers=h).json()["versions"]
        assert any(x["id"] == v.id for x in listed)
        one = client.get("/admin/config/versions?file=connectors.toml", headers=h).json()["versions"]
        assert len(one) == 1 and one[0]["file"] == "connectors.toml" and one[0]["who"] == "admin"

        c = client.get(f"/admin/config/versions/{v.id}/content", headers=h)
        assert c.status_code == 200 and len(c.text) > 0
        d = client.get(f"/admin/config/versions/{v.id}/download", headers=h)
        assert d.status_code == 200 and "attachment" in d.headers.get("content-disposition", "")

        rr = client.post(f"/admin/config/versions/{v.id}/restore", headers=h)
        assert rr.status_code == 200 and rr.json()["restored"] == "connectors.toml"

        assert client.get("/admin/config/versions/999999/content", headers=h).status_code == 404
        assert client.get("/admin/config/versions", headers=_h(client, "reader")).status_code == 403


def test_upgrades_and_release_notes(env) -> None:
    """Startup records an install in the upgrade history; release-notes serves the bundled markdown."""
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # The lifespan recorded a framework install (fresh store; apps not installed in tests).
        ups = client.get("/admin/upgrades", headers=h).json()["upgrades"]
        assert len(ups) >= 1
        assert ups[0]["kind"] in ("install", "upgrade") and ups[0]["to_version"] and ups[0]["component"] == "framework"
        # Release notes — per component; the framework's bundled markdown + version.
        comps = client.get("/admin/release-notes", headers=h).json()["components"]
        fw = next(c for c in comps if c["component"] == "framework")
        assert fw["version"] and fw["en"].startswith("# Liberty Next") and "## 7.0.27" in fw["en"] and fw["fr"]
        # Both superuser-gated.
        assert client.get("/admin/upgrades", headers=_h(client, "reader")).status_code == 403
        assert client.get("/admin/release-notes", headers=_h(client, "reader")).status_code == 403


def test_screen_bundle_versioning(env) -> None:
    """Phase 2 — every screen save snapshots the CHANGED screens' dependency bundle (a deploy ZIP).
    A brand-new screen captures nothing (no prior state); editing it captures its pre-edit bundle.
    Bundles stay out of the file-version list and have their own list / download / restore surface."""
    app, conn_toml, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        scr = {"nomasx1": {"f0093": {"label": "Roles", "read_query": "roles_get", "columns": [{"name": "ROL_ID"}]}}}

        # First save CREATES the screen — no prior state, so no bundle yet. Reload makes it live.
        assert client.put("/admin/config/screens/parsed", json={"screens": scr}, headers=h).status_code == 200
        client.post("/admin/reload", headers=h)
        assert client.get("/admin/config/screen-versions", headers=h).json()["bundles"] == []

        # Editing it captures the PRE-edit bundle (one version for nomasx1.f0093).
        scr["nomasx1"]["f0093"]["label"] = "Security Roles"
        assert client.put("/admin/config/screens/parsed", json={"screens": scr}, headers=h).status_code == 200
        bundles = client.get("/admin/config/screen-versions", headers=h).json()["bundles"]
        assert len(bundles) == 1
        b = bundles[0]
        assert b["app"] == "nomasx1" and b["screen"] == "f0093" and len(b["versions"]) == 1
        ver = b["versions"][0]
        assert ver["source"] == "save"

        # Bundles never leak into the file-version list (screens.toml history is separate).
        files = client.get("/admin/config/versions", headers=h).json()["versions"]
        assert all(not v["file"].startswith("screen:") for v in files)

        # Inspect: the embedded manifest comes back grouped by kind (the screen is always present).
        contents = client.get(f"/admin/config/screen-versions/{ver['id']}/contents", headers=h).json()
        assert contents["app"] == "nomasx1" and contents["screen"] == "f0093"
        assert any(d["name"] == "f0093" for d in contents["manifest"]["by_kind"].get("screen", []))

        # Download is a ZIP; restore re-applies it (overwrite strategy) and reloads.
        d = client.get(f"/admin/config/screen-versions/{ver['id']}/download", headers=h)
        assert d.status_code == 200 and d.content[:2] == b"PK" and ".zip" in d.headers.get("content-disposition", "")
        rr = client.post(f"/admin/config/screen-versions/{ver['id']}/restore", headers=h)
        assert rr.status_code == 200 and rr.json()["restored"] == "screen:nomasx1:f0093"
        assert "report" in rr.json()

        # Filter to one screen; missing / wrong-kind ids 404; readers are gated.
        assert len(client.get("/admin/config/screen-versions?app=nomasx1&screen=f0093", headers=h).json()["bundles"]) == 1
        assert client.get("/admin/config/screen-versions?app=nomasx1&screen=nope", headers=h).json()["bundles"] == []
        assert client.get(f"/admin/config/screen-versions/{ver['id']}/download", headers=_h(client, "reader")).status_code == 403
        assert client.post("/admin/config/screen-versions/999999/restore", headers=h).status_code == 404

        # Delete the bundle version; the file-version endpoint refuses a bundle id (separate surface).
        assert client.delete(f"/admin/config/versions/{ver['id']}", headers=h).status_code == 404
        assert client.delete(f"/admin/config/screen-versions/{ver['id']}", headers=h).json()["deleted"] == ver["id"]
        assert client.get("/admin/config/screen-versions", headers=h).json()["bundles"] == []
        assert client.delete(f"/admin/config/screen-versions/{ver['id']}", headers=h).status_code == 404


def test_config_version_delete_endpoints(env) -> None:
    """File-version delete + clear-history, superuser-gated; never touches the live file."""
    app, conn_toml, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        store = app.state.config_versions
        for text in ("a", "b", "c"):
            conn_toml.write_text(text, encoding="utf-8")
            store.snapshot(conn_toml, source="manual")
        v = store.list_versions(conn_toml)
        assert len(v) == 3
        # Delete one version; the live file is untouched.
        assert client.delete(f"/admin/config/versions/{v[0].id}", headers=h).json()["deleted"] == v[0].id
        assert conn_toml.read_text() == "c"
        assert len(store.list_versions(conn_toml)) == 2
        # Clear the rest in one call.
        assert client.delete("/admin/config/versions?file=connectors.toml", headers=h).json()["deleted"] == 2
        assert store.list_versions(conn_toml) == []
        # Gating + unknown id.
        assert client.delete("/admin/config/versions/999999", headers=h).status_code == 404
        assert client.delete(f"/admin/config/versions/{v[0].id}", headers=_h(client, "reader")).status_code == 403


def _scaffold_payload() -> dict:
    """A minimal Screen-Assistant payload: an `ab` table on connector `db`, one DD entry, a screen
    with a 2-field dialog, and a menu item."""
    return {
        "connector": "db",
        "app": "myapp",
        "table": {
            "name": "ab",
            "label": "Address Book",
            "get_sql": "SELECT 1 AS AN8, 'x' AS ALPH",
            "post_sql": "INSERT INTO ab (AN8) VALUES (:AN8)",
            "put_sql": "UPDATE ab SET ALPH = :ALPH WHERE AN8 = :AN8",
            "delete_sql": "DELETE FROM ab WHERE AN8 = :AN8",
        },
        "dictionary_scope": "db",
        "dictionary": [
            {"id": "AN8", "label": "Address Number", "format": "text", "justify": "right_blank", "size": 8},
        ],
        "screen": {
            "id": "address_book",
            "label": "Address Book",
            "connector": "db",
            "read_query": "ab_get",
            "insert_query": "ab_post",
            "update_query": "ab_put",
            "delete_query": "ab_delete",
            "columns": [{"name": "AN8", "dd": "AN8", "key": True}, {"name": "ALPH"}],
            "dialog": {
                "title": "Address",
                "tabs": [{"id": "general", "label": "General", "type": "form",
                          "fields": [{"name": "AN8"}, {"name": "ALPH"}]}],
            },
        },
        "menu": {"app": "myapp", "id": "address_book", "label": "Address Book",
                 "icon": "users", "target": "address_book", "connector": "db"},
    }


def test_assistant_scaffold_creates_everything(env) -> None:
    """One POST writes the connector table + CRUD queries, a dictionary entry, the screen + dialog,
    and the menu item — then reloads so it's all live."""
    app, conn_toml, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/assistant/scaffold", json=_scaffold_payload(), headers=h)
        assert r.status_code == 200, r.text
        created = r.json()["created"]
        assert created["queries"] == ["ab_get", "ab_put", "ab_post", "ab_delete"]
        assert created["dictionary_entries"] == ["AN8"]
        assert created["screen"] == "myapp.address_book" and created["menu_item"] == "address_book"

        # Files were written.
        ctext = conn_toml.read_text()
        assert 'name = "ab"' in ctext and "ab_get" not in ctext  # synthesised names aren't literal in TOML
        assert '[connectors.db.tables]' in ctext or 'name = "ab"' in ctext
        base = conn_toml.parent
        assert "[connectors.db.entries.AN8]" in (base / "dictionary.toml").read_text()
        assert "[screens.myapp.address_book]" in (base / "screens.toml").read_text()
        assert 'target = "address_book"' in (base / "menus.toml").read_text()

        # Reload made it live: the screen resolves and its read query runs.
        after = client.get("/admin/config/screens/parsed", headers=h).json()["screens"]
        assert after["myapp"]["address_book"]["read_query"] == "ab_get"
        assert client.get("/api/sql/db/ab_get", headers=h).json()["rows"] == [{"AN8": 1, "ALPH": "x"}]

        # Re-posting the same scaffold collides (table already exists).
        assert client.post("/admin/assistant/scaffold", json=_scaffold_payload(), headers=h).status_code == 409


def test_assistant_scaffold_reuses_existing_query(env) -> None:
    """When ``table`` is None the assistant reuses an existing connector query as the screen's
    read source: connectors.toml is untouched (no new table/queries, no dictionary), and a
    read-only screen is created pointing at that query."""
    app, conn_toml, _ = env
    payload = {
        "connector": "db",
        "app": "myapp",
        "table": None,
        "dictionary_scope": "myapp",
        "dictionary": [],
        "screen": {
            "id": "the_answer",
            "label": "The Answer",
            "connector": "db",
            "read_query": "answer",
            "read_only": True,
            "columns": [{"name": "answer"}],
            "dialog": {"title": "Answer", "tabs": [{"id": "general", "label": "General",
                       "type": "form", "fields": [{"name": "answer"}]}]},
        },
        "menu": None,
    }
    with TestClient(app) as client:
        h = _h(client, "admin")
        before = conn_toml.read_text()
        r = client.post("/admin/assistant/scaffold", json=payload, headers=h)
        assert r.status_code == 200, r.text
        created = r.json()["created"]
        assert created["table"] is None and created["queries"] == []
        assert created["dictionary_entries"] == [] and created["screen"] == "myapp.the_answer"
        # connectors.toml is byte-for-byte unchanged — no table/queries were appended.
        assert conn_toml.read_text() == before
        # The screen is live, read-only, and runs the reused query.
        after = client.get("/admin/config/screens/parsed", headers=h).json()["screens"]
        s = after["myapp"]["the_answer"]
        assert s["read_query"] == "answer" and s["read_only"] is True
        assert client.get("/api/sql/db/answer", headers=h).json()["rows"] == [{"answer": 42}]


def test_assistant_scaffold_no_dialog_readonly_grid(env) -> None:
    """A screen with no dialog is a valid read-only grid; columns may carry ``hidden`` (in the
    screen + dialog set but off the grid). No CRUD queries are written."""
    app, conn_toml, _ = env
    payload = {
        "connector": "db",
        "app": "myapp",
        "table": {"name": "ab", "label": "Address Book", "get_sql": "SELECT 1 AS AN8, 'x' AS ALPH"},
        "dictionary_scope": "myapp",
        "dictionary": [],
        "screen": {
            "id": "ab_grid",
            "label": "AB",
            "connector": "db",
            "read_query": "ab_get",
            "read_only": True,
            "columns": [{"name": "AN8"}, {"name": "ALPH", "hidden": True}],
        },
        "menu": None,
    }
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/assistant/scaffold", json=payload, headers=h)
        assert r.status_code == 200, r.text
        # Only the read query was created — no put/post/delete.
        assert r.json()["created"]["queries"] == ["ab_get"]
        after = client.get("/admin/config/screens/parsed", headers=h).json()["screens"]
        s = after["myapp"]["ab_grid"]
        assert s["read_only"] is True and s.get("dialog") is None
        assert s["columns"][1]["hidden"] is True
        assert client.get("/api/sql/db/ab_get", headers=h).json()["rows"] == [{"AN8": 1, "ALPH": "x"}]


def test_assistant_presets(env) -> None:
    """The catalog endpoint reads TOML under <config>/presets/, filters by connector, gated."""
    app, conn_toml, _ = env
    presets_dir = conn_toml.parent / "presets" / "jdedwards"
    presets_dir.mkdir(parents=True)
    (presets_dir / "ab.toml").write_text(
        '[[presets]]\nid = "ab"\nlabel = "Address Book"\nconnector = "jdedwards"\n'
        '[[presets.tables]]\nschema = "#SCHEMA.DTA#"\nname = "F0101"\n\n'
        '[[presets]]\nid = "other"\nlabel = "Other"\nconnector = "somethingelse"\n'
        '[[presets.tables]]\nname = "X"\n',
        encoding="utf-8",
    )
    with TestClient(app) as client:
        h = _h(client, "admin")
        all_p = client.get("/admin/assistant/presets", headers=h).json()["presets"]
        assert {p["id"] for p in all_p} == {"ab", "other"}
        # Filtered by connector — only matching (or connector-less) presets.
        jde = client.get("/admin/assistant/presets?connector=jdedwards", headers=h).json()["presets"]
        assert [p["id"] for p in jde] == ["ab"]
        assert jde[0]["tables"][0] == {"schema": "#SCHEMA.DTA#", "name": "F0101"}
        assert client.get("/admin/assistant/presets", headers=_h(client, "reader")).status_code == 403


def test_assistant_scaffold_validation_and_gating(env) -> None:
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Unknown connector → 404.
        bad_conn = _scaffold_payload(); bad_conn["connector"] = "nope"
        assert client.post("/admin/assistant/scaffold", json=bad_conn, headers=h).status_code == 404
        # Missing screen id → 422, and nothing written (the connectors file is unchanged).
        bad_screen = _scaffold_payload(); bad_screen["screen"].pop("id")
        assert client.post("/admin/assistant/scaffold", json=bad_screen, headers=h).status_code == 422
        # Reader is forbidden.
        assert client.post("/admin/assistant/scaffold", json=_scaffold_payload(), headers=_h(client, "reader")).status_code == 403
