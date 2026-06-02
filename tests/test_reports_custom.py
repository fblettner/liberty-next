"""Phase 4a — operator-authored custom reports.

These tests cover the storage / discovery / sandbox / dispatch flow without
spinning up the full FastAPI app. The admin-endpoint round-trip (PUT →
in-memory registry refresh) is covered separately in test_web_admin.py.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from liberty.reports.custom import (
    CUSTOM_SCOPE,
    CustomReportDataBinding,
    CustomReportTemplate,
    build_custom_report_entries,
    load_custom_reports,
    make_custom_callable,
)
from liberty.reports.schema import ReportContent, ReportDef


# --------------------------------------------------------------------------- #
# Storage — load_custom_reports
# --------------------------------------------------------------------------- #
def test_load_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert load_custom_reports(tmp_path / "missing.toml") == []


def test_load_returns_empty_when_path_is_none() -> None:
    assert load_custom_reports(None) == []


def test_load_parses_one_template(tmp_path: Path) -> None:
    toml_text = dedent(
        '''
        [reports.audit-summary]
        title = "Audit licences — synthèse"
        description = "Vue de synthèse"
        formats = ["pdf", "markdown"]

        [[reports.audit-summary.params]]
        name = "apps_id"
        label = "Application ID"
        type = "int"
        required = true

        [reports.audit-summary.data]
        connector = "nomasx1"
        query = "license_financial_get"

        [reports.audit-summary]
        template_inline = """
        ## Synthèse
        {% for row in ctx.data.rows -%}
        - {{ row.name }} — {{ row.compliance }}
        {% endfor %}
        """
        '''
    )
    # Note: rather than rely on tomllib's table-merge quirks, write a cleaner
    # form below; the dedent above demonstrates the *shape* operators write
    # by hand, but for the test fixture we keep one table block per id.
    path = tmp_path / "reports.toml"
    path.write_text(
        dedent(
            '''
            [reports.audit-summary]
            title = "Audit licences — synthèse"
            description = "Vue de synthèse"
            formats = ["pdf", "markdown"]
            template_inline = "## Synthèse\\n\\n{% for row in ctx.data.rows -%}- {{ row.name }}\\n{% endfor %}"

            [[reports.audit-summary.params]]
            name = "apps_id"
            label = "Application ID"
            type = "int"
            required = true

            [reports.audit-summary.data]
            connector = "nomasx1"
            query = "license_financial_get"
            '''
        ),
        encoding="utf-8",
    )
    templates = load_custom_reports(path)
    assert len(templates) == 1
    t = templates[0]
    assert t.id == "audit-summary"
    assert t.title.startswith("Audit licences")
    assert t.data.connector == "nomasx1"
    assert t.data.query == "license_financial_get"
    assert len(t.params) == 1 and t.params[0].name == "apps_id"
    assert t.params[0].type == "int" and t.params[0].required is True


def test_load_raises_on_invalid_id(tmp_path: Path) -> None:
    """An id violating the kebab-case slug pattern surfaces as a ValueError
    naming the offending entry — operator knows which table to fix.
    ``BadID`` is a syntactically-valid TOML key but the Pydantic id pattern
    (lowercase + digits + dashes/underscores) rejects uppercase."""
    path = tmp_path / "reports.toml"
    path.write_text(
        dedent(
            '''
            [reports.BadID]
            title = "X"
            template_inline = "ok"
            data = { connector = "c", query = "q" }
            '''
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_custom_reports(path)
    assert "BadID" in str(exc.value)


# --------------------------------------------------------------------------- #
# Schema — CustomReportTemplate
# --------------------------------------------------------------------------- #
def test_template_rejects_duplicate_param_names() -> None:
    with pytest.raises(ValidationError) as exc:
        CustomReportTemplate(
            id="x", title="X",
            data=CustomReportDataBinding(connector="c", query="q"),
            template_inline="hi",
            params=(
                {"name": "a", "label": "A"},
                {"name": "a", "label": "A again"},
            ),
        )
    assert "duplicate param name" in str(exc.value)


def test_template_rejects_unknown_fields() -> None:
    """Extra=forbid catches typos at validation time so a saved-but-misspelled
    field doesn't silently get dropped + re-saved as a different shape."""
    with pytest.raises(ValidationError):
        CustomReportTemplate(
            id="x", title="X",
            data=CustomReportDataBinding(connector="c", query="q"),
            template_inline="hi",
            random_extra="oops",  # type: ignore[call-arg]
        )


