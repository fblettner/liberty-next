"""The developer reference's Configuration view (C12): every app.toml key, where the UI edits it, who
reads it."""

from __future__ import annotations

from fastapi.testclient import TestClient

from liberty.config import Settings
from liberty.devref import FILE_ONLY, config_reference, ui_editors
from tests.test_web_admin_ops import _h, env  # noqa: F401 — the app fixture


def _ref() -> dict[str, dict]:
    return {r["key"]: r for r in config_reference()}


def test_every_settings_key_is_listed() -> None:
    ref = _ref()
    expected = {f"{s}.{k}" for s, f in Settings.model_fields.items() for k in f.annotation.model_fields}
    assert set(ref) == expected


def test_editors_and_file_only_reasons() -> None:
    ref = _ref()
    assert ref["app.default_page_size"]["editor"] == "Settings → App"
    assert ref["promotion.targets"]["editor"] == "Settings → Package → Promotion"
    assert ref["crypto.master_key"]["editor"] is None and ref["crypto.master_key"]["file_only"] == FILE_ONLY["crypto.master_key"]
    assert set(ui_editors()) <= set(ref)          # no editor registered for a key that doesn't exist


def test_readers_are_found_in_every_shape() -> None:
    ref = _ref()
    def files(key: str) -> set[str]:
        return {r["file"] for r in ref[key]["readers"]}
    assert "liberty/logfile.py" in files("app.log_file")                 # getattr(settings.app, "log_file", …)
    assert "liberty/ai/assistant.py" in files("ai.max_tokens")           # self.settings.max_tokens (AISettings)
    assert "liberty/notify/dispatcher.py" in files("notify.smtp_host")   # n.smtp_host
    assert "liberty/runtime.py" in files("changesets.enabled")           # getattr(getattr(settings, "changesets"…), "enabled")
    assert all(ref[k]["readers"] for k in ref), [k for k in ref if not ref[k]["readers"]]


def test_the_route_is_superuser_only(env) -> None:  # noqa: F811
    app, _ = env
    with TestClient(app) as client:
        assert client.get("/admin/dev/config").status_code == 401
        assert client.get("/admin/dev/config", headers=_h(client, "reader")).status_code == 403
        body = client.get("/admin/dev/config", headers=_h(client, "admin")).json()
        assert body["total"] == len(body["keys"]) and body["editable"] > 0 and "app.base_path" in body["gaps"]
