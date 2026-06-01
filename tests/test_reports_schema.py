"""Tests for ``liberty.reports.schema`` — pydantic validation rules on
:class:`ReportDef` / :class:`ReportParam` and the runtime
:class:`ReportContent` dataclass."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from liberty.reports.schema import (
    ReportContent,
    ReportDef,
    ReportParam,
)


def _make_def(**overrides):
    base = dict(
        id="audit-licences",
        scope="nomasx1",
        title="Audit Licences JD Edwards",
        callable="nomasx1.reports.audit_licences:generate",
        params=(
            ReportParam(name="apps_id", label="Application", type="int", required=True),
        ),
    )
    base.update(overrides)
    return ReportDef(**base)


# --------------------------------------------------------------------------- #
# ReportDef validation
# --------------------------------------------------------------------------- #


def test_report_def_minimal_valid() -> None:
    d = _make_def()
    assert d.id == "audit-licences"
    assert d.scope == "nomasx1"
    # Defaults — PDF + markdown are both supported out of the box.
    assert d.formats == ("pdf", "markdown")
    # Defaults — license-gated unless the declarer flips it off.
    assert d.licensed is True


def test_report_def_id_must_be_kebab_or_lower_alphanum() -> None:
    """The id lands in the URL path; uppercase / spaces / slashes would break
    routing or produce confusing 404s. Pattern restricts to lowercase
    alphanum + dashes + underscores."""
    for bad in ("Audit-Licences", "audit licences", "audit/licences", ""):
        with pytest.raises(ValidationError):
            _make_def(id=bad)


def test_report_def_scope_must_be_kebab_or_lower_alphanum() -> None:
    for bad in ("Nomasx1", "noma sx1", ""):
        with pytest.raises(ValidationError):
            _make_def(scope=bad)


def test_report_def_callable_must_match_module_function_shape() -> None:
    """``module.path:function`` — the registry resolves this string at startup.
    Free-form values would either crash at discovery or silently misroute."""
    for bad in (
        "nomasx1.reports.audit",       # missing :function
        ":generate",                   # missing module
        "nomasx1.reports.audit:",      # missing function
        "nomasx1.reports:audit:gen",   # extra colon
    ):
        with pytest.raises(ValidationError):
            _make_def(callable=bad)


def test_report_def_formats_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        _make_def(formats=())


def test_report_def_duplicate_param_names_rejected() -> None:
    """Two ReportParam entries with the same ``name`` would silently let one
    shadow the other in the validated body — fail at declaration time."""
    p = ReportParam(name="apps_id", label="A", type="int")
    p2 = ReportParam(name="apps_id", label="A2", type="string")
    with pytest.raises(ValidationError, match="duplicate"):
        _make_def(params=(p, p2))


def test_report_def_extra_keys_forbidden() -> None:
    """``extra='forbid'`` so a typo'd field surfaces as a clear validation
    error at the declaration site instead of silently dropping the value."""
    with pytest.raises(ValidationError):
        ReportDef(
            id="x", scope="x", title="t",
            callable="m:f",
            unknown_field="oops",  # type: ignore[call-arg]
        )


def test_report_def_is_frozen() -> None:
    """Defs are shared across requests; frozen=True prevents an accidental
    mutation (e.g. caller appending a param) from leaking to the next call."""
    d = _make_def()
    with pytest.raises(ValidationError):
        d.title = "mutated"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# ReportParam
# --------------------------------------------------------------------------- #


def test_report_param_type_must_be_one_of_four() -> None:
    """Limited to types the framework's coercion helper covers
    (:mod:`liberty.coercion`). Adding a new type means extending that helper
    AND the web layer's ``type_map``."""
    for ok in ("int", "float", "bool", "string"):
        ReportParam(name="x", label="X", type=ok)
    for bad in ("date", "json", "Int", "list"):
        with pytest.raises(ValidationError):
            ReportParam(name="x", label="X", type=bad)


def test_report_param_default_required_false() -> None:
    """Optional params without a default just don't get passed to the
    callable — the callable is responsible for its own defaults."""
    p = ReportParam(name="schema", label="Schema", type="string", required=False)
    assert p.required is False
    assert p.default is None


# --------------------------------------------------------------------------- #
# ReportContent (runtime dataclass)
# --------------------------------------------------------------------------- #


def test_report_content_defaults() -> None:
    """Minimal callable just returns markdown — other fields fall back to
    sensible defaults at render time (see render.render_content)."""
    c = ReportContent(markdown="# Hello\n\nbody.\n")
    assert c.markdown.startswith("# Hello")
    assert c.landscape_svg is None
    assert c.title == ""
    assert c.filename_base == "report"
    assert c.pdf_options == {}


def test_report_content_carries_overrides() -> None:
    c = ReportContent(
        markdown="# x",
        landscape_svg="<svg/>",
        title="Audit Gerflor",
        filename_base="audit-gerflor",
        pdf_options={"subtitle": "v1", "primary_color": "#0b3a82"},
    )
    assert c.landscape_svg == "<svg/>"
    assert c.pdf_options["subtitle"] == "v1"
