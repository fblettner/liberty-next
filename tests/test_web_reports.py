"""Tests for ``liberty.web.reports`` — the public ``/api/reports`` endpoints.

Each test spins up a full FastAPI app via ``create_app`` (so the lifespan
populates ``app.state.reports`` from a temp plugin tree), seeds an
authenticated user with the right permission, and exercises one route. The
stub reports are tiny — one returns markdown + an SVG, one always raises —
so we cover the happy paths AND the error mapping (403 / 404 / 422 / 500)
without depending on nomasx1.
"""
from __future__ import annotations

import asyncio
import os
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "reports-test-secret"

# --------------------------------------------------------------------------- #
# Fixture: a synthetic plugin tree on disk + a Liberty app pointed at it
# --------------------------------------------------------------------------- #
_STUB_REPORTS_INIT = '''
"""Stub reports module discovered by the framework's plugin walker.

* ``markdown-only`` — returns a small ReportContent
* ``with-svg`` — returns ReportContent + a landscape SVG
* ``raises`` — always raises, to exercise the 500 path
"""
from liberty.reports.schema import (
    ReportContent, ReportDef, ReportParam, ReportParamOption, ReportParamOptions,
)


def gen_markdown_only(*, apps_id: int):
    return ReportContent(
        markdown=f"# Stub\\n\\napps_id = {apps_id}\\n",
        title="Stub markdown",
        filename_base=f"stub-{apps_id}",
    )


def gen_with_svg(*, apps_id: int):
    return ReportContent(
        markdown=f"# Stub\\n\\n![arch.svg](arch.svg)\\n\\nbody\\n",
        landscape_svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"></svg>',
        title="Stub with SVG",
        filename_base=f"stubsvg-{apps_id}",
    )


def gen_raises(*, apps_id: int):
    raise RuntimeError(f"boom for apps_id={apps_id}")


def gen_picker(*, lang: str = "en", conn: str = "default"):
    return ReportContent(markdown=f"# {lang} / {conn}\\n", title="Picker", filename_base="picker")


REPORTS = [
    ReportDef(
        id="markdown-only",
        scope="stub",
        title="Stub markdown only",
        callable="stub.reports:gen_markdown_only",
        licensed=False,
        formats=("markdown", "pdf"),
        params=(ReportParam(name="apps_id", label="A", type="int", required=True),),
    ),
    ReportDef(
        id="with-svg",
        scope="stub",
        title="Stub with SVG",
        callable="stub.reports:gen_with_svg",
        licensed=False,
        formats=("pdf",),  # PDF-only on purpose to test format gating
        params=(ReportParam(name="apps_id", label="A", type="int", required=True),),
    ),
    ReportDef(
        id="raises",
        scope="stub",
        title="Stub that raises",
        callable="stub.reports:gen_raises",
        licensed=False,
        params=(ReportParam(name="apps_id", label="A", type="int", required=True),),
    ),
    ReportDef(
        id="picker",
        scope="stub",
        title="Stub with dropdowns",
        callable="stub.reports:gen_picker",
        licensed=False,
        formats=("markdown",),
        params=(
            ReportParam(
                name="lang", label="Language", type="string", required=False, default="en",
                options=ReportParamOptions(kind="static", values=(
                    ReportParamOption(value="en", label="English"),
                    ReportParamOption(value="fr", label="Français"),
                )),
            ),
            ReportParam(
                name="conn", label="Connector", type="string", required=False, default="default",
                options=ReportParamOptions(kind="connectors"),
            ),
        ),
    ),
]
'''


