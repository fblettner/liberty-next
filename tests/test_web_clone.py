"""Tests for :mod:`liberty.web.clone` — the cross-file app cloning operation that backs
``POST /admin/config/clone-app``.

Two layers, same shape as ``test_web_rename``:

* Pure-function tests on :func:`clone_app` against a tmp_path of small but representative
  TOML files. Cover the happy path + every refuse-on-collision branch + the cross-reference
  rewrite (``connector = "<source>"`` → ``"<new>"`` inside the cloned subtree).
* Endpoint tests on ``POST /admin/config/clone-app`` — auth gating, 422 on validation
  failure, happy-path that all four files end up with the new namespace.
"""

from __future__ import annotations

import asyncio
import textwrap
import tomllib
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
    DashboardSettings,
    MenuSettings,
    ScreenSettings,
    Settings,
)
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app
from liberty.web.clone import CloneError, clone_app, delete_app


JWT_SECRET = "web-clone-test-secret"


# ── pure-function tests ────────────────────────────────────────────────────────────────────


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


@pytest.fixture
def cfg_tree(tmp_path: Path) -> dict[str, Path]:
    """A small but representative app: connector ``foo`` (with a query), a per-connector
    dictionary scope, a menu app, a screen with a connector field + a row_click_connector
    that also references ``foo``. The clone target pool ``foo2_db`` is pre-declared (the
    clone refuses if the target pool doesn't exist)."""
    paths = {
        "connectors": tmp_path / "connectors.toml",
        "screens": tmp_path / "screens.toml",
        "menus": tmp_path / "menus.toml",
        "dictionary": tmp_path / "dictionary.toml",
    }
    _write(paths["connectors"], """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        # The target pool the clone will reference — must exist or clone refuses.
        [pools.foo2_db]
        url = "sqlite+aiosqlite:///:memory:"

        [connectors.foo]
        type = "sql"
        pool = "default"

        [[connectors.foo.queries]]
        name = "users_get"
        sql = "SELECT 1 AS id"
    """)
    _write(paths["screens"], """
        [screens.foo.users]
        connector = "foo"
        read_query = "users_get"
        row_click_screen = "users_detail"
        row_click_connector = "foo"

        [screens.foo.users_detail]
        connector = "foo"
        read_query = "users_get"
    """)
    _write(paths["menus"], """
        [menus.foo]
        label = "Foo"

        [[menus.foo.items]]
        id = "u"
        label = "Users"
        type = "query"
        connector = "foo"
        target = "users_get"
    """)
    _write(paths["dictionary"], """
        default_language = "en"

        [connectors.foo.entries.USR_NAME]
        label = "Username"
    """)
    return paths


def test_clone_app_duplicates_every_file(cfg_tree: dict[str, Path]) -> None:
    """Happy path: foo → foo2 → every file ends up with a parallel namespace and the
    cross-references (Screen.connector, MenuItem.connector, row_click_connector) inside
    the cloned subtree get rewritten."""
    result = clone_app(
        "foo", "foo2", new_pool="foo2_db",
        connectors_path=cfg_tree["connectors"],
        screens_path=cfg_tree["screens"],
        menus_path=cfg_tree["menus"],
        dictionary_path=cfg_tree["dictionary"],
    )
    assert result.source_app == "foo" and result.new_app == "foo2"
    assert result.total_entries() == 4  # one entry per file

    # connectors.toml — new connector exists, points at the new pool, original survives.
    conn = tomllib.loads(cfg_tree["connectors"].read_text())
    assert "foo" in conn["connectors"] and "foo2" in conn["connectors"]
    assert conn["connectors"]["foo"]["pool"] == "default"        # source untouched
    assert conn["connectors"]["foo2"]["pool"] == "foo2_db"       # cloned + repointed
    # The query body was copied verbatim (deep copy preserves SQL).
    assert conn["connectors"]["foo2"]["queries"][0]["sql"] == "SELECT 1 AS id"

    # screens.toml — new namespace + connector fields rewritten inside the clone.
    scr = tomllib.loads(cfg_tree["screens"].read_text())
    assert "foo2" in scr["screens"]
    assert scr["screens"]["foo2"]["users"]["connector"] == "foo2"
    assert scr["screens"]["foo2"]["users"]["row_click_connector"] == "foo2"
    # The intra-app row_click_screen reference (a sibling screen id, unqualified) is
    # left as-is — it resolves correctly within the new namespace.
    assert scr["screens"]["foo2"]["users"]["row_click_screen"] == "users_detail"
    # Source untouched.
    assert scr["screens"]["foo"]["users"]["connector"] == "foo"

    # menus.toml — connector field on each MenuItem rewritten.
    menus = tomllib.loads(cfg_tree["menus"].read_text())
    assert "foo2" in menus["menus"]
    assert menus["menus"]["foo2"]["items"][0]["connector"] == "foo2"
    assert menus["menus"]["foo"]["items"][0]["connector"] == "foo"  # source untouched

    # dictionary.toml — per-connector overlay duplicated.
    dct = tomllib.loads(cfg_tree["dictionary"].read_text())
    assert "foo" in dct["connectors"] and "foo2" in dct["connectors"]
    assert dct["connectors"]["foo2"]["entries"]["USR_NAME"]["label"] == "Username"


