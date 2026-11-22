"""A diagram of boxes and lines — the servers of a platform, the parts of a system — as ONE model,
laid out ONCE, painted TWICE.

An application says what there is (:class:`Diagram`): **groups** (a tier, or a host that may sit in
a tier), **nodes** (a card in a group: a kind, a label, a second line, a state, whether it is
*declared*, a few badges) and **links** (from one card or box to another, with a label and the
*evidence* — the word that says why the line is drawn). :func:`lay_out` places it: the top-level
groups are columns, left to right in the order given; the hosts of a tier are boxes inside it; the
cards have one fixed size. It answers plain data (:class:`Placed`: every box with its ``x, y, w,
h``, every link with its path) that goes to the browser as JSON and can be kept as it is.

Both painters only DRAW at these coordinates — the page (``frontend/src/common/Diagram.tsx``, in the
theme's colours, clickable) and :func:`render_svg` here, for a document: print colours on a light
background, the state as a coloured mark AND a word, a dashed outline for what is not declared, a
legend. The SVG is drawn from the data, never captured from the page, as the Explorer's charts are
(:mod:`liberty.explorer.chart_svg`, whose text helpers are used here).

What a document's renderer needs, and gets: no ``foreignObject`` (WeasyPrint has none) — every text
is a ``<text>``, cut to the room it has with an ellipsis; the whole SVG on ONE line (the reports'
markdown step turns a newline into ``<br>``); every text escaped.

Pure functions, no I/O. Nothing here knows what the boxes are: the words come from the caller.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict
from xml.sax.saxutils import escape

from liberty.explorer.chart_svg import FONT, GRID, INK, MUTED, PALETTE, _fit, _tw

State = Literal["ok", "warn", "down", "unknown"]
STATES: tuple[State, ...] = ("ok", "warn", "down", "unknown")
GroupKind = Literal["tier", "host"]


# ── the model ──────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Badge:
    """A fact of a node in two words: ``Version`` / ``9.2.8``."""

    label: str
    value: str


@dataclass(slots=True)
class Group:
    """A box that holds cards: a ``tier`` (a column of the diagram) or a ``host`` — which may sit
    in a tier (*parent*), or stand as a column by itself."""

    id: str
    label: str
    kind: GroupKind = "tier"
    parent: str | None = None
    declared: bool = True


@dataclass(slots=True)
class Node:
    """A card. *group* is the box it sits in; *detail* its second line; *declared* false draws it
    with a dashed outline (found, but not where things are declared)."""

    id: str
    group: str
    kind: str
    label: str
    detail: str = ""
    state: State = "unknown"
    declared: bool = True
    badges: list[Badge] = field(default_factory=list)


@dataclass(slots=True)
class Link:
    """A line from *source* to *target* — each a node or a group — written ``from`` / ``to`` once
    laid out. *evidence* says why it is drawn (``business function calls``): the legend lists them."""

    source: str
    target: str
    label: str = ""
    evidence: str = ""


@dataclass(slots=True)
class Diagram:
    groups: list[Group] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    #: one sentence under the legend (what could not be read, what the reader must know)
    note: str = ""


# ── laid out ───────────────────────────────────────────────────────────────────


class PlacedGroup(TypedDict):
    id: str
    label: str
    kind: str
    parent: str | None
    declared: bool
    x: float
    y: float
    w: float
    h: float


class PlacedNode(TypedDict):
    id: str
    group: str
    kind: str
    label: str
    detail: str
    state: str
    declared: bool
    badges: list[dict[str, str]]
    x: float
    y: float
    w: float
    h: float


#: a link once placed: its line and its arrowhead as SVG paths, and where its label sits (its middle)
PlacedLink = TypedDict("PlacedLink", {"from": str, "to": str, "label": str, "evidence": str, "path": str, "arrow": str, "x": float, "y": float})


class Placed(TypedDict):
    """A diagram with its places: what the page receives and what :func:`render_svg` draws."""

    width: float
    height: float
    groups: list[PlacedGroup]
    nodes: list[PlacedNode]
    links: list[PlacedLink]
    #: the distinct evidences of the links, in the order met: a line's colour is its evidence's rank
    evidences: list[str]
    note: str


@dataclass(slots=True, frozen=True)
class Sizes:
    """The measures of the layout, in the diagram's unit (a pixel on the page; a document scales it)."""

    node_w: float = 140
    node_h: float = 50
    node_gap: float = 6
    host_pad: float = 6          # around the cards of a host
    host_head: float = 16        # the line of a host's name
    host_gap: float = 8          # between two boxes of a tier
    tier_pad: float = 8
    tier_head: float = 22        # the line of a tier's name
    tier_gap: float = 60         # between two columns: where a link's label sits
    margin: float = 10
    max_column_h: float = 560    # a tier taller than this goes on in a second column of boxes beside the first
    lane_gap: float = 12         # between two lines that pass under the columns
    bow: float = 22              # how far a link between two boxes of the same column goes out
    arrow: float = 5