def _seed_auth(db_url: str, *, with_perm: bool) -> None:
    """Seed the auth DB with an ``alice`` user, optionally given the report
    run permission. Used to test the 403 path side-by-side with happy."""
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            perms = ["reports:stub:*:run"] if with_perm else []
            await svc.get_or_create_role("reporter", permissions=perms)
            await svc.create_user("alice", password="alicepw", roles=["reporter"])
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def app_factory(tmp_path: Path, monkeypatch):
    """Build a function that returns a fresh FastAPI app whose lifespan
    discovers reports from a stub plugin tree under *tmp_path*.

    *with_perm* — toggle the ``reports:stub:*:run`` permission on the test
    user, to drive the 403 path."""
    # Discovery walks ``$LIBERTY_APPS_DIR/../plugins`` (same convention as
    # ``ensure_plugins_on_sys_path``), so place the stub plugin at
    # ``tmp_path/apps/plugins/stub`` and point ``LIBERTY_APPS_DIR`` at the
    # sibling ``apps/config`` so the parent-of-config join lands right.
    apps_config = tmp_path / "apps" / "config"
    apps_config.mkdir(parents=True)
    plugins_dir = tmp_path / "apps" / "plugins"
    (plugins_dir / "stub" / "reports").mkdir(parents=True)
    (plugins_dir / "stub" / "__init__.py").write_text("")
    (plugins_dir / "stub" / "reports" / "__init__.py").write_text(_STUB_REPORTS_INIT)
    monkeypatch.setenv("LIBERTY_APPS_DIR", str(apps_config))

    # Make sure stub.reports imports cleanly inside the lifespan.
    sys.path.insert(0, str(plugins_dir))

    def build(*, with_perm: bool):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
        # Reset the DB file between calls in the same test if any.
        try:
            os.remove(tmp_path / "auth.db")
        except FileNotFoundError:
            pass
        _seed_auth(db_url, with_perm=with_perm)
        conn_toml = tmp_path / "connectors.toml"
        conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')
        settings = Settings(
            app=AppSettings(name="Liberty Test", static_dir=""),
            connectors=ConnectorSettings(config_path=conn_toml),
            auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        )
        return create_app(settings)

    yield build

    # Clean sys.path + any stub modules so they don't bleed across tests.
    if str(plugins_dir) in sys.path:
        sys.path.remove(str(plugins_dir))
    for mod in list(sys.modules):
        if mod == "stub" or mod.startswith("stub."):
            del sys.modules[mod]


def _login(client: TestClient, username: str = "alice", password: str = "alicepw") -> str:
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# GET /api/reports — list
# --------------------------------------------------------------------------- #


def test_list_returns_only_reports_caller_can_run(app_factory):
    """No permission → empty list (we don't leak the existence of reports the
    user can't run). With permission → all stub reports visible."""
    app_no_perm = app_factory(with_perm=False)
    with TestClient(app_no_perm) as client:
        token = _login(client)
        r = client.get("/api/reports", headers=_auth_headers(token))
        assert r.status_code == 200
        assert r.json()["reports"] == []

    app_with_perm = app_factory(with_perm=True)
    with TestClient(app_with_perm) as client:
        token = _login(client)
        r = client.get("/api/reports", headers=_auth_headers(token))
        assert r.status_code == 200
        ids = {(d["scope"], d["id"]) for d in r.json()["reports"]}
        assert ids == {
            ("stub", "markdown-only"), ("stub", "with-svg"),
            ("stub", "raises"), ("stub", "picker"),
        }


def test_list_filters_by_scope(app_factory):
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.get("/api/reports", params={"scope": "stub"}, headers=_auth_headers(token))
        assert r.status_code == 200
        assert {d["scope"] for d in r.json()["reports"]} == {"stub"}
        # Unknown scope → empty list, not 404.
        r = client.get("/api/reports", params={"scope": "nope"}, headers=_auth_headers(token))
        assert r.status_code == 200
        assert r.json()["reports"] == []


# --------------------------------------------------------------------------- #
# GET /api/reports/{scope}/{id} — metadata
# --------------------------------------------------------------------------- #


def test_get_metadata_happy_path(app_factory):
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.get("/api/reports/stub/markdown-only", headers=_auth_headers(token))
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == "Stub markdown only"
        assert body["formats"] == ["markdown", "pdf"]
        assert body["params"][0]["name"] == "apps_id"


def test_get_metadata_unknown_returns_404(app_factory):
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.get("/api/reports/stub/does-not-exist", headers=_auth_headers(token))
        assert r.status_code == 404


