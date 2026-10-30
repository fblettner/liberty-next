"""The developer reference's Configuration view (C12): every app.toml key, where the UI edits it, who
reads it."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.config import Settings
from liberty.devref import FILE_ONLY, _portable_path, _select_columns, config_reference, ui_editors
from tests.test_web_admin_ops import _h, env  # noqa: F401 — the app fixture


def _ref() -> dict[str, dict]:
    return {r["key"]: r for r in config_reference()}


def test_every_settings_key_is_listed() -> None:
    ref = _ref()
    expected = {f"{s}.{k}" for s, f in Settings.model_fields.items() for k in f.annotation.model_fields}
    assert set(ref) == expected


def test_editors_and_file_only_reasons() -> None:
    ref = _ref()
    assert ref["app.default_page_size"]["editor"] == "Liberty Next → Administer → App"
    assert ref["promotion.targets"]["editor"] == "Liberty Next → Deploy → Package → Promotion"
    assert ref["crypto.master_key"]["editor"] is None and ref["crypto.master_key"]["file_only"] == FILE_ONLY["crypto.master_key"]
    assert set(ui_editors()) <= set(ref)          # no editor registered for a key that doesn't exist


def test_readers_are_found_in_every_shape() -> None:
    ref = _ref()
    def files(key: str) -> set[str]:
        return {r["file"] for r in ref[key]["readers"]}
    assert "liberty/logfile.py" in files("app.log_file")                 # getattr(settings.app, "log_file", …)
    assert "liberty/ai/assistant.py" in files("ai.max_tokens")           # self.settings.max_tokens (AISettings)
    assert "liberty/notify/dispatcher.py" in files("notify.smtp_host")   # n.smtp_host
    assert "liberty/runtime.py" in files("changesets.enabled")           # getattr(getattr(settings, "changesets"…), "enabled")
    assert all(ref[k]["readers"] for k in ref), [k for k in ref if not ref[k]["readers"]]


def test_the_route_is_superuser_only(env) -> None:  # noqa: F811
    app, _ = env
    with TestClient(app) as client:
        assert client.get("/admin/dev/config").status_code == 401
        assert client.get("/admin/dev/config", headers=_h(client, "reader")).status_code == 403
        body = client.get("/admin/dev/config", headers=_h(client, "admin")).json()
        assert body["total"] == len(body["keys"]) and body["editable"] > 0 and body["gaps"] == []


def test_every_setting_is_editable_or_explained() -> None:
    """Keeps priority 1 true: a new app.toml key must get a field in a screen (``admin._*_SAFE_KEYS``,
    ``devref._OTHER_EDITORS``) or a reason in ``devref.FILE_ONLY``."""
    from liberty.devref import config_gaps
    assert config_gaps() == [], "settings not editable in the UI and not explained as file-only"


def test_api_reference_reads_access_from_the_code(env) -> None:  # noqa: F811
    from liberty.devref import api_reference
    app, _ = env
    routes = api_reference(app)
    by = {(r["method"], r["path"]): r for r in routes}
    public = {r["path"] for r in routes if r["access"] == "public"}
    assert public == {"/health", "/ready", "/info", "/api/theme", "/manifest.webmanifest",
                      "/auth/login", "/auth/refresh", "/auth/oidc/login", "/auth/oidc/callback",
                      # the password reset by mail (always the same answer; the link is a signed one-shot)
                      "/auth/forgot-password", "/auth/reset-password"}
    sql = by[("GET", "/api/sql/{connector}/{query}")]
    assert sql["access"] == "permission" and sql["permissions"] == ["sql:{connector}:{query}"] and sql["file"] == "liberty/web/connectors.py"
    assert by[("POST", "/admin/reload")]["access"] == "superuser"
    assert by[("POST", "/ai/chat")]["permissions"] == ["ai:chat"]
    assert by[("GET", "/admin/config/screens/parsed")]["group"] == "settings · screens"
    assert all(r["category"] != "Other" for r in routes), [r["path"] for r in routes if r["category"] == "Other"]
    login = by[("POST", "/auth/login")]
    assert login["request"]["type"] == "LoginRequest" and {f["name"] for f in login["request"]["fields"]} == {"username", "password"}
    assert login["request"]["example"] == {"username": "string", "password": "string"}
    assert login["response"]["type"] == "TokenPair" and "access_token" in login["response"]["example"]
    assert [(p["name"], p["in"]) for p in sql["parameters"]][:2] == [("connector", "path"), ("query", "path")]
    assert by[("GET", "/admin/dev/config")]["response"] is None          # a plain dict: not declared
    with TestClient(app) as client:
        assert client.get("/admin/dev/api", headers=_h(client, "reader")).status_code == 403
        assert client.get("/admin/dev/api", headers=_h(client, "admin")).json()["total"] == len(routes)


def test_data_model_framework_and_connector_tables(env) -> None:  # noqa: F811
    from liberty.devref import data_model
    app, tmp = env
    with open(tmp / "connectors.toml", "a") as f:
        f.write('\n[[connectors.foo.tables]]\nname = "items"\nlabel = "Items"\n'
                '[connectors.foo.tables.get]\nsql = "SELECT i.* FROM #SCHEMA#.ITEMS#DBLINK# i JOIN OTHER o ON 1 = 1"\n')
    with open(tmp / "screens.toml", "a") as f:
        f.write('\n[screens.foo.items]\nread_query = "items_get"\nkey_columns = ["ITEM_ID"]\n'
                '[[screens.foo.items.columns]]\nname = "ITEM_ID"\n[[screens.foo.items.columns]]\nname = "USR_NAME"\n')
    with TestClient(app) as client:
        tables = {t["id"]: t for t in data_model(app.state)}
        runs = tables["framework:nomaflow_job_runs"]
        assert runs["group"] == "nomaflow" and runs["key"] == ["id"] and any(c["name"] == "state" for c in runs["columns"])
        items = tables["foo:items"]
        assert items["physical"] == "ITEMS" and items["slots"] == ["get"] and items["key"] == ["ITEM_ID"]
        assert items["used_by"] == [{"kind": "screen", "name": "foo.items"}]
        cols = {c["name"]: c for c in items["columns"]}
        assert cols["ITEM_ID"]["key"] is True and cols["USR_NAME"]["label"] == "Username"   # the dictionary's label
        body = client.get("/admin/dev/data", headers=_h(client, "admin")).json()
        assert body["total"] == len(tables)


def test_select_columns_reads_the_outer_select_list() -> None:
    sql = """WITH x AS (SELECT a, b FROM t)
    SELECT DISTINCT t.apps_id, COUNT(*) AS n, CASE WHEN a = ',' THEN 'x, y' END tone, "Quoted",
        (SELECT MAX(z) FROM u) AS top, t.*, SUM(v)
    FROM t -- a, b
    WHERE k IN (SELECT k FROM w)"""
    assert _select_columns(sql) == ["apps_id", "n", "tone", "Quoted", "top"]
    assert _select_columns({"postgres": "SELECT a AS one FROM t", "oracle": "SELECT b FROM t"}) == ["one"]
    assert _select_columns("SELECT * FROM t") == []


def test_default_paths_are_shown_relative_to_the_apps_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    apps = tmp_path / "apps" / "config"
    monkeypatch.setenv("LIBERTY_APPS_DIR", str(apps))
    assert _portable_path(apps / "screens.toml") == "${LIBERTY_APPS_DIR}/screens.toml"
    assert _portable_path(apps.parent / "plugins" / "nomaflow" / "jobs.toml") == "${LIBERTY_APPS_DIR}/../plugins/nomaflow/jobs.toml"
    assert _portable_path(Path("config/app.toml")) == "config/app.toml"


def test_cli_reference_reads_every_installed_script() -> None:
    """The scripts come from the package's entry points and their parsers — no list kept by hand."""
    from liberty.devref import cli_reference
    scripts = {s["name"]: s for s in cli_reference()}
    assert {"liberty-next", "liberty-admin", "liberty-crypto", "liberty-license", "liberty-ops", "liberty-connectors", "liberty-migrate"} <= set(scripts)
    admin = scripts["liberty-admin"]
    assert admin["description"] and not admin["description"].startswith("``")      # the docstring's ``name`` — prefix dropped
    init = next(c for c in admin["commands"] if c["name"] == "init-db")
    user = next(a for a in init["arguments"] if a["name"] == "--admin-username")
    assert user["default"] == "admin" and user["value"] == "ADMIN_USERNAME" and not user["required"]
    assert init["usage"].startswith("liberty-admin init-db")
    crypto = scripts["liberty-crypto"]                                             # its parser now built by build_parser()
    assert [c["name"] for c in crypto["commands"]] == ["encrypt", "is-encrypted"]
    assert any(a["name"] == "--master-key" for a in crypto["options"])
    assert scripts["liberty-next"]["commands"] == [] and "uvicorn" in scripts["liberty-next"]["description"]