# --------------------------------------------------------------------------- #
# Sandbox + generic callable
# --------------------------------------------------------------------------- #
def _fake_connectors(rows: list[dict]) -> MagicMock:
    """A connectors mock whose ``get(name).execute(query, params)`` returns a
    result object with ``rows`` and ``columns``. Captures the call args on
    ``connectors.last_call`` so tests can assert what got forwarded."""
    cn = MagicMock()
    result = MagicMock()
    result.rows = rows
    result.columns = [MagicMock(name=k, label=k.title(), type="str") for k in (rows[0] if rows else {})]

    async def execute(query: str, params: dict) -> MagicMock:
        cn.last_call = {"query": query, "params": params}
        return result

    cn.get.return_value.execute = execute
    cn.last_call = None
    return cn


def test_callable_runs_query_and_renders_template() -> None:
    """End-to-end: data query → row dicts → Jinja render → ReportContent."""
    template = CustomReportTemplate(
        id="audit",
        title="Audit",
        data=CustomReportDataBinding(connector="nomasx1", query="rows_get"),
        template_inline="## Summary\n\n{% for r in ctx.data.rows -%}\n- {{ r.name }}: {{ r.qty }}\n{% endfor %}",
        params=(
            {"name": "apps_id", "label": "App", "type": "int", "required": True},
        ),
    )
    fn = make_custom_callable(template)
    connectors = _fake_connectors([
        {"name": "OTF", "qty": 600},
        {"name": "Diag Pack", "qty": 500},
    ])
    result = asyncio.run(fn(connectors=connectors, apps_id=10))
    assert isinstance(result, ReportContent)
    assert "OTF: 600" in result.markdown
    assert "Diag Pack: 500" in result.markdown
    assert result.title == "Audit"
    assert result.filename_base == "audit"
    # Only declared params get forwarded to the query — extras (the framework's
    # injected ``connectors`` etc.) are NOT in the query params.
    assert connectors.last_call == {"query": "rows_get", "params": {"apps_id": 10}}


def test_callable_drops_undeclared_params_from_query_call() -> None:
    """Operator params not declared on the template don't reach the query —
    only ``params=[{name: …}]`` declarations forward. Prevents accidental
    SQL bind errors from injected framework kwargs."""
    template = CustomReportTemplate(
        id="x", title="X",
        data=CustomReportDataBinding(connector="c", query="q"),
        template_inline="ok",
        params=({"name": "apps_id", "label": "App", "type": "int", "required": True},),
    )
    fn = make_custom_callable(template)
    cn = _fake_connectors([])
    asyncio.run(fn(connectors=cn, apps_id=10, _undeclared="surprise"))
    assert cn.last_call == {"query": "q", "params": {"apps_id": 10}}


def test_sandbox_blocks_dangerous_attribute_access() -> None:
    """The sandbox must refuse ``__class__`` / ``__subclasses__`` traversal —
    a classic Jinja escape route to arbitrary Python execution. SandboxedEnv
    catches it; without the sandbox a templating mistake becomes RCE."""
    template = CustomReportTemplate(
        id="evil", title="evil",
        data=CustomReportDataBinding(connector="c", query="q"),
        # ``''.__class__.__mro__[1]`` would return ``object`` in an unsandboxed
        # env. SandboxedEnvironment raises SecurityError on the attr access.
        template_inline="{{ ''.__class__.__mro__[1].__subclasses__() }}",
    )
    fn = make_custom_callable(template)
    cn = _fake_connectors([])
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(fn(connectors=cn))
    # The error chain wraps Jinja's SecurityError as a "render error".
    assert "render error" in str(exc.value)


