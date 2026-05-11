"""APIConnector — generic async HTTP client driven by endpoint definitions.

Ported from nomaubl's ``ApiConnectorClient``, with the JSON hand-parsing replaced
by Python's ``json`` module. Features:

* pluggable auth — ``none`` / ``basic`` / ``bearer`` / ``api_key`` / ``oauth2``
  (OAuth2 POSTs to a token endpoint, extracts the token by dot-path, caches it
  with a TTL, and refreshes once on a ``401``);
* ``{{placeholder}}`` substitution in the path, query params, headers and body —
  built-ins ``{{username}}`` / ``{{password}}`` / ``{{token}}`` plus per-endpoint
  declared params and any runtime overrides;
* response extraction by dot-path (``data.0.id`` indexes list element 0) for a
  single ``response_field`` and/or a named ``response_map``;
* ``multipart/form-data`` bodies via a line-based parts list (``name=value`` for
  text parts, ``name=@/path;filename=X;contentType=Y`` to stream a file).
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from liberty.connectors.base import EndpointNotFoundError
from liberty.connectors.config import ApiConnectorConfig, EndpointDef
from liberty.crypto import decrypt_or_keep

_log = logging.getLogger("liberty.connectors")


@dataclass(slots=True)
class ApiResult:
    """Outcome of :meth:`APIConnector.call`."""

    connector: str
    endpoint: str
    success: bool
    status_code: int
    url: str
    body: str | None = None
    json: Any = None
    extracted: Any = None
    mapped: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector": self.connector,
            "endpoint": self.endpoint,
            "success": self.success,
            "status_code": self.status_code,
            "url": self.url,
            "json": self.json,
            "body": None if self.json is not None else self.body,
            "extracted": self.extracted,
            "mapped": self.mapped,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 3),
        }


def substitute(text: str | None, params: dict[str, Any]) -> str:
    """Replace every ``{{key}}`` in *text* with ``str(params[key])`` (missing → "")."""
    if not text:
        return text or ""
    out = text
    for key, value in params.items():
        token = "{{" + key + "}}"
        if token in out:
            out = out.replace(token, "" if value is None else str(value))
    return out


def extract_path(data: Any, path: str | None) -> Any:
    """Walk *data* by a dot-path. Numeric segments index into lists; missing → ``None``."""
    if not path:
        return None
    current = data
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


class APIConnector:
    """A connector exposing a fixed set of named HTTP endpoints under a ``base_url``."""

    def __init__(
        self,
        name: str,
        config: ApiConnectorConfig,
        *,
        client: httpx.AsyncClient | None = None,
        master_key: str = "",
    ) -> None:
        self.name = name
        self.config = config
        self._endpoints: dict[str, EndpointDef] = {e.name: e for e in config.endpoints}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=config.timeout, verify=config.verify_ssl, follow_redirects=True
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        # Auth secrets may be stored as v1 "ENC:" values — decrypt them now (best-effort:
        # a missing/wrong master key leaves the value as-is and logs a warning).
        self.auth_username = self._maybe_decrypt(config.auth_username or "", master_key, "auth_username")
        self.auth_password = self._maybe_decrypt(config.auth_password or "", master_key, "auth_password")
        self.auth_token = self._maybe_decrypt(config.auth_token or "", master_key, "auth_token")

    def _maybe_decrypt(self, value: str, master_key: str, field_name: str) -> str:
        plain, err = decrypt_or_keep(value, master_key)
        if err:
            _log.warning("connector %s: could not decrypt %s — %s", self.name, field_name, err)
        return plain

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- introspection ----------------------------------------------------- #

    @property
    def endpoint_names(self) -> list[str]:
        return list(self._endpoints)

    def get_endpoint(self, name: str) -> EndpointDef:
        try:
            return self._endpoints[name]
        except KeyError:
            raise EndpointNotFoundError(
                f"Connector {self.name!r} has no endpoint {name!r}. "
                f"Available: {self.endpoint_names or '(none)'}."
            ) from None

    def describe(self) -> dict[str, Any]:
        """Metadata only — credentials are never included."""
        return {
            "name": self.name,
            "type": "api",
            "base_url": self.config.base_url,
            "auth_type": self.config.auth_type,
            "endpoints": [
                {
                    "name": e.name,
                    "label": e.label,
                    "description": e.description,
                    "method": e.method.upper(),
                    "path": e.path,
                    "content_type": e.content_type,
                    "response_field": e.response_field,
                    "response_map": e.response_map,
                    "params": [
                        {"name": p.name, "label": p.label, "default": p.default}
                        for p in e.params
                    ],
                }
                for e in self._endpoints.values()
            ],
        }

    # -- url / params helpers --------------------------------------------- #

    def _resolve_url(self, path_or_url: str) -> str:
        if not path_or_url:
            return self.config.base_url
        low = path_or_url.lower()
        if low.startswith("http://") or low.startswith("https://"):
            return path_or_url
        return self.config.base_url.rstrip("/") + "/" + path_or_url.lstrip("/")

    def _merge_params(self, endpoint: EndpointDef, params: dict[str, Any] | None) -> dict[str, Any]:
        merged: dict[str, Any] = {"username": self.auth_username, "password": self.auth_password}
        for p in endpoint.params:
            if p.default is not None:
                merged[p.name] = p.default
        if params:
            merged.update(params)
        return merged

    # -- auth -------------------------------------------------------------- #

    async def _get_token(self, params: dict[str, Any], *, force: bool = False) -> str | None:
        auth = self.config.auth_type
        if auth in ("bearer", "api_key"):
            return self.auth_token or None
        if auth != "oauth2":
            return None
        if not force and self._token and time.monotonic() < self._token_expires_at:
            return self._token
        token = await self._fetch_oauth2_token(params)
        if token is not None:
            self._token = token
            self._token_expires_at = time.monotonic() + max(1, self.config.auth_token_ttl)
        return token

    async def _fetch_oauth2_token(self, params: dict[str, Any]) -> str | None:
        cfg = self.config
        if not cfg.auth_token_endpoint:
            return None
        url = self._resolve_url(substitute(cfg.auth_token_endpoint, params))
        content_type = cfg.auth_token_content_type or "application/json"
        is_form = "x-www-form-urlencoded" in content_type.lower()

        headers = {"Content-Type": content_type}
        for k, v in cfg.auth_token_headers.items():
            headers[k] = substitute(v, params)

        kwargs: dict[str, Any] = {"headers": headers}
        if cfg.auth_token_body:
            kwargs["content"] = substitute(cfg.auth_token_body, params)
        elif is_form:
            kwargs["data"] = {
                "grant_type": "client_credentials",
                "client_id": self.auth_username,
                "client_secret": self.auth_password,
            }
            headers.pop("Content-Type", None)  # let httpx set the form content-type
        else:
            kwargs["json"] = {"username": self.auth_username, "password": self.auth_password}

        resp = await self._client.post(url, **kwargs)
        if resp.status_code < 200 or resp.status_code >= 300:
            return None
        try:
            payload = resp.json()
        except ValueError:
            return None
        if cfg.auth_token_field:
            return _stringify(extract_path(payload, cfg.auth_token_field))
        if isinstance(payload, dict):
            for key in ("access_token", "token", "id_token"):
                if key in payload:
                    return _stringify(payload[key])
        return None

    def _auth_headers(self, token: str | None) -> dict[str, str]:
        auth = self.config.auth_type
        if auth == "basic":
            raw = f"{self.auth_username}:{self.auth_password}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        if auth in ("bearer", "oauth2") and token:
            return {"Authorization": "Bearer " + token}
        if auth == "api_key" and token:
            return {self.config.auth_api_key_header or "X-Api-Key": token}
        return {}

    # -- call -------------------------------------------------------------- #

    async def call(self, endpoint_name: str, params: dict[str, Any] | None = None) -> ApiResult:
        endpoint = self.get_endpoint(endpoint_name)
        merged = self._merge_params(endpoint, params)

        token = await self._get_token(merged)
        if token is not None:
            merged["token"] = token

        url = self._resolve_url(substitute(endpoint.path, merged))
        query = {k: substitute(v, merged) for k, v in endpoint.query_params.items()}

        headers: dict[str, str] = {k: substitute(v, merged) for k, v in self.config.default_headers.items()}
        headers.update(self._auth_headers(token))
        for k, v in endpoint.headers.items():  # per-endpoint headers win
            headers[k] = substitute(v, merged)

        request_kwargs = self._build_body(endpoint, merged, headers)

        started = time.perf_counter()
        try:
            resp = await self._client.request(
                endpoint.method.upper(), url, params=query or None, headers=headers, **request_kwargs
            )
            if resp.status_code == 401 and self.config.auth_type == "oauth2":
                new_token = await self._get_token(merged, force=True)
                if new_token:
                    merged["token"] = new_token
                    headers.update(self._auth_headers(new_token))
                    # File-stream bodies were consumed; rebuild before the retry.
                    request_kwargs = self._build_body(endpoint, merged, headers)
                    resp = await self._client.request(
                        endpoint.method.upper(), url, params=query or None, headers=headers, **request_kwargs
                    )
        except httpx.HTTPError as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            return ApiResult(
                connector=self.name,
                endpoint=endpoint_name,
                success=False,
                status_code=0,
                url=url,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )
        duration_ms = (time.perf_counter() - started) * 1000.0

        body = resp.text
        try:
            parsed = resp.json()
        except ValueError:
            parsed = None
        ok = 200 <= resp.status_code < 300
        extracted = extract_path(parsed, endpoint.response_field) if (ok and parsed is not None) else None
        mapped = (
            {name: extract_path(parsed, path) for name, path in endpoint.response_map.items()}
            if (ok and parsed is not None and endpoint.response_map)
            else {}
        )
        return ApiResult(
            connector=self.name,
            endpoint=endpoint_name,
            success=ok,
            status_code=resp.status_code,
            url=str(resp.request.url),
            body=body,
            json=parsed,
            extracted=extracted,
            mapped=mapped,
            error=None if ok else f"HTTP {resp.status_code}",
            duration_ms=duration_ms,
        )

    # -- body construction ------------------------------------------------- #

    def _build_body(
        self, endpoint: EndpointDef, params: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        if endpoint.method.upper() in ("GET", "HEAD", "DELETE") or not endpoint.body:
            return {}
        content_type = (endpoint.content_type or "application/json").lower()
        if content_type.startswith("multipart/form-data"):
            data, files = _parse_multipart(endpoint.body, params)
            # Drop any explicit Content-Type so httpx sets the multipart boundary.
            for h in [h for h in headers if h.lower() == "content-type"]:
                headers.pop(h)
            return {"data": data, "files": files} if files else {"data": data}
        rendered = substitute(endpoint.body, params)
        if content_type.startswith("application/x-www-form-urlencoded"):
            return {"content": rendered}
        # application/json (default) and anything else: send verbatim.
        headers.setdefault("Content-Type", endpoint.content_type or "application/json")
        return {"content": rendered}


def _parse_multipart(
    template: str, params: dict[str, Any]
) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
    """Parse the line-based parts list into ``(text_parts, file_parts)`` for httpx."""
    data: dict[str, str] = {}
    files: dict[str, tuple[str, bytes, str]] = {}
    for raw in template.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, _, rest = line.partition("=")
        name = name.strip()
        if rest.startswith("@"):
            segments = rest[1:].split(";")
            path = substitute(segments[0].strip(), params)
            filename: str | None = None
            part_ct = "application/octet-stream"
            for seg in segments[1:]:
                if "=" not in seg:
                    continue
                k, _, v = seg.partition("=")
                k, v = k.strip().lower(), substitute(v.strip(), params)
                if k == "filename":
                    filename = v
                elif k == "contenttype":
                    part_ct = v
            p = Path(path)
            files[name] = (filename or p.name, p.read_bytes(), part_ct)
        else:
            data[name] = substitute(rest, params)
    return data, files


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)
