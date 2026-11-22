"""liberty.diagram — one model, laid out once, painted for a document: where the boxes go, how a
link is routed, what the model is refused for, and what the SVG holds (one line, no foreignObject,
every text escaped and cut to its room, the state as a mark and a word, a dashed outline for what is
not declared, a legend of what the drawing shows)."""
from __future__ import annotations

import json
import re
from xml.etree import ElementTree

import pytest

from liberty.diagram import STATE_COLORS, Badge, Diagram, Group, Link, Node, Sizes, badge_line, lay_out, render_svg

S = Sizes()


def model() -> Diagram:
    return Diagram(
        groups=[Group("web", "Web"), Group("logic", "Logic"), Group("data", "Database"),
                Group("h:web01", "web01.example.com", "host", "web"), Group("h:web02", "web02.example.com", "host", "web"),
                Group("h:ent01", "ent01.example.com", "host", "logic"), Group("h:db01", "db01.example.com", "host", "data", declared=False)],
        nodes=[Node("html1", "h:web01", "web", "html_1", "Web server", "ok", badges=[Badge("Version", "9.2.8"), Badge("Users", "12"), Badge("Home", "/u01")]),
               Node("ais1", "h:web01", "ais", "ais_1", "AIS server", "warn"),
               Node("html2", "h:web02", "web", "html_2", "Web server", "down"),
               Node("ent1", "h:ent01", "enterprise", "ent_1", "Enterprise server", "ok"),
               Node("db", "h:db01", "database", "ORCL", "Oracle 19", "unknown", declared=False, badges=[Badge("Sessions", "184")])],
        links=[Link("html1", "ent1", "12 k calls", "calls"), Link("h:ent01", "db", "120 sessions", "sessions"),
               Link("h:web01", "db", "40 sessions", "sessions"), Link("ais1", "html1", "", "same host"), Link("ent1", "html2", "back", "calls")],
        note="Read at 10:42.",
    )


def by_id(placed: dict, kind: str) -> dict[str, dict]:
    return {x["id"]: x for x in placed[kind]}


def inside(inner: dict, outer: dict) -> bool:
    return inner["x"] >= outer["x"] and inner["y"] >= outer["y"] and inner["x"] + inner["w"] <= outer["x"] + outer["w"] and inner["y"] + inner["h"] <= outer["y"] + outer["h"]


def apart(a: dict, b: dict) -> bool:
    return a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"] or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"]


def test_the_tiers_are_columns_in_the_order_given_with_their_hosts_and_cards_inside() -> None:
    placed = lay_out(model())
    g, n = by_id(placed, "groups"), by_id(placed, "nodes")
    assert g["web"]["x"] == S.margin and g["logic"]["x"] == g["web"]["x"] + g["web"]["w"] + S.tier_gap and g["data"]["x"] == g["logic"]["x"] + g["logic"]["w"] + S.tier_gap
    assert {g[t]["y"] for t in ("web", "logic", "data")} == {S.margin} and len({g[t]["h"] for t in ("web", "logic", "data")}) == 1      # as tall as the tallest
    assert all(inside(g[h], g[t]) for h, t in (("h:web01", "web"), ("h:web02", "web"), ("h:ent01", "logic"), ("h:db01", "data")))
    assert all(inside(n[c], g[h]) for c, h in (("html1", "h:web01"), ("ais1", "h:web01"), ("html2", "h:web02"), ("ent1", "h:ent01"), ("db", "h:db01")))
    assert {(c["w"], c["h"]) for c in n.values()} == {(S.node_w, S.node_h)}                                                               # one card size
    assert g["h:web02"]["y"] == g["h:web01"]["y"] + g["h:web01"]["h"] + S.host_gap and n["ais1"]["y"] == n["html1"]["y"] + S.node_h + S.node_gap
    cards = list(n.values())
    assert all(apart(a, b) for i, a in enumerate(cards) for b in cards[i + 1:])
    assert placed["width"] == g["data"]["x"] + g["data"]["w"] + S.margin and placed["height"] > g["web"]["y"] + g["web"]["h"]
    # what the page and the document receive: the model's own fields beside the places, JSON as it is
    assert n["html1"] == {"id": "html1", "group": "h:web01", "kind": "web", "label": "html_1", "detail": "Web server", "state": "ok", "declared": True,
                          "badges": [{"label": "Version", "value": "9.2.8"}, {"label": "Users", "value": "12"}, {"label": "Home", "value": "/u01"}],
                          "x": n["html1"]["x"], "y": n["html1"]["y"], "w": S.node_w, "h": S.node_h}
    assert g["h:db01"]["declared"] is False and g["h:db01"]["parent"] == "data" and placed["note"] == "Read at 10:42."
    assert json.loads(json.dumps(placed)) == placed and lay_out(model()) == placed                                                       # the same model, the same places


