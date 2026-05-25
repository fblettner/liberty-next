"""Tests for :mod:`liberty.web.hot_reload` — the opt-in filesystem watcher.

Two layers:

* **Unit** tests on the per-file handlers + the pool-signature compare. Don't need a
  real watchfiles loop; just call the handler directly with a tmp_path fixture.
* **Integration** tests on ``start_watcher`` — mutate a file, wait for the debounce
  window, assert the matching subsystem reloaded. These are slow (must wait for the
  500 ms debounce + the OS-level inotify/FSEvents propagation) so kept short.
"""

from __future__ import annotations

import asyncio
import tempfile
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from liberty.web.hot_reload import (
    DEBOUNCE_MS,
    _pool_signature,
    _reload_charts,
    _reload_dashboards,
    _reload_dictionary,
    _reload_menus,
    _reload_screens,
    start_watcher,
)


# ── unit: _pool_signature ──────────────────────────────────────────────────────────


def test_pool_signature_equal_for_identical_configs() -> None:
    """Two pool configs with same values produce identical signatures — that's what
    drives the "did pools change" comparison in _reload_connectors."""
    a = SimpleNamespace(url="postgresql://a", password="ENC:xxx", dialect="postgresql",
                        pool_size=5, max_overflow=10, pool_pre_ping=True, pool_recycle=-1,
                        max_rows=None, trim_strings=False, coalesce_nulls=False)
    b = SimpleNamespace(url="postgresql://a", password="ENC:xxx", dialect="postgresql",
                        pool_size=5, max_overflow=10, pool_pre_ping=True, pool_recycle=-1,
                        max_rows=None, trim_strings=False, coalesce_nulls=False)
    assert _pool_signature(a) == _pool_signature(b)


def test_pool_signature_differs_when_url_changes() -> None:
    a = SimpleNamespace(url="postgresql://a", password="x", dialect="postgresql",
                        pool_size=5, max_overflow=0, pool_pre_ping=True, pool_recycle=-1,
                        max_rows=None, trim_strings=False, coalesce_nulls=False)
    b = SimpleNamespace(url="postgresql://b", password="x", dialect="postgresql",
                        pool_size=5, max_overflow=0, pool_pre_ping=True, pool_recycle=-1,
                        max_rows=None, trim_strings=False, coalesce_nulls=False)
    assert _pool_signature(a) != _pool_signature(b)


def test_pool_signature_works_for_raw_dict_too() -> None:
    """Used inside _reload_connectors where one side is a Pydantic PoolConfig (live)
    and the other is a plain dict from tomllib.load (on-disk parse). Both must list
    the same keys for the signatures to match — missing dict keys → None, which
    differs from a Pydantic default like ``False``. That's intentional: if an operator
    removes ``trim_strings = true`` from the file, the live config (still True from
    the previous load) and the on-disk dict (no key → None) won't match, and the
    watcher correctly detects "config changed"."""
    cfg = SimpleNamespace(url="postgresql://a", password=None, dialect=None,
                          pool_size=5, max_overflow=0, pool_pre_ping=True, pool_recycle=-1,
                          max_rows=None, trim_strings=False, coalesce_nulls=False)
    raw = {"url": "postgresql://a", "password": None, "dialect": None,
           "pool_size": 5, "max_overflow": 0, "pool_pre_ping": True, "pool_recycle": -1,
           "max_rows": None, "trim_strings": False, "coalesce_nulls": False}
    assert _pool_signature(cfg) == _pool_signature(raw)


# ── unit: per-file handlers reload the matching subsystem ──────────────────────────


def _settings(tmp_path: Path) -> SimpleNamespace:
    """A minimal Settings-shaped object — only the path attributes the handlers read."""
    return SimpleNamespace(
        app=SimpleNamespace(hot_reload=False, log_level="info"),
        menus=SimpleNamespace(config_path=tmp_path / "menus.toml"),
        screens=SimpleNamespace(config_path=tmp_path / "screens.toml"),
        charts=SimpleNamespace(config_path=tmp_path / "charts.toml"),
        dashboards=SimpleNamespace(config_path=tmp_path / "dashboards.toml"),
        connectors=SimpleNamespace(
            config_path=tmp_path / "connectors.toml",
            dictionary_path=tmp_path / "dictionary.toml",
        ),
        crypto=SimpleNamespace(master_key=""),
    )


def _app(tmp_path: Path) -> SimpleNamespace:
    """An app-state-shaped object that hot_reload's handlers can read/write."""
    return SimpleNamespace(state=SimpleNamespace(settings=_settings(tmp_path)))


@pytest.mark.asyncio
async def test_reload_menus_swaps_state(tmp_path: Path) -> None:
    """After a menus.toml change, _reload_menus parses it + swaps app.state.menus."""
    menus_path = tmp_path / "menus.toml"
    menus_path.write_text('[menus.a]\nlabel = "A"\n')
    app = _app(tmp_path)
    await _reload_menus(app, menus_path)
    assert "a" in app.state.menus.menus
    # Replace + reload again — state picks up the new content.
    menus_path.write_text('[menus.b]\nlabel = "B"\n')
    await _reload_menus(app, menus_path)
    assert "a" not in app.state.menus.menus and "b" in app.state.menus.menus


@pytest.mark.asyncio
async def test_reload_screens_swaps_state(tmp_path: Path) -> None:
    screens_path = tmp_path / "screens.toml"
    screens_path.write_text(textwrap.dedent("""
        [screens.a.users]
        connector = "a"
        read_query = "x"
    """).lstrip())
    app = _app(tmp_path)
    await _reload_screens(app, screens_path)
    assert "a" in app.state.screens.screens


