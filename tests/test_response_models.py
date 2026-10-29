"""The documented response models (liberty/web/schemas.py) match what the routes return: each route
is called on a small configuration and its JSON validated against its model — top-level shapes are
closed, so a key a route adds without its model fails here. The routes themselves are untouched (the
models are declared under ``responses``, never ``response_model``)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from liberty.connectors.api import ApiResult
from liberty.web import schemas
from tests.test_web_admin_ops import _h, make_env


@pytest.fixture
def client(tmp_path):
    app, _ = make_env(tmp_path)
    with TestClient(app) as c:
        yield c


CASES = [
    ("GET", "/api/connectors", None, schemas.ConnectorList),
    ("GET", "/api/connectors/foo", None, schemas.ConnectorInfo),
    ("GET", "/api/sql/foo/_schemas", None, schemas.PoolSchemas),
    ("GET", "/api/sql/foo/_schema", None, schemas.PoolSchema),
    ("GET", "/api/sql/foo/users_get?_screen=users&_app=foo", None, schemas.QueryResultOut),
    ("POST", "/api/sql/foo/users_get", {}, schemas.QueryResultOut),
    ("GET", "/api/menus", None, schemas.MenusOut),
    ("GET", "/api/menus/foo", None, schemas.AppMenu),
    ("GET", "/api/external-urls", None, schemas.ExternalUrls),
    ("GET", "/api/screens", None, schemas.ScreensOut),
    ("GET", "/api/screens/foo", None, schemas.AppScreensOut),
    ("GET", "/api/screens/foo/users", None, schemas.ScreenDetail),
]


@pytest.mark.parametrize(("method", "path", "body", "model"), CASES, ids=[f"{m} {p}" for m, p, _, _ in CASES])
def test_route_matches_its_model(client, method, path, body, model) -> None:
    h = _h(client, "admin")
    r = client.get(path, headers=h) if method == "GET" else client.post(path, json=body, headers=h)
    assert r.status_code == 200, r.text
    model.model_validate(r.json())


def test_screen_summary_lists_every_key_of_the_list_view(client) -> None:
    """The compact descriptor is closed: every key the list view emits is documented."""
    screens = client.get("/api/screens", headers=_h(client, "admin")).json()["screens"]["foo"]
    assert set(screens[0]) == set(schemas.ScreenSummary.model_fields)


def test_import_and_api_results_match() -> None:
    schemas.ApiResultOut.model_validate(ApiResult(connector="c", endpoint="e", success=True, status_code=200, url="http://x",
                                                  json={"a": 1}).to_dict())
    schemas.ImportResult.model_validate({"committed": False, "mode": "upsert", "valid": 1, "invalid": 1, "results": [
        {"index": 0, "action": "insert", "ok": True, "rowcount": 1}, {"index": 1, "action": "update", "ok": False, "error": "boom"}]})


def test_documented_routes_carry_their_schema_in_openapi(client) -> None:
    spec = client.app.openapi()
    for method, path, _, model in CASES:
        route = path.split("?")[0].replace("/foo/users_get", "/{connector}/{query}").replace("/foo/users_put", "/{connector}/{query}")
        route = route.replace("/sql/foo/", "/sql/{connector}/").replace("/connectors/foo", "/connectors/{connector}")
        route = route.replace("/menus/foo", "/menus/{app}").replace("/screens/foo/users", "/screens/{app}/{screen_id}").replace("/screens/foo", "/screens/{app}")
        op = spec["paths"][route][method.lower()]
        ref = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert ref.get("$ref", "").endswith(model.__name__), (route, ref)
