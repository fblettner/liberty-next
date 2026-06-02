"""Tests for :mod:`liberty.reports.registry` — discovery of reports declared
by plugin packages, license gating, callable resolution, and the public
``ReportRegistry`` lookups."""
from __future__ import annotations

import textwrap
import sys
from pathlib import Path

import pytest

from liberty.licensing import LicenseResult
from liberty.reports.registry import (
    ReportRegistry,
    discover_reports,
)
from liberty.reports.schema import ReportDef, ReportParam, UnknownReportError


# --------------------------------------------------------------------------- #
# Fixtures — synthetic plugin tree on disk
# --------------------------------------------------------------------------- #
@pytest.fixture
def plugins_dir(tmp_path: Path):
    """Build a ``plugins/`` tree the way :func:`discover_reports` walks it.

    Each helper call appends a plugin with a ``reports/__init__.py`` that
    exposes ``REPORTS = [...]``. ``sys.path`` is patched + restored so the
    synthetic packages don't leak between tests."""
    d = tmp_path / "plugins"
    d.mkdir()
    # Make sure subsequent fresh-import attempts pick up THIS plugins dir, not
    # a leftover from another test.
    sys.path.insert(0, str(d))
    yield d
    if str(d) in sys.path:
        sys.path.remove(str(d))
    # Drop any cached fake-plugin modules so the next test gets fresh imports.
    for mod in list(sys.modules):
        if mod.startswith(("nomasx1_fake", "free_fake", "broken_fake", "bad_callable_fake")):
            del sys.modules[mod]


def _add_plugin(plugins_dir: Path, name: str, init_body: str) -> None:
    pdir = plugins_dir / name
    (pdir / "reports").mkdir(parents=True)
    (pdir / "__init__.py").write_text("")
    (pdir / "reports" / "__init__.py").write_text(textwrap.dedent(init_body))


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_discover_empty_plugins_dir_returns_empty_registry(plugins_dir):
    reg = discover_reports(plugins_dir, license=None)
    assert reg.names() == []
    assert reg.all() == []


def test_discover_picks_up_reports_constant(plugins_dir):
    """Plugins ship their reports via a top-level ``REPORTS`` list inside
    ``plugins/<name>/reports/__init__.py``."""
    _add_plugin(plugins_dir, "nomasx1_fake", """
        from liberty.reports.schema import ReportDef, ReportParam

        def generate():  # pragma: no cover — never called here
            pass

        REPORTS = [
            ReportDef(
                id="audit-licences",
                scope="nomasx1_fake",
                title="Audit Licences",
                callable="nomasx1_fake.reports:generate",
                licensed=False,  # so the no-license test path covers it
                params=(ReportParam(name="apps_id", label="A", type="int"),),
            ),
        ]
    """)
    reg = discover_reports(plugins_dir, license=None)
    assert reg.names() == ["nomasx1_fake:audit-licences"]
    d = reg.get("nomasx1_fake", "audit-licences")
    assert d.title == "Audit Licences"
    # Callable resolved + reachable through the registry
    assert callable(reg.get_callable("nomasx1_fake", "audit-licences"))


def test_discover_filters_licensed_when_license_missing(plugins_dir):
    """``licensed=True`` reports are dropped when the install's license
    doesn't cover the report's scope — same gate as licensed connectors."""
    _add_plugin(plugins_dir, "nomasx1_fake", """
        from liberty.reports.schema import ReportDef, ReportParam

        def generate():
            pass

        REPORTS = [
            ReportDef(
                id="audit",
                scope="nomasx1_fake",
                title="Audit",
                callable="nomasx1_fake.reports:generate",
                licensed=True,
            ),
        ]
    """)
    # No license at all → restricted; report is filtered out.
    reg = discover_reports(plugins_dir, license=None)
    assert reg.names() == []
    # Restricted license result (mode=restricted) doesn't cover anything.
    reg = discover_reports(plugins_dir, license=LicenseResult())
    assert reg.names() == []


def test_discover_unlicensed_reports_load_without_license(plugins_dir):
    """Customer-declared reports typically set ``licensed=False`` and load
    regardless of license."""
    _add_plugin(plugins_dir, "free_fake", """
        from liberty.reports.schema import ReportDef

        def generate():
            pass

        REPORTS = [
            ReportDef(
                id="r1", scope="free_fake", title="R1",
                callable="free_fake.reports:generate", licensed=False,
            ),
        ]
    """)
    reg = discover_reports(plugins_dir, license=None)
    assert reg.names() == ["free_fake:r1"]


