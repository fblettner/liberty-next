"""Tests for :mod:`liberty.web.rename` — the cross-file rename of a top-level config key.

Two layers:

* **Pure function** tests on :func:`rename_connector` against a tmp_path of TOML files. These
  cover every cross-file reference + edge cases (collision, invalid name, etc.).
* **Endpoint** tests on ``POST /admin/config/rename`` — auth gating, 422 on invalid input,
  happy-path that all referenced files get the new name.
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
from liberty.web.rename import (
    RenameError,
    rename_connector,
    rename_dictionary_entry,
    rename_lookup,
    rename_screen_app,
    rename_sequence,
)


# ── pure-function tests ────────────────────────────────────────────────────────────────────


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


@pytest.fixture
def cfg_tree(tmp_path: Path) -> dict[str, Path]:
    """A small but representative config tree. Connector ``foo`` lives in connectors.toml and
    is referenced from every other file (screens.toml: Screen.connector + action connector +
    row_click_connector; menus.toml: MenuItem.connector; dictionary.toml: per-connector scope
    + lookup.connector; dashboards.toml: widget connector; charts.toml: chart connector). A
    *second* connector ``bar`` carries a ``connector = "foo"`` reference in one of its lookups
    so we exercise the cross-connector LookupDef.connector path too."""
    paths = {
        "connectors": tmp_path / "connectors.toml",
        "screens": tmp_path / "screens.toml",
        "menus": tmp_path / "menus.toml",
        "dictionary": tmp_path / "dictionary.toml",
        "dashboards": tmp_path / "dashboards.toml",
        "charts": tmp_path / "charts.toml",
    }
    _write(paths["connectors"], """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        # the connector to rename — its top-level key is the rename target
        [connectors.foo]
        type = "sql"
        pool = "default"

        [[connectors.foo.queries]]
        name = "users_get"
        sql = "SELECT 1 AS id"

        [[connectors.foo.queries]]
        name = "users_put"
        sql = "UPDATE users SET name = :name WHERE id = :id"
        writable = true

        # a second connector — its dictionary-scoped lookups (in dictionary.toml below) carry
        # a `connector = "foo"` cross-reference we want the rename to update.
        [connectors.bar]
        type = "sql"
        pool = "default"

        [[connectors.bar.queries]]
        name = "things_get"
        sql = "SELECT 2 AS id"
    """)
    _write(paths["screens"], """
        [screens.foo.users]
        connector = "foo"           # the typical case — Screen.connector
        read_query = "users_get"
        update_query = "users_put"

        [[screens.foo.users.row_menu]]
        type = "navigate"
        connector = "foo"           # action variants — NavigateAction.connector
        to = "users_get"
        id = "drill"

        [[screens.foo.users.row_menu]]
        type = "run_query"
        connector = "foo"           # RunQueryAction.connector
        query = "users_put"
        id = "touch"

        [[screens.foo.users.dialog.on_save]]
        type = "run_query"
        connector = "foo"
        query = "users_put"
        id = "save_extra"

        [screens.foo.parent]
        connector = "foo"
        read_query = "users_get"
        # Promoted row-click pattern — points at another screen on the same connector.
        row_click_screen = "users"
        row_click_connector = "foo"
    """)
    _write(paths["menus"], """
        [menus.foo]
        label = "Foo App"

        [[menus.foo.items]]
        id = "u"
        label = "Users"
        type = "query"
        connector = "foo"           # MenuItem.connector
        target = "users_get"

        [[menus.foo.items]]
        id = "p"
        label = "Parent"
        type = "query"
        connector = "foo"
        target = "users_get"
    """)
    _write(paths["dictionary"], """
        default_language = "en"

        [entries.NAME]
        label = "Name"

        [lookups.colors]
        connector = "foo"           # shared lookup pointing at a query on `foo`
        query = "users_get"
        value = "id"
        label = "name"

        [connectors.foo.entries.USR_NAME]
        label = "Username"          # per-connector scope — renames with the connector

        # Per-connector-scoped lookup under `bar` whose query lives on `foo` — exercises the
        # cross-connector LookupDef.connector path inside a scope.
        [connectors.bar.lookups.user_names]
        description = "Resolve user names from foo"
        connector = "foo"
        query = "users_get"
        value = "id"
        label = "name"
    """)
    _write(paths["dashboards"], """
        [dashboards.overview]
        label = "Overview"

        [[dashboards.overview.widgets]]
        type = "chart"
        connector = "foo"           # ChartWidget inline mode
        query = "users_get"

        [dashboards.overview.widgets.spec]
        type = "bar"
        x = "id"
        y = ["id"]

        [[dashboards.overview.widgets]]
        type = "kpi"
        connector = "foo"           # KpiWidget — required field
        query = "users_get"
        column = "id"
    """)
    _write(paths["charts"], """
        [charts.users_per_day]
        label = "Users per day"
        connector = "foo"           # ChartDef.connector — required
        query = "users_get"

        [charts.users_per_day.spec]
        type = "bar"
        x = "id"
        y = ["id"]
    """)
    return paths


def test_rename_connector_updates_every_cross_file_reference(cfg_tree: dict[str, Path]) -> None:
    """The happy-path: ``foo`` → ``foo2`` updates every reference across six files. We assert
    the result counts + spot-check that the new name landed everywhere the rename promises."""
    result = rename_connector(
        "foo", "foo2",
        connectors_path=cfg_tree["connectors"],
        screens_path=cfg_tree["screens"],
        menus_path=cfg_tree["menus"],
        dictionary_path=cfg_tree["dictionary"],
        dashboards_path=cfg_tree["dashboards"],
        charts_path=cfg_tree["charts"],
    )
    assert result.kind == "connector"
    assert result.old_name == "foo" and result.new_name == "foo2"
    # Touched refs: 1 (connectors.toml — top-level [connectors.foo] rename)
    #             + 5 in screens (Screen.connector ×2 + NavigateAction + RunQueryAction + on_save
    #               run_query + 1 row_click_connector — counted breakdown below)
    #             + 2 in menus (two MenuItem.connector)
    #             + 3 in dictionary (1 [connectors.foo] scope + 1 lookups.colors.connector
    #               + 1 connectors.bar.lookups.user_names.connector cross-ref)
    #             + 2 in dashboards (2 widget connectors)
    #             + 1 in charts (the only ChartDef)
    assert result.files[str(cfg_tree["connectors"])] == 1
    assert result.files[str(cfg_tree["screens"])] == 6      # users(1) + 3 row_menu/on_save + parent(1) + row_click(1)
    assert result.files[str(cfg_tree["menus"])] == 2
    assert result.files[str(cfg_tree["dictionary"])] == 3
    assert result.files[str(cfg_tree["dashboards"])] == 2
    assert result.files[str(cfg_tree["charts"])] == 1

    # connectors.toml — top-level key renamed.
    d = tomllib.loads(cfg_tree["connectors"].read_text())
    assert "foo" not in d["connectors"] and "foo2" in d["connectors"]

    # screens.toml — every connector + row_click_connector field carries the new name.
    d = tomllib.loads(cfg_tree["screens"].read_text())
    users = d["screens"]["foo"]["users"]
    assert users["connector"] == "foo2"
    assert all(a["connector"] == "foo2" for a in users["row_menu"])
    assert users["dialog"]["on_save"][0]["connector"] == "foo2"
    assert d["screens"]["foo"]["parent"]["row_click_connector"] == "foo2"

    # menus.toml — every leaf's connector updated; the [menus.foo] app key is *not* renamed
    # (that's a separate concept and would need a 'rename_app' op).
    d = tomllib.loads(cfg_tree["menus"].read_text())
    assert "foo" in d["menus"]                                     # menu app key untouched
    assert all(i.get("connector") == "foo2" for i in d["menus"]["foo"]["items"])

    # dictionary.toml — per-connector scope renamed; shared lookup's connector updated;
    # the cross-connector lookup under `bar`'s scope also updated.
    d = tomllib.loads(cfg_tree["dictionary"].read_text())
    assert "foo" not in d["connectors"] and "foo2" in d["connectors"]
    assert d["lookups"]["colors"]["connector"] == "foo2"
    assert d["connectors"]["bar"]["lookups"]["user_names"]["connector"] == "foo2"

    # dashboards.toml — both widgets updated.
    d = tomllib.loads(cfg_tree["dashboards"].read_text())
    widgets = d["dashboards"]["overview"]["widgets"]
    assert all(w["connector"] == "foo2" for w in widgets)

    # charts.toml — the saved chart's connector updated.
    d = tomllib.loads(cfg_tree["charts"].read_text())
    assert d["charts"]["users_per_day"]["connector"] == "foo2"


def test_rename_connector_warns_when_matching_menu_app_key_exists(cfg_tree: dict[str, Path]) -> None:
    """When the connector being renamed shares its name with a ``[menus.<app>]`` block, the
    rename leaves the menu app key alone (apps and connectors are distinct concepts) but
    surfaces a warning so the operator notices the mismatch and can decide what to do."""
    result = rename_connector(
        "foo", "foo2",
        connectors_path=cfg_tree["connectors"],
        screens_path=cfg_tree["screens"],
        menus_path=cfg_tree["menus"],
        dictionary_path=cfg_tree["dictionary"],
        dashboards_path=cfg_tree["dashboards"],
        charts_path=cfg_tree["charts"],
    )
    assert any("[menus.foo]" in w for w in result.warnings)


def test_rename_connector_rejects_collision(cfg_tree: dict[str, Path]) -> None:
    """Renaming to a name that already exists must fail. The error message names the conflict."""
    with pytest.raises(RenameError, match="already exists"):
        rename_connector(
            "foo", "bar",                                          # `bar` is already defined
            connectors_path=cfg_tree["connectors"],
            screens_path=cfg_tree["screens"],
            menus_path=cfg_tree["menus"],
            dictionary_path=cfg_tree["dictionary"],
            dashboards_path=cfg_tree["dashboards"],
            charts_path=cfg_tree["charts"],
        )
    # And the on-disk files weren't touched.
    d = tomllib.loads(cfg_tree["connectors"].read_text())
    assert "foo" in d["connectors"] and "bar" in d["connectors"]


def test_rename_connector_rejects_invalid_identifier(cfg_tree: dict[str, Path]) -> None:
    """v2 keys must match ``[a-z][a-z0-9_]*``. ``Foo-Bar`` / ``1foo`` / ``foo bar`` / empty —
    all rejected. (v1 was free-form; v2 sticks to a consistent identifier shape.)"""
    for bad in ("Foo", "1foo", "foo-bar", "", "foo bar"):
        with pytest.raises(RenameError, match="invalid"):
            rename_connector(
                "foo", bad,
                connectors_path=cfg_tree["connectors"],
                screens_path=cfg_tree["screens"],
                menus_path=cfg_tree["menus"],
                dictionary_path=cfg_tree["dictionary"],
                dashboards_path=cfg_tree["dashboards"],
                charts_path=cfg_tree["charts"],
            )


def test_rename_connector_rejects_missing_connector(cfg_tree: dict[str, Path]) -> None:
    """If the connector doesn't exist in connectors.toml the rename can't proceed — nothing to
    rename. Surface a useful error so the operator notices a typo."""
    with pytest.raises(RenameError, match="not found"):
        rename_connector(
            "nonexistent", "anything",
            connectors_path=cfg_tree["connectors"],
            screens_path=cfg_tree["screens"],
            menus_path=cfg_tree["menus"],
            dictionary_path=cfg_tree["dictionary"],
        )


def test_rename_connector_rejects_self_rename(cfg_tree: dict[str, Path]) -> None:
    """``foo → foo`` is a no-op; the helper rejects it loudly so the caller doesn't think
    something happened when nothing did."""
    with pytest.raises(RenameError, match="identical"):
        rename_connector(
            "foo", "foo",
            connectors_path=cfg_tree["connectors"],
            screens_path=cfg_tree["screens"],
            menus_path=cfg_tree["menus"],
            dictionary_path=cfg_tree["dictionary"],
        )


def test_rename_connector_skips_missing_optional_files(tmp_path: Path) -> None:
    """Dashboards / charts are optional config files — the rename should silently skip them
    when the path is None or the file doesn't exist. The required files (connectors, screens,
    menus, dictionary) still get touched if they exist."""
    paths = {
        "connectors": tmp_path / "connectors.toml",
        "screens": tmp_path / "screens.toml",
        "menus": tmp_path / "menus.toml",
        "dictionary": tmp_path / "dictionary.toml",
    }
    _write(paths["connectors"], """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        [connectors.solo]
        type = "sql"
        pool = "default"
    """)
    _write(paths["screens"], "")          # empty files — perfectly valid
    _write(paths["menus"], "")
    _write(paths["dictionary"], "")
    result = rename_connector(
        "solo", "solo2",
        connectors_path=paths["connectors"],
        screens_path=paths["screens"],
        menus_path=paths["menus"],
        dictionary_path=paths["dictionary"],
        # dashboards_path / charts_path left None — should not raise.
    )
    assert result.total_refs() == 1
    d = tomllib.loads(paths["connectors"].read_text())
    assert "solo2" in d["connectors"]


# ── endpoint tests (POST /admin/config/rename) ─────────────────────────────────────────────

JWT_SECRET = "rename-test-secret"


def _toml(db_url: str) -> str:
    return textwrap.dedent(f"""
        [pools.default]
        url = "{db_url}"

        [connectors.foo]
        type = "sql"
        pool = "default"

        [[connectors.foo.queries]]
        name = "answer"
        sql = "SELECT 42 AS answer"
    """).lstrip()


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


@pytest.fixture
def env(tmp_path: Path):
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
    )
    return create_app(settings), conn_toml


def _h(client: TestClient, username: str) -> dict[str, str]:
    token = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_rename_endpoint_requires_superuser(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        body = {"kind": "connector", "old_name": "foo", "new_name": "foo2"}
        assert client.post("/admin/config/rename", json=body).status_code == 401
        assert client.post("/admin/config/rename", json=body, headers=_h(client, "reader")).status_code == 403


def test_rename_endpoint_renames_connector(env) -> None:
    app, conn_toml = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/rename",
            json={"kind": "connector", "old_name": "foo", "new_name": "foo2"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["old_name"] == "foo" and data["new_name"] == "foo2"
        assert data["total_refs"] >= 1
        # The file on disk reflects the new name.
        assert "[connectors.foo2]" in conn_toml.read_text()
        assert "[connectors.foo]" not in conn_toml.read_text()

        # The rename does NOT auto-reload; the registry still has `foo` until we call /admin/reload.
        # After reload the new name is live.
        r = client.post("/admin/reload", headers=h)
        assert r.status_code == 200
        names = {c["name"] for c in client.get("/api/connectors", headers=h).json()["connectors"]}
        assert names == {"foo2"}


def test_rename_endpoint_returns_422_on_unknown_connector(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/rename",
            json={"kind": "connector", "old_name": "missing", "new_name": "x"},
            headers=h,
        )
        assert r.status_code == 422
        assert "not found" in r.json()["detail"]


def test_rename_endpoint_returns_422_on_collision(env) -> None:
    app, conn_toml = env
    # Make a second connector so the rename target collides.
    conn_toml.write_text(conn_toml.read_text() + textwrap.dedent("""
        [connectors.bar]
        type = "sql"
        pool = "default"
    """))
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/rename",
            json={"kind": "connector", "old_name": "foo", "new_name": "bar"},
            headers=h,
        )
        assert r.status_code == 422
        assert "already exists" in r.json()["detail"]


def test_rename_endpoint_rejects_unknown_kind(env) -> None:
    """Unsupported kinds surface 422 with a useful message rather than silently no-oping. The
    full list of supported kinds is included in the error message so the operator sees what
    they could have picked."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/rename",
            json={"kind": "nonexistent", "old_name": "x", "new_name": "y"},
            headers=h,
        )
        assert r.status_code == 422
        assert "not supported" in r.json()["detail"]


