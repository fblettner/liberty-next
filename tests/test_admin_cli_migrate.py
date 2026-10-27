"""``liberty-admin migrate`` — drives each app plugin's ``db.upgrade_schema`` (the contract
nomasx1.db already exposes) from the CLI, so an upgrade can apply / check schema migrations
without opening the nomaflow UI. Exercised against a fake plugin written into tmp_path."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from liberty.admin_cli import discover_migratable_apps, main

PLUGIN_DB = textwrap.dedent(
    '''
    """Fake app schema module — records every call in calls.json next to the package."""
    import json
    from pathlib import Path

    _LOG = Path(__file__).parent / "calls.json"
    PENDING = ["0001_init", "0002_more"]


    def _log(entry):
        data = json.loads(_LOG.read_text()) if _LOG.exists() else []
        data.append(entry)
        _LOG.write_text(json.dumps(data))


    def pending_revisions(*, connectors, target_connector):
        _log({"fn": "pending_revisions", "target": target_connector, "names": connectors.names()})
        return list(PENDING)


    async def upgrade_schema(*, connectors, target_connector, revision="head"):
        _log({"fn": "upgrade_schema", "target": target_connector, "revision": revision})
        PENDING.clear()
        return 7
    '''
)


@pytest.fixture(autouse=True)
def _isolate_imports():
    """The fake plugin is imported by name — drop it from sys.modules / sys.path afterwards so
    one test's package can't shadow the next one's."""
    saved_path, saved_mods = list(sys.path), set(sys.modules)
    yield
    sys.path[:] = saved_path
    for name in set(sys.modules) - saved_mods:
        if name.startswith(("fakeapp", "bareapp")):
            del sys.modules[name]


@pytest.fixture
def env(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'fw.db'}"
    app_toml = tmp_path / "app.toml"
    app_toml.write_text('[auth]\nbackend = "toml"\n' f'toml_path = "{tmp_path / "auth.toml"}"\n')
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(
        f'[pools.default]\nurl = "{db_url}"\n\n'
        '[connectors.fakeapp]\ntype = "sql"\npool = "default"\n\n'
        '[connectors.other]\ntype = "sql"\npool = "default"\n'
    )
    plugins = tmp_path / "plugins"
    (plugins / "fakeapp" / "db").mkdir(parents=True)
    (plugins / "fakeapp" / "__init__.py").write_text("")
    (plugins / "fakeapp" / "db" / "__init__.py").write_text(PLUGIN_DB)
    # a plugin with no db module is simply not migratable
    (plugins / "bareapp").mkdir()
    (plugins / "bareapp" / "__init__.py").write_text("")
    base = ["--config-app", str(app_toml), "--config-connectors", str(conn_toml)]
    return base, plugins


def _run(base, plugins, *args, capsys):
    rc = main([*base, "migrate", "--plugins-dir", str(plugins), *args])
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else None)


def _calls(plugins: Path) -> list[dict]:
    log = plugins / "fakeapp" / "db" / "calls.json"
    return json.loads(log.read_text()) if log.exists() else []


def test_discovery_needs_a_db_module(env) -> None:
    _, plugins = env
    assert discover_migratable_apps(plugins) == ["fakeapp"]
    assert discover_migratable_apps(plugins / "nope") == []
    assert discover_migratable_apps(None) == []


def test_dry_run_lists_pending_and_applies_nothing(env, capsys) -> None:
    base, plugins = env
    rc, out = _run(base, plugins, "--dry-run", capsys=capsys)
    assert rc == 2  # pending revisions → a pre-flight can gate on it
    (app,) = out["apps"]
    assert app == {
        "app": "fakeapp", "target_connector": "fakeapp", "revision": "head",
        "pending": ["0001_init", "0002_more"], "status": "pending",
    }
    assert out["pending_total"] == 2 and out["errors"] == 0
    assert [c["fn"] for c in _calls(plugins)] == ["pending_revisions"]


