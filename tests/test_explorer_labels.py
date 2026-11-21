"""The export's reading of a coded column: label, code or both (``liberty.explorer.labels``)."""

from __future__ import annotations

from typing import Any

from liberty.explorer.labels import apply_shows, is_coded, rule_labels, show_modes, shown_text

LOOKUP = {"kind": "lookup", "connector": "app", "query": "companies", "value": "CODE", "label": "NAME"}
ENUM = {"kind": "enum", "values": [{"value": "A", "label": "Active"}, {"value": "I", "label": "Inactive"}, {"value": "X"}]}


def _runner(rows: list[dict[str, Any]], calls: list[tuple[str, str, dict[str, Any]]] | None = None):
    async def run(connector: str, query: str, params: dict[str, Any]) -> dict[str, Any]:
        if calls is not None:
            calls.append((connector, query, params))
        return {"rows": rows}
    return run


def test_show_modes_and_text() -> None:
    assert show_modes([{"field": "u.ID", "show": "both"}, {"field": "u.NAME"}, {"field": "u.ST", "show": "label"}, {"field": "s.DAY", "bucket": "month", "show": "code"}]) == {
        "u.ID": "both", "s.DAY@month": "code", "s.DAY": "code"}
    assert shown_text("101", "Alpine", "label") == "Alpine"
    assert shown_text("101", "Alpine", "both") == "101 · Alpine"
    assert shown_text("101", "Alpine", "code") == "101"
    assert shown_text("101", None, "both") == "101" and shown_text("FR", "FR", "both") == "FR"


def test_is_coded() -> None:
    assert is_coded({"kind": "dimension", "rule": LOOKUP}) and is_coded({"kind": "column", "rule": ENUM})
    assert not is_coded({"kind": "measure", "rule": ENUM}) and not is_coded({"kind": "dimension", "rule": ENUM, "bucket": "month"})
    assert not is_coded({"kind": "dimension", "rule": {"kind": "boolean"}}) and not is_coded({"kind": "dimension"})


async def test_rule_labels_enum_and_lookup() -> None:
    assert await rule_labels(ENUM, _runner([])) == {"A": "Active", "I": "Inactive", "X": "X"}
    calls: list[tuple[str, str, dict[str, Any]]] = []
    # the result's column case is the database's; a padded code is trimmed; a missing label is the code
    rows = [{"code": "101  ", "name": "Alpine"}, {"code": 102, "name": None}, {"code": None, "name": "nobody"}]
    assert await rule_labels(LOOKUP, _runner(rows, calls)) == {"101": "Alpine", "102": "102"}
    assert calls == [("app", "companies", {})]


async def test_rule_labels_params_narrow_the_rows_and_sources_union() -> None:
    udc = {"kind": "lookup", "connector": "jde", "query": "udc", "value": "KY", "label": "DL01", "params": {"SY": "01", "RT": "ST", "EMPTY": ""}}
    rows = [{"SY": "01  ", "RT": "ST", "KY": "A", "DL01": "Alabama"}, {"SY": "01", "RT": "CN", "KY": "A", "DL01": "Austria"}]
    calls: list[tuple[str, str, dict[str, Any]]] = []
    assert await rule_labels(udc, _runner(rows, calls)) == {"A": "Alabama"}
    assert calls == [("jde", "udc", {"SY": "01", "RT": "ST"})]                       # an empty parameter is not bound
    both = {**LOOKUP, "sources": [{"connector": "a", "query": "q"}, {"connector": "b", "query": "q"}]}
    calls = []
    assert await rule_labels(both, _runner([{"CODE": "1", "NAME": "One"}], calls)) == {"1": "One"}
    assert [c[0] for c in calls] == ["a", "b"]


async def test_apply_shows_per_mode() -> None:
    cols = [{"key": "s.CO", "field": "s.CO", "kind": "dimension", "label": "Company", "rule": LOOKUP},
            {"key": "sum(s.Q)", "field": "s.Q", "kind": "measure", "label": "Quantity"}]
    rows = [{"s.CO": 101, "sum(s.Q)": 5}, {"s.CO": 999, "sum(s.Q)": 1}, {"s.CO": None, "sum(s.Q)": 2}]
    run = _runner([{"CODE": "101", "NAME": "Alpine"}])
    base = {"rows": [{"field": "s.CO"}]}
    # no runner: nothing changes (the codes, as before)
    assert await apply_shows(cols, rows, base, None, split=True) == (cols, rows)
    # label (the default): the code replaced, an unknown code kept, an empty group kept empty
    c, r = await apply_shows(cols, rows, base, run, split=True)
    assert [x["key"] for x in c] == ["s.CO", "sum(s.Q)"] and [x["s.CO"] for x in r] == ["Alpine", "999", None]
    assert rows[0]["s.CO"] == 101                                                    # the caller's rows are not touched
    # code: as it comes
    c, r = await apply_shows(cols, rows, {"rows": [{"field": "s.CO", "show": "code"}]}, run, split=True)
    assert [x["s.CO"] for x in r] == [101, 999, None]
    # both, in a table: two columns — the code under "<label> (ID)", then the label
    c, r = await apply_shows(cols, rows, {"rows": [{"field": "s.CO", "show": "both"}]}, run, split=True)
    assert [(x["key"], x["label"]) for x in c] == [("s.CO", "Company (ID)"), ("s.CO__label", "Company"), ("sum(s.Q)", "Quantity")]
    assert [(x["s.CO"], x["s.CO__label"]) for x in r] == [(101, "Alpine"), (999, "999"), (None, None)]
    # both, where a value is one cell (a pivot, a chart)
    c, r = await apply_shows(cols, rows, {"rows": [{"field": "s.CO", "show": "both"}]}, run, split=False)
    assert [x["key"] for x in c] == ["s.CO", "sum(s.Q)"] and [x["s.CO"] for x in r] == ["101 · Alpine", "999", None]


async def test_a_lookup_that_cannot_be_read_leaves_the_codes() -> None:
    async def refused(connector: str, query: str, params: dict[str, Any]) -> dict[str, Any]:
        raise PermissionError("no")
    cols = [{"key": "s.CO", "field": "s.CO", "kind": "dimension", "label": "Company", "rule": LOOKUP}]
    rows = [{"s.CO": 101}]
    memo: dict[str, dict[str, str]] = {}
    c, r = await apply_shows(cols, rows, {"rows": [{"field": "s.CO", "show": "both"}]}, refused, split=True, memo=memo)
    assert c == cols and r == rows and len(memo) == 1                                 # asked once, then remembered