# ── sequence / lookup rename ────────────────────────────────────────────────────────────────


@pytest.fixture
def dict_with_sequence_and_lookup(tmp_path: Path) -> Path:
    """A dictionary doc with both shared and connector-scoped sequences + lookups, each
    cross-referenced from matching DictionaryEntry rules_values. Exercises the in-scope
    rules_values update + leaves cross-scope refs alone."""
    p = tmp_path / "dictionary.toml"
    _write(p, """
        default_language = "en"

        # shared entries — reference the shared sequences/lookups
        [entries.APPS_ID]
        rules = "SEQUENCE"
        rules_values = "get_apps_id"   # → renames if we rename sequence "get_apps_id"

        [entries.STATUS]
        rules = "LOOKUP"
        rules_values = "user_status"   # → renames if we rename lookup "user_status"

        [entries.STATIC_BOOL]
        rules = "BOOLEAN"
        rules_values = "Y"             # NOT touched by sequence/lookup rename — different rule

        [sequences.get_apps_id]
        description = "Next APPS_ID"
        query = "next_apps_id"

        [lookups.user_status]
        description = "User status code → label"
        query = "user_statuses_get"
        value = "code"
        label = "label"

        # per-connector overlay — sequence with the same id "1" in shared scope and scoped
        # scope. Scope-aware rename must only touch the requested scope.
        [connectors.nomasx1.entries.NSX_ID]
        rules = "SEQUENCE"
        rules_values = "get_apps_id"   # scoped entry referencing scoped sequence

        [connectors.nomasx1.sequences.get_apps_id]
        description = "nomasx1's own APPS_ID generator"
        query = "next_apps_id_nomasx1"

        [connectors.nomasx1.lookups.user_status]
        description = "nomasx1's status overrides"
        query = "user_statuses_get_nomasx1"
        value = "code"
        label = "label"
    """)
    return p


