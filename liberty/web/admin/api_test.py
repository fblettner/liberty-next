"""``/admin`` — API connector test (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, router
from pydantic import BaseModel, ValidationError

from liberty.config import (
    substitute_env,
)

# ── API connector test ──────────────────────────────────────────────────────────────────────


class ApiTestBody(BaseModel):
    """Submitted by the ConnectorsBuilder's Test button — the operator's *in-progress* API
    connector definition (may not be saved yet). The endpoint builds a temporary
    :class:`APIConnector` from it and runs the smallest meaningful test:

    * ``oauth2`` — POST the token endpoint, return the resolved token (or the error).
    * ``basic`` / ``bearer`` / ``api_key`` — no separate verifier; if the operator gave
      ``test_endpoint``, run that. Otherwise just confirm the auth headers build cleanly.
    * ``none`` — nothing to test; succeed immediately.

    The connector is built in-memory, used once, then disposed; nothing touches disk.
    """

    config: dict[str, Any]                                       # one ApiConnectorConfig dict
    test_endpoint: str | None = None                             # optional: name of an endpoint to fire as a smoke call
    params: dict[str, Any] | None = None                         # placeholder values forwarded to the smoke call


@router.post("/config/api/test", summary="Test API connector")
async def test_api_connector(body: ApiTestBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Smoke-test the operator's API connector definition without saving. Two modes:

    * **OAuth2 token fetch** — the most useful test, since it confirms the token endpoint +
      auth_username/password + auth_token_body all work in concert. Returns the resolved
      token (truncated for display) or the error from the token endpoint.
    * **Endpoint smoke call** — if ``test_endpoint`` is provided AND it exists on the
      connector, fire it with no params and return the result. Useful for basic / bearer /
      api_key auth where the token fetch isn't a separate step.
    """
    from liberty.connectors.api import APIConnector
    from liberty.connectors.config import ApiConnectorConfig

    try:
        cfg = ApiConnectorConfig.model_validate(substitute_env(body.config))
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid api connector config: {exc}") from exc

    settings = request.app.state.settings
    conn = APIConnector(
        "__test__", cfg,
        master_key=settings.crypto.master_key,
    )
    try:
        if cfg.auth_type == "oauth2":
            # Force a fresh fetch — the cache is per-connector-instance + we just made it,
            # but force=True keeps the behaviour explicit (no surprise on re-runs).
            token = await conn._get_token({}, force=True)
            if token is None:
                return {
                    "kind": "oauth2_token",
                    "success": False,
                    "error": "token endpoint did not return a token (check auth_token_endpoint / auth_token_body / auth_token_field).",
                }
            # Truncate for display — the full token isn't useful in the UI and may be sensitive.
            return {
                "kind": "oauth2_token",
                "success": True,
                "token_preview": (token[:12] + "…") if len(token) > 16 else token,
                "ttl_seconds": cfg.auth_token_ttl,
            }
        # No separate OAuth2 step. If the operator named a test endpoint, fire it — forwarding
        # any ``params`` they supplied at the Test tab.
        if body.test_endpoint:
            try:
                result = await conn.call(body.test_endpoint, body.params or {})
            except Exception as exc:                              # noqa: BLE001 — surface any error
                return {"kind": "smoke_call", "success": False, "error": f"call failed: {exc}"}
            return {
                "kind": "smoke_call",
                "success": result.success,
                "status_code": result.status_code,
                "url": result.url,
                "duration_ms": result.duration_ms,
                "error": result.error,
                "warnings": result.warnings,
                "extracted": result.extracted,
                "mapped": result.mapped,
                "body": result.body[:4000] if isinstance(result.body, str) else result.body,
            }
        # Nothing to fire — auth headers build cleanly + the config validated; report so.
        return {
            "kind": "config_only",
            "success": True,
            "message": f"config is valid; pick an endpoint to fire a smoke call (auth_type={cfg.auth_type}).",
        }
    finally:
        await conn.aclose()


# ── Create an endpoint from what the server describes ───────────────────────────────────────


class EndpointGenerateBody(BaseModel):
    """One step of the endpoint editor's "Create from…" — see
    :class:`liberty.connectors.config.EndpointGenerator`."""

    connector: str                                               # a saved API connector
    generator: str                                               # one of its generators' ids
    step: str                                                    # search | options | build
    query: str | None = None                                     # search: the text typed
    candidate: str | None = None                                 # options / build: the candidate's id
    options: dict[str, Any] | None = None                        # build: the answers to the options


_GENERATOR_STEPS = ("search", "options", "build")


@router.post("/config/api/generate", summary="Create an endpoint from discovery")
async def generate_api_endpoint(body: EndpointGenerateBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Run one step of a connector's endpoint generator (a plugin function declared in the
    connector's ``generators``) against the **saved** connector — it calls the server, so it
    needs the stored credentials. Nothing is written: ``build`` answers with an endpoint
    definition the editor adds, unsaved, for the operator to review.

    * ``search`` → ``{"candidates": [{"id", "label", "description"}]}``
    * ``options`` → ``{"options": [{"name", "label", "kind": "text" | "choice" | "multi",
      "choices": [{"value", "label"}], "default", "help"}]}`` (may be empty)
    * ``build`` → ``{"endpoint": {…}}``, validated against the endpoint model
    """
    from liberty.connectors.base import ConnectorError
    from liberty.connectors.config import EndpointDef
    from liberty.jobs.discover import discover_callables
    from liberty.plugins.invoke import PluginInvocationError, build_kwargs, call_target, resolve_callable
    from liberty.web.plugins import _plugins_root

    if body.step not in _GENERATOR_STEPS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"step must be one of {', '.join(_GENERATOR_STEPS)}")
    connectors = request.app.state.connectors
    try:
        conn = connectors.api(body.connector)
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{body.connector}: not a saved API connector — save it first ({exc})") from exc
    generator = next((g for g in conn.config.generators if g.id == body.generator), None)
    if generator is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{body.connector} has no generator {body.generator!r} (saved)")
    # only a real plugin entry point: the module:function text cannot import anything else
    if generator.callable not in {c.callable for c in discover_callables(_plugins_root())}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown plugin callable: {generator.callable}")
    try:
        target = resolve_callable(generator.callable)
        kwargs = build_kwargs(
            target,
            {"connector": body.connector, "step": body.step, "query": body.query or "", "candidate": body.candidate or "",
             "options": dict(body.options or {})},
            injections=(("connectors", connectors), ("settings", request.app.state.settings)),
        )
        out = await call_target(target, kwargs)
    except PluginInvocationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — the generator's own error (the server refused, nothing found…) as a message
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{generator.label}: {exc}") from exc
    if not isinstance(out, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{generator.callable} must answer an object for step {body.step}")
    if body.step == "search":
        return {"candidates": [c for c in out.get("candidates") or [] if isinstance(c, dict) and c.get("id")]}
    if body.step == "options":
        return {"options": [o for o in out.get("options") or [] if isinstance(o, dict) and o.get("name")], "note": out.get("note")}
    try:
        endpoint = EndpointDef.model_validate(out.get("endpoint"))
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{generator.callable} built an invalid endpoint: {exc}") from exc
    # where it comes from, kept on the endpoint: "Refresh" asks the same thing again later
    from liberty.connectors.config import EndpointSource
    endpoint.source = EndpointSource(generator=body.generator, candidate=body.candidate or "", options=dict(body.options or {}))
    return {"endpoint": endpoint.model_dump(mode="json", exclude_defaults=True)}
