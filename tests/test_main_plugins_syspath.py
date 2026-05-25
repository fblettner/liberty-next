"""Tests for ``liberty.main._ensure_plugins_on_sys_path`` — the import-hook
that makes ``${LIBERTY_APPS_DIR}/../plugins/`` importable so a python step's
``callable = "nomasx1.security:j_x"`` resolves to the apps repo (PHASE13 §5.3).
"""

from __future__ import annotations

import importlib
import sys

import pytest

from liberty.main import _ensure_plugins_on_sys_path


@pytest.fixture(autouse=True)
def _restore_sys_path():
    """Snapshot + restore sys.path so each test starts from a clean slate
    (the hook mutates the global; tests can't leak state into each other)."""
    saved = list(sys.path)
    yield
    sys.path[:] = saved


def test_injects_plugins_dir_when_apps_dir_set(tmp_path, monkeypatch) -> None:
    """LIBERTY_APPS_DIR=apps/config/ → plugins/ is its sibling. The resolved
    absolute path lands at sys.path[0]."""
    apps_config = tmp_path / "apps" / "config"
    plugins = tmp_path / "apps" / "plugins"
    plugins.mkdir(parents=True)
    apps_config.mkdir(parents=True)

    monkeypatch.setenv("LIBERTY_APPS_DIR", str(apps_config))
    _ensure_plugins_on_sys_path()

    expected = str(plugins.resolve())
    assert sys.path[0] == expected


def test_resolves_real_callable_under_plugins(tmp_path, monkeypatch) -> None:
    """End-to-end: drop a fake plugin package under plugins/, set
    LIBERTY_APPS_DIR, call the hook — the module imports + the function
    is reachable. This is the path a python step takes."""
    plugins = tmp_path / "apps" / "plugins"
    apps_config = tmp_path / "apps" / "config"
    plugins.mkdir(parents=True)
    apps_config.mkdir(parents=True)
    pkg = plugins / "fakemod"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "fn.py").write_text("def hello() -> str:\n    return 'world'\n")

    monkeypatch.setenv("LIBERTY_APPS_DIR", str(apps_config))
    _ensure_plugins_on_sys_path()

    # Clear cached imports of the same module name if a prior test left them.
    for mod_name in [m for m in sys.modules if m == "fakemod" or m.startswith("fakemod.")]:
        del sys.modules[mod_name]

    module = importlib.import_module("fakemod.fn")
    assert module.hello() == "world"


def test_idempotent_on_repeat_calls(tmp_path, monkeypatch) -> None:
    """Calling the hook twice doesn't duplicate the entry — important because
    tests construct multiple apps + the path would otherwise grow without
    bound."""
    apps_config = tmp_path / "apps" / "config"
    plugins = tmp_path / "apps" / "plugins"
    plugins.mkdir(parents=True)
    apps_config.mkdir(parents=True)

    monkeypatch.setenv("LIBERTY_APPS_DIR", str(apps_config))
    _ensure_plugins_on_sys_path()
    after_first = list(sys.path)
    _ensure_plugins_on_sys_path()
    assert sys.path == after_first


def test_no_op_when_plugins_dir_missing(tmp_path, monkeypatch) -> None:
    """If LIBERTY_APPS_DIR points somewhere without a sibling plugins/, the
    hook silently does nothing — operator hasn't opted into plugins yet."""
    apps_config = tmp_path / "apps" / "config"
    apps_config.mkdir(parents=True)
    # NB: no plugins dir created

    monkeypatch.setenv("LIBERTY_APPS_DIR", str(apps_config))
    before = list(sys.path)
    _ensure_plugins_on_sys_path()
    assert sys.path == before


def test_no_op_when_apps_dir_unset(monkeypatch) -> None:
    """No env var → fall back to ./plugins relative to cwd. When that doesn't
    exist either, the hook is a no-op."""
    monkeypatch.delenv("LIBERTY_APPS_DIR", raising=False)
    # Force cwd to a place without a plugins/ dir.
    monkeypatch.chdir("/tmp")
    before = list(sys.path)
    _ensure_plugins_on_sys_path()
    assert sys.path == before