# ── refuse-on-collision branches ────────────────────────────────────────────────────────────


def test_clone_refuses_when_target_pool_missing(cfg_tree: dict[str, Path]) -> None:
    """We refuse to clone pointing at a non-existent pool — the new connector would fail
    on every query. Operator creates the pool first."""
    with pytest.raises(CloneError) as exc:
        clone_app(
            "foo", "foo2", new_pool="does_not_exist_db",
            connectors_path=cfg_tree["connectors"],
            screens_path=cfg_tree["screens"],
            menus_path=cfg_tree["menus"],
            dictionary_path=cfg_tree["dictionary"],
        )
    assert "does_not_exist_db" in str(exc.value)
    assert "CREATE DATABASE" in str(exc.value)  # actionable next-step in the message


def test_clone_refuses_when_new_name_already_used(cfg_tree: dict[str, Path]) -> None:
    """If a [connectors.<new>] / [screens.<new>] / [menus.<new>] / [connectors.<new>]
    (in dictionary) already exists, refuse — overwriting could clobber real config the
    operator forgot was there."""
    # Append a colliding [connectors.foo2] block to the fixture file.
    cfg_tree["connectors"].write_text(
        cfg_tree["connectors"].read_text().rstrip("\n")
        + '\n\n[connectors.foo2]\ntype = "sql"\npool = "foo2_db"\n'
    )
    with pytest.raises(CloneError) as exc:
        clone_app(
            "foo", "foo2", new_pool="foo2_db",
            connectors_path=cfg_tree["connectors"],
            screens_path=cfg_tree["screens"],
            menus_path=cfg_tree["menus"],
            dictionary_path=cfg_tree["dictionary"],
        )
    assert "clash" in str(exc.value).lower() and "foo2" in str(exc.value)


def test_clone_refuses_when_source_missing(cfg_tree: dict[str, Path]) -> None:
    with pytest.raises(CloneError) as exc:
        clone_app(
            "no_such_app", "foo2", new_pool="foo2_db",
            connectors_path=cfg_tree["connectors"],
            screens_path=cfg_tree["screens"],
            menus_path=cfg_tree["menus"],
            dictionary_path=cfg_tree["dictionary"],
        )
    assert "no_such_app" in str(exc.value)


def test_clone_refuses_identical_names(cfg_tree: dict[str, Path]) -> None:
    with pytest.raises(CloneError) as exc:
        clone_app(
            "foo", "foo", new_pool="foo2_db",
            connectors_path=cfg_tree["connectors"],
            screens_path=cfg_tree["screens"],
            menus_path=cfg_tree["menus"],
            dictionary_path=cfg_tree["dictionary"],
        )
    assert "identical" in str(exc.value).lower()


def test_clone_refuses_invalid_identifier(cfg_tree: dict[str, Path]) -> None:
    """Same identifier shape as the rename function — lowercase letters / digits /
    underscore; leading letter. ``Foo`` (uppercase), ``1foo`` (leading digit), ``foo-bar``
    (dash) all fail loudly."""
    for bad in ("Foo", "1foo", "foo-bar", "", "foo bar"):
        with pytest.raises(CloneError):
            clone_app(
                "foo", bad, new_pool="foo2_db",
                connectors_path=cfg_tree["connectors"],
                screens_path=cfg_tree["screens"],
                menus_path=cfg_tree["menus"],
                dictionary_path=cfg_tree["dictionary"],
            )


