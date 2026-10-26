"""The ``reports`` / ``ldap`` extras are optional (A3.4).

WeasyPrint used to be imported at module load by ``liberty.reports.render`` — pulled in
by ``liberty.reports`` and ``liberty.web.reports``, i.e. by ``liberty.main`` — so the API
could not boot on a machine without Pango even when no report was ever rendered. It is
now imported inside :func:`build_pdf`; a PDF request without it fails with a clear
message, and markdown output never needs it. Same shape for the ``ldap_sync`` step.
"""

from __future__ import annotations

import builtins
import sys

import pytest

from liberty.reports.render import ReportsUnavailableError, build_pdf, render_content
from liberty.reports.schema import ReportContent


@pytest.fixture
def no_weasyprint(monkeypatch):
    """Make ``import weasyprint`` fail as it would on a core-only install."""
    for name in [m for m in sys.modules if m == "weasyprint" or m.startswith("weasyprint.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "weasyprint" or name.startswith("weasyprint."):
            raise ImportError("No module named 'weasyprint'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_app_imports_without_weasyprint() -> None:
    """The render module, the reports router and therefore ``liberty.main`` must import on a
    core-only install. A fresh interpreter with ``weasyprint`` blocked (``sys.modules[...] =
    None`` makes the import raise) — no reload tricks, which would create a second
    ``ReportsUnavailableError`` class."""
    import subprocess

    code = (
        "import sys; sys.modules['weasyprint'] = None; "
        "import liberty.reports.render, liberty.web.reports, liberty.main; print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr[-2000:]


def test_markdown_output_needs_no_weasyprint(no_weasyprint) -> None:
    body, ctype, filename = render_content(
        ReportContent(markdown="# Hello\n\ntext"), "markdown", app_name="App", report_title="T",
    )
    assert b"# Hello" in body and ctype.startswith("text/markdown") and filename.endswith(".md")


def test_pdf_without_weasyprint_fails_with_the_extra_hint(no_weasyprint) -> None:
    with pytest.raises(ReportsUnavailableError) as ei:
        build_pdf("# Hello")
    assert "liberty-next[reports]" in str(ei.value)


def test_pdf_still_renders_with_weasyprint_installed() -> None:
    pytest.importorskip("weasyprint")
    out = build_pdf("# Hello\n\nbody")
    assert out[:4] == b"%PDF"


def test_ldap_sync_without_ldap3_fails_with_the_extra_hint(monkeypatch) -> None:
    from liberty.jobs.schema import Step, StepType
    from liberty.jobs.steps.base import StepFailed
    from liberty.jobs.steps.ldap_sync import _search_ldap

    monkeypatch.delitem(sys.modules, "ldap3", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ldap3" or name.startswith("ldap3."):
            raise ImportError("No module named 'ldap3'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    step = Step(
        name="s", type=StepType.LDAP_SYNC, server="ldap://x", bind_dn="cn=a", bind_password="p",
        search_base="dc=x", search_filter="(objectClass=*)", mapping={}, target_connector="c", target_query="q",
    )
    with pytest.raises(StepFailed) as ei:
        _search_ldap(step)
    assert "liberty-next[ldap]" in str(ei.value)
