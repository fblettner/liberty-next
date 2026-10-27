"""A config dashboard as sections: the page's binding rules (filters through the screen's dd map,
the period's binds and range, the period before), the widget shaping (KPI, chart, table) and the
section list with one KPI card row."""

from __future__ import annotations

from datetime import date

import pytest

from liberty.dashboards.report import (
    PeriodPick, chart_section, dashboard_sections, filter_params, kpi_display, kpi_value, period_params, previous_period, table_section,
)


def test_previous_period_follows_the_page_rules() -> None:
    assert previous_period(date(2026, 1, 1), date(2026, 12, 31)) == (date(2025, 1, 1), date(2025, 12, 31))
    assert previous_period(date(2026, 9, 1), date(2026, 9, 30)) == (date(2026, 8, 1), date(2026, 8, 31))
    assert previous_period(date(2026, 10, 1), date(2026, 10, 27)) == (date(2026, 9, 1), date(2026, 9, 27))
    assert previous_period(date(2026, 10, 21), date(2026, 10, 27)) == (date(2026, 10, 14), date(2026, 10, 20))
    assert previous_period(date(2026, 4, 1), date(2026, 6, 30)) == (date(2026, 1, 1), date(2026, 3, 31))
    assert previous_period(date(2026, 1, 1), date(2026, 1, 31)) == (date(2025, 1, 1), date(2025, 1, 31))    # from January 1st: the year before (year-to-date)


def test_period_and_filter_params_bind_like_the_page() -> None:
    period = {"dd": "SALES_DATE", "basis": [{"value": "doc", "label": "Document", "dd": "SALES_DATE"}, {"value": "paid", "label": "Paid", "column": "PAID_ON"}]}
    pick = PeriodPick(date(2026, 9, 1), date(2026, 9, 30), "paid")
    p = period_params(period, pick, None)
    assert p == {"PERIOD_FROM": "2026-09-01", "PERIOD_TO": "2026-09-30", "PREV_FROM": "2026-08-01", "PREV_TO": "2026-08-31", "PERIOD_BASIS": "paid", "PAID_ON_from": "2026-09-01", "PAID_ON_to": "2026-09-30"}
    p = period_params(period, PeriodPick(date(2026, 9, 1), date(2026, 9, 30), "doc"), {"SALES_DATE": "SDATE"})
    assert p["SDATE_from"] == "2026-09-01" and "PAID_ON_from" not in p
    assert period_params(period, PeriodPick(date(2026, 9, 1), date(2026, 9, 30), "doc"), None)["_dd_SALES_DATE_from"] == "2026-09-01"   # no screen → the item itself
    all_dates = period_params(period, PeriodPick(), None)
    assert all_dates["PERIOD_FROM"] == "0001-01-01" and all_dates["PREV_TO"] == "9999-12-31" and not any(k.endswith("_from") for k in all_dates if k != "PERIOD_FROM" and k != "PREV_FROM")
    assert period_params(None, pick, None) == {} and period_params(period, None, None) == {}
    filters = [{"id": "bu", "label": "Business unit", "dictionary_key": "BU_CODE"}, {"id": "co", "label": "Company", "dictionary_key": "CO"}]
    assert filter_params(filters, {"bu": "B1", "co": ""}, {"BU_CODE": "UNIT", "CO": "COMPANY"}) == {"UNIT": "B1", "UNIT_op": "equals"}
    assert filter_params(filters, {"bu": "B1"}, None) == {"_dd_BU_CODE": "B1"} and filter_params(filters, {"bu": "B1"}, {"OTHER": "X"}) == {"_dd_BU_CODE": "B1"}


RESULT = {
    "columns": [{"name": "unit", "label": "Unit"}, {"name": "month", "label": "Month"}, {"name": "qty", "label": "Quantity"}, {"name": "secret", "label": "S", "hidden": True}],
    "rows": [
        {"unit": "A", "month": "2026-02", "qty": 5, "secret": 1}, {"unit": "A", "month": "2026-01", "qty": "7", "secret": 1},
        {"unit": "B", "month": "2026-01", "qty": None, "secret": 1}, {"unit": None, "month": "2026-02", "qty": 2.5, "secret": 1},
    ],
}


def test_kpi_chart_and_table_shaping() -> None:
    assert kpi_value(RESULT, "QTY", "count") == 4 and kpi_value(RESULT, "QTY", "sum") == 14.5 and kpi_value(RESULT, "qty", "max") == 7
    assert kpi_value(RESULT, "qty", "avg") == pytest.approx(14.5 / 3) and kpi_value({"columns": [], "rows": []}, "qty", "sum") is None
    assert kpi_display(0.256, "percent", "fr") == "25,6 %" and kpi_display(1234.5, "currency", "en") == "1,234.50 €" and kpi_display(3.0, None, None) == 3.0
    s = chart_section("By unit", {"spec": {"type": "bar", "x": "UNIT", "y": ["QTY"], "aggregation": "sum", "stacked": True}}, RESULT)
    assert s.kind == "chart" and s.chart == "bar" and s.stacked and s.headers == ["Unit", "Quantity"]
    assert s.rows == [["A", 12.0], ["B", None], ["(empty)", 2.5]]
    s = chart_section("Trend", {"spec": {"type": "area", "x": "month", "y": ["qty"], "aggregation": "count", "sort_by_x": True}}, RESULT)
    assert s.chart == "line" and s.rows == [["2026-01", 2.0], ["2026-02", 2.0]]
    s = chart_section("Raw", {"spec": {"type": "line", "x": "month", "y": ["qty"], "aggregation": "none"}}, RESULT)
    assert len(s.rows) == 4 and s.rows[1] == ["2026-01", 7.0]
    assert chart_section("Empty", {"spec": {"type": "pie", "x": "", "y": []}}, RESULT).headers == []
    t = table_section("Rows", {"columns": ["QTY", "nope", "unit"], "max_rows": 2}, RESULT)
    assert t.headers == ["Quantity", "Unit"] and t.rows == [[5, "A"], ["7", "A"]]
    assert table_section("All", {"columns": []}, RESULT).headers == ["Unit", "Month", "Quantity"]      # hidden column dropped


