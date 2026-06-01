"""Markdown → PDF rendering pipeline.

Ported verbatim from NOMANA-IT's ``notion_md_to_pdf.py`` (the helper that
produced the Audit Licences JD Edwards report). Same library stack
(:mod:`weasyprint` + :mod:`markdown`), same CSS, same Notion-quirk fixes,
same cover / TOC / landscape-SVG layout. The look & feel of the existing
PDFs is preserved byte-for-byte.

Public entry points used by the web layer:

* :func:`render_content` — :class:`ReportContent` → ``(bytes, content_type, filename)``
  for either ``"markdown"`` or ``"pdf"``.
* :func:`build_pdf` — the lower-level routine; reusable from a CLI / a job
  that wants to produce a PDF without going through the web layer.
* :class:`BuildOptions` — cover metadata, headers, colours.

Plugins typically only build a :class:`~liberty.reports.ReportContent` and let
the framework call ``render_content`` — they don't import this module.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Optional

import markdown
from weasyprint import CSS, HTML
from weasyprint.text.fonts import FontConfiguration

from liberty.reports.schema import OutputFormat, ReportContent


# ---------------------------------------------------------------------------
# Public configuration object
# ---------------------------------------------------------------------------
@dataclass
class BuildOptions:
    """Document metadata + rendering options. See the docstring of every field
    for usage; defaults match the visual style of the existing NOMANA-IT
    reports (deep blue gradient cover, narrow leader-dot TOC, status pills).
    """

    # Cover page
    title: str = "Document"
    subtitle: str = ""
    client: str = ""
    author: str = ""
    date: str = ""
    version: str = "1.0"
    confidential: bool = True
    cover_eyebrow: str = "Rapport"
    cover_brand: Optional[str] = None  # defaults to `author` if None
    cover_ref: str = "Document confidentiel"

    # Headers & footers (use {title}, {author}, {date} placeholders if needed)
    header_left: Optional[str] = None    # defaults to `title`
    header_right: Optional[str] = None   # defaults to "{author} · {date}"
    footer_left: str = "Confidentiel"

    # Landscape SVG insertion
    landscape_svg_title: str = "Architecture"
    svg_width_mm: float = 240.0
    svg_height_mm: float = 157.0

    # Marker pattern used in the markdown to indicate where the landscape
    # SVG should be inserted. Default matches "![*.svg](*)" image syntax.
    svg_image_pattern: str = r"!\[[^\]]*\.svg\]\([^)]+\)"

    # Theme color (hex). Used for headings, table headers, cover gradient base.
    primary_color: str = "#0b3a82"
    primary_color_light: str = "#2563eb"


# ---------------------------------------------------------------------------
# Markdown pre-processing — Notion-export quirks
# ---------------------------------------------------------------------------
def fix_notion_markdown(md_text: str) -> str:
    """Repair four common Notion-export quirks before markdown rendering:

    1. Bullet lists glued to the preceding paragraph (missing blank line)
    2. Bullet lists inside blockquotes losing their ``>`` prefix
    3. ``- + text`` producing a spurious nested list
    4. Tables with em-dash separators instead of ASCII dashes

    Returns a corrected markdown string. Idempotent — safe to call twice.
    """
    # Bullet lists glued to a paragraph (no blank line between):
    #     "Intro :\n- item"  →  "Intro :\n\n- item"
    md_text = re.sub(
        r"^(?!\s*[-|*+]|\s*\d+\.|\s*$|\s*>|\s*#|\s*```)(.+)\n(- )",
        r"\1\n\n\2",
        md_text,
        flags=re.MULTILINE,
    )

    # Bullet lists inside a blockquote, missing the separator empty quote line:
    #     "> Intro :\n> - item"  →  "> Intro :\n>\n> - item"
    md_text = re.sub(
        r"^(>\s+(?!- ).+)\n(>\s+- )",
        r"\1\n>\n\2",
        md_text,
        flags=re.MULTILINE,
    )

    # Orphan bullets directly after a blockquote line (without the > prefix):
    #     "> Intro :\n- item\n- item"
    # → re-attach them inside the blockquote.
    def _fix_orphan_bullets(match: re.Match) -> str:
        header = match.group(1)
        bullets = match.group(2)
        fixed = "\n".join(
            "> " + line for line in bullets.splitlines() if line.strip()
        )
        return f"{header}\n>\n{fixed}\n"

    md_text = re.sub(
        r"^(>\s.+[:].*)\n((?:- .+\n)+)",
        _fix_orphan_bullets,
        md_text,
        flags=re.MULTILINE,
    )

    # "- + text" → "- text" (the "+" is interpreted as a nested list marker)
    md_text = re.sub(
        r"^(>?\s*-\s+)\+\s+",
        r"\1",
        md_text,
        flags=re.MULTILINE,
    )

    # Em-dashes used as table separators are not recognised. We only normalise
    # them on lines that look like a table separator (start with "|", contain
    # only dash-like chars, colons, and pipes).
    def _fix_table_sep(match: re.Match) -> str:
        line = match.group(0)
        return line.replace("–", "-").replace("—", "-")

    md_text = re.sub(
        r"^\|[\s\-–—:|]+\|\s*$",
        _fix_table_sep,
        md_text,
        flags=re.MULTILINE,
    )

    return md_text


# ---------------------------------------------------------------------------
# Markdown → HTML pipeline
# ---------------------------------------------------------------------------
# Status pill rendering: maps emoji indicators to coloured pills.
_PILL_MAP = {
    "\U0001F534": '<span class="pill pill-red">●</span>',         # 🔴
    "\U0001F7E0": '<span class="pill pill-orange">●</span>',      # 🟠
    "\U0001F7E1": '<span class="pill pill-yellow">●</span>',      # 🟡
    "\U0001F7E2": '<span class="pill pill-green">●</span>',       # 🟢
    "⚠️": '<span class="pill pill-warn">!</span>',     # ⚠️
    "❌": '<span class="pill pill-red">✕</span>',            # ❌
}


def _normalize_svg(svg_text: str, width_mm: float, height_mm: float) -> str:
    """Ensure the SVG has explicit dimensions so WeasyPrint can render it."""
    open_tag = re.match(r"<svg\b[^>]*>", svg_text)
    if open_tag and "width=" not in open_tag.group(0):
        svg_text = svg_text.replace(
            "<svg ",
            f'<svg width="{width_mm}mm" height="{height_mm}mm" '
            f'preserveAspectRatio="xMidYMid meet" ',
            1,
        )
    return svg_text


def _tag_callouts_by_color(html: str) -> str:
    """Add a CSS class to blockquotes based on the first pill colour found
    inside, so we can colour-code callouts (red, orange, yellow, green).
    """
    def repl(match: re.Match) -> str:
        inner = match.group(1)
        cls = "callout"
        for color in ("red", "orange", "yellow", "green", "warn"):
            if f"pill-{color}" in inner:
                cls += f" callout-{color}"
                break
        return f'<blockquote class="{cls}">{inner}</blockquote>'

    return re.sub(r"<blockquote>(.*?)</blockquote>", repl, html, flags=re.DOTALL)


def md_to_html_body(
    md_text: str,
    svg_text: Optional[str],
    options: BuildOptions,
) -> tuple[str, str]:
    """Convert markdown to the HTML body and the TOC HTML fragment.

    Returns ``(html_body, toc_html)``.
    """
    # Convert Notion <aside>...</aside> blocks to <div class="aside">.
    # markdown="1" tells the markdown parser to re-parse the inner content.
    md_text = re.sub(
        r"<aside>\s*\n+",
        '\n<div class="aside" markdown="1">\n\n',
        md_text,
    )
    md_text = re.sub(r"\n*</aside>", "\n\n</div>\n", md_text)

    # Replace SVG image references with a placeholder that we'll fill in later
    md_text = re.sub(
        options.svg_image_pattern,
        "<!-- ARCHITECTURE_SVG_PLACEHOLDER -->",
        md_text,
    )

    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "toc",
            "attr_list",
            "md_in_html",
            "sane_lists",
            "nl2br",
        ],
        extension_configs={
            "toc": {
                "toc_depth": "2-3",
                "anchorlink": False,
                "permalink": False,
            }
        },
    )

    html_body = md.convert(md_text)
    toc_html = md.toc

    # Replace emoji pills (after markdown rendering, to avoid breaking HTML)
    for emoji, pill in _PILL_MAP.items():
        html_body = html_body.replace(emoji, pill)
        toc_html = toc_html.replace(emoji, pill)

    # Insert the landscape SVG page
    if svg_text and "<!-- ARCHITECTURE_SVG_PLACEHOLDER -->" in html_body:
        svg_text = _normalize_svg(svg_text, options.svg_width_mm, options.svg_height_mm)
        landscape_block = f"""
