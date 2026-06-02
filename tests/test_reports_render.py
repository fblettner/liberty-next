"""Tests for :mod:`liberty.reports.render` — the markdown / PDF pipeline ported
from NOMANA-IT's ``notion_md_to_pdf.py``. The CSS + cover layout reproduce the
visual identity of the existing reports, so the tests assert on **structure**
(PDF magic byte, plausible size, landscape page when an SVG is present,
section markers in the HTML body) rather than pixel-perfect output."""
from __future__ import annotations

from liberty.reports.render import (
    BuildOptions,
    build_pdf,
    fix_notion_markdown,
    md_to_html_body,
    render_content,
)
from liberty.reports.schema import ReportContent


# --------------------------------------------------------------------------- #
# fix_notion_markdown — the four Notion-export quirks
# --------------------------------------------------------------------------- #


def test_notion_fix_bullet_glued_to_paragraph() -> None:
    """Notion writes ``Intro:\\n- item`` instead of ``Intro:\\n\\n- item`` —
    without the blank line the markdown parser treats the bullet as part of
    the paragraph. Fix inserts the blank line."""
    out = fix_notion_markdown("Intro :\n- item\n")
    assert "Intro :\n\n- item" in out


def test_notion_fix_em_dash_table_separator() -> None:
    """``|—————|`` is what Notion exports — markdown only recognises ASCII
    dashes as a table separator. Fix swaps em-dashes for hyphens on lines
    that look like a separator row."""
    md = "| A | B |\n| – | – |\n| 1 | 2 |\n"
    out = fix_notion_markdown(md)
    assert "| - | - |" in out


def test_notion_fix_orphan_plus_in_bullet() -> None:
    """``- + something`` ends up parsed as a bullet containing a sublist —
    Notion's export quirk. Fix drops the leading ``+``."""
    out = fix_notion_markdown("- + first item\n- + second item\n")
    assert "- first item" in out
    assert "- second item" in out
    assert "- + " not in out


def test_notion_fix_idempotent() -> None:
    md = "Intro :\n\n- already correct\n"
    assert fix_notion_markdown(md) == fix_notion_markdown(fix_notion_markdown(md))


# --------------------------------------------------------------------------- #
# md_to_html_body — section markers + SVG insertion
# --------------------------------------------------------------------------- #


def test_h2_gets_section_start_class_so_pdf_breaks_page() -> None:
    """The CSS reads ``.section-start`` to insert a page break before every
    top-level heading. md_to_html_body decorates every ``<h2>`` accordingly."""
    md = "# Title\n\n## First Section\n\nbody.\n\n## Second Section\n\nbody.\n"
    html, _ = md_to_html_body(md, None, BuildOptions(title="X"))
    assert html.count('class="section-start"') == 2


def test_landscape_block_replaces_svg_placeholder() -> None:
    """The pipeline inserts a dedicated landscape page wherever the markdown
    embeds ``![*.svg](*)``. **The marker pattern matches on the ALT TEXT** so
    the convention is to name the SVG file in both slots:
    ``![architecture.svg](path/architecture.svg)``."""
    md = "# T\n\nbefore.\n\n![architecture.svg](path/architecture.svg)\n\nafter.\n"
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"></svg>'
    html, _ = md_to_html_body(md, svg, BuildOptions(landscape_svg_title="Architecture"))
    assert 'class="landscape-page"' in html
    assert "Architecture" in html
    assert "<svg" in html


def test_no_landscape_block_when_svg_omitted() -> None:
    """Without an SVG, the marker should be cleanly removed — not leak the
    HTML comment into the output. Same alt-text convention applies."""
    md = "# T\n\n![architecture.svg](path/architecture.svg)\n"
    html, _ = md_to_html_body(md, None, BuildOptions())
    assert "ARCHITECTURE_SVG_PLACEHOLDER" not in html
    assert 'class="landscape-page"' not in html


# --------------------------------------------------------------------------- #
# build_pdf
# --------------------------------------------------------------------------- #


def test_build_pdf_minimal_returns_pdf_bytes() -> None:
    """Smoke check that WeasyPrint runs end-to-end with the bundled CSS:
    output starts with ``%PDF-`` and is large enough to contain at least the
    cover + TOC + one content page."""
    md = "# Title\n\n## Section\n\nbody.\n"
    pdf = build_pdf(md, options=BuildOptions(title="Test"))
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 5_000


