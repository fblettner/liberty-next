"""Charts drawn server-side, as SVG, for the PDF exports (an Explorer analysis or dashboard, a
config dashboard, the digest a job renders). The page draws its charts with Recharts in the
theme's colours; a document needs the same figure in **print colours** with a legend, compact
axis ticks and the values on the bars — so the picture is drawn here from the section's data
(x labels, one list of values per series), never captured from the page.

Three kinds, the Explorer's: ``bar`` (grouped, ``stacked``, ``horizontal``), ``line`` and
``pie`` (``donut``). The palette and the proportions follow nomaubl's report charts. Pure
functions, no I/O; :func:`render_chart` returns the ``<svg>`` text (``None`` without data).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal
from xml.sax.saxutils import escape

ChartKind = Literal["bar", "line", "pie"]

PALETTE = ["#0b3a8c", "#109e8a", "#e88a1a", "#dc2626", "#7c3aed", "#9ca3af", "#0ea5e9", "#b45309"]
INK = "#1f2937"
MUTED = "#6b7280"
GRID = "#d9dee7"
FONT = "Helvetica, Arial, sans-serif"
WIDTH = 560                     # the drawing unit: scaled by the viewBox to the page width (174 mm → 1 unit ≈ 0.9 pt)
PIE_MAX_SLICES = 12             # beyond: the smallest slices become "Others"


@dataclass(slots=True)
class Series:
    name: str
    values: list[float | None] = field(default_factory=list)
    color: str | None = None


# ── numbers ────────────────────────────────────────────────────────────────────


def fmt_number(v: float | int | None, lang: str | None = None, decimals: int | None = None) -> str:
    """A number for a document: thousands separated, FR with a space and a decimal comma,
    EN with a comma and a point. *decimals* ``None`` keeps up to 2 and strips the zeros."""
    if v is None:
        return ""
    fr = (lang or "en").lower().startswith("fr")
    if decimals is None:
        s = f"{v:,.2f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
    else:
        s = f"{v:,.{decimals}f}"
    if fr:
        s = s.replace(",", " ").replace(".", ",")
    return s


def fmt_compact(v: float | None, lang: str | None = None) -> str:
    """``1.2 M``, ``35 k``, ``950`` — fits a tick or the top of a narrow bar."""
    if v is None:
        return ""
    a = abs(v)
    if a >= 1e9:
        return fmt_number(v / 1e9, lang, 1) + " G"
    if a >= 1e6:
        return fmt_number(v / 1e6, lang, 1) + " M"
    if a >= 1e4:
        return fmt_number(v / 1e3, lang, 0) + " k"
    return fmt_number(v, lang, 0 if a >= 100 or v == int(v) else None)


def nice_axis(lo: float, hi: float) -> tuple[float, float, float]:
    """The axis bounds and the step that give round ticks around *lo*..*hi* (zero included)."""
    lo, hi = min(lo, 0.0), max(hi, 0.0)
    if hi - lo <= 0:
        return 0.0, 1.0, 0.25
    raw = (hi - lo) / 4
    p = 10 ** math.floor(math.log10(raw))
    m = raw / p
    step = p * (1 if m <= 1 else 2 if m <= 2 else 2.5 if m <= 2.5 else 5 if m <= 5 else 10)
    return math.floor(lo / step) * step, math.ceil(hi / step) * step, step


# ── drawing helpers ────────────────────────────────────────────────────────────


def _tw(s: str, size: float) -> float:
    """An estimate of the width of *s* in a sans font at *size* (no font metrics here)."""
    return sum(0.3 if c in " .,:;'|!il1" else 0.75 if c.isupper() or c in "mwMW" else 0.56 for c in s) * size


def _fit(s: str, size: float, max_w: float) -> str:
    if _tw(s, size) <= max_w:
        return s
    while len(s) > 2 and _tw(s + "…", size) > max_w:
        s = s[:-1]
    return s + "…"


def _text(x: float, y: float, s: str, *, size: float = 9, color: str = INK, anchor: str = "start", weight: str = "normal", rotate: float = 0) -> str:
    tr = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate else ""
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" text-anchor="{anchor}" font-weight="{weight}"{tr}>{escape(s)}</text>'


def _rect(x: float, y: float, w: float, h: float, fill: str, rx: float = 0) -> str:
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{max(h, 0):.1f}" fill="{fill}"{f" rx={chr(34)}{rx}{chr(34)}" if rx else ""}/>'


def _legend(series: list[Series], colors: list[str], width: float, y: float) -> tuple[list[str], float]:
    """The legend rows at *y*; returns the elements and the height taken."""
    out: list[str] = []
    x, row, size = 0.0, 0, 9.0
    for s, c in zip(series, colors, strict=True):
        w = 14 + _tw(s.name, size) + 14
        if x > 0 and x + w - 14 > width:
            x, row = 0.0, row + 1
        yy = y + row * 14
        out.append(_rect(x, yy, 9, 9, c, 1.5))
        out.append(_text(x + 13, yy + 8, s.name, size=size, color=MUTED))
        x += w
    return out, (row + 1) * 14 + 6


def _colors(series: list[Series]) -> list[str]:
    return [s.color if s.color and s.color.startswith("#") else PALETTE[i % len(PALETTE)] for i, s in enumerate(series)]


def _v(x: float | None) -> float:
    return 0.0 if x is None or math.isnan(x) else float(x)


# ── the kinds ──────────────────────────────────────────────────────────────────


def _axes_vertical(labels: list[str], series: list[Series], stacked: bool, lang: str | None, width: float, top: float, height: float) -> tuple[list[str], float, float, float, float, float, float]:
    """The y axis (gridlines + ticks) and the x labels; returns the elements and the plot
    frame ``x0, y0 (bottom), plot_w, plot_h, lo, hi``."""
    n = len(labels)
    if stacked:
        tops = [sum(max(_v(s.values[i]), 0) for s in series) for i in range(n)]
        bottoms = [sum(min(_v(s.values[i]), 0) for s in series) for i in range(n)]
        lo, hi = min(bottoms, default=0), max(tops, default=0)
    else:
        vals = [_v(s.values[i]) for s in series for i in range(n)]
        lo, hi = min(vals, default=0), max(vals, default=0)
    lo, hi, step = nice_axis(lo, hi)
    ticks = []
    t = lo
    while t <= hi + step / 2:
        ticks.append(t)
        t += step
    tick_w = max(_tw(fmt_compact(t, lang), 8) for t in ticks) + 8
    x0 = tick_w + 2
    plot_w = width - x0 - 6
    label_h = 16
    y0 = height - label_h
    plot_h = y0 - top
    out: list[str] = []
    for t in ticks:
        y = y0 - plot_h * (t - lo) / (hi - lo)
        out.append(f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x0 + plot_w:.1f}" y2="{y:.1f}" stroke="{MUTED if abs(t) < 1e-9 else GRID}" stroke-width="0.6"/>')
        out.append(_text(x0 - 5, y + 3, fmt_compact(t, lang), size=8, color=MUTED, anchor="end"))
    slot = plot_w / max(n, 1)
    every = max(1, math.ceil(max((_tw(lab, 8) for lab in labels), default=0) / max(slot - 4, 1))) if n > 1 else 1
    for i, lab in enumerate(labels):
        if i % every:
            continue
        out.append(_text(x0 + slot * i + slot / 2, y0 + 12, _fit(lab, 8, max(slot * every - 4, 20)), size=8, color=MUTED, anchor="middle"))
    return out, x0, y0, plot_w, plot_h, lo, hi


def _bars(labels: list[str], series: list[Series], colors: list[str], stacked: bool, lang: str | None, width: float, top: float, height: float) -> list[str]:
    out, x0, y0, plot_w, plot_h, lo, hi = _axes_vertical(labels, series, stacked, lang, width, top, height)
    n, k = len(labels), len(series)
    slot = plot_w / max(n, 1)

    def y_of(v: float) -> float:
        return y0 - plot_h * (v - lo) / (hi - lo)

    zero = y_of(0)
    show_values = n * (1 if stacked else k) <= 24
    if stacked:
        bw = max(3.0, min(slot * 0.62, 48))
        for i in range(n):
            x = x0 + slot * i + (slot - bw) / 2
            up, down = 0.0, 0.0
            for s, c in zip(series, colors, strict=True):
                v = _v(s.values[i])
                if v == 0:
                    continue
                if v > 0:
                    out.append(_rect(x, y_of(up + v), bw, y_of(up) - y_of(up + v), c))
                    up += v
                else:
                    out.append(_rect(x, y_of(down), bw, y_of(down + v) - y_of(down), c))
                    down += v
            if show_values and up > 0:
                out.append(_text(x + bw / 2, y_of(up) - 3, fmt_compact(up, lang), size=7.5, anchor="middle"))
    else:
        gap = min(6.0, slot * 0.2)
        bw = max(2.0, (slot - gap) / k)
        for i in range(n):
            for si, (s, c) in enumerate(zip(series, colors, strict=True)):
                v = _v(s.values[i])
                x = x0 + slot * i + gap / 2 + bw * si
                y = y_of(max(v, 0))
                out.append(_rect(x, y, bw - (1 if k > 1 else 0), abs(y_of(v) - zero), c))
                if show_values and v != 0:
                    out.append(_text(x + bw / 2, (y - 3) if v > 0 else (y_of(v) + 9), fmt_compact(v, lang), size=7.5, anchor="middle"))
    return out


def _lines(labels: list[str], series: list[Series], colors: list[str], lang: str | None, width: float, top: float, height: float) -> list[str]:
    out, x0, y0, plot_w, plot_h, lo, hi = _axes_vertical(labels, series, False, lang, width, top, height)
    n = len(labels)
    step = plot_w / (n - 1) if n > 1 else 0

    def pt(i: int, v: float) -> tuple[float, float]:
        return (x0 + step * i if n > 1 else x0 + plot_w / 2), y0 - plot_h * (v - lo) / (hi - lo)

    for s, c in zip(series, colors, strict=True):
        pts = [pt(i, _v(s.values[i])) for i in range(n) if s.values[i] is not None]
        if len(pts) > 1:
            out.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{c}" stroke-width="1.6" stroke-linejoin="round"/>')
        if n <= 60:
            out += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="{c}"/>' for x, y in pts]
    return out


def _horizontal(labels: list[str], series: list[Series], colors: list[str], stacked: bool, lang: str | None, width: float, top: float, height: float) -> list[str]:
    n, k = len(labels), len(series)
    if stacked:
        tops = [sum(max(_v(s.values[i]), 0) for s in series) for i in range(n)]
        lo, hi = 0.0, max(tops, default=0)
    else:
        vals = [_v(s.values[i]) for s in series for i in range(n)]
        lo, hi = min(vals, default=0), max(vals, default=0)
    lo, hi, _step = nice_axis(lo, hi)
    lab_w = min(max((_tw(lab, 8.5) for lab in labels), default=0), width * 0.4) + 8
    x0 = lab_w
    plot_w = width - x0 - 56
    row_h = (height - top - 4) / max(n, 1)
    out: list[str] = []

    def x_of(v: float) -> float:
        return x0 + plot_w * (v - lo) / (hi - lo)

    zero = x_of(0)
    out.append(f'<line x1="{zero:.1f}" y1="{top:.1f}" x2="{zero:.1f}" y2="{height - 4:.1f}" stroke="{MUTED}" stroke-width="0.6"/>')
    for i, lab in enumerate(labels):
        y = top + row_h * i
        out.append(_text(x0 - 6, y + row_h / 2 + 3, _fit(lab, 8.5, lab_w - 8), size=8.5, anchor="end"))
        if stacked:
            bh = row_h * 0.62
            run = 0.0
            for s, c in zip(series, colors, strict=True):
                v = max(_v(s.values[i]), 0)
                if v > 0:
                    out.append(_rect(x_of(run), y + (row_h - bh) / 2, x_of(run + v) - x_of(run), bh, c))
                    run += v
            if run > 0:
                out.append(_text(x_of(run) + 4, y + row_h / 2 + 3, fmt_compact(run, lang), size=8, color=MUTED))
        else:
            bh = max(2.0, row_h * 0.72 / k)
            for si, (s, c) in enumerate(zip(series, colors, strict=True)):
                v = _v(s.values[i])
                yy = y + row_h * 0.14 + bh * si
                out.append(_rect(min(zero, x_of(v)), yy, abs(x_of(v) - zero), bh - (1 if k > 1 else 0), c))
                if v != 0:
                    out.append(_text(max(zero, x_of(v)) + 4, yy + bh / 2 + 3, fmt_compact(v, lang), size=7.5, color=MUTED))
    return out


def _pie(labels: list[str], series: Series, lang: str | None, width: float, top: float, height: float, donut: bool) -> list[str]:
    pairs = [(lab, max(_v(v), 0)) for lab, v in zip(labels, series.values, strict=True)]
    pairs = [p for p in pairs if p[1] > 0]
    pairs.sort(key=lambda p: -p[1])
    if len(pairs) > PIE_MAX_SLICES:
        rest = sum(v for _, v in pairs[PIE_MAX_SLICES - 1:])
        pairs = [*pairs[:PIE_MAX_SLICES - 1], ("Others" if not (lang or "").lower().startswith("fr") else "Autres", rest)]
    total = sum(v for _, v in pairs)
    out: list[str] = []
    r = min((height - top) / 2 - 6, 110)
    cx, cy = r + 10, top + (height - top) / 2
    if total <= 0:
        return [_text(cx, cy, "—", size=10, color=MUTED, anchor="middle")]
    start = -math.pi / 2
    for i, (_lab, v) in enumerate(pairs):
        sweep = 2 * math.pi * v / total
        c = PALETTE[i % len(PALETTE)]
        if abs(sweep - 2 * math.pi) < 1e-9:
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{c}"/>')
        else:
            x1, y1 = cx + r * math.cos(start), cy + r * math.sin(start)
            x2, y2 = cx + r * math.cos(start + sweep), cy + r * math.sin(start + sweep)
            large = 1 if sweep > math.pi else 0
            out.append(f'<path d="M{cx:.1f},{cy:.1f} L{x1:.1f},{y1:.1f} A{r:.1f},{r:.1f} 0 {large},1 {x2:.1f},{y2:.1f} Z" fill="{c}" stroke="#fff" stroke-width="1"/>')
        start += sweep
    if donut:
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r * 0.55:.1f}" fill="#fff"/>')
        out.append(_text(cx, cy + 4, fmt_compact(total, lang), size=11, anchor="middle", weight="bold"))
    # the legend on the right: swatch, label, then the value and the share right-aligned
    lx = cx + r + 24
    row_h = min(16.0, (height - top) / max(len(pairs), 1))
    ly = cy - row_h * len(pairs) / 2 + 2
    shares = [f"{fmt_number(val, lang)}  ({fmt_number(100 * val / total, lang, 1)} %)" for _lab, val in pairs]
    val_w = max(_tw(x, 8.5) for x in shares)
    lab_w = min(max(_tw(lab, 8.5) for lab, _x in pairs), width - lx - 14 - val_w - 16)
    vx = lx + 14 + lab_w + 12 + val_w
    for i, ((lab, _x), share) in enumerate(zip(pairs, shares, strict=True)):
        y = ly + row_h * i
        out.append(_rect(lx, y, 9, 9, PALETTE[i % len(PALETTE)], 1.5))
        out.append(_text(lx + 14, y + 8, _fit(lab, 8.5, lab_w), size=8.5))
        out.append(_text(vx, y + 8, share, size=8.5, color=MUTED, anchor="end"))
    return out


# ── the entry point ────────────────────────────────────────────────────────────


def render_chart(
    kind: str, labels: list[str], series: list[Series], *, stacked: bool = False, horizontal: bool = False, donut: bool = False,
    lang: str | None = None, width_mm: float | None = None,
) -> str | None:
    """The ``<svg>`` of a chart: *labels* on the x axis, one :class:`Series` per colour. The
    figure is ``WIDTH`` units wide; *width_mm* sets the printed width (the height follows).
    ``None`` without labels or without a series."""
    series = [s for s in series if s.values]
    if not labels or not series:
        return None
    labels = [str(x) if x not in (None, "") else "—" for x in labels]
    for s in series:
        s.values = [*s.values[:len(labels)], *[None] * (len(labels) - len(s.values))]
    width = float(WIDTH)
    legend = kind != "pie" and len(series) > 1
    if kind == "pie":
        height = 190.0
    elif horizontal:
        height = min(340.0, 24 + len(labels) * (20 if stacked or len(series) == 1 else 10 + 9 * len(series)))
    else:
        height = 240.0
    colors = _colors(series)
    body: list[str] = []
    top = 6.0
    if legend:
        items, h = _legend(series, colors, width, top)
        body += items
        top += h
        height += h
    if kind == "pie":
        body += _pie(labels, series[0], lang, width, top, height, donut)
    elif kind == "line":
        body += _lines(labels, series, colors, lang, width, top, height)
    elif horizontal:
        body += _horizontal(labels, series, colors, stacked, lang, width, top, height)
    else:
        body += _bars(labels, series, colors, stacked, lang, width, top, height)
    size = f' width="{width_mm:.1f}mm" height="{height / width * width_mm:.1f}mm"' if width_mm else f' width="{width:.0f}" height="{height:.0f}"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}"{size} font-family="{FONT}" role="img">'
        + "".join(body) + "</svg>"
    )