<div class="landscape-page">
  <h3 class="landscape-title">{options.landscape_svg_title}</h3>
  <div class="svg-container">
{svg_text}
  </div>
</div>
<!-- end-landscape -->
"""
        html_body = html_body.replace(
            "<!-- ARCHITECTURE_SVG_PLACEHOLDER -->",
            landscape_block,
        )
    else:
        # No SVG provided: just clean up the placeholder
        html_body = html_body.replace("<!-- ARCHITECTURE_SVG_PLACEHOLDER -->", "")

    # Force a page break before every top-level section (h2)
    html_body = re.sub(
        r'<h2 id="([^"]+)">',
        r'<h2 id="\1" class="section-start">',
        html_body,
    )

    # Special case: the h2 immediately after the landscape page should NOT
    # have page-break-before, otherwise the orientation change + the break
    # produce a blank page. We also strip any <hr/> that markdown inserted
    # from a "---" separator between the landscape block and the next h2.
    html_body = re.sub(
        r'(<!-- end-landscape -->)\s*(?:<hr\s*/?>\s*)?<h2 id="([^"]+)" class="section-start">',
        r'\1<h2 id="\2" class="section-after-landscape">',
        html_body,
    )

    html_body = _tag_callouts_by_color(html_body)
    return html_body, toc_html


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------
def _build_full_html(
    html_body: str,
    toc_html: str,
    options: BuildOptions,
) -> str:
    """Wrap the body & TOC with the cover page and TOC page."""
    brand = options.cover_brand or options.author or ""
    meta_rows = []
    if options.client:
        meta_rows.append(f"<tr><td>Client</td><td>{options.client}</td></tr>")
    if options.date:
        meta_rows.append(f"<tr><td>Date</td><td>{options.date}</td></tr>")
    if options.author:
        meta_rows.append(f"<tr><td>Auteur</td><td>{options.author}</td></tr>")
    if options.version:
        meta_rows.append(f"<tr><td>Version</td><td>{options.version}</td></tr>")
    meta_html = "\n".join(meta_rows)

    confidential_footer = (
        "Document confidentiel — Reproduction et diffusion interdites sans autorisation"
        if options.confidential
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<title>{options.title}</title>
</head>
<body>

<section class="cover">
  <div class="cover-top">
    <div class="cover-brand">{brand}</div>
    <div class="cover-ref">{options.cover_ref}</div>
  </div>

  <div class="cover-center">
    <div class="cover-eyebrow">{options.cover_eyebrow}</div>
    <h1 class="cover-title">{options.title}</h1>
    <div class="cover-subtitle">{options.subtitle}</div>
    <div class="cover-rule"></div>
    <table class="cover-meta">{meta_html}</table>
  </div>

  <div class="cover-bottom">{confidential_footer}</div>
</section>

<section class="toc-page">
  <h1 class="toc-heading">Table des matières</h1>
  {toc_html}
</section>

<main class="content">
{html_body}
</main>

</body>
</html>
"""