def test_rename_sequence_shared_updates_matching_entry_rules_values(dict_with_sequence_and_lookup: Path) -> None:
    """Renaming a shared sequence updates the shared ``DictionaryEntry.rules_values`` reference
    in the same scope; the per-connector-scoped sequence + its referencing scoped entry stay
    untouched (different scope, narrower rename)."""
    p = dict_with_sequence_and_lookup
    result = rename_sequence("get_apps_id", "next_apps_id_seq", dictionary_path=p)
    assert result.kind == "sequence"
    assert result.total_refs() == 2          # 1 sequence key + 1 entry rules_values
    d = tomllib.loads(p.read_text())
    assert "get_apps_id" not in d["sequences"] and "next_apps_id_seq" in d["sequences"]
    assert d["entries"]["APPS_ID"]["rules_values"] == "next_apps_id_seq"
    # Per-connector overlay untouched — scope was None (= shared).
    assert d["connectors"]["nomasx1"]["entries"]["NSX_ID"]["rules_values"] == "get_apps_id"
    assert "get_apps_id" in d["connectors"]["nomasx1"]["sequences"]


def test_rename_sequence_scoped_only_touches_that_scope(dict_with_sequence_and_lookup: Path) -> None:
    """``scope='nomasx1'`` renames the per-connector overlay; the shared shared sequence with
    the same id is left alone (sequences in different scopes can carry the same id but mean
    different things — a v1 migration produces exactly that shape)."""
    p = dict_with_sequence_and_lookup
    result = rename_sequence("get_apps_id", "nsx_apps_seq", dictionary_path=p, scope="nomasx1")
    assert result.total_refs() == 2          # scoped sequence key + scoped entry rules_values
    d = tomllib.loads(p.read_text())
    # Scoped side renamed.
    nsx = d["connectors"]["nomasx1"]
    assert "get_apps_id" not in nsx["sequences"] and "nsx_apps_seq" in nsx["sequences"]
    assert nsx["entries"]["NSX_ID"]["rules_values"] == "nsx_apps_seq"
    # Shared side untouched.
    assert "get_apps_id" in d["sequences"]
    assert d["entries"]["APPS_ID"]["rules_values"] == "get_apps_id"