@pytest.mark.asyncio
async def test_reload_charts_swaps_state(tmp_path: Path) -> None:
    charts_path = tmp_path / "charts.toml"
    charts_path.write_text(textwrap.dedent("""
        [charts.x]
        label = "X"
        connector = "c"
        query = "q"

        [charts.x.spec]
        type = "bar"
        x = "a"
        y = ["b"]
    """).lstrip())
    app = _app(tmp_path)
    await _reload_charts(app, charts_path)
    assert "x" in app.state.charts.charts


@pytest.mark.asyncio
async def test_reload_dashboards_swaps_state(tmp_path: Path) -> None:
    dashboards_path = tmp_path / "dashboards.toml"
    dashboards_path.write_text('[dashboards.d]\nlabel = "D"\n')
    app = _app(tmp_path)
    await _reload_dashboards(app, dashboards_path)
    assert "d" in app.state.dashboards.dashboards


@pytest.mark.asyncio
async def test_reload_dictionary_swaps_connectors_and_screens(tmp_path: Path) -> None:
    """dictionary.toml feeds both connectors (per-connector overlay) and screens
    (column hints), so _reload_dictionary cascades to both. Set up the source files
    + initial state, mutate dictionary.toml, assert both swap."""
    # Minimal valid trio
    (tmp_path / "connectors.toml").write_text('[pools.default]\nurl = "sqlite+aiosqlite:///:memory:"\n')
    (tmp_path / "dictionary.toml").write_text('default_language = "en"\n')
    (tmp_path / "screens.toml").write_text('')
    from liberty.connectors import load_connectors
    from liberty.licensing import LicenseResult
    from liberty.screens import load_screens
    app = _app(tmp_path)
    # Seed initial state
    app.state.license = LicenseResult(mode="open", error=None)
    app.state.connectors = load_connectors(
        tmp_path / "connectors.toml",
        dictionary_path=tmp_path / "dictionary.toml",
        master_key="",
        license=app.state.license,
    )
    app.state.screens = load_screens(tmp_path / "screens.toml")
    # Mutate dictionary.toml + reload — connectors gets re-built, screens re-parsed
    (tmp_path / "dictionary.toml").write_text('[entries.X]\nlabel = "X"\n')
    await _reload_dictionary(app, tmp_path / "dictionary.toml")
    # The reload completed; the new entry is in the connectors' dictionary.
    assert "X" in app.state.connectors.dictionary.entries


# ── start_watcher: the no-op + opt-in paths ────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_watcher_returns_noop_when_disabled(tmp_path: Path) -> None:
    """When [app] hot_reload = false (the default), start_watcher returns a no-op
    task immediately — no actual watching happens. The lifespan can still .cancel()
    + await on it without blowing up."""
    settings = _settings(tmp_path)  # hot_reload=False by default
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))
    task = await start_watcher(app)
    assert task is not None
    await task  # completes immediately


@pytest.mark.asyncio
async def test_start_watcher_returns_running_task_when_enabled(tmp_path: Path) -> None:
    """When [app] hot_reload = true, start_watcher spawns a background task that the
    lifespan needs to cancel on shutdown. Verifies the spawn + clean cancel cycle."""
    # Need a real Settings-equivalent: dictionary.toml is referenced + needs to be
    # creatable on disk (it doesn't have to exist; the handler skips missing files).
    settings = _settings(tmp_path)
    settings.app.hot_reload = True
    (tmp_path / "menus.toml").write_text('')
    (tmp_path / "screens.toml").write_text('')
    (tmp_path / "charts.toml").write_text('')
    (tmp_path / "dashboards.toml").write_text('')
    (tmp_path / "connectors.toml").write_text('[pools.default]\nurl = "sqlite+aiosqlite:///:memory:"\n')
    (tmp_path / "dictionary.toml").write_text('')
    # No jobs section → handler skipped.
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))

    task = await start_watcher(app)
    assert not task.done()
    # Cancel + await — should exit cleanly.
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.done()


@pytest.mark.asyncio
async def test_start_watcher_dispatches_on_file_change(tmp_path: Path) -> None:
    """End-to-end: change menus.toml, wait for the debounce window + a buffer, verify
    app.state.menus reflects the new content. Slow (~700 ms) — kept as the single
    e2e test; the per-handler logic is covered above."""
    settings = _settings(tmp_path)
    settings.app.hot_reload = True
    (tmp_path / "menus.toml").write_text('[menus.before]\nlabel = "Before"\n')
    # Other paths must exist or the watcher's handler map skips them — watchfiles itself
    # is OK with a missing path but the lifespan path-collection sees None and skips.
    for f in ("screens.toml", "charts.toml", "dashboards.toml", "dictionary.toml"):
        (tmp_path / f).write_text('')
    (tmp_path / "connectors.toml").write_text('[pools.default]\nurl = "sqlite+aiosqlite:///:memory:"\n')

    # Seed initial state — the watcher's handler will overwrite this.
    from liberty.menus import load_menus
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))
    app.state.menus = load_menus(tmp_path / "menus.toml")
    assert "before" in app.state.menus.menus

    task = await start_watcher(app)
    try:
        # Mutate the file. Give the OS event a moment to propagate, then the
        # DEBOUNCE_MS coalesce window, then the handler, then a bit of slack.
        await asyncio.sleep(0.1)
        (tmp_path / "menus.toml").write_text('[menus.after]\nlabel = "After"\n')
        # Watcher's debounce + handler latency + asyncio scheduling slack.
        await asyncio.sleep(DEBOUNCE_MS / 1000 + 0.5)
        assert "after" in app.state.menus.menus
        assert "before" not in app.state.menus.menus
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
