"""``/api/screens`` route tests — permission-filtered listing, single-screen drill-in,
hot-reload, and the ``/info`` summary. Mirrors the layout of ``test_web_menus.py`` so the
two stay easy to keep in sync."""
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
    NotifySettings,
    AppSettings,
    AuthSettings,
    ConnectorSettings,
    ScreenSettings,
    Settings,
)
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-screens-test-secret"


def _connectors_toml(db_url: str) -> str:
    return textwrap.dedent(
        f"""
        [pools.default]
        url = "{db_url}"

        [connectors.app1]
        type = "sql"
        pool = "default"
        [[connectors.app1.queries]]
        name = "users_get"
        sql = "SELECT 1 AS id"
        [[connectors.app1.queries]]
        name = "secret_get"
        sql = "SELECT 1 AS id"

        [[connectors.app1.queries]]
        name = "periods_get"
        sql = "SELECT 1 AS id"

        [[connectors.app1.queries]]
        name = "periods_put"
        sql = "SELECT 1 AS id"
        writable = true
        # A cross-pool screen target — same DB pool, different connector name. The screen sets
        # `connector = "external"` so its permission gates on `sql:external:...` not `sql:app1:...`.
        [connectors.external]
        type = "sql"
        pool = "default"
        [[connectors.external.queries]]
        name = "things_get"
        sql = "SELECT 1 AS id"
        """
    )


def _screens_toml() -> str:
    return textwrap.dedent(
        """
        [screens.app1.users]
        label = "Users"
        description = "User accounts"
        read_query = "users_get"
        update_query = "users_get"
        audit_table = "AUD_USERS"

        [screens.app1.users.dialog]
        title = "User"

        [[screens.app1.users.dialog.tabs]]
        id = "general"
        label = "General"

        [[screens.app1.users.dialog.tabs.fields]]
        name = "USR_ID"

        # A screen whose read query lives on a different connector (cross-pool).
        [screens.app1.things]
        label = "Things"
        connector = "external"
        read_query = "things_get"

        # A screen the "user" role cannot read — its permission is locked behind sql:app1:secret_get
        [screens.app1.periods]
        label = "Periods"
        read_query = "periods_get"
        update_query = "periods_put"
        delete_query = "periods_put"
        editable_when = [{ field = "STATUS", value = ["open", "validated"] }]

        [screens.app1.secret]
        label = "Secret"
        read_query = "secret_get"
        """
    )


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            # "user" can read users_get on app1 and things_get on external — *not* secret_get
            await svc.get_or_create_role(
                "user", permissions=["sql:app1:users_get", "sql:external:things_get"],
            )
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("user", password="userpw", roles=["user"])
            await svc.create_user("nobody", password="nobodypw")
        await pools.dispose()

    asyncio.run(go())


