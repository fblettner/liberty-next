"""Tests for :class:`liberty.jobs.HttpStepExecutor`.

httpx provides :class:`httpx.MockTransport` for exactly this — a synchronous
handler that the executor's :class:`httpx.AsyncClient` routes every request
through. The executor exposes a ``transport`` constructor seam so the test
doesn't have to monkey-patch ``httpx.AsyncClient`` globally.
"""

from __future__ import annotations

import json

import httpx
import pytest

from liberty.jobs import (
    HttpStepExecutor,
    ManualTrigger,
    RunContext,
    Step,
    StepFailed,
    StepType,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _ctx() -> RunContext:
    return RunContext(
        run_id="http-run-1",
        job_id="http-job-1",
        trigger=ManualTrigger(triggered_by="tests"),
    )


def _step(**kwargs) -> Step:
    kwargs.setdefault("type", StepType.HTTP.value)
    kwargs.setdefault("name", "http-step")
    return Step.model_validate(kwargs)


def _executor(handler) -> HttpStepExecutor:
    """Build an executor wired to a mock transport with the given request handler."""
    return HttpStepExecutor(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# happy paths — 2xx success, the four common methods
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_200_returns_status_and_body_in_extras():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(200, text="hello")

    result = await _executor(handler).execute(
        _step(url="https://example.com/health", method="GET"), _ctx()
    )
    assert seen == {"method": "GET", "url": "https://example.com/health"}
    assert result.rows_affected is None             # HTTP isn't row-shaped
    assert result.extras["status"] == 200
    assert result.extras["body"] == "hello"
    assert isinstance(result.extras["elapsed_seconds"], float)


@pytest.mark.asyncio
async def test_default_method_is_get():
    """When the operator omits `method`, the executor defaults to GET (no body)."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(204)
    result = await _executor(handler).execute(_step(url="https://example.com/ping"), _ctx())
    assert result.extras["status"] == 204
    assert result.extras["body"] == ""              # 204 No Content


@pytest.mark.asyncio
async def test_method_is_case_insensitive():
    """`post` / `Post` / `POST` are all valid — uppercased before the call."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return httpx.Response(200, text="ok")

    await _executor(handler).execute(_step(url="https://example.com/x", method="post"), _ctx())
    assert seen["method"] == "POST"


@pytest.mark.asyncio
async def test_post_json_body_seeds_content_type():
    """A dict body becomes JSON; Content-Type is added when missing."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(201, text='{"id":42}')

    await _executor(handler).execute(
        _step(
            url="https://example.com/widgets",
            method="POST",
            body={"name": "frob", "n": 3},
        ),
        _ctx(),
    )
    assert seen["content_type"] == "application/json"
    assert seen["body"] == {"name": "frob", "n": 3}


@pytest.mark.asyncio
async def test_post_dict_body_respects_operator_content_type():
    """When the operator sets Content-Type already, the executor does NOT overwrite it —
    even for a dict body. (Some APIs want application/vnd.api+json on JSON payloads.)"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(200)

    await _executor(handler).execute(
        _step(
            url="https://example.com/x",
            method="POST",
            headers={"content-type": "application/vnd.api+json"},   # lowercase on purpose
            body={"k": "v"},
        ),
        _ctx(),
    )
    assert seen["content_type"] == "application/vnd.api+json"


@pytest.mark.asyncio
async def test_post_string_body_sent_verbatim():
    """A str body is sent raw, no Content-Type added (operator owns it)."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(200)

    await _executor(handler).execute(
        _step(url="https://example.com/x", method="POST", body="raw-payload"),
        _ctx(),
    )
    assert seen["body"] == b"raw-payload"
    assert seen["content_type"] is None             # operator didn't set one


@pytest.mark.asyncio
async def test_headers_pass_through():
    """Custom headers (auth, X-Forwarded-For, idempotency keys) reach the wire."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["idempotency"] = request.headers.get("idempotency-key")
        return httpx.Response(200)

    await _executor(handler).execute(
        _step(
            url="https://example.com/x",
            method="POST",
            headers={"Authorization": "Bearer abc", "Idempotency-Key": "k-1"},
            body="payload",
        ),
        _ctx(),
    )
    assert seen["authorization"] == "Bearer abc"
    assert seen["idempotency"] == "k-1"


# --------------------------------------------------------------------------- #
# failure paths — non-2xx, network errors, timeouts, validation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_non_2xx_response_raises_step_failed_with_status_in_message():
    """A 4xx / 5xx response → StepFailed; the message includes the status + body excerpt."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    with pytest.raises(StepFailed) as exc:
        await _executor(handler).execute(
            _step(url="https://example.com/health", method="GET"), _ctx()
        )
    msg = str(exc.value)
    assert "503" in msg
    assert "upstream down" in msg


@pytest.mark.asyncio
async def test_4xx_response_raises_with_body_excerpt():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text='{"error":"not found"}')

    with pytest.raises(StepFailed) as exc:
        await _executor(handler).execute(
            _step(url="https://example.com/x", method="GET"), _ctx()
        )
    assert "404" in str(exc.value)
    assert "not found" in str(exc.value)


@pytest.mark.asyncio
async def test_unknown_method_raises_step_failed_without_network_call():
    """Validation happens before the request — operator typo doesn't hit the wire."""
    def handler(request: httpx.Request) -> httpx.Response:                 # pragma: no cover
        pytest.fail("handler should not have been called")

    with pytest.raises(StepFailed) as exc:
        await _executor(handler).execute(
            _step(url="https://example.com/x", method="WAT"), _ctx()
        )
    assert "WAT" in str(exc.value)
    assert "allowed" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_missing_url_raises_step_failed():
    """Step.model_validate enforces the URL — schema-level check, not executor."""
    with pytest.raises(Exception):                                          # noqa: B017 — pydantic ValueError
        Step.model_validate({"type": "http", "name": "no-url"})


@pytest.mark.asyncio
async def test_network_error_becomes_step_failed():
    """httpx-level connect / DNS / TLS errors all surface as StepFailed."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(StepFailed) as exc:
        await _executor(handler).execute(
            _step(url="https://example.com/x"), _ctx()
        )
    assert "ConnectError" in str(exc.value)
    assert "connection refused" in str(exc.value)


@pytest.mark.asyncio
async def test_timeout_exception_becomes_step_failed_with_timeout_in_message():
    """An httpx.TimeoutException is mapped to StepFailed with the timeout value
    surfaced (so operators see WHICH timeout fired — the executor's, not the runner's)."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(StepFailed) as exc:
        await _executor(handler).execute(
            _step(url="https://example.com/x", timeout_seconds=5), _ctx()
        )
    assert "timed out" in str(exc.value)
    assert "5s" in str(exc.value)


# --------------------------------------------------------------------------- #
# body truncation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_large_response_body_truncated_in_extras():
    """A multi-KB response is trimmed to ~4KB + appended with a truncation marker
    so the StepRun extras column doesn't bloat."""
    big = "x" * 10_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big)

    result = await _executor(handler).execute(
        _step(url="https://example.com/x"), _ctx()
    )
    body = result.extras["body"]
    assert len(body.encode("utf-8")) < 10_000
    assert "truncated" in body
    assert "10000 bytes total" in body


@pytest.mark.asyncio
async def test_small_response_body_not_truncated():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="small")

    result = await _executor(handler).execute(
        _step(url="https://example.com/x"), _ctx()
    )
    assert result.extras["body"] == "small"
    assert "truncated" not in result.extras["body"]