def _r(v: float) -> float:
    return round(float(v), 1)


def _p(x: float, y: float) -> str:
    return f"{x:.1f} {y:.1f}"


def _rounded(points: list[tuple[float, float]], radius: float) -> str:
    """A line through *points* (each segment horizontal or vertical) with its corners rounded."""
    out = [f"M {_p(*points[0])}"]
    for (ax, ay), (bx, by), (cx, cy) in zip(points, points[1:], points[2:], strict=False):
        r = min(radius, (abs(bx - ax) + abs(by - ay)) / 2, (abs(cx - bx) + abs(cy - by)) / 2)
        if r <= 0:
            out.append(f"L {_p(bx, by)}")
            continue
        ix, iy = (bx - r * _sign(bx - ax), by - r * _sign(by - ay))
        ox, oy = (bx + r * _sign(cx - bx), by + r * _sign(cy - by))
        out.append(f"L {_p(ix, iy)} Q {_p(bx, by)}, {_p(ox, oy)}")
    out.append(f"L {_p(*points[-1])}")
    return " ".join(out)


def _sign(v: float) -> int:
    return (v > 0) - (v < 0)


def lay_out(model: Diagram, sizes: Sizes | None = None) -> Placed:
    """Place *model*: one column per top-level group in the order given, each column's own cards
    first then the boxes it holds, every column as tall as the tallest. A link between two
    neighbouring columns is a curve from one side to the other; between two boxes of the same
    column, a bow on the right; across a column, a line that passes UNDER the columns so it never
    crosses a card. Deterministic: the same model gives the same places.

    A :class:`ValueError` names what the model gets wrong (an id used twice, a node in a group
    that does not exist, a box inside a box inside a box, a link to nothing)."""
    s = sizes or Sizes()
    ids = [g.id for g in model.groups] + [n.id for n in model.nodes]
    twice = sorted({i for i in ids if ids.count(i) > 1})
    if twice:
        raise ValueError(f"diagram: the id {twice[0]!r} is used twice (groups and nodes share one set of ids)")
    by_id = {g.id: g for g in model.groups}
    for g in model.groups:
        if g.parent is not None and (g.parent not in by_id or by_id[g.parent].parent is not None):
            raise ValueError(f"diagram: the group {g.id!r} sits in {g.parent!r}, which is not a top-level group")
    for n in model.nodes:
        if n.group not in by_id:
            raise ValueError(f"diagram: the node {n.id!r} sits in {n.group!r}, which is not a group")
    known = set(ids)
    for k in model.links:
        for end in (k.source, k.target):
            if end not in known:
                raise ValueError(f"diagram: a link names {end!r}, which is neither a node nor a group")

    sub_w = s.node_w + 2 * s.host_pad                # a host box, and the room a card of the tier itself takes
    boxes: dict[str, dict[str, float]] = {}
    column_of: dict[str, int] = {}
    tops = [g for g in model.groups if g.parent is None]
    widths: list[float] = []
    inner_h = 0.0
    # each column measured from its own corner first: its width and its height are needed to place the next
    local: list[list[tuple[str, float, float, float, float]]] = []
    for c, top in enumerate(tops):
        column_of[top.id] = c
        own = [n for n in model.nodes if n.group == top.id]
        own_ids = {n.id for n in own}
        held = [g for g in model.groups if g.parent == top.id]
        blocks: list[tuple[str, float, list[Node]]] = [(n.id, s.node_h, []) for n in own]
        for g in held:
            cards = [n for n in model.nodes if n.group == g.id]
            blocks.append((g.id, s.host_head + len(cards) * s.node_h + max(0, len(cards) - 1) * s.node_gap + s.host_pad, cards))
        placed: list[tuple[str, float, float, float, float]] = []
        room = s.max_column_h - s.tier_head - 2 * s.tier_pad
        col, y, tallest, before_card = 0, 0.0, 0.0, False
        for block_id, h, cards in blocks:
            is_card = block_id in own_ids
            gap = 0.0 if y == 0 else (s.node_gap if is_card and before_card else s.host_gap)
            if y > 0 and y + gap + h > room:
                col, y, gap = col + 1, 0.0, 0.0
            x = s.tier_pad + col * (sub_w + s.host_gap)
            top_y = s.tier_head + s.tier_pad + y + gap
            if is_card:
                placed.append((block_id, x + s.host_pad, top_y, s.node_w, s.node_h))
            else:
                placed.append((block_id, x, top_y, sub_w, h))
                for i, n in enumerate(cards):
                    placed.append((n.id, x + s.host_pad, top_y + s.host_head + i * (s.node_h + s.node_gap), s.node_w, s.node_h))
            y += gap + h
            tallest, before_card = max(tallest, y), is_card
            for inside in (block_id, *(n.id for n in cards)):
                column_of[inside] = c
        widths.append(2 * s.tier_pad + (col + 1) * sub_w + col * s.host_gap)
        inner_h = max(inner_h, tallest)
        local.append(placed)

    column_h = s.tier_head + 2 * s.tier_pad + inner_h
    lefts: list[float] = []
    x = s.margin
    for c, top in enumerate(tops):
        lefts.append(x)
        boxes[top.id] = {"x": x, "y": s.margin, "w": widths[c], "h": column_h}
        for item, bx, by, bw, bh in local[c]:
            boxes[item] = {"x": x + bx, "y": s.margin + by, "w": bw, "h": bh}
        x += widths[c] + s.tier_gap
    right = (lefts[-1] + widths[-1]) if tops else s.margin
    bottom = s.margin + (column_h if tops else 0)

    links: list[PlacedLink] = []
    lanes = 0
    bowed = 0.0
    for k in model.links:
        a, b = boxes[k.source], boxes[k.target]
        ca, cb = column_of[k.source], column_of[k.target]
        y1, y2 = a["y"] + a["h"] / 2, b["y"] + b["h"] / 2
        if ca == cb:                                         # the same column: a bow on the right
            x1, x2 = a["x"] + a["w"], b["x"] + b["w"]
            out = max(x1, x2) + s.bow
            path = f"M {_p(x1, y1)} C {_p(out, y1)}, {_p(out, y2)}, {_p(x2, y2)}"
            mid = (max(x1, x2) + s.bow * 0.75, (y1 + y2) / 2)
            way = -1
            bowed = max(bowed, out)
        else:
            way = 1 if cb > ca else -1
            x1 = a["x"] + a["w"] if way > 0 else a["x"]
            x2 = b["x"] if way > 0 else b["x"] + b["w"]
            if abs(cb - ca) == 1:                            # neighbours: a curve across the gap
                bend = (x2 - x1) / 2
                path = f"M {_p(x1, y1)} C {_p(x1 + bend, y1)}, {_p(x2 - bend, y2)}, {_p(x2, y2)}"
                mid = ((x1 + x2) / 2, (y1 + y2) / 2)
            else:                                            # across a column: down, under the columns, up
                lane_y = bottom + 10 + lanes * s.lane_gap
                shift = ((lanes % 4) - 1.5) * 5              # two such lines do not share their way down
                lanes += 1
                edge_a = lefts[ca] + widths[ca] if way > 0 else lefts[ca]
                edge_b = lefts[cb] if way > 0 else lefts[cb] + widths[cb]
                g1, g2 = edge_a + way * s.tier_gap / 2 + shift, edge_b - way * s.tier_gap / 2 + shift
                path = _rounded([(x1, y1), (g1, y1), (g1, lane_y), (g2, lane_y), (g2, y2), (x2, y2)], 5)
                mid = ((g1 + g2) / 2, lane_y - 3)
        tip = s.arrow * way
        arrow = f"M {_p(x2, y2)} L {_p(x2 - tip, y2 - s.arrow * 0.56)} L {_p(x2 - tip, y2 + s.arrow * 0.56)} Z"
        links.append({"from": k.source, "to": k.target, "label": k.label, "evidence": k.evidence, "path": path, "arrow": arrow,
                      "x": _r(mid[0]), "y": _r(mid[1])})

    def at(item: str) -> tuple[float, float, float, float]:
        b = boxes[item]
        return _r(b["x"]), _r(b["y"]), _r(b["w"]), _r(b["h"])

    groups: list[PlacedGroup] = []
    for g in model.groups:
        gx, gy, gw, gh = at(g.id)
        groups.append({"id": g.id, "label": g.label, "kind": g.kind, "parent": g.parent, "declared": g.declared, "x": gx, "y": gy, "w": gw, "h": gh})
    nodes: list[PlacedNode] = []
    for n in model.nodes:
        nx, ny, nw, nh = at(n.id)
        nodes.append({"id": n.id, "group": n.group, "kind": n.kind, "label": n.label, "detail": n.detail, "state": n.state if n.state in STATES else "unknown",
                      "declared": n.declared, "badges": [{"label": b.label, "value": b.value} for b in n.badges], "x": nx, "y": ny, "w": nw, "h": nh})
    return {
        "width": _r(max(right, bowed) + s.margin),
        "height": _r(bottom + (10 + lanes * s.lane_gap if lanes else 0) + s.margin),
        "groups": groups,
        "nodes": nodes,
        "links": links,
        "evidences": list(dict.fromkeys(k.evidence for k in model.links if k.evidence)),
        "note": model.note,
    }


