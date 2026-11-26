"""The application's entry page is never kept by the browser: it names the build's files, whose names change at
each build — a kept copy runs the old application against the new server after an upgrade."""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

from liberty.main import SPAStaticFiles


def _client(tmp_path: Path, base_path: str = "") -> TestClient:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<html><head><base href="/"></head><body>app</body></html>')
    (tmp_path / "assets" / "index-abc.js").write_text("console.log(1)")
    app = Starlette()
    app.mount("/", SPAStaticFiles(directory=str(tmp_path), html=True, base_path=base_path))
    return TestClient(app)


def test_the_entry_page_is_asked_again_each_time(tmp_path) -> None:
    client = _client(tmp_path)
    for path in ("/", "/index.html", "/app/nomajde/security"):       # the last: a page of the application, served the entry page
        answer = client.get(path)
        assert answer.status_code == 200 and "app" in answer.text, path
        assert answer.headers["cache-control"] == "no-cache", path


def test_the_build_files_keep_their_own_caching(tmp_path) -> None:
    answer = _client(tmp_path).get("/assets/index-abc.js")
    assert answer.status_code == 200 and "cache-control" not in answer.headers


def test_under_a_base_path_too(tmp_path) -> None:
    answer = _client(tmp_path, base_path="/liberty").get("/anything")
    assert answer.headers["cache-control"] == "no-cache" and '<base href="/liberty/"' in answer.text