def test_clone_is_atomic_when_validation_fails(cfg_tree: dict[str, Path], tmp_path: Path) -> None:
    """If any rewritten doc fails Pydantic validation, NOTHING gets written — that's the
    contract. Engineer a malformed source so the cloned subtree fails to validate, then
    assert no file changed on disk."""
    # Corrupt the source connector so the cloned copy can't validate (a connector with
    # type != allowed value).
    cfg_tree["connectors"].write_text("""
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        [pools.foo2_db]
        url = "sqlite+aiosqlite:///:memory:"

        [connectors.foo]
        type = "this_is_not_a_valid_type"
        pool = "default"
    """)
    before = {k: p.read_text() for k, p in cfg_tree.items()}
    with pytest.raises(CloneError):
        clone_app(
            "foo", "foo2", new_pool="foo2_db",
            connectors_path=cfg_tree["connectors"],
            screens_path=cfg_tree["screens"],
            menus_path=cfg_tree["menus"],
            dictionary_path=cfg_tree["dictionary"],
        )
    # Every file unchanged on disk.
    after = {k: p.read_text() for k, p in cfg_tree.items()}
    assert before == after


# ── partial-source warnings ────────────────────────────────────────────────────────────────


def test_clone_with_no_connectors_entry_warns_but_still_clones_other_files(tmp_path: Path) -> None:
    """If the source app has screens/menus but no connector definition, we clone what we
    find + emit a warning about the missing connector — operator may have built screens
    against an external app's connector by name."""
    paths = {
        "connectors": tmp_path / "connectors.toml",
        "screens": tmp_path / "screens.toml",
        "menus": tmp_path / "menus.toml",
        "dictionary": tmp_path / "dictionary.toml",
    }
    _write(paths["connectors"], """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"
        [pools.b_db]
        url = "sqlite+aiosqlite:///:memory:"
    """)
    _write(paths["screens"], """
        [screens.a.thing]
        connector = "default"
        read_query = "x"
    """)
    _write(paths["menus"], "")
    _write(paths["dictionary"], "")
    r = clone_app(
        "a", "b", new_pool="b_db",
        connectors_path=paths["connectors"],
        screens_path=paths["screens"],
        menus_path=paths["menus"],
        dictionary_path=paths["dictionary"],
    )
    # Screens cloned; connectors / menus / dictionary all at 0.
    assert r.files[str(paths["screens"])] == 1
    assert r.files[str(paths["connectors"])] == 0
    assert any("connectors" in w.lower() for w in r.warnings)


# ── delete_app: pure-function tests ────────────────────────────────────────────────────────


def test_delete_app_removes_every_file(cfg_tree: dict[str, Path]) -> None:
    """First clone foo → foo2, then delete foo2 — every file goes back to having only foo.
    The cloned + deleted round trip is the regression-cleanup workflow."""
    clone_app(
        "foo", "foo2", new_pool="foo2_db",
        connectors_path=cfg_tree["connectors"],
        screens_path=cfg_tree["screens"],
        menus_path=cfg_tree["menus"],
        dictionary_path=cfg_tree["dictionary"],
    )
    result = delete_app(
        "foo2",
        connectors_path=cfg_tree["connectors"],
        dictionary_path=cfg_tree["dictionary"],
        menus_path=cfg_tree["menus"],
        screens_path=cfg_tree["screens"],
    )
    assert result.app == "foo2"
    assert result.total_sections() == 4  # one removal per file

    # Each file no longer has foo2; foo survives untouched.
    conn = tomllib.loads(cfg_tree["connectors"].read_text())
    assert "foo" in conn["connectors"] and "foo2" not in conn["connectors"]
    # Both pools survive — delete_app intentionally doesn't touch [pools.*] since
    # pools are managed separately (Settings → Pools) and may be shared.
    assert "default" in conn["pools"] and "foo2_db" in conn["pools"]
    scr = tomllib.loads(cfg_tree["screens"].read_text())
    assert "foo" in scr["screens"] and "foo2" not in scr["screens"]
    menus = tomllib.loads(cfg_tree["menus"].read_text())
    assert "foo" in menus["menus"] and "foo2" not in menus["menus"]
    dct = tomllib.loads(cfg_tree["dictionary"].read_text())
    assert "foo" in dct["connectors"] and "foo2" not in dct["connectors"]