def test_a_card_of_the_tier_itself_a_host_without_a_tier_and_an_empty_model() -> None:
    placed = lay_out(Diagram(groups=[Group("t", "Tier"), Group("h", "host01", "host", "t"), Group("alone", "host02", "host")],
                             nodes=[Node("a", "t", "x", "A"), Node("b", "t", "x", "B"), Node("c", "h", "x", "C"), Node("d", "alone", "x", "D")]))
    g, n = by_id(placed, "groups"), by_id(placed, "nodes")
    assert n["a"]["x"] == n["c"]["x"] and n["b"]["y"] == n["a"]["y"] + S.node_h + S.node_gap                  # the tier's own cards first, in line with those of its hosts
    assert g["h"]["y"] == n["b"]["y"] + S.node_h + S.host_gap and inside(n["d"], g["alone"]) and g["alone"]["x"] > g["t"]["x"] + g["t"]["w"]
    empty = lay_out(Diagram())
    assert empty["groups"] == [] and empty["nodes"] == [] and empty["links"] == [] and render_svg(empty) is None


def test_a_tier_too_tall_goes_on_beside() -> None:
    hosts = [Group(f"h{i}", f"host{i:02}", "host", "t") for i in range(6)]
    m = Diagram(groups=[Group("t", "Tier"), Group("u", "Next"), *hosts], nodes=[Node(f"n{i}", f"h{i}", "x", f"N{i}") for i in range(6)] + [Node("z", "u", "x", "Z")])
    one, two = lay_out(m), lay_out(m, Sizes(max_column_h=260))
    g1, g2 = by_id(one, "groups"), by_id(two, "groups")
    assert len({g1[f"h{i}"]["x"] for i in range(6)}) == 1 and len({g2[f"h{i}"]["x"] for i in range(6)}) == 3     # 2 boxes fit in 260, the others go on beside
    assert g2["t"]["w"] > g1["t"]["w"] and g2["t"]["h"] < g1["t"]["h"] and g2["t"]["h"] <= 260
    assert all(inside(g2[f"h{i}"], g2["t"]) for i in range(6)) and g2["u"]["x"] == g2["t"]["x"] + g2["t"]["w"] + S.tier_gap
    boxes = [g2[f"h{i}"] for i in range(6)]
    assert all(apart(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:])


