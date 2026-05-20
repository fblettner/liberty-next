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
from liberty.web.rename import RenameError, rename_connector


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
    """Only ``connector`` is supported for now; the others surface 422 with a useful message
    rather than silently no-oping or crashing."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/config/rename",
            json={"kind": "sequence", "old_name": "x", "new_name": "y"},
            headers=h,
        )
        assert r.status_code == 422
        assert "not supported" in r.json()["detail"]
