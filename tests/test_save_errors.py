"""A refused configuration save says which object, which field and what is wrong
(:mod:`liberty.web.save_errors`) — and the routes answer it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from liberty.config import (
    AISettings,
    AppSettings,
    AuthSettings,
    ChartSettings,
    ConnectorSettings,
    CryptoSettings,
    DashboardSettings,
    MenuSettings,
    NotifySettings,
    ScreenSettings,
    Settings,
)
from liberty.connectors.config import PoolConfig
from liberty.connectors.dictionary import DictionaryFile
from liberty.jobs import JobsFile
from liberty.main import create_app
from liberty.menus.config import MenusFile
from liberty.screens.config import ScreensFile, parse_screens
from liberty.web.save_errors import MAX_PROBLEMS, ConfigInvalid, config_invalid, problems_of
from tests.test_web_admin import JWT_SECRET, _h, _seed, _toml


def _refused(model: type[BaseModel], data: Any) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(data)
    return caught.value


def _screens(screen: dict[str, Any]) -> dict[str, Any]:
    return {"screens": {"demo": {"orders": {"id": "orders", "label": "Orders", "read_query": "orders_get", **screen}}}}


def test_a_missing_field_is_placed_by_the_labels_of_what_holds_it() -> None:
    data = _screens({"columns": [{"name": "A"}, {"name": "B", "visible_when": [{"field": "STATUS"}]}]})
    problems = problems_of(_refused(ScreensFile, data), data, ScreensFile)
    assert len(problems) == 1                      # the other shape of the choice says nothing
    problem = problems[0]
    assert problem.path == ["screens", "demo", "orders", "columns", 1, "visible_when", 0, "value"]
    assert problem.where.startswith('demo › Screen "Orders" › Column "B" › ')
    assert problem.where.endswith("#1 › Is one of")          # the label the form shows for ``value``
    assert problem.message == "is required" and problem.code == "missing"


def test_the_words_are_plain() -> None:
    data = _screens({"max_rows": "many", "columns": [{"name": "A", "rules": 3}]})
    said = {p.where.split(" › ")[-1]: p.message for p in problems_of(_refused(ScreensFile, data), data, ScreensFile)}
    assert said["Rows read at most"] == "must be a whole number"   # the field's label in the designer
    assert list(said.values()).count("must be text") == 1


def test_a_name_that_is_not_a_setting_is_said_in_the_message() -> None:
    data = {"jobs": [{"id": "load_a", "steps": [{"name": "copy", "type": "sql_copy", "tabel": "x"}]}]}
    problem = next(p for p in problems_of(_refused(JobsFile, data), data, JobsFile) if p.code == "extra_forbidden")
    assert problem.where == 'Job "load_a" › Step "copy"'
    assert problem.message == '"tabel" is not a setting that exists here'
    assert problem.path == ["jobs", 0, "steps", 0, "tabel"]


def test_an_action_without_a_type_and_with_an_unknown_one() -> None:
    data = _screens({"row_menu": [{"label": "Open"}, {"label": "Close", "type": "nope"}]})
    first, second = problems_of(_refused(ScreensFile, data), data, ScreensFile)
    assert first.path == ["screens", "demo", "orders", "row_menu", 0] and first.message == "has no type"
    assert "nope" in second.message and "run_query" in second.message and "'" not in second.message


def test_a_job_and_its_step_are_named() -> None:
    data = {"jobs": [{"id": "load_a", "steps": [{"name": "copy", "type": "sql_copy", "timeout_seconds": "soon"}]}]}
    problems = problems_of(_refused(JobsFile, data), data, JobsFile)
    assert problems and all(p.where.startswith('Job "load_a"') for p in problems)
    timeout = next(p for p in problems if p.path[-1] == "timeout_seconds")
    assert timeout.path == ["jobs", 0, "steps", 0, "timeout_seconds"]
    assert 'Step "copy"' in timeout.where


def test_a_dictionary_entry_and_a_menu_entry_are_named() -> None:
    data = {"connectors": {"demo": {"entries": {"STATUS": {"label": "Status", "decimals": "two"}}}}}
    problem = problems_of(_refused(DictionaryFile, data), data, DictionaryFile)[0]
    assert problem.where.startswith('demo › Dictionary entry "Status"')
    assert problem.path[:4] == ["connectors", "demo", "entries", "STATUS"]
    data = {"menus": {"demo": {"items": [{"id": "orders", "label": "Orders", "type": "nope"}]}}}
    problem = problems_of(_refused(MenusFile, data), data, MenusFile)[0]
    assert 'Menu entry "orders"' in problem.where and problem.path[:4] == ["menus", "demo", "items", 0]


def test_a_part_validated_alone_keeps_its_place() -> None:
    raw = {"url": 5}
    exc = _refused(PoolConfig, raw)
    refused = config_invalid("pools", exc, raw, PoolConfig, prefix=("pools", "main"), prefix_where='Pool "main"')
    assert isinstance(refused, ConfigInvalid) and refused.status_code == 422
    assert refused.problems[0].path[:2] == ["pools", "main"]
    assert str(refused.detail).startswith('Pool "main" › ')


def test_a_rule_checked_by_hand_keeps_its_own_sentence() -> None:
    with pytest.raises(ValueError) as caught:
        parse_screens({"screens": {"demo": {"orders": {"id": "other", "read_query": "q"}}}})
    refused = config_invalid("screens", caught.value, {}, ScreensFile)
    assert refused.problems and refused.detail
    assert "validation error" not in str(refused.detail) and "type=" not in str(refused.detail)


def test_the_text_shows_the_first_problems_and_counts_the_rest() -> None:
    data = _screens({"columns": [{"name": f"C{i}", "rules": i} for i in range(MAX_PROBLEMS + 5)]})
    refused = config_invalid("screens", _refused(ScreensFile, data), data, ScreensFile)
    assert len(refused.problems) == MAX_PROBLEMS
    lines = str(refused.detail).split("\n")
    assert len(lines) == 6 and lines[-1] == f"… and {MAX_PROBLEMS + 5 - 5} more"


# ── through the routes ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(_toml(db_url))
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        notify=NotifySettings(config_path=tmp_path / "notifications.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
        crypto=CryptoSettings(master_key="test-master-key-for-config-saves"),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_a_refused_screen_save_answers_text_and_problems(client: TestClient) -> None:
    h = _h(client, "admin")
    bad = {"demo": {"orders": {"label": "Orders", "read_query": "q", "columns": [{"name": "A", "rules": 3}]}}}
    r = client.put("/admin/config/screens/parsed", json={"screens": bad}, headers=h)
    assert r.status_code == 422
    body = r.json()
    assert body["detail"] == 'demo › Screen "Orders" › Column "A" › Kind of field: must be text'
    assert body["section"] == "screens"
    assert body["errors"] == [{
        "path": ["screens", "demo", "orders", "columns", 0, "rules"],
        "where": 'demo › Screen "Orders" › Column "A" › Kind of field', "message": "must be text", "code": "string_type",
    }]


@pytest.mark.parametrize(("route", "body", "start"), [
    ("/admin/config/menus/parsed", {"menus": {"demo": {"items": [{"id": "a", "label": "A", "type": "nope"}]}}}, "menus"),
    ("/admin/config/charts/parsed", {"charts": {"demo": {"c1": {"label": "C", "spec": 3}}}}, "charts"),
    ("/admin/config/dashboards/parsed", {"dashboards": {"demo": {"d1": {"label": "D", "widgets": 3}}}}, "dashboards"),
    ("/admin/config/notifications/parsed", {"notifications": {"r1": {"label": "R", "trigger": 3}}}, "notifications"),
    ("/admin/config/dictionary/parsed", {"dictionary": {"entries": {"A": {"decimals": "x"}}}}, "entries"),
    ("/admin/config/pools", {"pools": {"main": {"url": 5}}}, "pools"),
    ("/admin/config/theme/parsed", {"theme": {"vars": 3}}, "theme"),
])
def test_every_builder_route_answers_problems(client: TestClient, route: str, body: dict[str, Any], start: str) -> None:
    r = client.put(route, json=body, headers=_h(client, "admin"))
    assert r.status_code == 422, r.text
    answer = r.json()
    assert isinstance(answer["detail"], str) and "validation error" not in answer["detail"]
    assert "type=" not in answer["detail"] and "pydantic" not in answer["detail"]
    assert answer["errors"] and answer["errors"][0]["path"][0] == start
    assert all(e["message"] for e in answer["errors"])