# ── painted for a document ─────────────────────────────────────────────────────

#: the state's mark, in print colours (the reports' own green / orange / red)
STATE_COLORS: dict[str, str] = {"ok": "#16a34a", "warn": "#ea580c", "down": "#dc2626", "unknown": "#9ca3af"}
PAPER = "#ffffff"
TIER_FILL = "#f4f6fa"
OUTLINE = "#9aa4b5"
DASH = "4 3"
#: a link's colour by the rank of its evidence (the charts' palette, the ones that read as lines)
LINK_COLORS = [PALETTE[0], PALETTE[1], PALETTE[4], PALETTE[7], PALETTE[6]]
BADGES_ON_CARD = 2

WORDS: dict[str, dict[str, str]] = {
    "en": {"ok": "OK", "warn": "To review", "down": "Down", "unknown": "Not known", "undeclared": "Not declared"},
    "fr": {"ok": "OK", "warn": "À revoir", "down": "Arrêté", "unknown": "Inconnu", "undeclared": "Non déclaré"},
}

_UNPRINTABLE = {c: " " for c in range(32)}
_NOT_A_PATH = re.compile(r"[^MLCQZ0-9 .,-]")              # a path as lay_out writes it, and nothing else


def _flat(value: Any) -> str:
    """A text on one line, without what XML refuses: a newline in a label must not break the SVG."""
    return " ".join(str("" if value is None else value).translate(_UNPRINTABLE).split())