def test_build_pdf_with_svg_produces_landscape_page() -> None:
    """When an SVG is provided alongside markdown that references it (alt
    text ending in ``.svg`` per the marker convention), the PDF file grows
    materially compared to the same markdown without an SVG (extra landscape
    page with rendered vector content)."""
    md = (
        "# T\n\n## A\n\nbody.\n\n"
        "![architecture.svg](path/architecture.svg)\n\n"
        "## B\n\nmore.\n"
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
        '<rect x="0" y="0" width="200" height="100" fill="blue"/></svg>'
    )
    without = build_pdf(md, svg_text=None, options=BuildOptions())
    with_svg = build_pdf(md, svg_text=svg, options=BuildOptions())
    assert with_svg.startswith(b"%PDF-")
    assert len(with_svg) > len(without)


# --------------------------------------------------------------------------- #
# render_content — the framework's format dispatcher
# --------------------------------------------------------------------------- #


def test_render_content_markdown_format_passes_through() -> None:
    """Markdown output is byte-equal to ``ReportContent.markdown.encode()`` —
    no Notion fixes applied (those are PDF-only because the markdown parser
    is what needs them; raw markdown downloads stay verbatim)."""
    md = "# Title\n\nbody.\n"
    c = ReportContent(markdown=md, filename_base="x")
    body, ct, fn = render_content(c, "markdown")
    assert body == md.encode("utf-8")
    assert ct == "text/markdown; charset=utf-8"
    assert fn == "x.md"


def test_render_content_pdf_format_uses_defaults_then_overrides() -> None:
    """Cover defaults flow from ``app_name`` + ``report_title``; per-call
    ``pdf_options`` override individual keys."""
    md = "# Hello\n\nbody.\n"
    c = ReportContent(
        markdown=md,
        filename_base="audit-1",
        pdf_options={"subtitle": "Custom subtitle", "primary_color": "#0b3a82"},
    )
    body, ct, fn = render_content(
        c, "pdf",
        app_name="Liberty Next",
        report_title="Audit Licences",
    )
    assert body.startswith(b"%PDF-")
    assert ct == "application/pdf"
    assert fn == "audit-1.pdf"


def test_render_content_drops_unknown_pdf_options() -> None:
    """A typo'd pdf_options key shouldn't crash BuildOptions — we filter
    unknown keys silently (matches BuildOptions' dataclass shape)."""
    c = ReportContent(
        markdown="# x",
        filename_base="x",
        pdf_options={"unknown_field_name": "oops", "primary_color": "#000"},
    )
    body, _ct, _fn = render_content(c, "pdf", app_name="Liberty Next")
    assert body.startswith(b"%PDF-")


def test_render_content_branding_overrides_framework_defaults_but_not_plugin() -> None:
    """Phase 3b precedence: operator-curated branding (Settings → Reports →
    Branding, persisted in ``[reports.branding]`` of app.toml) sits BETWEEN
    framework defaults and the plugin's per-report ``pdf_options``.

    * Framework defaults — title / cover_brand / author (least specific)
    * Branding from this call — overrides defaults but yields to plugin
    * Plugin's ``content.pdf_options`` — most specific, always wins

    Validated by capturing the BuildOptions instance the merge produces."""
    from unittest.mock import patch
    from liberty.reports import render as render_mod

    captured: dict[str, object] = {}

    def fake_build_pdf(_md: str, _svg, opts: render_mod.BuildOptions) -> bytes:
        captured["opts"] = opts
        return b"%PDF-1.4 fake"

    c = ReportContent(
        markdown="# x",
        filename_base="x",
        pdf_options={"primary_color": "#FF0000"},  # plugin pick, must survive
    )
    branding = {
        "author": "ACME Consulting",           # overrides app_name
        "primary_color": "#00FF00",            # SHOULD be overridden by plugin
        "primary_color_light": "#00AA00",      # plugin doesn't set → branding wins
        "cover_eyebrow": "Rapport d'audit",    # framework default → branding wins
        "footer_left": "",                     # empty → ignored (no clobber)
    }
    with patch.object(render_mod, "build_pdf", side_effect=fake_build_pdf):
        render_content(
            c, "pdf",
            app_name="Liberty Next",
            report_title="Audit",
            branding=branding,
        )

    opts: render_mod.BuildOptions = captured["opts"]  # type: ignore[assignment]
    # Plugin wins for primary_color
    assert opts.primary_color == "#FF0000"
    # Branding wins for everything not set by the plugin
    assert opts.author == "ACME Consulting"
    assert opts.primary_color_light == "#00AA00"
    assert opts.cover_eyebrow == "Rapport d'audit"
    # Empty branding value didn't clobber the framework default
    assert opts.footer_left == "Confidentiel"


def test_render_content_unknown_format_raises_value_error() -> None:
    """Defensive — the web layer validates format against ``ReportDef.formats``
    before calling this, but a programming error shouldn't silently produce
    junk."""
    c = ReportContent(markdown="# x")
    try:
        render_content(c, "html")  # type: ignore[arg-type]
    except ValueError as exc:
        assert "unsupported" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for unsupported format")