def test_discover_licensed_passes_when_license_covers_scope(plugins_dir):
    """Full-mode license that covers the scope (either ``apps=None`` =
    everything, or scope explicitly listed) lets a licensed report in."""
    _add_plugin(plugins_dir, "nomasx1_fake", """
        from liberty.reports.schema import ReportDef

        def generate():
            pass

        REPORTS = [
            ReportDef(
                id="audit", scope="nomasx1_fake", title="Audit",
                callable="nomasx1_fake.reports:generate", licensed=True,
            ),
        ]
    """)
    full = LicenseResult(mode="full", apps=["nomasx1_fake"])
    reg = discover_reports(plugins_dir, license=full)
    assert reg.names() == ["nomasx1_fake:audit"]


def test_discover_skips_plugin_with_no_reports_package(plugins_dir):
    """A plugin without ``reports/`` is just ignored; not an error."""
    (plugins_dir / "no_reports_plugin").mkdir()
    (plugins_dir / "no_reports_plugin" / "__init__.py").write_text("")
    reg = discover_reports(plugins_dir, license=None)
    assert reg.names() == []


def test_discover_bad_callable_fails_fast(plugins_dir):
    """A typo in ``callable=`` should trip at discovery (startup), not at
    first request. ``RuntimeError`` with a clear message."""
    _add_plugin(plugins_dir, "bad_callable_fake", """
        from liberty.reports.schema import ReportDef

        REPORTS = [
            ReportDef(
                id="r", scope="bad_callable_fake", title="R",
                callable="bad_callable_fake.reports:does_not_exist",
                licensed=False,
            ),
        ]
    """)
    with pytest.raises(RuntimeError, match="cannot resolve"):
        discover_reports(plugins_dir, license=None)


def test_discover_broken_plugin_doesnt_kill_the_walker(plugins_dir, caplog):
    """One broken plugin (syntax error in its reports module) doesn't take
    the whole registry down — it's logged and skipped so other plugins
    still load."""
    _add_plugin(plugins_dir, "broken_fake", """
        # Intentional syntax error -- this should make import_module fail.
        def bad(:
            pass
    """)
    _add_plugin(plugins_dir, "free_fake", """
        from liberty.reports.schema import ReportDef

        def generate():
            pass

        REPORTS = [
            ReportDef(
                id="ok", scope="free_fake", title="OK",
                callable="free_fake.reports:generate", licensed=False,
            ),
        ]
    """)
    import logging
    caplog.set_level(logging.WARNING, logger="liberty.reports.registry")
    reg = discover_reports(plugins_dir, license=None)
    # Broken plugin skipped, healthy plugin loaded
    assert reg.names() == ["free_fake:ok"]
    assert any("broken_fake" in m for m in caplog.messages)


# --------------------------------------------------------------------------- #
# Registry lookups
# --------------------------------------------------------------------------- #


def test_registry_get_unknown_raises_typed_error():
    """The web layer maps ``UnknownReportError`` to HTTP 404 — guarantee it's
    the public subclass and not bare ``KeyError``."""
    reg = ReportRegistry()
    with pytest.raises(UnknownReportError):
        reg.get("nomasx1", "missing")


def test_registry_duplicate_id_rejected():
    """Two plugins each declaring ``nomasx1:audit`` must be rejected — first
    one to land is the source of truth; the second is the bug."""
    d1 = ReportDef(
        id="audit", scope="nomasx1", title="A1",
        callable="m1:fn", licensed=False,
    )
    d2 = ReportDef(
        id="audit", scope="nomasx1", title="A2",
        callable="m2:fn", licensed=False,
    )
    with pytest.raises(ValueError, match="duplicate"):
        ReportRegistry([d1, d2])


def test_registry_list_for_filters_by_scope():
    d1 = ReportDef(
        id="r", scope="nomasx1", title="R",
        callable="m:f", licensed=False,
    )
    d2 = ReportDef(
        id="r", scope="nomajde", title="R",
        callable="m:f", licensed=False,
    )
    reg = ReportRegistry([d1, d2])
    assert {d.scope for d in reg.list_for("nomasx1")} == {"nomasx1"}
    assert {d.scope for d in reg.list_for(None)} == {"nomasx1", "nomajde"}


def test_registry_get_callable_missing_callable_raises_lookup_error():
    """If the registry was constructed without resolved callables (e.g.
    tests built it from defs only), a ``get_callable`` call surfaces the
    inconsistency clearly rather than returning None."""
    d = ReportDef(
        id="r", scope="s", title="R", callable="m:f", licensed=False,
    )
    reg = ReportRegistry([d])  # callables map intentionally omitted
    with pytest.raises(LookupError):
        reg.get_callable("s", "r")