def test_apply_calls_upgrade_schema_with_the_app_connector(env, capsys) -> None:
    base, plugins = env
    rc, out = _run(base, plugins, capsys=capsys)
    assert rc == 0
    (app,) = out["apps"]
    assert app["status"] == "applied" and app["result"] == 7
    calls = _calls(plugins)
    assert calls[-1] == {"fn": "upgrade_schema", "target": "fakeapp", "revision": "head"}
    # the registry handed to the plugin is the real one, loaded from --config-connectors
    assert sorted(calls[0]["names"]) == ["fakeapp", "other"]


def test_apply_records_the_upgrade_in_history(env, capsys, tmp_path) -> None:
    from liberty.versioning import ConfigVersionStore

    base, plugins = env
    assert _run(base, plugins, capsys=capsys)[0] == 0
    rows = ConfigVersionStore(tmp_path).list_upgrades()
    assert rows and rows[0]["component"] == "fakeapp-schema" and rows[0]["to_version"] == "0002_more"


def test_target_connector_and_revision_are_forwarded(env, capsys) -> None:
    base, plugins = env
    rc, out = _run(base, plugins, "--target-connector", "other", "--revision", "0001_init", capsys=capsys)
    assert rc == 0
    assert _calls(plugins)[-1] == {"fn": "upgrade_schema", "target": "other", "revision": "0001_init"}
    assert out["apps"][0]["target_connector"] == "other"


def test_unknown_connector_is_an_error_not_a_crash(env, capsys) -> None:
    base, plugins = env
    rc, out = _run(base, plugins, "--target-connector", "ghost", capsys=capsys)
    assert rc == 1
    assert out["apps"][0]["status"] == "error" and "ghost" in out["apps"][0]["reason"]
    assert _calls(plugins) == []  # never got as far as the plugin


def test_unknown_app_fails_fast(env, capsys) -> None:
    base, plugins = env
    rc = main([*base, "migrate", "--plugins-dir", str(plugins), "--app", "bareapp"])
    err = capsys.readouterr().err
    assert rc == 1 and "bareapp" in err and "fakeapp" in err


def test_plugin_exception_is_reported_per_app(env, capsys) -> None:
    base, plugins = env
    (plugins / "fakeapp" / "db" / "__init__.py").write_text(
        "async def upgrade_schema(*, connectors, target_connector, revision='head'):\n"
        "    raise RuntimeError('boom')\n"
    )
    rc, out = _run(base, plugins, capsys=capsys)
    assert rc == 1
    assert out["apps"][0] == {"app": "fakeapp", "status": "error", "reason": "RuntimeError: boom"}


def test_stamp_then_apply(env, capsys) -> None:
    """--stamp REV sets the version through the plugin's stamp_revision, then applies what is pending
    (or just lists it with --dry-run)."""
    base, plugins = env
    (plugins / "fakeapp" / "db" / "__init__.py").write_text(PLUGIN_DB + '''

def stamp_revision(*, connectors, target_connector, revision):
    _log({"fn": "stamp_revision", "target": target_connector, "revision": revision})
    return 1
''')
    rc, out = _run(base, plugins, "--app", "fakeapp", "--stamp", "0006_x", "--dry-run", capsys=capsys)
    assert rc == 2 and out["apps"][0]["stamped"] == "0006_x" and out["apps"][0]["status"] == "pending"
    assert [c["fn"] for c in _calls(plugins)] == ["stamp_revision", "pending_revisions"]
    rc, out = _run(base, plugins, "--app", "fakeapp", "--stamp", "0006_x", capsys=capsys)
    assert rc == 0 and out["apps"][0]["status"] == "applied"
    assert [c["fn"] for c in _calls(plugins)][-2:] == ["pending_revisions", "upgrade_schema"]
    # without --app it refuses; a plugin without stamp_revision is an error, not a crash
    assert main([*base, "migrate", "--plugins-dir", str(plugins), "--stamp", "x"]) == 1
    (plugins / "fakeapp" / "db" / "__init__.py").write_text(PLUGIN_DB)
    sys.modules.pop("fakeapp.db", None)  # re-import the rewritten module
    rc, out = _run(base, plugins, "--app", "fakeapp", "--stamp", "x", capsys=capsys)
    assert rc == 1 and "stamp_revision" in out["apps"][0]["reason"]
