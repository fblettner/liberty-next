"""Request ids + structured logs (liberty.observability)."""

from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from liberty.observability import JsonFormatter, RequestIdFilter, make_formatter, request_id_var


def _client():
    from liberty.config import AppSettings, AuthSettings, Settings
    from liberty.main import create_app

    return TestClient(create_app(Settings(app=AppSettings(static_dir=""), auth=AuthSettings(jwt_secret="x" * 40))))


def test_response_carries_a_request_id_minted_or_echoed() -> None:
    with _client() as c:
        r = c.get("/health")
        assert r.status_code == 200 and len(r.headers["x-request-id"]) == 32
        r = c.get("/health", headers={"X-Request-ID": "ticket-4711"})
        assert r.headers["x-request-id"] == "ticket-4711"
        r = c.get("/health", headers={"X-Request-ID": "x" * 300})  # too long → replaced
        assert r.headers["x-request-id"] != "x" * 300 and len(r.headers["x-request-id"]) == 32


def test_json_formatter_and_filter() -> None:
    rec = logging.LogRecord("liberty.test", logging.WARNING, __file__, 1, "hello %s", ("you",), None)
    rec.screen = "users"  # extra={...}
    token = request_id_var.set("req-1")
    try:
        assert RequestIdFilter().filter(rec) is True and rec.request_id == "req-1"
        out = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(token)
    assert out["level"] == "WARNING" and out["logger"] == "liberty.test" and out["msg"] == "hello you"
    assert out["request_id"] == "req-1" and out["screen"] == "users" and out["ts"].endswith("+00:00")
    assert "exc" not in out


def test_json_formatter_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = logging.LogRecord("liberty.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    out = json.loads(JsonFormatter().format(rec))
    assert "ValueError: boom" in out["exc"]


def test_text_formatter_has_the_request_id() -> None:
    rec = logging.LogRecord("liberty.test", logging.INFO, __file__, 1, "ok", (), None)
    RequestIdFilter().filter(rec)
    line = make_formatter("text").format(rec)
    assert "INFO" in line and "liberty.test [-] — ok" in line
