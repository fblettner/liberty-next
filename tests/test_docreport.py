"""The documentation reports (C12): the technical report (the release) and the implementation report
(this install), Markdown rendered like the applications' reports, superuser only."""

from __future__ import annotations

from fastapi.testclient import TestClient

from liberty.docreport import _renumber, catalog
from liberty.docreport.implementation import MASK, _mask
from liberty.docreport.md import cell, table
from tests.test_web_admin_ops import _h, env  # noqa: F401 — the app fixture


def test_catalog_lists_both_reports_in_both_languages() -> None:
    fr, en = catalog("fr"), catalog("en")
    assert [r["kind"] for r in fr] == ["implementation", "technical"]
    assert fr[1]["title"] == "Rapport technique" and en[1]["title"] == "Technical report"
    tech = {s["id"]: s for s in en[1]["sections"]}
    assert {"data", "configuration", "api", "appendix"} <= set(tech)
    assert {o["id"] for o in en[1]["options"]} == {"api_detail", "snippets"}


def test_markdown_helpers_and_masking() -> None:
    assert cell("a|b\nc") == "a\\|b c" and cell(None) == "—" and cell(["x", "y"]) == "x, y"
    assert table(("A", "B"), []) == ""
    assert table(("A", "B"), [(1, "`x`")], align_right=(0,)).splitlines()[1] == "| ---: | --- |"
    assert _mask("password", "s3cret") == MASK and _mask("key", "ENC:abc") == MASK
    assert _mask("url", "postgresql+asyncpg://u:pw@h:5432/db") == f"postgresql+asyncpg://u:{MASK}@h:5432/db"
    assert _mask("password", "${DB_PASSWORD}") == "${DB_PASSWORD}"   # a reference, not the secret
    assert _renumber("## 3. A\n### 3.1 x\n## 7. B\n### 7.2 y") == "## 1. A\n### 1.1 x\n## 2. B\n### 2.2 y\n"


def test_reports_render_as_markdown_superuser_only(env) -> None:  # noqa: F811
    app, _tmp = env
    with TestClient(app) as client:
        assert client.get("/admin/dev/reports", headers=_h(client, "reader")).status_code == 403
        listed = client.get("/admin/dev/reports?lang=en", headers=_h(client, "admin")).json()["reports"]
        assert [r["kind"] for r in listed] == ["implementation", "technical"]

        tech = client.get("/admin/dev/reports/technical?format=markdown&lang=en&api_detail=true&snippets=true", headers=_h(client, "admin"))
        assert tech.status_code == 200 and tech.headers["content-type"].startswith("text/markdown")
        text = tech.text
        for title in ("## 1. Scope", "## 3. Data model", "## 4. Configuration reference", "## 5. API reference", "## 6. Command line", "## 8. Appendix"):
            assert title in text
        assert "nomaflow_job_runs" in text and "`/admin/dev/reports/{kind}`" in text and "liberty-admin" in text

        impl = client.get("/admin/dev/reports/implementation?format=markdown&sections=context,security,appendixConfig", headers=_h(client, "admin")).text
        assert "## 1. Contexte" in impl and "## 2. Sécurité" in impl and "## 3. Annexe A" in impl
        assert "admin" in impl and "jwt_secret" in impl and MASK in impl   # the users are listed, the secret is not

        full = client.get("/admin/dev/reports/implementation?format=markdown", headers=_h(client, "admin"))
        assert full.status_code == 200 and "## 4. Écrans" in full.text
        assert client.get("/admin/dev/reports/nope", headers=_h(client, "admin")).status_code == 404