def test_get_metadata_no_permission_returns_403(app_factory):
    app = app_factory(with_perm=False)
    with TestClient(app) as client:
        token = _login(client)
        r = client.get("/api/reports/stub/markdown-only", headers=_auth_headers(token))
        assert r.status_code == 403


# --------------------------------------------------------------------------- #
# POST /api/reports/{scope}/{id}/run — happy paths
# --------------------------------------------------------------------------- #


def test_run_markdown_format_returns_markdown_body(app_factory):
    """``format=markdown`` streams the raw markdown — same byte-for-byte as
    what the report callable returned."""
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.post(
            "/api/reports/stub/markdown-only/run",
            json={"params": {"apps_id": 5}, "format": "markdown"},
            headers=_auth_headers(token),
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/markdown")
        assert 'filename="stub-5.md"' in r.headers["content-disposition"]
        assert "apps_id = 5" in r.text


def test_run_pdf_format_returns_pdf_body(app_factory):
    """``format=pdf`` runs the markdown → PDF pipeline. ``%PDF-`` magic
    confirms WeasyPrint actually produced a PDF, not an HTML error page."""
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.post(
            "/api/reports/stub/markdown-only/run",
            json={"params": {"apps_id": 1}, "format": "pdf"},
            headers=_auth_headers(token),
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"
        assert 'filename="stub-1.pdf"' in r.headers["content-disposition"]
        assert r.content.startswith(b"%PDF-")


def test_run_coerces_string_param_to_int(app_factory):
    """JSON-from-the-frontend often sends ``"5"`` for a number field — the
    framework coerces via :mod:`liberty.coercion`, same as for nomaflow op_kwargs."""
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.post(
            "/api/reports/stub/markdown-only/run",
            json={"params": {"apps_id": "5"}, "format": "markdown"},
            headers=_auth_headers(token),
        )
        assert r.status_code == 200
        assert "apps_id = 5" in r.text  # int(5), not str("5") interpolated


# --------------------------------------------------------------------------- #
# POST /api/reports/{scope}/{id}/run — error paths
# --------------------------------------------------------------------------- #


def test_run_unknown_report_returns_404(app_factory):
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.post(
            "/api/reports/stub/does-not-exist/run",
            json={"params": {"apps_id": 1}, "format": "pdf"},
            headers=_auth_headers(token),
        )
        assert r.status_code == 404


def test_run_no_permission_returns_403(app_factory):
    app = app_factory(with_perm=False)
    with TestClient(app) as client:
        token = _login(client)
        r = client.post(
            "/api/reports/stub/markdown-only/run",
            json={"params": {"apps_id": 1}, "format": "pdf"},
            headers=_auth_headers(token),
        )
        assert r.status_code == 403


def test_run_unsupported_format_returns_422(app_factory):
    """``with-svg`` declares formats=("pdf",) only — requesting markdown
    should 422, not silently fall through to PDF."""
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.post(
            "/api/reports/stub/with-svg/run",
            json={"params": {"apps_id": 1}, "format": "markdown"},
            headers=_auth_headers(token),
        )
        assert r.status_code == 422
        assert "supported" in r.json()["detail"].lower()


def test_run_missing_required_param_returns_422(app_factory):
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.post(
            "/api/reports/stub/markdown-only/run",
            json={"params": {}, "format": "markdown"},
            headers=_auth_headers(token),
        )
        assert r.status_code == 422
        assert "apps_id" in r.json()["detail"]


def test_run_uncoercible_param_returns_422(app_factory):
    """``"not-a-number"`` for an int param → clean 422 with the offending
    key + value in the message, not a 500 / stacktrace."""
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.post(
            "/api/reports/stub/markdown-only/run",
            json={"params": {"apps_id": "not-a-number"}, "format": "markdown"},
            headers=_auth_headers(token),
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "apps_id" in detail and "not-a-number" in detail


def test_run_extra_unknown_param_returns_422(app_factory):
    """A typo'd extra key shouldn't silently slip into **kwargs — that would
    silently drop the operator's intent. Reject."""
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.post(
            "/api/reports/stub/markdown-only/run",
            json={"params": {"apps_id": 1, "typo_extra": "x"}, "format": "markdown"},
            headers=_auth_headers(token),
        )
        assert r.status_code == 422
        assert "typo_extra" in r.json()["detail"]


def test_run_callable_exception_returns_500(app_factory):
    """The callable raised — surface as 500 with a tidy ``{detail}``, log the
    traceback (not the test's job to assert on logs)."""
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.post(
            "/api/reports/stub/raises/run",
            json={"params": {"apps_id": 1}, "format": "pdf"},
            headers=_auth_headers(token),
        )
        assert r.status_code == 500
        detail = r.json()["detail"]
        assert "report failed" in detail.lower()
        assert "RuntimeError" in detail


# --------------------------------------------------------------------------- #
# GET /api/reports/{scope}/{id}/options/{param} — dropdown resolution
# --------------------------------------------------------------------------- #


def test_options_static_returns_declared_values(app_factory):
    """A static-options param resolves to its inline value/label list."""
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.get("/api/reports/stub/picker/options/lang", headers=_auth_headers(token))
        assert r.status_code == 200, r.text
        opts = r.json()["options"]
        assert opts == [
            {"value": "en", "label": "English"},
            {"value": "fr", "label": "Français"},
        ]


def test_options_connectors_lists_pools(app_factory):
    """A ``connectors`` source lists the configured pool names — 'default' here."""
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.get("/api/reports/stub/picker/options/conn", headers=_auth_headers(token))
        assert r.status_code == 200, r.text
        values = [o["value"] for o in r.json()["options"]]
        assert "default" in values


def test_options_404_for_param_without_options(app_factory):
    """A param that declares no options has no options endpoint."""
    app = app_factory(with_perm=True)
    with TestClient(app) as client:
        token = _login(client)
        r = client.get("/api/reports/stub/markdown-only/options/apps_id", headers=_auth_headers(token))
        assert r.status_code == 404


def test_options_requires_run_permission(app_factory):
    """Same gate as run — no permission, no options (no leaking choices)."""
    app = app_factory(with_perm=False)
    with TestClient(app) as client:
        token = _login(client)
        r = client.get("/api/reports/stub/picker/options/lang", headers=_auth_headers(token))
        assert r.status_code == 403


# --------------------------------------------------------------------------- #
# Param-options resolver — query-kind column/row mapping (unit, no DB)
# --------------------------------------------------------------------------- #
def test_resolve_query_options_maps_value_label_case_insensitively():
    """A ``query`` source maps declared value/label columns to the result rows
    case-insensitively (declared APPS_ID/APPS_NAME vs Postgres-lowercased rows),
    over dict rows. Regression: the bare table name isn't runnable — the report
    must point at the '<table>_get' slot — but given a runnable query the mapping
    must produce {value,label}."""
    import asyncio
    from liberty.web import reports as reports_mod
    from liberty.reports.schema import ReportDef, ReportParam, ReportParamOptions

    payload = {
        "columns": [{"name": "apps_id"}, {"name": "apps_name"}],
        "rows": [{"apps_id": 10, "apps_name": "Gerflor"}, {"apps_id": 20, "apps_name": "ACME"}],
    }

    class _Result:
        def to_dict(self):
            return payload

    class _Conn:
        pool_name = "default"

        async def execute(self, query, params):
            return _Result()

    class _Pools:
        def names(self):
            return ["nomasx1", "default"]

    class _Registry:
        pools = _Pools()

        def sql(self, name):
            return _Conn()

    param = ReportParam(
        name="apps_id", label="A", type="int", required=True,
        options=ReportParamOptions(
            kind="query", connector_param="target_connector",
            query="settings_applications_get", value_column="APPS_ID", label_column="APPS_NAME",
        ),
    )
    report = ReportDef(
        id="r", scope="s", title="t", callable="x:y", licensed=False,
        params=(
            param,
            ReportParam(name="target_connector", label="C", type="string", required=False, default="nomasx1"),
        ),
    )
    opts = asyncio.run(
        reports_mod._resolve_options(param, report, _Registry(), {"target_connector": "nomasx1"})
    )
    assert [(o.value, o.label) for o in opts] == [("10", "Gerflor"), ("20", "ACME")]