def test_rename_sequence_rejects_collision(dict_with_sequence_and_lookup: Path) -> None:
    """Adding a second shared sequence with the target name should make the rename fail loudly
    rather than silently merging."""
    p = dict_with_sequence_and_lookup
    p.write_text(p.read_text() + "\n[sequences.other_seq]\ndescription = 'noop'\nquery = 'q'\n")
    with pytest.raises(RenameError, match="already exists"):
        rename_sequence("get_apps_id", "other_seq", dictionary_path=p)


def test_rename_sequence_rejects_missing_sequence(dict_with_sequence_and_lookup: Path) -> None:
    with pytest.raises(RenameError, match="not found"):
        rename_sequence("missing", "x", dictionary_path=dict_with_sequence_and_lookup)


def test_rename_sequence_rejects_missing_scope(dict_with_sequence_and_lookup: Path) -> None:
    """Scope must already exist in the dictionary — operator can't add a new scope via a
    rename (that's an add operation, not a rename)."""
    with pytest.raises(RenameError, match="scope.*not found"):
        rename_sequence(
            "get_apps_id", "x",
            dictionary_path=dict_with_sequence_and_lookup,
            scope="nonexistent",
        )


def test_rename_lookup_updates_matching_entry_rules_values(dict_with_sequence_and_lookup: Path) -> None:
    """Lookups follow the same rules — referencing rules is ``LOOKUP`` (not SEQUENCE/NN) so
    only LOOKUP-typed entries' rules_values get updated; the BOOLEAN entry stays untouched."""
    p = dict_with_sequence_and_lookup
    result = rename_lookup("user_status", "user_status_codes", dictionary_path=p)
    assert result.total_refs() == 2          # 1 lookup key + 1 entry rules_values
    d = tomllib.loads(p.read_text())
    assert "user_status" not in d["lookups"] and "user_status_codes" in d["lookups"]
    assert d["entries"]["STATUS"]["rules_values"] == "user_status_codes"
    # BOOLEAN entry untouched — different rule kind.
    assert d["entries"]["STATIC_BOOL"]["rules_values"] == "Y"


