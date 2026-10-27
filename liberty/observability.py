"""Request ids and structured logs — the small operations layer every log line rides on.

* :class:`RequestIdMiddleware` — pure ASGI: takes the caller's ``X-Request-ID`` (or mints a
  UUID4), exposes it on ``request.state.request_id`` / the :data:`request_id_var` contextvar
  for the duration of the request, and echoes it in the response header so a support ticket
  can quote one id that matches one log line.
* :class:`RequestIdFilter` — stamps ``record.request_id`` on every log record (``"-"`` outside a
  request); install it on the handlers the app owns.
* :class:`JsonFormatter` — one JSON object per line (``ts`` UTC ISO, ``level``, ``logger``,
  ``msg``, ``request_id``, ``exc`` when there is one, plus any ``extra={...}`` keys), for a log
  shipper. ``[app] log_format = "json"`` turns it on; ``"text"`` (the default) keeps the
  human-readable line. Timestamps are UTC either way, so lines from several hosts sort.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_var: ContextVar[str] = ContextVar("liberty_request_id", default="-")

REQUEST_ID_HEADER = "x-request-id"
_MAX_ID_LEN = 128  # a caller-supplied id is echoed back: cap it and keep it printable

# Attributes every LogRecord carries — anything else on the record came from ``extra=``.
_STD_ATTRS = frozenset(vars(logging.LogRecord("x", 0, "", 0, "", (), None)).keys()) | {
    "message", "asctime", "request_id",
}


def _clean_id(raw: str | None) -> str | None:
    if not raw:
        return None
    rid = raw.strip()
    if not rid or len(rid) > _MAX_ID_LEN or not rid.isprintable():
        return None
    return rid


class RequestIdMiddleware:
    """ASGI middleware (no Starlette ``BaseHTTPMiddleware`` — that one breaks streaming
    responses and the NDJSON / SSE routes stream)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        incoming = None
        for k, v in scope.get("headers") or ():
            if k == REQUEST_ID_HEADER.encode():
                incoming = _clean_id(v.decode("latin-1"))
                break
        rid = incoming or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid
        token = request_id_var.set(rid)

        async def send_with_id(message: dict) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", rid.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_var.get()),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in _STD_ATTRS and not k.startswith("_"):
                out[k] = v
        return json.dumps(out, default=str, ensure_ascii=False)


def make_formatter(fmt: str) -> logging.Formatter:
    """The stdout formatter for ``[app] log_format`` — ``"json"`` or the text line."""
    if fmt == "json":
        return JsonFormatter()
    f = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] — %(message)s", datefmt="%Y-%m-%dT%H:%M:%S",
    )
    f.converter = lambda *a: datetime.now(timezone.utc).timetuple()  # type: ignore[assignment]
    return f