def _text(x: float, y: float, value: Any, *, size: float, room: float, color: str = INK, anchor: str = "start", weight: str = "normal") -> str:
    s = _flat(value)
    if not s:
        return ""
    s = _fit(s, size, max(room, size))
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" text-anchor="{anchor}" font-weight="{weight}">{escape(s)}</text>'


def _box(b: Mapping[str, Any], *, fill: str, stroke: str, rx: float, dashed: bool = False, width: float = 0.8) -> str:
    dash = f' stroke-dasharray="{DASH}"' if dashed else ""
    return (f'<rect x="{_num(b.get("x")):.1f}" y="{_num(b.get("y")):.1f}" width="{_num(b.get("w")):.1f}" height="{_num(b.get("h")):.1f}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"{dash}/>')


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def badge_line(node: Mapping[str, Any], limit: int = BADGES_ON_CARD) -> str:
    """What a card says of its badges on one line: the first *limit* of them, ``Sessions 184 · Port 1521``
    (a badge without a value is left out; the page's panel lists them all)."""
    said = [f"{_flat(b.get('label'))} {_flat(b.get('value'))}".strip() for b in node.get("badges") or [] if isinstance(b, Mapping) and _flat(b.get("value"))]
    return " · ".join(said[:limit])


def render_svg(
    placed: Placed | Mapping[str, Any], *, lang: str | None = None, title: str | None = None, words: Mapping[str, str] | None = None,
    width_mm: float | None = None, max_height_mm: float | None = None,
) -> str | None:
    """The ``<svg>`` of a laid-out diagram, for a document: self-contained, on one line, print
    colours on a light background. Each card says its state twice — a coloured mark and the word —
    and what is not declared has a dashed outline; the legend under the drawing names the states
    that occur, the dashed outline and each evidence with its line.

    *words*: the state words (``ok`` ``warn`` ``down`` ``unknown``) and ``undeclared``, where the
    caller's differ from the defaults of *lang*. *width_mm* is the printed width; with
    *max_height_mm* the drawing is made smaller when its height would pass it (a page it must fit
    in). ``None`` when there is no card to draw."""
    nodes = [n for n in placed.get("nodes") or [] if isinstance(n, Mapping)]
    if not nodes:
        return None
    w = {**WORDS["fr" if (lang or "en").lower().startswith("fr") else "en"], **{k: v for k, v in (words or {}).items() if v}}
    groups = [g for g in placed.get("groups") or [] if isinstance(g, Mapping)]
    links = [k for k in placed.get("links") or [] if isinstance(k, Mapping)]
    evidences = [_flat(e) for e in placed.get("evidences") or []]
    width, height = max(_num(placed.get("width")), 1.0), max(_num(placed.get("height")), 1.0)
    body: list[str] = []

    for g in (g for g in groups if not g.get("parent")):
        body.append(_box(g, fill=TIER_FILL, stroke=GRID, rx=6, dashed=g.get("declared") is False))
        body.append(_text(_num(g.get("x")) + 8, _num(g.get("y")) + 15, g.get("label"), size=9, room=_num(g.get("w")) - 16, color=MUTED, weight="bold"))
    for g in (g for g in groups if g.get("parent")):
        undeclared = g.get("declared") is False
        body.append(_box(g, fill=PAPER, stroke=MUTED if undeclared else GRID, rx=4, dashed=undeclared))
        body.append(_text(_num(g.get("x")) + 6, _num(g.get("y")) + 11, g.get("label"), size=8, room=_num(g.get("w")) - 12))

    def color_of(evidence: Any) -> str:
        e = _flat(evidence)
        return LINK_COLORS[evidences.index(e) % len(LINK_COLORS)] if e in evidences else MUTED

    for k in links:
        c = color_of(k.get("evidence"))
        body.append(f'<path d="{_NOT_A_PATH.sub("", _flat(k.get("path")))}" fill="none" stroke="{c}" stroke-width="1.1"/>')
        body.append(f'<path d="{_NOT_A_PATH.sub("", _flat(k.get("arrow")))}" fill="{c}"/>')
    for n in nodes:
        x, y, nw, nh = _num(n.get("x")), _num(n.get("y")), _num(n.get("w")), _num(n.get("h"))
        state = n.get("state") if n.get("state") in STATE_COLORS else "unknown"
        undeclared = n.get("declared") is False
        body.append(_box(n, fill=PAPER, stroke=INK if undeclared else OUTLINE, rx=4, dashed=undeclared))
        body.append(f'<rect x="{x + 3:.1f}" y="{y + 5:.1f}" width="3" height="{max(nh - 10, 0):.1f}" rx="1.5" fill="{STATE_COLORS[state]}"/>')
        room = nw - 16
        body.append(_text(x + 11, y + 15, n.get("label"), size=9.5, room=room, weight="bold"))
        body.append(_text(x + 11, y + 27, n.get("detail"), size=8, room=room, color=MUTED))
        word = _flat(w[state])
        body.append(_text(x + 11, y + 40, word, size=7.5, room=room, weight="bold"))
        rest = badge_line(n)
        used = _tw(word, 7.5) * 1.08 + 5                     # (bold runs a little wider than the estimate)
        if rest and room - used > 20:
            body.append(_text(x + 11 + used, y + 40, rest, size=7.5, room=room - used, color=MUTED))
    for k in links:                                          # the labels last: over the lines and beside the cards
        label = _flat(k.get("label"))
        if not label:
            continue
        label = _fit(label, 7.5, 110)
        lw = _tw(label, 7.5) + 6
        x, y = _num(k.get("x")), _num(k.get("y"))
        body.append(f'<rect x="{x - lw / 2:.1f}" y="{y - 6:.1f}" width="{lw:.1f}" height="10" rx="2" fill="{PAPER}" fill-opacity="0.9"/>')
        body.append(_text(x, y + 2, label, size=7.5, room=120, anchor="middle"))

    # the legend, under the drawing: only what the drawing shows
    entries: list[tuple[str, str, str]] = [("state", s, _flat(w[s])) for s in STATES if any((n.get("state") if n.get("state") in STATE_COLORS else "unknown") == s for n in nodes)]
    if any(x.get("declared") is False for x in (*nodes, *groups)):
        entries.append(("dashed", "", _flat(w["undeclared"])))
    entries += [("line", color_of(e), e) for e in evidences]
    top = 0.0
    if title and _flat(title):
        top = 18.0
        body.append(_text(10, -6, title, size=11, room=width - 20, weight="bold"))
    lx, ly = 10.0, height + 4
    for kind, what, label in entries:
        need = 18 + _tw(label, 8) + 14
        if lx > 10 and lx + need - 14 > width - 10:
            lx, ly = 10.0, ly + 13
        if kind == "state":
            body.append(f'<rect x="{lx:.1f}" y="{ly:.1f}" width="3" height="9" rx="1.5" fill="{STATE_COLORS[what]}"/>')
        elif kind == "dashed":
            body.append(f'<rect x="{lx:.1f}" y="{ly + 0.5:.1f}" width="13" height="8" rx="2" fill="{PAPER}" stroke="{INK}" stroke-width="0.8" stroke-dasharray="{DASH}"/>')
        else:
            body.append(f'<line x1="{lx:.1f}" y1="{ly + 4.5:.1f}" x2="{lx + 13:.1f}" y2="{ly + 4.5:.1f}" stroke="{what}" stroke-width="1.4"/>')
        body.append(_text(lx + (7 if kind == "state" else 17), ly + 8, label, size=8, room=width - 40, color=MUTED))
        lx += need
    bottom = ly + 13 if entries else height
    note = _flat(placed.get("note"))
    if note:
        body.append(_text(10, bottom + 9, note, size=8, room=width - 20, color=MUTED))
        bottom += 14
    full = top + bottom + 4

    if width_mm:
        scale = min(1.0, max_height_mm / (full / width * width_mm)) if max_height_mm else 1.0
        size = f' width="{width_mm * scale:.1f}mm" height="{full / width * width_mm * scale:.1f}mm"'
    else:
        size = f' width="{width:.0f}" height="{full:.0f}"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 {-int(top)} {width:.0f} {full:.0f}"{size} font-family="{FONT}" role="img">'
        f'<rect x="0" y="{-int(top)}" width="{width:.0f}" height="{full:.0f}" fill="{PAPER}"/>'
        + "".join(body) + "</svg>"
    )