def numbers(path: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+\.\d+", path)]


def test_a_link_is_a_curve_a_bow_or_a_line_under_the_columns() -> None:
    placed = lay_out(model())
    g, n = by_id(placed, "groups"), by_id(placed, "nodes")
    near, to_db, across, bow, back = placed["links"]
    assert (near["from"], near["to"], near["label"], near["evidence"]) == ("html1", "ent1", "12 k calls", "calls")
    # neighbours: from the side of one facing the other, the label in the middle of the gap, the arrow at the target
    p = numbers(near["path"])
    assert near["path"].startswith("M ") and " C " in near["path"] and p[:2] == [n["html1"]["x"] + S.node_w, n["html1"]["y"] + S.node_h / 2]
    assert p[-2:] == [n["ent1"]["x"], n["ent1"]["y"] + S.node_h / 2] and near["x"] == (p[0] + p[-2]) / 2
    assert numbers(near["arrow"])[:2] == p[-2:] and numbers(near["arrow"])[2] == p[-2] - S.arrow                    # it points right
    # from a box (a host) to a card of the next column
    assert numbers(to_db["path"])[:2] == [g["h:ent01"]["x"] + g["h:ent01"]["w"], g["h:ent01"]["y"] + g["h:ent01"]["h"] / 2]
    # across a column: no curve, the line goes under every column and comes back up — it crosses no card
    bottom = g["web"]["y"] + g["web"]["h"]
    assert " C " not in across["path"] and across["y"] > bottom and max(numbers(across["path"])[1::2]) > bottom
    assert placed["height"] > lay_out(Diagram(groups=model().groups, nodes=model().nodes))["height"]                # the lane has its room
    assert g["web"]["x"] + g["web"]["w"] < across["x"] < g["data"]["x"]
    # two cards of the same column: a bow on the right, the arrow pointing back in
    b = numbers(bow["path"])
    assert b[0] == b[-2] == n["ais1"]["x"] + S.node_w and b[2] == b[0] + S.bow and numbers(bow["arrow"])[2] == b[-2] + S.arrow
    # right to left: from the left side of the source to the right side of the target, the arrow pointing left
    r = numbers(back["path"])
    assert r[0] == n["ent1"]["x"] and r[-2] == n["html2"]["x"] + S.node_w and numbers(back["arrow"])[2] == r[-2] + S.arrow
    assert placed["evidences"] == ["calls", "sessions", "same host"]                                                # in the order met: a line's colour is its rank


def test_two_lines_under_the_columns_do_not_share_their_lane() -> None:
    m = model()
    m.links = [Link("h:web01", "db", "a", "sessions"), Link("h:web02", "db", "b", "sessions"), Link("db", "html1", "c", "sessions")]
    one, two, three = lay_out(m)["links"]
    assert two["y"] == one["y"] + S.lane_gap and three["y"] == two["y"] + S.lane_gap
    assert numbers(three["path"])[0] == by_id(lay_out(m), "groups")["h:db01"]["x"] + 6                              # (a card: 6 inside its host's box) leaving by its left side


@pytest.mark.parametrize(("m", "said"), [
    (Diagram(groups=[Group("a", "A"), Group("a", "B")]), "'a' is used twice"),
    (Diagram(groups=[Group("a", "A")], nodes=[Node("a", "a", "x", "A")]), "'a' is used twice"),
    (Diagram(groups=[Group("a", "A")], nodes=[Node("n", "gone", "x", "N")]), "'gone', which is not a group"),
    (Diagram(groups=[Group("a", "A"), Group("b", "B", "host", "a"), Group("c", "C", "host", "b")]), "'b', which is not a top-level group"),
    (Diagram(groups=[Group("a", "A", "host", "nowhere")]), "'nowhere', which is not a top-level group"),
    (Diagram(groups=[Group("a", "A")], nodes=[Node("n", "a", "x", "N")], links=[Link("n", "gone")]), "'gone', which is neither a node nor a group"),
])
def test_a_model_that_is_wrong_is_refused_with_its_reason(m: Diagram, said: str) -> None:
    with pytest.raises(ValueError, match=re.escape(said)):
        lay_out(m)


def texts(svg: str) -> list[str]:
    return ["".join(t.itertext()) for t in ElementTree.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")]


def test_the_svg_is_one_line_of_plain_shapes_with_a_legend_of_what_it_shows() -> None:
    placed = lay_out(model())
    svg = render_svg(placed, title="Architecture")
    assert svg and "\n" not in svg and "foreignObject" not in svg and svg.startswith("<svg ") and svg.endswith("</svg>")
    root = ElementTree.fromstring(svg)                                                # well-formed
    said = texts(svg)
    assert said[-1] == "Read at 10:42." and "Architecture" in said
    for label in ("Web", "web01.example.com", "html_1", "Web server", "ORCL", "12 k calls", "120 sessions"):
        assert label in said
    # the state: a coloured mark AND a word, on each card
    marks = [r.get("fill") for r in root.iter("{http://www.w3.org/2000/svg}rect") if r.get("width") == "3"]
    assert marks[:5] == [STATE_COLORS[s] for s in ("ok", "warn", "down", "ok", "unknown")]
    assert [w for w in said if w in ("OK", "To review", "Down", "Not known")][:5] == ["OK", "To review", "Down", "OK", "Not known"]
    assert "Version 9.2.8 · Users 12" in said and "Sessions 184" in said                # the first two badges of a card, the others are the panel's
    # what is not declared: a dashed outline — the host's box, its card, and the legend's sample
    assert svg.count("stroke-dasharray") == 3
    # the legend: the states that occur, the dashed outline, each evidence with its line
    legend = said[said.index("OK", said.index("120 sessions")):-1]
    assert legend == ["OK", "To review", "Down", "Not known", "Not declared", "calls", "sessions", "same host"]
    lines = {p.get("stroke") for p in root.iter("{http://www.w3.org/2000/svg}path") if p.get("fill") == "none"}
    assert len(lines) == 3                                                            # one colour per evidence
    # light background, dark text
    assert root[0].tag.endswith("rect") and root[0].get("fill") == "#ffffff" and 'fill="#1f2937"' in svg

    plain = Diagram(groups=[Group("t", "T")], nodes=[Node("n", "t", "x", "N", state="ok")])
    quiet = render_svg(lay_out(plain)) or ""
    assert texts(quiet) == ["T", "N", "OK", "OK"] and "stroke-dasharray" not in quiet   # nothing undeclared, no link: the legend says the one state


def test_every_text_is_escaped_flattened_and_cut_to_its_room() -> None:
    long = "a_very_long_instance_name_that_cannot_fit_on_a_card_of_this_width"
    m = Diagram(groups=[Group("t", 'Tier <b>&"one"'), Group("h", "host\nwith a\tbreak", "host", "t")],
                nodes=[Node("n", "h", "x", long, "<script>alert(1)</script>", "ok", badges=[Badge("A & B", "<1>"), Badge("Empty", "")]), Node("o", "h", "x", "O")],
                links=[Link("n", "o", "x < y", "why & how")], note="line one\nline two")
    svg = render_svg(lay_out(m), title="A & B <c>") or ""
    assert "\n" not in svg and "\t" not in svg and "<script>" not in svg and "<b>" not in svg
    said = texts(svg)                                                                  # parsed back: the texts are what was given
    assert 'Tier <b>&"one"' in said and "host with a break" in said and "A & B <c>" in said and "x < y" in said and "why & how" in said and "line one line two" in said
    assert "A & B <1>" in said and not any("Empty" in s for s in said)                  # a badge without a value is not written
    cut = next(s for s in said if s.startswith("a_very_long"))
    assert cut.endswith("…") and len(cut) < len(long)
    assert "<script>alert(1)</script>" in said                                         # (it fits: written whole, as text)


def test_the_words_the_language_and_the_printed_size() -> None:
    placed = lay_out(model())
    fr = texts(render_svg(placed, lang="fr") or "")
    assert "À revoir" in fr and "Arrêté" in fr and "Non déclaré" in fr
    own = texts(render_svg(placed, lang="fr", words={"ok": "En marche", "undeclared": "Non déclaré dans la console", "warn": ""}) or "")
    assert "En marche" in own and "Non déclaré dans la console" in own and "À revoir" in own and "OK" not in own      # an empty word keeps the default
    svg = render_svg(placed) or ""
    box = re.search(r'viewBox="0 -?\d+ (\d+) (\d+)"', svg)
    assert box is not None
    w, h = (float(x) for x in box.groups())
    assert f' width="{w:.0f}" height="{h:.0f}"' in svg and w == placed["width"] and h > placed["height"]              # the legend and the note under the drawing
    wide = render_svg(placed, width_mm=240) or ""
    assert f'width="240.0mm" height="{240 * h / w:.1f}mm"' in wide
    fitted = render_svg(placed, width_mm=240, max_height_mm=60) or ""                                                  # a page it must fit in: smaller, same shape
    assert f'width="{60 * w / h:.1f}mm" height="60.0mm"' in fitted and render_svg(placed, width_mm=240, max_height_mm=500) == wide
    titled = render_svg(placed, title="T") or ""
    assert 'viewBox="0 -18 ' in titled


def test_it_draws_what_was_kept_and_nothing_else() -> None:
    """A laid-out diagram may come back from a store: the SVG is drawn from that data as it is — a
    number that is not one, a state nobody knows, a path that is not a path do not get through."""
    kept = json.loads(json.dumps(lay_out(model())))
    assert render_svg(kept) == render_svg(lay_out(model()))
    kept["nodes"][0].update(state="exploded", x="oops", badges="none")
    kept["links"][0].update(path='M 0 0 L 9 9" onload="alert(1)', arrow="<x>", label=None)
    kept["links"].append("not a link")
    svg = render_svg(kept) or ""
    ElementTree.fromstring(svg)
    assert "onload" not in svg and 'd="M 0 0 L 9 9 1"' in svg and "exploded" not in svg and "Not known" in texts(svg)
    assert badge_line({"badges": [{"label": "A", "value": 1}, {"label": "B", "value": None}, {"label": "C", "value": "x"}, {"label": "D", "value": "y"}]}) == "A 1 · C x"


def test_weasyprint_draws_it_on_the_reports_landscape_page() -> None:
    from liberty.reports.render import BuildOptions, build_pdf, md_to_html_body

    opts = BuildOptions()
    svg = render_svg(lay_out(model()), title="Architecture", width_mm=opts.svg_width_mm, max_height_mm=opts.svg_height_mm) or ""
    md = "# T\n\n## A\n\nbody.\n\n![architecture.svg](architecture.svg)\n\n## B\n\nmore.\n"
    html, _toc = md_to_html_body(md, svg, opts)
    assert svg in html                                                                 # its own size: the renderer leaves it as it is
    # in the markdown itself (a figure in the text): on one line, the markdown step adds no <br> inside it
    inline, _toc = md_to_html_body(f"# T\n\n<div>{svg}</div>\n", None, opts)
    assert svg in inline
    without, with_svg = build_pdf(md, svg_text=None, options=opts), build_pdf(md, svg_text=svg, options=opts)
    assert with_svg.startswith(b"%PDF-") and len(with_svg) > len(without) + 2000
