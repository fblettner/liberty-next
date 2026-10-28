"""Markdown building blocks of the documentation reports: a bilingual text picker, GFM tables with the
cells made safe, the "reading" callout. The PDF renderer (:mod:`liberty.reports.render`) turns ``##`` into
sections, ``###`` into sub-sections (both in the contents) and ``<aside>`` into a callout; ``nl2br`` is on,
so a paragraph stays on one line."""

from __future__ import annotations

from typing import Any, Iterable, Sequence


class Lang:
    """``t(fr, en)`` picks the report language's text."""

    def __init__(self, lang: str) -> None:
        self.code = "en" if lang == "en" else "fr"

    def __call__(self, fr: str, en: str) -> str:
        return en if self.code == "en" else fr


def cell(value: Any) -> str:
    """One table cell: ``—`` for nothing, lists joined, pipes and line breaks neutralised."""
    if value is None or value == "" or value == [] or value == {}:
        return "—"
    if isinstance(value, bool):
        return "✓" if value else ""
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(v) for v in value)
    text = str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return text.strip() or "—"


def code(value: Any) -> str:
    """Inline code, or ``—``."""
    text = cell(value)
    return text if text == "—" else f"`{text.replace('`', '')}`"


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], *, align_right: Sequence[int] = ()) -> str:
    rows = [list(r) for r in rows]
    if not rows:
        return ""
    head = "| " + " | ".join(cell(h) for h in headers) + " |"
    sep = "| " + " | ".join("---:" if i in align_right else "---" for i in range(len(headers))) + " |"
    body = ["| " + " | ".join(c if isinstance(c, str) and c.startswith("`") else cell(c) for c in r) + " |" for r in rows]
    return "\n".join([head, sep, *body]) + "\n"


def aside(text: str) -> str:
    return f"<aside>\n\n{text}\n\n</aside>\n"


def first_sentence(text: str | None, limit: int = 140) -> str:
    """The first sentence of a docstring-like text, cut at *limit* characters."""
    if not text:
        return ""
    flat = " ".join(text.split())
    end = flat.find(". ")
    flat = flat if end < 0 else flat[: end + 1]
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"