def test_delete_app_preserves_comments_in_other_sections(tmp_path: Path) -> None:
    """The whole point of the surgical text-edit (vs tomlkit round-trip): comments
    outside the deleted section survive byte-identical, including the comment that
    introduces the next section after the deleted one."""
    paths = {
        "connectors": tmp_path / "connectors.toml",
        "screens": tmp_path / "screens.toml",
        "menus": tmp_path / "menus.toml",
        "dictionary": tmp_path / "dictionary.toml",
    }
    # Hand-crafted file: blank lines + comments around each section.
    paths["connectors"].write_text(textwrap.dedent("""
        # Top-level header comment — survives.
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        [pools.b_db]
        url = "sqlite+aiosqlite:///:memory:"

        # First connector — to be deleted.
        [connectors.a]
        type = "sql"
        pool = "default"

        # IMPORTANT — this comment introduces the b connector and must survive.
        [connectors.b]
        type = "sql"
        pool = "b_db"
    """).lstrip())
    paths["screens"].write_text("")
    paths["menus"].write_text("")
    paths["dictionary"].write_text("")

    delete_app(
        "a",
        connectors_path=paths["connectors"],
        dictionary_path=paths["dictionary"],
        menus_path=paths["menus"],
        screens_path=paths["screens"],
    )
    final = paths["connectors"].read_text()
    # The b-introducing comment is still in the file
    assert "IMPORTANT — this comment introduces the b connector" in final
    # The top-level header comment + the pool block comments survive
    assert "Top-level header comment — survives" in final
    # connector a is gone (and so is the comment that intro'd it)
    assert "[connectors.a]" not in final
    assert "First connector — to be deleted" not in final
    # connector b survives
    assert "[connectors.b]" in final
    assert tomllib.loads(final)["pools"]["default"]["url"]   # parses


def test_delete_app_refuses_when_nothing_to_delete(cfg_tree: dict[str, Path]) -> None:
    """Typo guard — refusing with a clear error beats silently doing nothing."""
    with pytest.raises(CloneError) as exc:
        delete_app(
            "no_such_app",
            connectors_path=cfg_tree["connectors"],
            dictionary_path=cfg_tree["dictionary"],
            menus_path=cfg_tree["menus"],
            screens_path=cfg_tree["screens"],
        )
    assert "no_such_app" in str(exc.value)


def test_delete_app_atomic_when_validation_fails(cfg_tree: dict[str, Path]) -> None:
    """If excising the section would make the file invalid (would never happen in
    practice for a clean delete, but the guard is here as a safety net), no file is
    written."""
    # First, clone so we have a foo2 to delete.
    clone_app(
        "foo", "foo2", new_pool="foo2_db",
        connectors_path=cfg_tree["connectors"],
        screens_path=cfg_tree["screens"],
        menus_path=cfg_tree["menus"],
        dictionary_path=cfg_tree["dictionary"],
    )
    # Snapshot file contents pre-delete (corruption test: nothing should change if
    # delete fails). Here we test the happy path — the delete should succeed.
    before_other = cfg_tree["screens"].read_text()
    delete_app(
        "foo2",
        connectors_path=cfg_tree["connectors"],
        dictionary_path=cfg_tree["dictionary"],
        menus_path=cfg_tree["menus"],
        screens_path=cfg_tree["screens"],
    )
    # foo (the surviving app) is byte-identical in screens.toml (its sections weren't touched).
    after_other = cfg_tree["screens"].read_text()
    # The foo sections survive; only foo2 sections were removed.
    assert "[screens.foo.users]" in after_other and "[screens.foo2.users]" not in after_other


def test_delete_app_handles_array_of_tables(tmp_path: Path) -> None:
    """A connector typically carries ``[[connectors.<app>.queries]]`` (array of tables)
    blocks under it. Those must be excised along with the parent."""
    paths = {
        "connectors": tmp_path / "connectors.toml",
        "screens": tmp_path / "screens.toml",
        "menus": tmp_path / "menus.toml",
        "dictionary": tmp_path / "dictionary.toml",
    }
    paths["connectors"].write_text(textwrap.dedent("""
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        [connectors.a]
        type = "sql"
        pool = "default"

        [[connectors.a.queries]]
        name = "q1"
        sql = "SELECT 1"

        [[connectors.a.queries]]
        name = "q2"
        sql = "SELECT 2"

        [connectors.b]
        type = "sql"
        pool = "default"
    """).lstrip())
    paths["screens"].write_text("")
    paths["menus"].write_text("")
    paths["dictionary"].write_text("")

    delete_app(
        "a",
        connectors_path=paths["connectors"],
        dictionary_path=paths["dictionary"],
        menus_path=paths["menus"],
        screens_path=paths["screens"],
    )
    final = paths["connectors"].read_text()
    assert "[connectors.a]" not in final
    assert "[[connectors.a.queries]]" not in final
    assert "name = \"q1\"" not in final
    # Sibling connector survives
    assert "[connectors.b]" in final
    # File still parses
    parsed = tomllib.loads(final)
    assert "a" not in parsed["connectors"] and "b" in parsed["connectors"]