_NOTIFICATIONS_TOML = """
[notifications.period_closed]
trigger = { kind = "screen", app = "app1", screen = "periods", events = ["update"], when = [{ field = "STATUS", value = "closed" }] }
recipients = ["user:admin"]
subject = "Period {ID} closed by {USER}"
link = "/screen/app1/periods"

[notifications.ask]
trigger = { kind = "action" }
recipients = ["user:admin"]
subject = "Look at {ID}"
"""


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_connectors_toml(db_url))
    (tmp_path / "screens.toml").write_text(_screens_toml())
    (tmp_path / "notifications.toml").write_text(_NOTIFICATIONS_TOML)
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        screens=ScreenSettings(config_path=Path(tmp_path / "screens.toml")),
        notify=NotifySettings(config_path=Path(tmp_path / "notifications.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def _h(client: TestClient, username: str) -> dict[str, str]:
    tok = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_list_screens_admin_sees_everything(app) -> None:
    with TestClient(app) as client:
        r = client.get("/api/screens", headers=_h(client, "admin"))
        assert r.status_code == 200
        app1 = r.json()["screens"]["app1"]
        ids = [s["id"] for s in app1]
        assert ids == ["users", "things", "periods", "secret"]
        # Effective connector — explicit `connector` wins; same-app screens inherit the app name.
        connectors = {s["id"]: s["connector"] for s in app1}
        assert connectors == {"users": "app1", "things": "external", "periods": "app1", "secret": "app1"}
        users = next(s for s in app1 if s["id"] == "users")
        assert users["label"] == "Users" and users["description"] == "User accounts"
        assert users["read_query"] == "users_get" and users["update_query"] == "users_get"
        assert users["audit"] is True and users["has_dialog"] is True
        # Slice 6 follow-up — the catalog also flags row_menu / actions presence so the
        # TableView fetches the full body even on screens without a dialog.
        assert users["has_row_menu"] is False and users["has_actions"] is False


def test_list_screens_pruned_by_permission(app) -> None:
    with TestClient(app) as client:
        # "user" can read users_get + things_get; the secret screen drops out
        app1 = client.get("/api/screens", headers=_h(client, "user")).json()["screens"]["app1"]
        assert [s["id"] for s in app1] == ["users", "things"]
        # "nobody" sees no screens at all → the app disappears from the dict
        assert client.get("/api/screens", headers=_h(client, "nobody")).json()["screens"] == {}


def test_get_one_app_screens_and_404(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.get("/api/screens/app1", headers=h)
        assert r.status_code == 200 and r.json()["app"] == "app1"
        # Unknown app → 404
        assert client.get("/api/screens/ghost", headers=h).status_code == 404
        # An app whose screens the caller can't read → 404 (parity with /api/menus)
        assert client.get("/api/screens/app1", headers=_h(client, "nobody")).status_code == 404


def test_get_one_screen_returns_full_body_and_hides_unreadable(app) -> None:
    with TestClient(app) as client:
        body = client.get("/api/screens/app1/users", headers=_h(client, "admin")).json()
        assert body["id"] == "users" and body["has_dialog"] is True
        # The full descriptor includes the dialog body so the frontend can render the form.
        assert body["dialog"]["title"] == "User"
        assert body["dialog"]["tabs"][0]["fields"][0]["name"] == "USR_ID"
        # "user" can read users → 200; cannot read secret → 404 (not 403, so we don't leak its existence)
        assert client.get("/api/screens/app1/users", headers=_h(client, "user")).status_code == 200
        assert client.get("/api/screens/app1/secret", headers=_h(client, "user")).status_code == 404
        # Unknown screen → 404 too
        assert client.get("/api/screens/app1/ghost", headers=_h(client, "admin")).status_code == 404


def test_screens_route_requires_auth(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/screens").status_code == 401


def _connectors_with_lookup_query(db_url: str) -> str:
    """Like ``_connectors_toml`` but also exposes a ``roles_lookup`` query the dictionary's
    LOOKUP rule below references — so the prompt-resolution test can ask the screens API to
    resolve a PromptField.dd whose rule references it."""
    return textwrap.dedent(
        f"""
        [pools.default]
        url = "{db_url}"

        [connectors.app1]
        type = "sql"
        pool = "default"
        [[connectors.app1.queries]]
        name = "users_get"
        sql = "SELECT 1 AS id"
        [[connectors.app1.queries]]
        name = "roles_lookup"
        sql = "SELECT 1 AS id"
        [[connectors.app1.queries]]
        name = "create_user_run"
        sql = "INSERT INTO ghost VALUES (:USR_ID, :ROLE)"
        writable = true
        """
    )


def _screens_with_prompt_action() -> str:
    return textwrap.dedent(
        """
        [screens.app1.users]
        label = "Users"
        read_query = "users_get"
        # A toolbar action with prompt_fields. The dd on each PromptField tells the screens
        # API to resolve a display rule against the dictionary below — USR_ID has format='text',
        # ROLE has a LOOKUP rule.
        [[screens.app1.users.actions]]
        id = "create_user"
        type = "run_query"
        label = "Create User"
        query = "create_user_run"
        prompt_title = "New user"
        prompt_cols = 2
        [[screens.app1.users.actions.prompt_fields]]
        name = "USR_ID"
        dd = "USR_ID"
        required = true
        [[screens.app1.users.actions.prompt_fields]]
        name = "ROLE"
        dd = "ROLE_ID"
        """
    )


def _dictionary_toml() -> str:
    return textwrap.dedent(
        """
        default_language = "en"

        [entries.USR_ID]
        label = "User id"
        format = "text"

        [entries.ROLE_ID]
        label = "Role"
        rules = "LOOKUP"
        rules_values = "ROLES"

        [lookups.ROLES]
        query = "roles_lookup"
        connector = "app1"
        value = "ROLE_ID"
        label = "ROLE_NAME"
        """
    )


@pytest.fixture
def app_with_prompts(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_connectors_with_lookup_query(db_url))
    (tmp_path / "screens.toml").write_text(_screens_with_prompt_action())
    (tmp_path / "dictionary.toml").write_text(_dictionary_toml())
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(
            config_path=Path(tmp_path / "connectors.toml"),
            dictionary_path=Path(tmp_path / "dictionary.toml"),
        ),
        screens=ScreenSettings(config_path=Path(tmp_path / "screens.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def test_get_screen_resolves_prompt_field_rules(app_with_prompts) -> None:
    """Each action's ``prompt_fields`` get their ``dd`` resolved against the shared dictionary
    at ``GET /api/screens/{app}/{id}`` time — same enrichment ``Column`` carries on read-result
    columns. The frontend uses ``rule.kind`` to pick the widget (text / number / LOOKUP-combo),
    so this resolution lets a prompt render a SearchSelect for a ROLE_ID dd without any extra
    plumbing on the frontend."""
    with TestClient(app_with_prompts) as client:
        body = client.get("/api/screens/app1/users", headers=_h(client, "admin")).json()
        actions = body["actions"]
        assert len(actions) == 1
        fields = actions[0]["prompt_fields"]
        usr, role = fields
        # USR_ID — plain text entry, label resolved from the dictionary.
        assert usr["name"] == "USR_ID" and usr["label"] == "User id" and usr["format"] == "text"
        assert "rule" not in usr  # text format has no display rule
        # ROLE — LOOKUP rule shipped wire-ready, frontend renders a SearchSelect.
        assert role["name"] == "ROLE" and role["label"] == "Role"
        assert role["rule"]["kind"] == "lookup"
        assert role["rule"]["connector"] == "app1" and role["rule"]["query"] == "roles_lookup"
        assert role["rule"]["value"] == "ROLE_ID" and role["rule"]["label"] == "ROLE_NAME"
        assert client.get("/api/screens/app1").status_code == 401
        assert client.get("/api/screens/app1/users").status_code == 401


def _screens_with_columns() -> str:
    """Phase 1 mirror — a screen carrying its own ``columns`` list. Same ColumnHint shape
    used on ``QueryDef.columns``; the screens API resolves each hint against the dictionary
    (label / format / rule) and ships it as ``ScreenDetail.columns`` so the TableView can
    swap it in for the SQL endpoint's result columns."""
    return textwrap.dedent(
        """
        [screens.app1.users]
        label = "Users"
        read_query = "users_get"

        [[screens.app1.users.columns]]
        name = "USR_ID"
        dd = "USR_ID"
        filter = true
        width = 120

        [[screens.app1.users.columns]]
        name = "ROLE"
        dd = "ROLE_ID"
        label = "Override Label"
        align = "right"
        """
    )


@pytest.fixture
def app_with_screen_columns(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_connectors_with_lookup_query(db_url))
    (tmp_path / "screens.toml").write_text(_screens_with_columns())
    (tmp_path / "dictionary.toml").write_text(_dictionary_toml())
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(
            config_path=Path(tmp_path / "connectors.toml"),
            dictionary_path=Path(tmp_path / "dictionary.toml"),
        ),
        screens=ScreenSettings(config_path=Path(tmp_path / "screens.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def _screens_with_dialog_and_column_metadata() -> str:
    """Phase 2 verification: per-column metadata (``dd`` / ``label`` / ``format`` / ``rules`` /
    ``rules_values`` / ``default`` / ``lookup_param_binds``) lives on ``Screen.columns`` (single
    source of truth). The dialog field carries only layout (``name`` / ``colspan`` / ``required``).
    The backend resolver merges the column hint onto the field's wire payload so the dialog
    FieldRow renders without knowing about the column / field split."""
    return textwrap.dedent(
        """
        [screens.app1.users]
        label = "Users"
        read_query = "users_get"

        [[screens.app1.users.columns]]
        name = "ROLE"
        dd = "ROLE_ID"
        default = "ADMIN"
        required = true
        colspan = 2

        [[screens.app1.users.columns.lookup_param_binds]]
        param = "ROL_APPS_ID"
        source = "USR_APPS_ID"

        [screens.app1.users.dialog]
        title = "User"

        [[screens.app1.users.dialog.tabs]]
        id = "general"

        [[screens.app1.users.dialog.tabs.fields]]
        name = "ROLE"
        """
    )


@pytest.fixture
def app_with_dialog_columns(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(_connectors_with_lookup_query(db_url))
    (tmp_path / "screens.toml").write_text(_screens_with_dialog_and_column_metadata())
    (tmp_path / "dictionary.toml").write_text(_dictionary_toml())
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(
            config_path=Path(tmp_path / "connectors.toml"),
            dictionary_path=Path(tmp_path / "dictionary.toml"),
        ),
        screens=ScreenSettings(config_path=Path(tmp_path / "screens.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def test_dialog_field_inherits_metadata_from_screen_column(app_with_dialog_columns) -> None:
    """Single source of truth — the dialog field wire payload picks up EVERYTHING (``dd`` /
    ``label`` / ``default`` / ``lookup_param_binds`` / ``rule`` / ``required`` AND the dialog
    ``colspan``) from the matching ``Screen.columns`` entry (case-insensitive name match). The
    field config is a bare ``{name}`` reference; the frontend's FieldRow reads ``field.label`` etc.
    transparently — the metadata moved one level up, the wire shape is unchanged."""
    with TestClient(app_with_dialog_columns) as client:
        body = client.get("/api/screens/app1/users", headers=_h(client, "admin")).json()
        field = body["dialog"]["tabs"][0]["fields"][0]
        # required + colspan now come from the COLUMN (the field carries only ``name``).
        assert field["name"] == "ROLE" and field["required"] is True and field["colspan"] == 2
        # Display metadata merged from Screen.columns[name="ROLE"].
        assert field["dd"] == "ROLE_ID"
        assert field["label"] == "Role"          # resolved from the dictionary entry under ROLE_ID
        assert field["default"] == "ADMIN"
        assert field["lookup_param_binds"] == [{"param": "ROL_APPS_ID", "source": "USR_APPS_ID"}]
        # Rule resolved against the dictionary (ROLE_ID has rules = "LOOKUP" → SearchSelect widget).
        assert field["rule"]["kind"] == "lookup"
        assert field["rule"]["query"] == "roles_lookup"


def test_screen_columns_resolved_on_wire(app_with_screen_columns) -> None:
    """Phase 1 — ``Screen.columns`` rides through the screens API resolved against the shared
    dictionary in the request's language, with the same shape ``Column.to_dict()`` emits for
    read-result columns. The list-view also flags ``has_columns = true`` so the TableView
    eagerly fetches the body even on screens with no dialog / row_menu / actions."""
    with TestClient(app_with_screen_columns) as client:
        # List view — the has_* flag tells the frontend to fetch the detail.
        listing = client.get("/api/screens", headers=_h(client, "admin")).json()
        users_item = next(s for s in listing["screens"]["app1"] if s["id"] == "users")
        assert users_item["has_columns"] is True
        # Detail view — columns are resolved (label/format/rule from the dictionary, the
        # hint's own filter/width/align/dd surfaced as-is).
        body = client.get("/api/screens/app1/users", headers=_h(client, "admin")).json()
        cols = body["columns"]
        assert len(cols) == 2
        usr, role = cols
        # USR_ID resolves its label from the dictionary entry, keeps the hint's filter+width.
        assert usr["name"] == "USR_ID" and usr["label"] == "User id" and usr["format"] == "text"
        assert usr["filter"] is True and usr["width"] == 120 and usr["dd"] == "USR_ID"
        # ROLE keeps its explicit label override; the dictionary supplies the LOOKUP rule.
        assert role["name"] == "ROLE" and role["label"] == "Override Label" and role["align"] == "right"
        assert role["rule"]["kind"] == "lookup"
        assert role["rule"]["connector"] == "app1" and role["rule"]["query"] == "roles_lookup"
        assert role["dd"] == "ROLE_ID"


def test_screen_columns_absent_when_empty(app) -> None:
    """A screen without its own ``columns`` doesn't ship the field on the wire and the
    list view's ``has_columns`` is false — back-compat with screens.toml from before Phase 1."""
    with TestClient(app) as client:
        listing = client.get("/api/screens", headers=_h(client, "admin")).json()
        for s in listing["screens"]["app1"]:
            assert s["has_columns"] is False
        body = client.get("/api/screens/app1/users", headers=_h(client, "admin")).json()
        assert "columns" not in body


def test_info_reports_screen_apps(app) -> None:
    with TestClient(app) as client:
        info = client.get("/info").json()
        assert info["screens"] == {"apps": ["app1"], "total": 4}


def test_reload_rereads_screens(app, tmp_path) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Wipe the screens file → reload should drop everything
        (tmp_path / "screens.toml").write_text("")
        r = client.post("/admin/reload", headers=h)
        assert r.status_code == 200 and r.json()["screen_apps"] == []
        assert client.get("/api/screens", headers=h).json()["screens"] == {}


def test_editable_when_on_the_wire_and_enforced_on_writes(app) -> None:
    """The rule rides with the screen; an update / delete through the screen whose original row
    fails it is refused (409) before any SQL runs; an allowed row goes through; a request that
    leaves the rule's field out is refused too."""
    with TestClient(app) as client:
        h = _h(client, "admin")
        scr = next(s for s in client.get("/api/screens/app1", headers=h).json()["screens"] if s["id"] == "periods")
        assert scr["editable_when"] == [{"field": "STATUS", "value": ["open", "validated"]}]
        ok = client.post("/api/sql/app1/periods_put", json={"params": {"ID": 1, "STATUS": "closed", "STATUS_ORIGINAL": "open"}}, headers=h)
        assert ok.status_code == 200          # the row WAS open when loaded: closing it is allowed
        r = client.post("/api/sql/app1/periods_put", json={"params": {"ID": 1, "STATUS": "open", "STATUS_ORIGINAL": "closed"}}, headers=h)
        assert r.status_code == 409 and "read-only (STATUS)" in r.json()["detail"]
        r = client.post("/api/sql/app1/periods_put", json={"params": {"ID": 1}}, headers=h)
        assert r.status_code == 409 and "was not sent" in r.json()["detail"]
        # a plain key (no _ORIGINAL) counts as the row's value — a delete binds those
        assert client.post("/api/sql/app1/periods_put", json={"params": {"ID": 1, "STATUS": "validated"}}, headers=h).status_code == 200
        assert client.post("/api/sql/app1/periods_put", json={"params": {"ID": 1, "STATUS": "closed"}}, headers=h).status_code == 409
        # the read query is not concerned
        assert client.get("/api/sql/app1/periods_get", headers=h).status_code == 200


def test_screen_write_fires_notification_rules(app) -> None:
    """A committed update through the screen whose row matches a rule lands in the recipients'
    inbox (dispatched on a task — a following request lets it finish); a non-matching row and a
    refused write do not; an action rule fires on demand, a screen rule cannot."""
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/api/sql/app1/periods_put", json={"params": {"ID": 7, "STATUS": "closed", "STATUS_ORIGINAL": "open"}}, headers=h)
        assert r.status_code == 200
        client.get("/api/notifications/unread_count", headers=h)
        rows = client.get("/api/notifications", headers=h).json()["notifications"]
        assert [n["subject"] for n in rows] == ["Period 7 closed by admin"] and rows[0]["rule"] == "period_closed" and rows[0]["link"] == "/screen/app1/periods"
        assert rows[0]["payload"]["STATUS_ORIGINAL"] == "open" and rows[0]["payload"]["EVENT"] == "update"
        client.post("/api/sql/app1/periods_put", json={"params": {"ID": 8, "STATUS": "validated", "STATUS_ORIGINAL": "open"}}, headers=h)
        client.get("/api/notifications/unread_count", headers=h)
        assert len(client.get("/api/notifications", headers=h).json()["notifications"]) == 1
        assert client.post("/api/sql/app1/periods_put", json={"params": {"ID": 9, "STATUS": "closed", "STATUS_ORIGINAL": "closed"}}, headers=h).status_code == 409
        client.get("/api/notifications/unread_count", headers=h)
        assert len(client.get("/api/notifications", headers=h).json()["notifications"]) == 1
        r = client.post("/api/notify/fire", json={"rule": "ask", "payload": {"ID": 3}}, headers=h)
        assert r.status_code == 200 and r.json()["portal_users"] == ["admin"]
        assert client.post("/api/notify/fire", json={"rule": "period_closed", "payload": {}}, headers=h).status_code == 404
        assert client.get("/api/notifications", headers=h).json()["notifications"][0]["subject"] == "Look at 3"