@pytest.mark.asyncio
async def test_dashboard_sections_group_the_kpis_and_keep_going_on_a_failure() -> None:
    dashboard = {
        "filters": [{"id": "bu", "label": "Unit", "dictionary_key": "BU"}],
        "period": {"column": "SOLD_ON"},
        "widgets": [
            {"type": "kpi", "label": "Lines", "connector": "c", "query": "q1", "column": "qty", "aggregation": "count"},
            {"type": "kpi", "label": "Quantity", "connector": "c", "query": "q1", "column": "qty", "aggregation": "sum", "format": "currency"},
            {"type": "chart", "label": "By unit", "connector": "c", "query": "q1", "spec": {"type": "pie", "x": "unit", "y": ["qty"], "aggregation": "sum"}},
            {"type": "table", "label": "Broken", "connector": "c", "query": "boom", "columns": []},
            {"type": "kpi", "label": "Later", "connector": "c", "query": "q1", "column": "qty", "aggregation": "min"},
        ],
    }
    seen: list[tuple[str, str, dict]] = []

    async def run(c: str, q: str, params: dict) -> dict:
        seen.append((c, q, params))
        if q == "boom":
            raise RuntimeError("no such table")
        return RESULT

    sections = await dashboard_sections(dashboard, run=run, dd_map=lambda c, q: {"BU": "unit"}, values={"bu": "A"}, pick=PeriodPick(date(2026, 2, 1), date(2026, 2, 28)), lang="fr")
    assert [s.kind for s in sections] == ["kpi", "chart", "error", "kpi"]
    assert sections[0].title == "Chiffres clés" and sections[0].headers == ["Lines", "Quantity"] and sections[0].rows == [[4.0, "14,50 €"]]
    assert sections[1].chart == "pie" and sections[2].rows == [["no such table"]] and sections[3].rows == [[2.5]]
    assert seen[0][2] == {"unit": "A", "unit_op": "equals", "PERIOD_FROM": "2026-02-01", "PERIOD_TO": "2026-02-28", "PREV_FROM": "2026-01-01", "PREV_TO": "2026-01-31", "SOLD_ON_from": "2026-02-01", "SOLD_ON_to": "2026-02-28"}


def test_split_dd_binds_takes_the_reserved_params_out() -> None:
    from liberty.web.connectors import split_dd_binds
    plain, dd = split_dd_binds({"PERIOD_FROM": "2026-01-01", "_dd_BU_CODE": "B1", "_dd_SALES_DATE_from": "2026-01-01", "_dd_SALES_DATE_to": "2026-01-31", "_dd_": "x"})
    assert plain == {"PERIOD_FROM": "2026-01-01"} and dd == {"BU_CODE": "B1", "SALES_DATE__from": "2026-01-01", "SALES_DATE__to": "2026-01-31"}
    assert split_dd_binds({}) == ({}, {})


def test_synthesis_widgets_shape_and_format() -> None:
    from liberty.dashboards.report import bars_section, fill_template, format_value, list_section, stat_card
    assert format_value(1234.5, "number", None, "en") == "1,234.5" and format_value(1234, "currency", None, "fr") == "1 234 €"
    assert format_value(0.256, "percent", None, "en") == "25.6 %" and format_value(12, "integer", "users", "en") == "12 users" and format_value("x", None, None, None) == "x"
    res = {"columns": [{"name": "title", "label": "Decision"}, {"name": "hint"}, {"name": "amount", "label": "Amount"}, {"name": "n"}, {"name": "tone"}, {"name": "status"}, {"name": "users"}, {"name": "enabled"}],
           "rows": [{"title": "Dormant", "hint": "No login for 90 days", "amount": 1500, "n": 7, "tone": "warn", "status": "to review", "users": 120, "enabled": 110},
                    {"title": "Generic", "hint": None, "amount": 0, "n": 2, "tone": "error", "status": "", "users": 120, "enabled": 110}]}
    assert fill_template("{USERS:integer} users · {ENABLED} on · {NOPE}", res["rows"][0], res, "en") == "120 users · 110 on · {NOPE}"
    assert stat_card({"label": "Users", "value_column": "USERS", "format": "integer", "sub": "{ENABLED:integer} enabled"}, res, "en") == ("Users", "120 — 110 enabled")
    s = list_section("Decisions", {"title_column": "TITLE", "subtitle_column": "HINT", "value_column": "AMOUNT", "format": "currency", "count_column": "N", "count_label": "{n} users", "tag_column": "STATUS", "max_rows": 1}, res, "en")
    assert s.headers == ["Decision", "hint", "Amount", "n", "status"] and s.rows == [["Dormant", "No login for 90 days", "1,500 €", "7 users", "to review"]]
    b = bars_section("Per component", {"label_column": "TITLE", "value_column": "AMOUNT", "share": "total", "note": "n"}, res, "en")
    assert b.headers == ["Decision", "Amount", "%"] and b.rows[0] == ["Dormant", "1,500", "100.0 %"] and b.note == "n"