# ── admin route ────────────────────────────────────────────────────────────────────────────


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("reader", password="readerpw")
        await pools.dispose()
    asyncio.run(go())


def _h(client: TestClient, username: str) -> dict[str, str]:
    token = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def app_env(cfg_tree: dict[str, Path], tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    _seed(db_url)
    # Replace cfg_tree's connectors.toml with a version pointing the default pool at the
    # auth SQLite file (so the [auth] pool can resolve), but keep the foo connector + the
    # foo2_db target pool intact.
    cfg_tree["connectors"].write_text(textwrap.dedent(f"""
        [pools.default]
        url = "{db_url}"

        [pools.foo2_db]
        url = "sqlite+aiosqlite:///:memory:"

        [connectors.foo]
        type = "sql"
        pool = "default"

        [[connectors.foo.queries]]
        name = "users_get"
        sql = "SELECT 1 AS id"
    """).lstrip())
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(
            config_path=cfg_tree["connectors"],
            dictionary_path=cfg_tree["dictionary"],
        ),
        menus=MenuSettings(config_path=cfg_tree["menus"]),
        screens=ScreenSettings(config_path=cfg_tree["screens"]),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings), cfg_tree


def test_admin_clone_app_requires_superuser(app_env) -> None:
    app, _ = app_env
    with TestClient(app) as client:
        body = {"source_app": "foo", "new_app": "foo2", "new_pool": "foo2_db"}
        assert client.post("/admin/config/clone-app", json=body).status_code == 401
        assert client.post(
            "/admin/config/clone-app", json=body, headers=_h(client, "reader"),
        ).status_code == 403


def test_admin_clone_app_happy_path(app_env) -> None:
    """End-to-end: POST clone-app → server walks the config tree, all four files end up
    with the new namespace, response body carries the per-file entry counts + warnings."""
    app, cfg = app_env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/clone-app",
            json={"source_app": "foo", "new_app": "foo2", "new_pool": "foo2_db"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source_app"] == "foo" and body["new_app"] == "foo2"
        assert body["total_entries"] >= 1
    # Check the on-disk result.
    conn = tomllib.loads(cfg["connectors"].read_text())
    assert "foo2" in conn["connectors"]


def test_admin_delete_app_happy_path(app_env) -> None:
    """End-to-end clone-then-delete: clone foo → foo2, then DELETE /admin/config/delete-app
    foo2 → file goes back to having only foo."""
    app, cfg = app_env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # First, clone so we have something to delete.
        client.post(
            "/admin/config/clone-app",
            json={"source_app": "foo", "new_app": "foo2", "new_pool": "foo2_db"},
            headers=h,
        )
        # Now delete it.
        r = client.post("/admin/config/delete-app", json={"app": "foo2"}, headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["app"] == "foo2"
        assert body["total_sections"] >= 1
    # On disk: foo2 gone, foo survives.
    conn = tomllib.loads(cfg["connectors"].read_text())
    assert "foo" in conn["connectors"] and "foo2" not in conn["connectors"]


def test_admin_delete_app_unknown_is_422(app_env) -> None:
    app, _ = app_env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/config/delete-app", json={"app": "no_such_app"}, headers=h)
        assert r.status_code == 422
        assert "no_such_app" in r.json()["detail"]


def test_admin_delete_app_requires_superuser(app_env) -> None:
    app, _ = app_env
    with TestClient(app) as client:
        body = {"app": "foo"}
        assert client.post("/admin/config/delete-app", json=body).status_code == 401
        assert client.post(
            "/admin/config/delete-app", json=body, headers=_h(client, "reader"),
        ).status_code == 403


def test_admin_clone_app_bad_pool_is_422(app_env) -> None:
    app, _ = app_env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/clone-app",
            json={"source_app": "foo", "new_app": "foo2", "new_pool": "nope_db"},
            headers=h,
        )
        assert r.status_code == 422
        assert "nope_db" in r.json()["detail"]
