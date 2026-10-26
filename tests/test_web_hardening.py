"""A3.6 hardening: the package-upload size caps and the ``/ready`` readiness probe."""

from __future__ import annotations

import io
import textwrap
import zipfile
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.config import (
    ActionSettings,
    AISettings,
    AppSettings,
    AuthSettings,
    ChartSettings,
    ConnectorSettings,
    DashboardSettings,
    JobsSettings,
    MenuSettings,
    ScreenSettings,
    Settings,
    ThemeSettings,
)
from liberty.main import create_app


def _settings(tmp_path: Path, pool_url: str) -> Settings:
    (tmp_path / "connectors.toml").write_text(textwrap.dedent(f'''
        [pools.default]
        url = "{pool_url}"
    '''))
    (tmp_path / "jobs.toml").write_text("")
    return Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=tmp_path / "connectors.toml"),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        actions=ActionSettings(config_path=tmp_path / "actions.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        theme=ThemeSettings(config_path=tmp_path / "theme.toml"),
        auth=AuthSettings(backend="db", toml_path=tmp_path / "auth.toml", jwt_secret="t" * 40),
        ai=AISettings(enabled=False),
        jobs=JobsSettings(config_path=tmp_path / "jobs.toml", enabled=False),
    )


@pytest.fixture
def sqlite_settings(tmp_path: Path) -> Settings:
    return _settings(tmp_path, f"sqlite+aiosqlite:///{tmp_path / 'fw.db'}")


def _superuser_headers(client: TestClient, app) -> dict[str, str]:
    client.portal.call(app.state.auth_backend.ready)
    client.portal.call(partial(app.state.auth_backend.create_user, "admin", password="adminpw", is_superuser=True))
    tok = client.post("/auth/login", json={"username": "admin", "password": "adminpw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ── /admin/import-package caps ─────────────────────────────────────────────────────


def test_import_package_rejects_an_oversized_upload(sqlite_settings, monkeypatch) -> None:
    import liberty.web.admin as admin

    monkeypatch.setattr(admin, "MAX_PACKAGE_BYTES", 64)
    app = create_app(sqlite_settings)
    with TestClient(app) as client:
        h = _superuser_headers(client, app)
        blob = _zip({"menus.toml": b"# " + b"x" * 500})  # well over 64 bytes on the wire
        r = client.post("/admin/import-package", headers=h, files={"package": ("p.zip", blob, "application/zip")})
        assert r.status_code == 413 and "exceeds" in r.json()["detail"]


def test_import_package_rejects_a_zip_that_unpacks_too_big(sqlite_settings, monkeypatch) -> None:
    """A zip bomb declares a huge unpacked size for a tiny archive — refused before any
    member is read (the declared sizes are in the central directory)."""
    import liberty.web.package_import as pi

    monkeypatch.setattr(pi, "MAX_UNPACKED_BYTES", 10_000)
    app = create_app(sqlite_settings)
    with TestClient(app) as client:
        h = _superuser_headers(client, app)
        blob = _zip({"menus.toml": b"#" * 50_000})  # ~50 KB unpacked, a few hundred bytes zipped
        assert len(blob) < 10_000
        r = client.post("/admin/import-package", headers=h, files={"package": ("p.zip", blob, "application/zip")})
        assert r.status_code == 400 and "unpacked" in r.json()["detail"]


def test_import_package_still_accepts_a_normal_package(sqlite_settings) -> None:
    app = create_app(sqlite_settings)
    with TestClient(app) as client:
        h = _superuser_headers(client, app)
        blob = _zip({"menus.toml": b'[menus.demo]\nlabel = "Demo"\nitems = []\n'})
        r = client.post("/admin/import-package?strategy=overwrite", headers=h,
                        files={"package": ("p.zip", blob, "application/zip")})
        assert r.status_code == 200, r.text
        report = r.json()["report"]
        # The upload reached the importer and the framework reloaded afterwards — the
        # endpoint works end to end again (an UploadFile isinstance check against the
        # FastAPI subclass used to reject every real upload as "missing").
        assert [f["file"] for f in report["files"]] == ["menus.toml"]
        assert report["files"][0]["errors"] == [] and report["reloaded"] is True


# ── /ready ─────────────────────────────────────────────────────────────────────────


def test_ready_ok_when_the_framework_pool_answers(sqlite_settings) -> None:
    with TestClient(create_app(sqlite_settings)) as client:
        r = client.get("/ready")
        assert r.status_code == 200 and r.json() == {"status": "ok", "version": r.json()["version"], "pool": "default"}
        assert client.get("/health").status_code == 200  # liveness untouched


def test_ready_503_when_the_framework_pool_is_unreachable(tmp_path: Path) -> None:
    """A Postgres URL on a closed port: connect is refused at once → 503, while /health
    keeps answering 200 (the process itself is fine)."""
    settings = _settings(tmp_path, "postgresql+asyncpg://u:p@127.0.0.1:1/db")
    settings = settings.model_copy(update={"auth": AuthSettings(backend="toml", toml_path=tmp_path / "auth.toml", jwt_secret="t" * 40)})
    with TestClient(create_app(settings)) as client:
        r = client.get("/ready")
        assert r.status_code == 503 and r.json()["status"] == "unavailable" and r.json()["pool"] == "default"
        assert client.get("/health").status_code == 200
