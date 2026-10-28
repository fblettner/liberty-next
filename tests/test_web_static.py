from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from liberty.config import AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.main import create_app

INDEX_HTML = '<!doctype html><html><body><div id="root"></div></body></html>'


def _app(tmp_path, *, static_dir: str, connectors_toml: Path | None = None):
    cfg_path = connectors_toml or (tmp_path / "no-connectors.toml")  # missing → empty registry
    return create_app(Settings(app=AppSettings(static_dir=static_dir), connectors=ConnectorSettings(config_path=cfg_path)))


def test_spa_served_with_client_route_fallback(tmp_path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML)
    (dist / "assets" / "app.js").write_text("console.log('hi')")
    with TestClient(_app(tmp_path, static_dir=str(dist))) as client:
        # the SPA shell
        r = client.get("/")
        assert r.status_code == 200 and '<div id="root">' in r.text
        # a client-side route → fall back to index.html (not 404)
        r = client.get("/connectors/foo/bar")
        assert r.status_code == 200 and '<div id="root">' in r.text
        # real static assets are served
        r = client.get("/assets/app.js")
        assert r.status_code == 200 and "console.log" in r.text
        # API routes are NOT shadowed by the SPA mount
        assert client.get("/api/connectors").status_code == 401  # auth required, not 404/index.html
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/info").json()["frontend"] == str(dist)


def test_no_static_dir_means_no_mount(tmp_path) -> None:
    with TestClient(_app(tmp_path, static_dir="")) as client:
        assert client.get("/").status_code == 404  # nothing mounted at /
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/info").json()["frontend"] is None


def test_license_endpoints_default_restricted(tmp_path) -> None:
    with TestClient(_app(tmp_path, static_dir="")) as client:
        assert client.get("/info").json()["license"] == {"mode": "restricted"}  # no [license] key configured
        assert client.get("/api/license").status_code == 401  # the full status needs auth, not 404


def test_missing_static_dir_means_no_mount(tmp_path) -> None:
    with TestClient(_app(tmp_path, static_dir=str(tmp_path / "does-not-exist"))) as client:
        assert client.get("/").status_code == 404
        assert client.get("/info").json()["frontend"] is None


def test_connector_error_surfaces_as_503(tmp_path) -> None:
    # An unconfigured pool (env var unset → empty url) must not crash with a 500 stack
    # trace on /auth/login — the global ConnectorError handler turns it into a clean 503.
    # (Use the DB auth backend so login actually touches the pool.)
    toml = tmp_path / "connectors.toml"
    toml.write_text('[pools.default]\nurl = "${THIS_ENV_VAR_IS_DEFINITELY_NOT_SET}"\n')
    app = create_app(Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=toml),
        auth=AuthSettings(backend="db", pool="default"),
    ))
    with TestClient(app) as client:
        r = client.post("/auth/login", json={"username": "x", "password": "y"})
        assert r.status_code == 503
        assert "empty url" in r.json()["detail"]


BASED_HTML = '<!doctype html><html><head><base href="/" /></head><body><div id="root"></div></body></html>'


def _based_app(tmp_path, base_path: str):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(BASED_HTML)
    (dist / "assets" / "app.js").write_text("console.log('hi')")
    return create_app(Settings(app=AppSettings(static_dir=str(dist), base_path=base_path),
                               connectors=ConnectorSettings(config_path=tmp_path / "no-connectors.toml")))


def test_base_path_is_normalized() -> None:
    assert AppSettings(base_path="liberty/").base_path == "/liberty"
    assert AppSettings(base_path=" /a/b/ ").base_path == "/a/b"
    assert AppSettings(base_path="/").base_path == ""
    assert AppSettings().base_path == ""


def test_base_path_rewrites_the_index_base_href(tmp_path) -> None:
    # A proxy serving https://host/liberty/ strips the prefix: the app sees the bare paths.
    with TestClient(_based_app(tmp_path, "/liberty")) as client:
        for path in ("/", "/index.html", "/screen/app/x"):
            r = client.get(path)
            assert r.status_code == 200 and '<base href="/liberty/"' in r.text, path
        assert "console.log" in client.get("/assets/app.js").text
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/connectors").status_code == 401  # routed, not the index
        # A proxy that keeps the prefix reaches the same routes (root_path).
        assert client.get("/liberty/health").json()["status"] == "ok"
        # The URLs the server builds carry the prefix (the docs point at the prefixed schema).
        assert "/liberty/openapi.json" in client.get("/docs").text


def test_no_base_path_serves_the_index_as_built(tmp_path) -> None:
    with TestClient(_based_app(tmp_path, "")) as client:
        assert '<base href="/"' in client.get("/screen/app/x").text