# ── screen-app rename ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def screens_and_menus(tmp_path: Path) -> dict[str, Path]:
    """Minimal screens.toml + menus.toml sharing an app name ``foo`` — the typical case the
    rename targets."""
    paths = {"screens": tmp_path / "screens.toml", "menus": tmp_path / "menus.toml"}
    _write(paths["screens"], """
        [screens.foo.users]
        read_query = "users_get"
    """)
    _write(paths["menus"], """
        [menus.foo]
        label = "Foo App"

        [[menus.foo.items]]
        id = "u"
        label = "Users"
        type = "query"
        target = "users_get"
    """)
    return paths


def test_rename_screen_app_renames_both_screens_and_menus(screens_and_menus: dict[str, Path]) -> None:
    """The screen-app rename touches *both* files when they share the app key — the typical
    case in real deployments (NOMASX1's app is named ``nomasx1`` everywhere)."""
    result = rename_screen_app(
        "foo", "foo2",
        screens_path=screens_and_menus["screens"],
        menus_path=screens_and_menus["menus"],
    )
    assert result.files[str(screens_and_menus["screens"])] == 1
    assert result.files[str(screens_and_menus["menus"])] == 1
    assert result.warnings == []
    s = tomllib.loads(screens_and_menus["screens"].read_text())
    m = tomllib.loads(screens_and_menus["menus"].read_text())
    assert "foo" not in s["screens"] and "foo2" in s["screens"]
    assert "foo" not in m["menus"] and "foo2" in m["menus"]