def _build_css(options: BuildOptions) -> str:
    """Build the CSS stylesheet, parameterised by *options*.

    The CSS body is intentionally preserved verbatim from NOMANA-IT's working
    template — every cosmetic detail (cover gradient stops, pill sizes, leader
    dots, callout colour palette) reproduces the visual identity of the
    existing reports. Don't edit casually; cf. the original
    ``notion_md_to_pdf.py``.
    """
    primary = options.primary_color
    primary_light = options.primary_color_light
    header_left = options.header_left or options.title
    header_right = options.header_right or (
        f"{options.author} · {options.date}" if options.author and options.date
        else options.author or options.date
    )
    footer_left = options.footer_left

    return f"""
/* ============ PAGE LAYOUT ============ */
@page {{
  size: A4;
  margin: 22mm 18mm 22mm 18mm;
  @top-left {{
    content: "{header_left}";
    font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 8.5pt; color: #6b7280;
    border-bottom: 0.5pt solid #e5e7eb;
    padding-bottom: 3mm; width: 110mm;
  }}
  @top-right {{
    content: "{header_right}";
    font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 8.5pt; color: #6b7280;
    border-bottom: 0.5pt solid #e5e7eb;
    padding-bottom: 3mm; width: 60mm; white-space: nowrap;
  }}
  @bottom-left {{
    content: "{footer_left}";
    font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 8.5pt; color: #9ca3af;
  }}
  @bottom-right {{
    content: "Page " counter(page) " / " counter(pages);
    font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 8.5pt; color: #6b7280; white-space: nowrap;
  }}
}}

@page :first {{
  margin: 0;
  @top-left {{ content: ""; border-bottom: none; }}
  @top-right {{ content: ""; border-bottom: none; }}
  @bottom-left {{ content: ""; }}
  @bottom-right {{ content: ""; }}
}}

@page landscape {{
  size: A4 landscape;
  margin: 18mm 18mm 18mm 18mm;
  @top-left {{
    content: "{header_left} — {options.landscape_svg_title}";
    font-size: 8.5pt; color: #6b7280;
    border-bottom: 0.5pt solid #e5e7eb;
    padding-bottom: 3mm; width: 180mm; white-space: nowrap;
  }}
  @top-right {{
    content: "{header_right}";
    font-size: 8.5pt; color: #6b7280;
    border-bottom: 0.5pt solid #e5e7eb;
    padding-bottom: 3mm; width: 60mm; white-space: nowrap;
  }}
  @bottom-right {{
    content: "Page " counter(page) " / " counter(pages);
    font-size: 8.5pt; color: #6b7280; white-space: nowrap;
  }}
  @bottom-left {{
    content: "{footer_left}";
    font-size: 8.5pt; color: #9ca3af;
  }}
}}

/* ============ TYPOGRAPHY ============ */
html, body {{
  font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 10pt; line-height: 1.45;
  color: #1f2937; margin: 0; padding: 0;
}}

/* ============ COVER PAGE ============ */
.cover {{
  page: first-page;
  page-break-after: always;
  height: 297mm; width: 210mm;
  display: flex; flex-direction: column;
  background: linear-gradient(160deg, {primary} 0%, #1e40af 45%, {primary_light} 100%);
  color: #fff;
  position: relative;
  box-sizing: border-box;
  padding: 28mm 22mm;
}}
.cover-top {{
  display: flex; justify-content: space-between;
  font-size: 10pt; letter-spacing: 1.5pt;
  text-transform: uppercase; opacity: 0.85;
}}
.cover-brand {{ font-weight: 700; }}
.cover-ref {{ font-style: italic; opacity: 0.7; }}
.cover-center {{ margin-top: 60mm; }}
.cover-eyebrow {{
  font-size: 11pt; letter-spacing: 4pt;
  text-transform: uppercase; opacity: 0.7;
  margin-bottom: 8mm;
}}
.cover-title {{
  font-size: 30pt; font-weight: 800; line-height: 1.15;
  margin: 0 0 8mm 0; letter-spacing: -0.5pt;
}}
.cover-subtitle {{
  font-size: 14pt; font-weight: 300; opacity: 0.9;
  line-height: 1.4; max-width: 140mm;
}}
.cover-rule {{
  height: 2pt; width: 60mm; background: #fff;
  margin: 14mm 0 12mm 0; opacity: 0.8;
}}
.cover-meta {{ border-collapse: collapse; font-size: 11pt; }}
.cover-meta td {{ padding: 2mm 0; vertical-align: top; }}
.cover-meta td:first-child {{
  text-transform: uppercase; letter-spacing: 1.5pt;
  font-size: 9pt; opacity: 0.7;
  padding-right: 16mm; width: 30mm;
}}
.cover-meta td:last-child {{ font-weight: 600; }}
.cover-bottom {{
  position: absolute; bottom: 18mm; left: 22mm; right: 22mm;
  font-size: 9pt; opacity: 0.7;
  border-top: 1pt solid rgba(255,255,255,0.3);
  padding-top: 4mm;
}}

/* ============ TABLE OF CONTENTS ============ */
.toc-page {{ page-break-after: always; }}
.toc-heading {{
  font-size: 22pt; color: {primary};
  border-bottom: 2pt solid {primary};
  padding-bottom: 4mm; margin: 0 0 8mm 0;
}}
.toc-page .toc {{ font-size: 10.5pt; }}
.toc-page .toc > ul {{ list-style: none; padding-left: 0; }}
.toc-page .toc ul ul {{ list-style: none; padding-left: 6mm; margin: 1mm 0; }}
.toc-page .toc li {{ margin: 1.5mm 0; }}
.toc-page .toc > ul > li {{ margin: 3mm 0 1mm 0; }}
.toc-page .toc > ul > li > a {{
  font-weight: 700; color: {primary}; font-size: 11pt;
}}
.toc-page .toc a {{ text-decoration: none; color: #1f2937; }}
.toc-page .toc a::after {{
  content: leader('. ') target-counter(attr(href), page);
  color: #6b7280; font-weight: 400;
}}

/* ============ CONTENT TYPOGRAPHY ============ */
.content h1 {{
  font-size: 18pt; color: {primary};
  margin: 8mm 0 4mm 0;
  border-bottom: 1.5pt solid {primary}; padding-bottom: 2mm;
}}
.content h2 {{
  font-size: 15pt; color: {primary};
  margin: 7mm 0 3mm 0; padding-bottom: 1.5mm;
  border-bottom: 0.8pt solid #c7d2fe;
}}
.content h2.section-start {{
  page-break-before: always; margin-top: 0;
}}
.content h3 {{
  font-size: 12.5pt; color: #1e3a8a;
  margin: 6mm 0 2mm 0; font-weight: 700;
}}
.content h4 {{
  font-size: 11pt; color: #1e3a8a;
  margin: 4mm 0 1.5mm 0; font-weight: 700;
}}
.content p {{ margin: 2mm 0; text-align: justify; }}
.content strong {{ font-weight: 700; color: #111827; }}
.content em {{ font-style: italic; }}
.content ul, .content ol {{ margin: 2mm 0 3mm 0; padding-left: 7mm; }}
.content li {{ margin: 1mm 0; }}
.content hr {{
  border: none; border-top: 0.5pt solid #e5e7eb; margin: 5mm 0;
}}

/* ============ CODE ============ */
.content code {{
  background: #f3f4f6;
  border: 0.5pt solid #e5e7eb;
  border-radius: 2pt;
  padding: 0.3mm 1.2mm;
  font-family: "SF Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace;
  font-size: 9pt; color: #be123c;
}}
.content pre {{
  background: #f9fafb;
  border: 0.5pt solid #e5e7eb;
  border-left: 2pt solid {primary_light};
  border-radius: 2pt;
  padding: 3mm 4mm;
  font-size: 8.5pt;
  overflow: hidden;
  page-break-inside: avoid;
}}
.content pre code {{
  background: transparent; border: none; padding: 0; color: #1f2937;
}}

/* ============ TABLES ============ */
.content table {{
  border-collapse: collapse; width: 100%;
  margin: 4mm 0; font-size: 9pt;
  page-break-inside: auto;
}}
.content thead {{ display: table-header-group; }}
.content tr {{ page-break-inside: avoid; }}
.content th {{
  background: {primary}; color: #fff;
  font-weight: 600; text-align: left;
  padding: 2mm 2.5mm; border: 0.5pt solid {primary};
  font-size: 9pt; vertical-align: top;
}}
.content td {{
  padding: 1.8mm 2.5mm;
  border: 0.5pt solid #e5e7eb;
  vertical-align: top; font-size: 9pt;
}}
.content tbody tr:nth-child(even) td {{ background: #f8fafc; }}

/* ============ CALLOUTS ============ */
.content blockquote, .content .aside {{
  margin: 3mm 0; padding: 3mm 4mm;
  border-left: 3pt solid #6b7280;
  background: #f9fafb;
  border-radius: 2pt;
  font-size: 9.5pt;
  page-break-inside: avoid;
}}
.content blockquote p {{ margin: 1mm 0; }}
.content blockquote ul, .content blockquote ol {{ margin: 1mm 0; }}
.content .callout-red    {{ border-left-color: #dc2626; background: #fef2f2; }}
.content .callout-orange {{ border-left-color: #ea580c; background: #fff7ed; }}
.content .callout-yellow {{ border-left-color: #ca8a04; background: #fefce8; }}
.content .callout-green  {{ border-left-color: #16a34a; background: #f0fdf4; }}
.content .callout-warn   {{ border-left-color: #b45309; background: #fef3c7; }}
.content .aside          {{ border-left-color: {primary};  background: #eff6ff; }}

/* ============ STATUS PILLS ============ */
.pill {{
  display: inline-block;
  width: 9pt; height: 9pt;
  line-height: 8.5pt; text-align: center;
  border-radius: 50%; color: white;
  font-size: 6.5pt; font-weight: 700;
  vertical-align: middle; margin-right: 1.5pt;
}}
.pill-red {{ background: #dc2626; }}
.pill-orange {{ background: #ea580c; }}
.pill-yellow {{ background: #ca8a04; }}
.pill-green {{ background: #16a34a; }}
.pill-warn {{ background: #b45309; }}

/* ============ LANDSCAPE PAGE ============ */
.landscape-page {{
  page: landscape;
  page-break-before: always;
  page-break-inside: avoid;
}}
.landscape-title {{
  font-size: 13pt; color: {primary};
  margin: 0 0 3mm 0; padding-bottom: 1.5mm;
  border-bottom: 1pt solid #c7d2fe;
  page-break-after: avoid;
}}
.svg-container {{
  text-align: center; margin-top: 1mm;
  page-break-inside: avoid; page-break-before: avoid;
}}
.svg-container svg {{ display: block; margin: 0 auto; }}

/* ============ LINKS ============ */
a {{ color: #1d4ed8; text-decoration: none; }}
h1, h2, h3, h4 {{ page-break-after: avoid; }}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def build_pdf(
    markdown_text: str,
    svg_text: Optional[str] = None,
    options: Optional[BuildOptions] = None,
    *,
    apply_notion_fixes: bool = True,
) -> bytes:
    """Build a PDF report from markdown text. Returns the PDF as bytes.

    Args:
        markdown_text: The markdown source (typically a Notion-style export
            with embedded ``![*.svg](*)`` references for the landscape page).
        svg_text: Optional SVG to embed on a landscape page. The position in
            the document is determined by ``options.svg_image_pattern``
            (default: any ``![*.svg](*)``).
        options: :class:`BuildOptions` (metadata, theme, headers / footers).
        apply_notion_fixes: When True (default), runs :func:`fix_notion_markdown`
            on the input to repair common Notion-export quirks.

    Returns:
        The generated PDF as bytes — ready to stream or persist.
    """
    if options is None:
        options = BuildOptions()

    if apply_notion_fixes:
        markdown_text = fix_notion_markdown(markdown_text)

    html_body, toc_html = md_to_html_body(markdown_text, svg_text, options)
    full_html = _build_full_html(html_body, toc_html, options)
    css_text = _build_css(options)

    font_config = FontConfiguration()
    pdf_buffer = io.BytesIO()
    HTML(string=full_html).write_pdf(
        pdf_buffer,
        stylesheets=[CSS(string=css_text, font_config=font_config)],
        font_config=font_config,
    )
    return pdf_buffer.getvalue()


def render_content(
    content: ReportContent,
    fmt: OutputFormat,
    *,
    app_name: str = "",
    report_title: str = "",
) -> tuple[bytes, str, str]:
    """Render *content* to *fmt*. Returns ``(body, content_type, filename)``.

    The framework calls this from the web layer after dispatching the report
    callable. ``app_name`` and ``report_title`` provide the cover-page
    defaults when ``content.pdf_options`` doesn't override them.
    """
    if fmt == "markdown":
        body = content.markdown.encode("utf-8")
        filename = f"{content.filename_base or 'report'}.md"
        return body, "text/markdown; charset=utf-8", filename

    if fmt == "pdf":
        # Cover defaults: report title → ReportDef.title → app name. Per-call
        # ``pdf_options`` always wins.
        defaults = {
            "title": content.title or report_title or "Report",
            "cover_brand": app_name,
            "author": app_name,
        }
        merged = {**defaults, **(content.pdf_options or {})}
        # Only keep BuildOptions-known keys to avoid silently swallowing typos.
        known = {f.name for f in BuildOptions.__dataclass_fields__.values()}
        merged = {k: v for k, v in merged.items() if k in known}
        opts = BuildOptions(**merged)
        body = build_pdf(content.markdown, content.landscape_svg, opts)
        filename = f"{content.filename_base or 'report'}.pdf"
        return body, "application/pdf", filename

    raise ValueError(f"unsupported output format: {fmt!r}")