def test_sandbox_allows_normal_filters() -> None:
    """Plain Jinja filters (``length``, ``join``, ``selectattr``, …) must
    still work — sandbox only blocks dunder / global access."""
    template = CustomReportTemplate(
        id="x", title="X",
        data=CustomReportDataBinding(connector="c", query="q"),
        template_inline=(
            "Total: {{ ctx.data.rows | length }} | "
            "Names: {{ ctx.data.rows | map(attribute='name') | join(', ') }}"
        ),
    )
    fn = make_custom_callable(template)
    cn = _fake_connectors([{"name": "A"}, {"name": "B"}])
    result = asyncio.run(fn(connectors=cn))
    assert "Total: 2" in result.markdown
    assert "Names: A, B" in result.markdown


# --------------------------------------------------------------------------- #
# Registry entries
# --------------------------------------------------------------------------- #
def test_build_entries_emits_one_def_and_callable_per_template() -> None:
    """Round-trip: a list of templates becomes ReportDefs (scope='custom',
    licensed=False, callable marker per id) + a parallel callables dict
    that the registry merges in via ``ReportRegistry.add``."""
    templates = [
        CustomReportTemplate(
            id="r1", title="R1",
            data=CustomReportDataBinding(connector="c", query="q1"),
            template_inline="ok",
        ),
        CustomReportTemplate(
            id="r2", title="R2",
            data=CustomReportDataBinding(connector="c", query="q2"),
            template_inline="ok",
        ),
    ]
    defs, callables = build_custom_report_entries(templates)
    assert {d.id for d in defs} == {"r1", "r2"}
    assert all(d.scope == CUSTOM_SCOPE for d in defs)
    assert all(d.licensed is False for d in defs)
    assert set(callables) == {"custom:r1", "custom:r2"}
    assert all(callable(fn) for fn in callables.values())


def test_registry_add_rejects_collision_with_existing_def() -> None:
    """A custom id colliding with a plugin id raises ValueError so the
    operator is forced to rename. Matches the constructor contract."""
    from liberty.reports.registry import ReportRegistry

    plugin_def = ReportDef(
        id="x", scope="plugin",
        title="X",
        callable="m:f",
        licensed=False,
    )
    custom_def = ReportDef(
        id="x", scope="plugin",  # SAME scope+id — collision
        title="dup",
        callable="liberty.reports.custom:custom_report__x",
        licensed=False,
    )
    reg = ReportRegistry([plugin_def], callables={"plugin:x": lambda: None})
    with pytest.raises(ValueError) as exc:
        reg.add([custom_def], {"plugin:x": lambda: None})
    assert "duplicate report id" in str(exc.value)


def test_registry_add_merges_custom_alongside_plugin() -> None:
    """Different scope or id → no collision, both reports are now in the
    registry. The wiring layer relies on this to fold plugin + custom into
    one atomic registry without re-doing plugin discovery."""
    from liberty.reports.registry import ReportRegistry

    plugin_def = ReportDef(
        id="x", scope="nomasx1",
        title="Plugin X",
        callable="m:f",
        licensed=False,
    )
    custom_def = ReportDef(
        id="x", scope=CUSTOM_SCOPE,  # different scope
        title="Custom X",
        callable="liberty.reports.custom:custom_report__x",
        licensed=False,
    )
    reg = ReportRegistry([plugin_def], callables={"nomasx1:x": lambda: None})
    reg.add([custom_def], {"custom:x": lambda: None})
    assert set(reg.names()) == {"nomasx1:x", "custom:x"}