def test_rename_screen_app_warns_when_no_matching_menu(tmp_path: Path) -> None:
    """Operator may rename an app whose menu sibling has a different name. The rename touches
    screens.toml + emits a warning so the operator notices the menu wasn't touched."""
    s_path = tmp_path / "screens.toml"
    m_path = tmp_path / "menus.toml"
    _write(s_path, """
        [screens.foo.users]
        read_query = "users_get"
    """)
    _write(m_path, """
        # Menu uses a different app key (an alias).
        [menus.bar]
        label = "Bar"
    """)
    result = rename_screen_app("foo", "foo2", screens_path=s_path, menus_path=m_path)
    assert result.files[str(s_path)] == 1
    assert result.files[str(m_path)] == 0
    assert any("no matching" in w for w in result.warnings)


def test_rename_screen_app_rejects_missing_app(screens_and_menus: dict[str, Path]) -> None:
    with pytest.raises(RenameError, match="not found"):
        rename_screen_app(
            "nonexistent", "x",
            screens_path=screens_and_menus["screens"],
            menus_path=screens_and_menus["menus"],
        )


def test_rename_screen_app_rejects_screens_collision(screens_and_menus: dict[str, Path]) -> None:
    """Two screen apps sharing the target name would clobber each other — refuse."""
    p = screens_and_menus["screens"]
    p.write_text(p.read_text() + "\n[screens.bar.x]\nread_query = 'x_get'\n")
    with pytest.raises(RenameError, match="already exists"):
        rename_screen_app(
            "foo", "bar",
            screens_path=p,
            menus_path=screens_and_menus["menus"],
        )


