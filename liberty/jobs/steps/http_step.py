"""``http`` step executor — fire an HTTP request, succeed on 2xx, fail otherwise.

The job-system equivalent of v1's HttpOperator: fire a one-shot HTTP call
(typically a webhook into Slack / Teams / Jira / a monitoring backend / a
downstream service) and let the rest of the pipeline depend on a 2xx response.

What it does (deliberately small surface):

* ``method`` — one of GET / POST / PUT / DELETE / PATCH / HEAD / OPTIONS. Case-
  insensitive on input; uppercased before the call. Defaults to ``GET``.
* ``url`` — full URL (no template expansion today; if an operator needs
  per-fire substitution they go through a ``python`` step that calls httpx
  directly with custom logic — the same way v1 did).
* ``headers`` — passed through verbatim. When ``body`` is a dict and no
  ``Content-Type`` is set, the executor adds ``application/json`` (matches
  the obvious default — anyone setting a dict body wants JSON).
* ``body`` — accepted as ``None`` / ``str`` / ``bytes`` / ``dict`` / ``list``:
  - ``None`` → no body
  - ``str`` / ``bytes`` → sent verbatim as the request body
  - ``dict`` / ``list`` → JSON-encoded
* ``timeout_seconds`` — passed through to httpx. The runner *also* wraps the
  whole step in :func:`asyncio.wait_for` with the same value, so timeouts
  surface as either an httpx TimeoutException (StepFailed) or an asyncio
  CancelledError (StepCancelled) depending on which trips first — both produce
  a sensible operator-facing error.

Success semantics: any 2xx response → :class:`StepResult` with ``rows_affected
= None`` (HTTP isn't row-shaped) and ``extras`` carrying the status code, the
response body (truncated to 4KB to keep the StepRun row compact), and the
elapsed seconds. Non-2xx → :class:`StepFailed` with the status code + a
short body excerpt so the operator can debug from the run page without
fishing through external logs.

Network-level failures (DNS, refused connection, TLS errors, timeouts) all
become :class:`StepFailed` with the underlying error type in the message —
the runner counts them against the retry policy like any other step failure,
which is the right behaviour for flaky webhook endpoints.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from liberty.jobs.schema import Step, StepType
from liberty.jobs.steps.base import RunContext, StepFailed, StepResult

_log = logging.getLogger(__name__)


# Max size we keep of the response body in StepResult.extras. The StepRun extras
# column lives in the DB long-term so trimming guards against an accidentally
# multi-MB response body (a webhook returning the dashboard HTML on error, say)
# bloating the runs table. 4 KB is enough for a JSON error payload + then some;
# operators chasing a bigger response should hit the endpoint directly.
_BODY_TRUNCATE_BYTES = 4096

_ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


class HttpStepExecutor:
    """Executes one ``http`` step. Stateless; a fresh :class:`httpx.AsyncClient`
    per call (one request, then close) — the per-call overhead is negligible
    against the network round-trip, and skipping a shared client means we
    don't have to manage its lifecycle alongside the executor.

    *transport* is the seam tests use to inject an :class:`httpx.MockTransport`
    without monkey-patching globals. Production paths pass nothing and httpx
    picks its default transport (a real network call)."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        if step.type is not StepType.HTTP:
            raise StepFailed(
                f"HttpStepExecutor received a step of type {step.type.value!r} — "
                "the runner wired the wrong executor for this step type"
            )
        if not step.url:
            # Schema validator catches this at parse time; defensive guard for
            # hand-built Steps from tests.
            raise StepFailed(f"http step {step.name!r}: missing required field 'url'")

        method = self._resolve_method(step)
        headers, content, json_body = self._build_payload(step)

        _log.info(
            "nomaflow.http start run=%s step=%r %s %s timeout=%ds",
            ctx.run_id, step.name, method, step.url, step.timeout_seconds,
        )

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=step.timeout_seconds, transport=self._transport) as client:
                response = await client.request(
                    method,
                    step.url,
                    headers=headers or None,
                    content=content,
                    json=json_body,
                )
        except httpx.TimeoutException as exc:
            raise StepFailed(
                f"http step {step.name!r}: {method} {step.url} timed out after "
                f"{step.timeout_seconds}s ({type(exc).__name__})"
            ) from exc
        except httpx.HTTPError as exc:
            # Catch-all for httpx-level failures: connect errors, DNS, TLS, etc.
            # Surface the exception class name so operators can grep for it in
            # the run log when triaging a flaky webhook.
            raise StepFailed(
                f"http step {step.name!r}: {method} {step.url} failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        elapsed = time.monotonic() - started

        body_excerpt = _truncate_body(response.text or "")

        if not (200 <= response.status_code < 300):
            raise StepFailed(
                f"http step {step.name!r}: {method} {step.url} → {response.status_code} "
                f"{response.reason_phrase or ''}: {body_excerpt or '(empty body)'}"
            )

        _log.info(
            "nomaflow.http done run=%s step=%r %s %s → %d in %.3fs",
            ctx.run_id, step.name, method, step.url, response.status_code, elapsed,
        )
        return StepResult(
            extras={
                "status": response.status_code,
                "elapsed_seconds": round(elapsed, 3),
                "body": body_excerpt,
            },
        )

    # -- internals ------------------------------------------------------- #

    def _resolve_method(self, step: Step) -> str:
        """Uppercase the configured method + validate against the closed set.
        Default is GET so a no-body smoke-test webhook works with just a URL."""
        method = (step.method or "GET").strip().upper()
        if method not in _ALLOWED_METHODS:
            raise StepFailed(
                f"http step {step.name!r}: unsupported method {method!r} "
                f"(allowed: {', '.join(sorted(_ALLOWED_METHODS))})"
            )
        return method

    def _build_payload(self, step: Step) -> tuple[dict[str, str], Any, Any]:
        """Decide between ``content`` (raw bytes / str) and ``json`` (dict / list)
        based on the body's Python type, and seed a default JSON Content-Type when
        the operator's headers didn't set one. Returns ``(headers, content, json)``
        — exactly one of ``content`` / ``json`` is non-None (or both None for a
        body-less request)."""
        headers = dict(step.headers) if step.headers else {}
        body = step.body
        if body is None:
            return headers, None, None
        if isinstance(body, (dict, list)):
            # Seed the Content-Type unless the operator explicitly set one — case-
            # insensitive header lookup since "Content-Type" / "content-type" are
            # equivalent on the wire and operators write both.
            if not any(k.lower() == "content-type" for k in headers):
                headers["Content-Type"] = "application/json"
            return headers, None, body
        # str / bytes — pass through verbatim. httpx accepts both for `content`.
        return headers, body, None


def _truncate_body(body: str) -> str:
    """Truncate a response body to :data:`_BODY_TRUNCATE_BYTES` so a giant payload
    doesn't bloat the StepRun extras column. Appends an explicit marker when
    truncation happened so the operator knows there was more — the marker is
    bytes-accurate (truncation happens on the byte length, not character count,
    so multibyte UTF-8 doesn't push the column over the cap)."""
    encoded = body.encode("utf-8")
    if len(encoded) <= _BODY_TRUNCATE_BYTES:
        return body
    truncated = encoded[:_BODY_TRUNCATE_BYTES].decode("utf-8", errors="ignore")
    return f"{truncated}…[truncated, {len(encoded)} bytes total]"