# ── dictionary entry rename ─────────────────────────────────────────────────────────────────


@pytest.fixture
def dict_with_entry_refs(tmp_path: Path) -> dict[str, Path]:
    """A dictionary with entry ``APPS_ID`` referenced from a SequenceDef.dd_id and a
    LookupDef.return_params, plus a screens.toml that uses APPS_ID as a column.dd AND as a
    prompt_field.dd. Renaming the entry should ripple through every reference."""
    d = tmp_path / "dictionary.toml"
    s = tmp_path / "screens.toml"
    _write(d, """
        default_language = "en"

        [entries.APPS_ID]
        label = "Application ID"
        rules = "SEQUENCE"
        rules_values = "1"

        [entries.STATUS]
        label = "Status"

        [sequences.1]
        description = "Next APPS_ID"
        query = "next_apps_id"
        dd_id = "APPS_ID"              # → updates when entry APPS_ID is renamed

        [lookups.1]
        description = "App by id"
        query = "apps_get"
        value = "id"
        label = "label"
        return_params = ["APPS_ID", "STATUS"]   # → APPS_ID entry updates this list
    """)
    _write(s, """
        # Two screens — both reference APPS_ID as a column.dd; one also has a prompt_field.dd.
        [screens.app1.users]
        read_query = "users_get"

        [[screens.app1.users.columns]]
        name = "USR_APPS_ID"
        dd = "APPS_ID"                 # ColumnHint.dd reference

        [[screens.app1.users.columns]]
        name = "STATUS"
        dd = "STATUS"

        [[screens.app1.users.actions]]
        id = "drill"
        type = "navigate"
        to = "targets_get"

        [[screens.app1.users.actions.prompt_fields]]
        name = "APPS_ID_INPUT"
        dd = "APPS_ID"                 # PromptField.dd reference
    """)
    return {"dictionary": d, "screens": s}


def test_rename_dictionary_entry_updates_dict_and_screens(dict_with_entry_refs: dict[str, Path]) -> None:
    """Renaming entry ``APPS_ID`` → ``APP_ID``: updates the dictionary key, SequenceDef.dd_id,
    one slot in LookupDef.return_params, plus 2 references in screens.toml (one ColumnHint.dd,
    one PromptField.dd)."""
    result = rename_dictionary_entry(
        "APPS_ID", "APP_ID",
        dictionary_path=dict_with_entry_refs["dictionary"],
        screens_path=dict_with_entry_refs["screens"],
    )
    assert result.kind == "dictionary_entry"
    # Dictionary: 1 entry key + 1 SequenceDef.dd_id + 1 LookupDef.return_params slot = 3
    assert result.files[str(dict_with_entry_refs["dictionary"])] == 3
    # Screens: 1 ColumnHint.dd + 1 PromptField.dd = 2
    assert result.files[str(dict_with_entry_refs["screens"])] == 2

    d = tomllib.loads(dict_with_entry_refs["dictionary"].read_text())
    assert "APPS_ID" not in d["entries"] and "APP_ID" in d["entries"]
    assert d["sequences"]["1"]["dd_id"] == "APP_ID"
    assert d["lookups"]["1"]["return_params"] == ["APP_ID", "STATUS"]

    s = tomllib.loads(dict_with_entry_refs["screens"].read_text())
    cols = s["screens"]["app1"]["users"]["columns"]
    assert cols[0]["dd"] == "APP_ID"
    assert cols[1]["dd"] == "STATUS"     # unrelated entry — untouched
    prompt = s["screens"]["app1"]["users"]["actions"][0]["prompt_fields"][0]
    assert prompt["dd"] == "APP_ID"


def test_rename_dictionary_entry_rejects_missing_entry(dict_with_entry_refs: dict[str, Path]) -> None:
    with pytest.raises(RenameError, match="not found"):
        rename_dictionary_entry(
            "MISSING", "X",
            dictionary_path=dict_with_entry_refs["dictionary"],
            screens_path=dict_with_entry_refs["screens"],
        )


def test_rename_dictionary_entry_rejects_collision(dict_with_entry_refs: dict[str, Path]) -> None:
    """``APPS_ID → STATUS`` clobbers the existing STATUS entry — refuse."""
    with pytest.raises(RenameError, match="already exists"):
        rename_dictionary_entry(
            "APPS_ID", "STATUS",
            dictionary_path=dict_with_entry_refs["dictionary"],
            screens_path=dict_with_entry_refs["screens"],
        )


# ── endpoint dispatch on the new kinds ──────────────────────────────────────────────────────


def test_rename_endpoint_dispatches_sequence(env, tmp_path: Path) -> None:
    """Endpoint accepts ``kind=sequence`` and forwards to rename_sequence — minimal sanity
    check that the dispatcher's branch wiring is correct."""
    # Write a dictionary file with a sequence; the env fixture already wired settings to
    # the same tmp_path so the endpoint sees it.
    dict_path = tmp_path / "dictionary.toml"
    _write(dict_path, """
        [sequences.seq1]
        description = "test"
        query = "next_id"
    """)
    app, _ = env
    # The env fixture's dictionary_path resolves via _dictionary_path() to tmp_path/dictionary.toml.
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/rename",
            json={"kind": "sequence", "old_name": "seq1", "new_name": "seq2"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["old_name"] == "seq1" and data["new_name"] == "seq2"
        assert "seq2" in dict_path.read_text()
        assert "seq1" not in dict_path.read_text()


def test_rename_endpoint_dispatches_screen_app(env, tmp_path: Path) -> None:
    """Endpoint accepts ``kind=screen_app`` and forwards to rename_screen_app."""
    s_path = tmp_path / "screens.toml"
    _write(s_path, """
        [screens.foo.users]
        read_query = "u"
    """)
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/rename",
            json={"kind": "screen_app", "old_name": "foo", "new_name": "foo2"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert "[screens.foo2.users]" in s_path.read_text()


def test_rename_endpoint_passes_scope_through(env, tmp_path: Path) -> None:
    """Endpoint forwards ``scope`` to the underlying rename function for scope-aware kinds."""
    dict_path = tmp_path / "dictionary.toml"
    _write(dict_path, """
        [sequences.s1]
        query = "q1"

        [connectors.foo.sequences.s1]
        query = "q2"
    """)
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/rename",
            json={"kind": "sequence", "old_name": "s1", "new_name": "s1_renamed", "scope": "foo"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        d = tomllib.loads(dict_path.read_text())
        # Scoped side renamed; shared side untouched.
        assert "s1" not in d["connectors"]["foo"]["sequences"]
        assert "s1_renamed" in d["connectors"]["foo"]["sequences"]
        assert "s1" in d["sequences"]
