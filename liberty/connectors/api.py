"""APIConnector — generic async HTTP client driven by endpoint definitions.

Ported from nomaubl's ``ApiConnectorClient``, with the JSON hand-parsing replaced
by Python's ``json`` module. Features:

* pluggable auth — ``none`` / ``basic`` / ``bearer`` / ``api_key`` / ``oauth2``
  (OAuth2 POSTs to a token endpoint, extracts the token by dot-path, caches it
  with a TTL, and refreshes once on a ``401``);
* ``{{placeholder}}`` substitution in the path, query params, headers and body —
  built-ins ``{{username}}`` / ``{{password}}`` / ``{{token}}`` plus per-endpoint
  declared params and any runtime overrides. A value stays where its placeholder is: it
  cannot leave the endpoint's path (:func:`substitute_path`), the JSON string it is
  written in (:func:`substitute_json`), its form pair (:func:`substitute_form`) nor, for
  a user's values, the folder of a file part (:func:`file_part_path`) — the permission
  on an endpoint is on that call;
* response extraction by dot-path (``data.0.id`` indexes list element 0) for a
  single ``response_field`` and/or a named ``response_map``;
* ``multipart/form-data`` bodies via a line-based parts list (``name=value`` for
  text parts, ``name=@/path;filename=X;contentType=Y`` to stream a file);
* one server per environment: ``targets`` keyed by SQL pool name, picked by
  :meth:`APIConnector.for_pool` from the pool the request (``X-Liberty-Pool``) or the
  job step runs for — :data:`current_pool`.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, unquote

import httpx

from liberty.connectors.base import ApiTargetMissingError, EndpointNotFoundError
from liberty.connectors.config import ApiConnectorConfig, ApiTargetConfig, EndpointDef
from liberty.crypto import decrypt_or_keep

_log = logging.getLogger("liberty.connectors")

# The SQL pool (environment) this request / job step runs for: the ``X-Liberty-Pool`` header,
# set by the auth dependency, or a job step's ``pool``, set by the runner. ConnectorRegistry.api()
# hands out the matching target of an API connector — so an action, a plugin or a step calling
# AIS reaches the environment whose data is on screen without naming it.
current_pool: ContextVar[str | None] = ContextVar("liberty_current_pool", default=None)


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
    warnings: str | None = None        # what the server answered at the endpoint's warning_fields: said, the call stands
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
            "warnings": self.warnings,
            "duration_ms": round(self.duration_ms, 3),
        }


class ApiParamError(ValueError):
    """A parameter value that would take the call somewhere else than its endpoint says."""


class ApiAuthError(RuntimeError):
    """The call cannot be signed: no user to make it as, or no key to sign with. Said to the caller
    as the call's error — nothing is sent."""


_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")


def _fill(text: str | None, params: dict[str, Any], render: Any) -> str:
    """Replace every ``{{key}}`` of *text* whose key is in *params* by ``render(key, value, at)``,
    in ONE pass: a value is never read again for placeholders (a value holding ``{{token}}``
    stays that text). An unknown key is left as written."""
    if not text:
        return text or ""

    def one(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in params:
            return m.group(0)
        value = params[key]
        return render(key, "" if value is None else str(value), m.start())

    return _PLACEHOLDER.sub(one, text)


def substitute(text: str | None, params: dict[str, Any]) -> str:
    """Replace every ``{{key}}`` in *text* with ``str(params[key])`` (a key not in *params* is
    left as written), as plain text — for headers, query values and non-JSON bodies. A path
    goes through :func:`substitute_path`, a JSON body through :func:`substitute_json`."""
    return _fill(text, params, lambda _key, value, _at: value)


def substitute_path(text: str | None, params: dict[str, Any]) -> str:
    """:func:`substitute` for an endpoint's path. A value may add segments (``report/execute``)
    but may not leave the path the endpoint gives: a ``.`` / ``..`` segment — written plainly,
    percent-encoded or with a backslash — is refused, and ``?`` / ``#`` are encoded so a value
    cannot start a query string. Without this, the permission on one endpoint would open every
    path of the connector (``../other/service``)."""

    def render(key: str, value: str, _at: int) -> str:
        plain = value
        for _ in range(3):                      # %252e%252e → %2e%2e → ..
            decoded = unquote(plain)
            if decoded == plain:
                break
            plain = decoded
        if _dot_segment(plain):
            raise ApiParamError(f"parameter {key!r}: a path value cannot hold a '.' or '..' segment")
        return value.replace("?", "%3F").replace("#", "%23")

    out = _fill(text, params, render)
    template = (text or "").lstrip().lower()
    if out.lstrip().lower().startswith(("http://", "https://")) and not template.startswith(("http://", "https://")):
        raise ApiParamError("a parameter cannot give the address of the call: the endpoint's path decides it")
    return out


def _json_string_content(value: str) -> str:
    """*value* as the inside of a JSON string. A value that already is one (no bare quote, valid
    escapes — what a caller who escaped it sends) is kept; anything else is escaped. Either way
    it cannot close the string it is placed in."""
    try:
        json.loads(f'"{value}"')
        return value
    except ValueError:
        return json.dumps(value, ensure_ascii=False)[1:-1]


def _string_spans(text: str) -> list[tuple[int, int]]:
    """The ``(start, end)`` of every JSON string literal of *text* (quotes excluded)."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    i = 0
    while i < len(text):
        c = text[i]
        if start is not None and c == "\\":
            i += 2
            continue
        if c == '"':
            if start is None:
                start = i + 1
            else:
                spans.append((start, i))
                start = None
        i += 1
    return spans


def _http_error(status: int, parsed: Any, error_fields: list[str]) -> str:
    """``HTTP 500`` — with what the server says of it when its answer is JSON and the endpoint's
    ``error_fields`` name where (``errorMessage``): "HTTP 500 — There are no active users…"."""
    said = _errors_in(parsed, error_fields) if (parsed is not None and error_fields) else None
    return f"HTTP {status}" + (f" — {said}" if said else "")


def _transport_error(exc: Exception, url: str) -> str:
    """A call that never reached the server, in words an operator can act on: which server, and —
    when it could not even be connected to — that the address is the connector's to check (the
    driver's own text, "[Errno 8] nodename nor servname provided", names neither)."""
    said = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        target = httpx.URL(url)
        where = f"{target.scheme}://{target.host}" + (f":{target.port}" if target.port else "")
        return f"cannot connect to {where} — check the connector's base URL ({said})"
    return said


_ABSENT = "\x00absent\x00"       # stands for a variable the caller did not give, while a function's body is filled
# the endpoints an action is serving right now, outermost first — an action that calls its own endpoint is refused
_serving: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("liberty_endpoint_actions", default=())


def _absent_as_none(value: Any) -> Any:
    """A filled body, the marker taken out: a value that was exactly one absent variable becomes
    ``None``; inside a longer text the variable is blank, as it always was."""
    if isinstance(value, str):
        return None if value == _ABSENT else value.replace(_ABSENT, "")
    if isinstance(value, dict):
        return {(k.replace(_ABSENT, "") if isinstance(k, str) else k): _absent_as_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_absent_as_none(v) for v in value]
    return value


def substitute_json(text: str | None, params: dict[str, Any]) -> str:
    """:func:`substitute` for a JSON body. A placeholder written inside a string literal
    (``"name": "{{NAME}}"``) receives its value as string content — a quote in the value cannot
    end the string and add fields to the request. A placeholder outside quotes is the author's
    choice of raw JSON (``{{BODY}}``, a number) and is inserted as is."""
    if not text:
        return text or ""
    spans = _string_spans(text)

    def render(_key: str, value: str, at: int) -> str:
        return _json_string_content(value) if any(a <= at < b for a, b in spans) else value

    return _fill(text, params, render)


_FORM_SAFE = re.compile(r"(?:[A-Za-z0-9._~*+-]|%[0-9A-Fa-f]{2})*")


def substitute_form(text: str | None, params: dict[str, Any]) -> str:
    """:func:`substitute` for an ``application/x-www-form-urlencoded`` body. In a ``name=value``
    pair a value is form-encoded, so it cannot add a pair (``&admin=1``); one a caller already
    encoded is kept. A placeholder standing alone as a pair (``{{BODY}}``) is raw, the author's
    choice."""
    if not text:
        return text or ""

    def render(_key: str, value: str, _at: int) -> str:
        return value if _FORM_SAFE.fullmatch(value) else quote_plus(value)

    return "&".join(_fill(pair, params, render) if "=" in pair else substitute(pair, params) for pair in text.split("&"))


def _dot_segment(value: str) -> bool:
    return any(seg.strip() in (".", "..") for seg in value.replace("\\", "/").split("/"))


def file_part_path(template: str, params: dict[str, Any], *, untrusted: bool) -> Path:
    """The file a multipart part sends (``name=@<template>``). Server-side callers (a job, a
    plugin) may give any path. When the values come from a user (*untrusted*: the HTTP route, a
    screen action, the assistant, a replayed change), the endpoint must name the folder
    (``@/data/out/{{NAME}}``) and the value stays inside it — otherwise the permission on the
    endpoint would let its holder send any file of the server to that API."""
    rendered = substitute(template, params)
    path = Path(rendered)
    if not untrusted or "{{" not in template:
        return path
    literal = template.split("{{", 1)[0]
    folder = literal.rsplit("/", 1)[0] if "/" in literal else ""
    if not folder:
        raise ApiParamError("a file to send must be in a folder the endpoint names (@/folder/{{NAME}}), not one a parameter gives")
    for key, value in params.items():
        if "{{" + key + "}}" in template and value is not None and _dot_segment(str(value)):
            raise ApiParamError(f"parameter {key!r}: a file name cannot hold a '.' or '..' segment")
    root = Path(folder).resolve()
    if not path.resolve().is_relative_to(root):
        raise ApiParamError("the file to send is outside the folder the endpoint names")
    return path


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
        # ``user_jwt``: the key that signs a token per user (read when first needed), the tokens signed
        self._jwt_key_source = self._maybe_decrypt(config.auth_jwt_key or "", master_key, "auth_jwt_key")
        self._jwt_key: Any = None
        self._jwt_x5t: str | None = None
        self._user_tokens: dict[str, tuple[str, float]] = {}
        # Per-environment servers: one connector each (its own client and token), same endpoints.
        # ``governed_pools`` = the pools a call is refused for when they have no target — the
        # registry widens it to the pools of the SQL connectors running on ``config.pool``.
        self.target_pool: str | None = config.pool
        # the registry this connector belongs to (set by it): what an endpoint served by a plugin
        # function receives as ``connectors``
        self.registry: Any = None
        # why the server pool this connector names could not be read (set by the registry): said as
        # the error of every call that would go to the server
        self.server_problem: str | None = None
        self._targets: dict[str, APIConnector] = {
            pool: APIConnector._for_target(name, config, pool, target, client=client, master_key=master_key)
            for pool, target in config.targets.items()
        }
        self.governed_pools: frozenset[str] = frozenset([*config.targets, *([config.pool] if config.pool else [])])

    @staticmethod
    def _for_target(name: str, config: ApiConnectorConfig, pool: str, target: ApiTargetConfig, *,
                    client: httpx.AsyncClient | None, master_key: str) -> APIConnector:
        overrides = {k: v for k, v in target.model_dump().items() if v not in (None, "")}
        return APIConnector(name, config.model_copy(update={**overrides, "pool": pool, "targets": {}}),
                            client=client, master_key=master_key)

    def for_pool(self, pool: str | None) -> APIConnector:
        """The connector to call for *pool*: itself without targets, for no pool, for its own
        pool or a pool outside its environments; the pool's target when it has one. A pool of its
        environments without a target raises :class:`ApiTargetMissingError` — never the default
        server, which would be another environment (often production)."""
        if not self._targets or not pool or pool == self.config.pool:
            return self
        target = self._targets.get(pool)
        if target is not None:
            return target
        if pool in self.governed_pools:
            raise ApiTargetMissingError(
                f"API connector {self.name!r} has no target for pool {pool!r} — add one under "
                f"[connectors.{self.name}.targets.{pool}] (its server is not {self.config.pool!r}'s)."
            )
        return self

    def _maybe_decrypt(self, value: str, master_key: str, field_name: str) -> str:
        plain, err = decrypt_or_keep(value, master_key)
        if err:
            _log.warning("connector %s: could not decrypt %s — %s", self.name, field_name, err)
        return plain

    async def aclose(self) -> None:
        for target in self._targets.values():
            await target.aclose()
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
            "show_in_switcher": self.config.show_in_switcher,
            "endpoints": [
                {
                    "name": e.name,
                    "label": e.label,
                    "description": e.description,
                    "group": e.group,
                    "method": e.method.upper(),
                    "writable": e.writable,
                    "path": e.path,
                    "content_type": e.content_type,
                    "skip_blank_query": True if e.skip_blank_query else None,
                    "rows_field": e.rows_field,
                    "callable": e.callable,
                    "action": e.action,
                    "limit_param": e.limit_param,
                    "error_fields": list(e.error_fields),
                    "warning_fields": list(e.warning_fields),
                    "response_field": e.response_field,
                    "response_map": e.response_map,
                    "params": [
                        {"name": p.name, "label": p.label, "default": p.default, "help": p.help, "example": p.example,
                         "choices": list(p.choices), "required": True if p.required else None, "lookup": self._param_lookup(p)}
                        for p in e.params
                    ],
                }
                for e in self._endpoints.values()
            ],
        }

    def _param_lookup(self, param: Any) -> dict[str, Any] | None:
        """The dictionary lookup a parameter names, as the browser reads it (the registry's dictionary)."""
        dictionary = getattr(getattr(self, "registry", None), "dictionary", None)
        return dictionary.param_lookup(param, self.name) if dictionary is not None else None

    # -- url / params helpers --------------------------------------------- #

    def _resolve_url(self, path_or_url: str) -> str:
        if not path_or_url:
            return self.config.base_url
        low = path_or_url.lower()
        if low.startswith("http://") or low.startswith("https://"):
            return path_or_url
        return self.config.base_url.rstrip("/") + "/" + path_or_url.lstrip("/")

    def _merge_params(self, endpoint: EndpointDef, params: dict[str, Any] | None) -> dict[str, Any]:
        # ``{{username}}`` / ``{{password}}`` are the connector's own credentials (a token request's
        # body) — unless the endpoint DECLARES a parameter of that name: then it is the endpoint's, and
        # left blank it is blank. (A user filter called ``username`` was sent as the connector's login,
        # and a ``password`` parameter left empty would have carried the connector's password.)
        declared = {p.name for p in endpoint.params}
        merged: dict[str, Any] = {k: v for k, v in (("username", self.auth_username), ("password", self.auth_password)) if k not in declared}
        if params:
            merged.update(params)
        for p in endpoint.params:
            value = merged.get(p.name)
            if value is None or value == "":
                # left out, or sent blank (a cleared field): the default — never the text "{{name}}"
                value = merged[p.name] = p.default if p.default is not None else ""
                if p.required and value == "":
                    raise ApiParamError(f"parameter {p.name!r} ({p.label or p.name}) is required")
            if p.choices and value != "":
                # one of the declared values, whatever the letter case it was typed in
                known = {c.lower(): c for c in p.choices}
                if str(value).lower() not in known:
                    raise ApiParamError(f"parameter {p.name!r}: {str(value)!r} is not one of {', '.join(p.choices)}")
                merged[p.name] = known[str(value).lower()]
        return merged

    # -- auth -------------------------------------------------------------- #

    # ``user_jwt`` — the call is made as the signed-in user: a short-lived token that names him on the
    # server, signed with the connector's key (the server trusts its certificate). No password kept.

    def acting_user(self) -> str | None:
        """Who the call is made as on the server: the signed-in user's ``auth_jwt_user_attribute``
        (or his Liberty name when the setting is blank), upper-cased when asked. ``None`` when
        nobody is signed in (a job) or the user has no such attribute."""
        from liberty.auth.principal import current_principal
        principal = current_principal.get()
        if principal is None:
            return None
        attribute = (self.config.auth_jwt_user_attribute or "").strip()
        raw = (principal.attributes or {}).get(attribute) if attribute else principal.username
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        name = str(raw or "").strip()
        if not name:
            return None
        return name.upper() if self.config.auth_jwt_uppercase else name

    def _signing_key(self) -> Any:
        """The private key, loaded once: the PEM text of the setting (its ``\\n`` taken as line
        ends: a one-line field keeps a key that way) or the PEM file it names."""
        if self._jwt_key is None:
            source = (self._jwt_key_source or "").strip()
            if not source:
                raise ApiAuthError(f"connector {self.name!r}: no key to sign the user's token with (auth_jwt_key)")
            if "-----BEGIN" in source:
                pem = source.replace("\\n", "\n")
            else:
                try:
                    pem = Path(source).expanduser().read_text(encoding="utf-8")
                except OSError as exc:
                    raise ApiAuthError(f"connector {self.name!r}: the key file of auth_jwt_key cannot be read ({type(exc).__name__})") from exc
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            try:
                self._jwt_key = load_pem_private_key(pem.encode("utf-8"), password=None)
            except (ValueError, TypeError) as exc:
                raise ApiAuthError(f"connector {self.name!r}: auth_jwt_key is not a private key in PEM format") from exc
        return self._jwt_key

    def _certificate_thumbprint(self) -> str | None:
        """``x5t`` — the SHA-1 fingerprint of the key's certificate (base64url), when one is given:
        what a server holding several trusted certificates may pick the right one by."""
        source = (self.config.auth_jwt_certificate or "").strip()
        if not source:
            return None
        if self._jwt_x5t is None:
            import hashlib

            from cryptography import x509
            from cryptography.hazmat.primitives.serialization import Encoding
            try:
                raw = source.replace("\\n", "\n").encode("utf-8") if "-----BEGIN" in source else Path(source).expanduser().read_bytes()
                cert = x509.load_pem_x509_certificate(raw) if b"-----BEGIN" in raw else x509.load_der_x509_certificate(raw)
            except (OSError, ValueError) as exc:
                raise ApiAuthError(f"connector {self.name!r}: auth_jwt_certificate cannot be read as a certificate ({type(exc).__name__})") from exc
            self._jwt_x5t = base64.urlsafe_b64encode(hashlib.sha1(cert.public_bytes(Encoding.DER)).digest()).rstrip(b"=").decode()  # noqa: S324 — x5t IS a SHA-1 thumbprint (RFC 7515)
        return self._jwt_x5t

    def user_token(self, user: str, *, force: bool = False, single_use: bool = False) -> str:
        """A signed token that names *user*, kept until shortly before it expires. Each token has
        its own id (``jti``). *single_use*: a token that may sign in once (``at_use_nbr`` = 1, as
        JD Edwards reads it for a browser session) — never kept, a new one each time."""
        import jwt as pyjwt
        now = time.time()
        kept = self._user_tokens.get(user)
        if kept and not force and not single_use and kept[1] - 30 > now:
            return kept[0]
        cfg = self.config
        claims: dict[str, Any] = {"jti": str(uuid.uuid4()), cfg.auth_jwt_claim or "sub": user, "iat": int(now), "exp": int(now) + cfg.auth_jwt_ttl}
        if single_use:
            claims["at_use_nbr"] = 1
        if cfg.auth_jwt_issuer:
            claims["iss"] = cfg.auth_jwt_issuer
        if cfg.auth_jwt_audience:
            claims["aud"] = cfg.auth_jwt_audience
        thumb = self._certificate_thumbprint()
        token = pyjwt.encode(claims, self._signing_key(), algorithm=cfg.auth_jwt_algorithm, headers={"x5t": thumb} if thumb else None)
        if single_use:
            return token
        if len(self._user_tokens) > 5000:                       # a long-lived process, many users: start again
            self._user_tokens.clear()
        self._user_tokens[user] = (token, float(claims["exp"]))
        return token

    async def _get_token(self, params: dict[str, Any], *, force: bool = False) -> str | None:
        auth = self.config.auth_type
        if auth == "user_jwt":
            user = self.acting_user()
            if user:
                return self.user_token(user, force=force)
            if self.config.auth_jwt_fallback == "service" and self.auth_username:
                return None                                     # the service account: Basic, in _auth_headers
            raise ApiAuthError(
                f"connector {self.name!r} makes each call as the signed-in user, and no user can be named: "
                + ("nobody is signed in (a job, a schedule)" if self._no_principal() else f"your account has no {self.config.auth_jwt_user_attribute!r} attribute")
                + " — ask an administrator")
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

    @staticmethod
    def _no_principal() -> bool:
        from liberty.auth.principal import current_principal
        return current_principal.get() is None

    def _auth_headers(self, token: str | None) -> dict[str, str]:
        auth = self.config.auth_type
        if auth == "user_jwt":
            if token:
                return {"Authorization": "Bearer " + token}
            auth = "basic"                                      # no user could be named and the fallback is the service account
        if auth == "basic":
            raw = f"{self.auth_username}:{self.auth_password}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        if auth in ("bearer", "oauth2") and token:
            return {"Authorization": "Bearer " + token}
        if auth == "api_key" and token:
            return {self.config.auth_api_key_header or "X-Api-Key": token}
        return {}

    def preview(self, endpoint_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """What :meth:`call` would send, without sending it: the method, the address and the
        parameters as they would be filled (defaults, allowed values). Raises
        :class:`ApiParamError` on a value :meth:`call` would refuse — a check before a card."""
        endpoint = self.get_endpoint(endpoint_name)
        merged = self._merge_params(endpoint, params)
        url = self._resolve_url(substitute_path(endpoint.path, merged))
        return {"method": endpoint.method.upper(), "url": url, "writable": endpoint.writable,
                "params": {p.name: merged.get(p.name) for p in endpoint.params}}

    # -- call -------------------------------------------------------------- #

    async def call(self, endpoint_name: str, params: dict[str, Any] | None = None, *, untrusted: bool = False) -> ApiResult:
        """Call *endpoint_name* with *params*. *untrusted* says the values come from a user (the
        HTTP route, a screen action, the assistant…) and not from server-side code: a file part
        may then only send a file of the folder the endpoint names (:func:`file_part_path`)."""
        endpoint = self.get_endpoint(endpoint_name)
        try:
            # checked before anything is sent (a token request included)
            merged = self._merge_params(endpoint, params)
            substitute_path(endpoint.path, merged)
        except ApiParamError as exc:
            return ApiResult(connector=self.name, endpoint=endpoint_name, success=False, status_code=0,
                             url=self.config.base_url, error=f"{type(exc).__name__}: {exc}")
        if endpoint.callable or endpoint.action:
            # what the caller did not give and no default fills: the function sees ``None`` there, not ""
            absent = {q.name for q in endpoint.params if q.name not in (params or {}) and merged.get(q.name) == ""}
            if endpoint.action:
                return await self._call_action(endpoint, merged, absent)
            return await self._call_plugin(endpoint, merged, absent)

        if self.server_problem:                                 # no server to call: nothing is sent
            return ApiResult(connector=self.name, endpoint=endpoint_name, success=False, status_code=0,
                             url=self.config.base_url, error=f"API connector {self.name!r}: {self.server_problem}")
        try:
            token = await self._get_token(merged)
        except ApiAuthError as exc:                             # nobody to make the call as: nothing is sent
            return ApiResult(connector=self.name, endpoint=endpoint_name, success=False, status_code=0,
                             url=self._resolve_url(substitute_path(endpoint.path, merged)), error=str(exc))
        if token is not None:
            merged["token"] = token

        url = self._resolve_url(substitute_path(endpoint.path, merged))
        query = {k: substitute(v, merged) for k, v in endpoint.query_params.items()}
        if endpoint.skip_blank_query:
            query = {k: v for k, v in query.items() if str(v).strip() != ""}     # an optional filter left blank is absent, not empty

        headers: dict[str, str] = {k: substitute(v, merged) for k, v in self.config.default_headers.items()}
        headers.update(self._auth_headers(token))
        for k, v in endpoint.headers.items():  # per-endpoint headers win
            headers[k] = substitute(v, merged)

        try:
            request_kwargs = self._build_body(endpoint, merged, headers, untrusted=untrusted)
        except (ApiParamError, OSError) as exc:       # a refused value, or a file part that cannot be read
            return ApiResult(connector=self.name, endpoint=endpoint_name, success=False, status_code=0,
                             url=url, error=f"{type(exc).__name__}: {exc}")

        started = time.perf_counter()
        try:
            resp = await self._client.request(
                endpoint.method.upper(), url, params=query or None, headers=headers, **request_kwargs
            )
            if resp.status_code == 401 and self.config.auth_type == "oauth2":      # (a signed user token is never the cause of a stale 401)
                new_token = await self._get_token(merged, force=True)
                if new_token:
                    merged["token"] = new_token
                    headers.update(self._auth_headers(new_token))
                    # File-stream bodies were consumed; rebuild before the retry.
                    request_kwargs = self._build_body(endpoint, merged, headers, untrusted=untrusted)
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
                error=_transport_error(exc, url),
                duration_ms=duration_ms,
            )
        duration_ms = (time.perf_counter() - started) * 1000.0

        body = resp.text
        try:
            parsed = resp.json()
        except ValueError:
            parsed = None
        ok = 200 <= resp.status_code < 300
        # a server that answers 200 with its errors inside (a form that refused the save): a failure
        refused = _errors_in(parsed, endpoint.error_fields) if (ok and parsed is not None) else None
        warned = _errors_in(parsed, endpoint.warning_fields) if parsed is not None else None
        extracted = extract_path(parsed, endpoint.response_field) if (ok and parsed is not None) else None
        mapped = (
            {name: extract_path(parsed, path) for name, path in endpoint.response_map.items()}
            if (ok and parsed is not None and endpoint.response_map)
            else {}
        )
        return ApiResult(
            connector=self.name,
            endpoint=endpoint_name,
            success=ok and refused is None,
            status_code=resp.status_code,
            url=str(resp.request.url),
            body=body,
            json=parsed,
            extracted=extracted,
            mapped=mapped,
            # an HTTP error keeps the server's own words when the endpoint says where they are
            error=refused if ok else _http_error(resp.status_code, parsed, endpoint.error_fields),
            warnings=warned,
            duration_ms=duration_ms,
        )

    async def _call_plugin(self, endpoint: EndpointDef, merged: dict[str, Any], absent: set[str] | None = None) -> ApiResult:
        """An endpoint served by a plugin function (``callable``): its arguments are the
        endpoint's parameters — or the object its ``body`` gives once filled — and its answer is
        treated as an HTTP answer's JSON (error / warning fields, response field and map).

        A value of the body that is exactly ONE variable the caller did not give (*absent*: not
        sent, no default) reaches the function as ``None`` — "not given" is not "blank": a
        function that types values in a form leaves alone the field nobody sent."""
        from liberty.plugins.invoke import (
            PluginInvocationError, build_kwargs, call_target, normalise_result, plugins_root, resolve_callable,
        )

        ref = endpoint.callable or ""
        url = f"plugin:{ref}"

        def failed(message: str, started: float | None = None) -> ApiResult:
            return ApiResult(connector=self.name, endpoint=endpoint.name, success=False, status_code=0, url=url, error=message,
                             duration_ms=(time.perf_counter() - started) * 1000.0 if started is not None else 0.0)

        try:
            kwargs = self._arguments(endpoint, merged, absent)
        except (ApiParamError, ValueError) as exc:
            return failed(f"{type(exc).__name__}: {exc}")
        # only a real plugin entry point: the module:function text cannot import anything else
        from liberty.jobs.discover import discover_callables
        if ref not in {c.callable for c in discover_callables(plugins_root())}:
            return failed(f"Unknown plugin callable: {ref}")
        started = time.perf_counter()
        try:
            target = resolve_callable(ref)
            raw = await call_target(target, build_kwargs(target, kwargs, injections=(
                ("connectors", self.registry), ("connector", self.name), ("settings", getattr(self.registry, "settings", None)))))
            answer = normalise_result(raw)
        except PluginInvocationError as exc:
            return failed(str(exc), started)
        except Exception as exc:  # noqa: BLE001 — the function's own failure (the server refused, nothing found…) is the endpoint's
            return failed(str(exc) or type(exc).__name__, started)
        parsed: Any = dict(answer.extras or {})
        if answer.rows_affected is not None:
            parsed.setdefault("rows_affected", answer.rows_affected)
        return self._answered(endpoint, parsed, url, started)

    @staticmethod
    def _arguments(endpoint: EndpointDef, merged: dict[str, Any], absent: set[str] | None) -> dict[str, Any]:
        """What an endpoint served by a function or an action hands it: its parameters that hold
        something — or the JSON object its ``body`` gives once filled (fixed values and variables
        mixed; a variable nobody gave is ``None``). Raises ValueError / ApiParamError."""
        if not endpoint.body:
            return {p.name: merged.get(p.name) for p in endpoint.params if merged.get(p.name) not in (None, "")}
        marked = {**merged, **{name: _ABSENT for name in absent or ()}}
        kwargs = _absent_as_none(json.loads(substitute_json(endpoint.body, marked)))
        if not isinstance(kwargs, dict):
            raise ValueError(f"{endpoint.name}: the body of an endpoint served by a function or an action must be a JSON object (its arguments)")
        return kwargs

    def _answered(self, endpoint: EndpointDef, parsed: Any, url: str, started: float) -> ApiResult:
        """The answer of a function or an action, treated as an HTTP answer's JSON."""
        parsed = json.loads(json.dumps(parsed, default=str))
        refused = _errors_in(parsed, endpoint.error_fields)
        return ApiResult(
            connector=self.name, endpoint=endpoint.name, success=refused is None, status_code=200, url=url,
            body=json.dumps(parsed), json=parsed, extracted=extract_path(parsed, endpoint.response_field) if refused is None else None,
            mapped={name: extract_path(parsed, path) for name, path in endpoint.response_map.items()} if refused is None and endpoint.response_map else {},
            error=refused, warnings=_errors_in(parsed, endpoint.warning_fields), duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    async def _call_action(self, endpoint: EndpointDef, merged: dict[str, Any], absent: set[str] | None = None) -> ApiResult:
        """An endpoint served by a shared action (``action``): the steps of ``actions.toml`` run on
        the server (:mod:`liberty.screens.actions`) with the endpoint's arguments as ``INPUT``;
        what they ``return`` is the answer.

        **Whose permissions.** The endpoint's: whoever may call it (``api:<connector>:<endpoint>``,
        checked by the route before this) runs its steps — they are not checked again against the
        caller, as a step of an orchestration is not. The caller stays the caller for everything
        else: the row-level data scope of the signed-in user still narrows every query, the name
        on a change package or a notification is theirs. Without a signed-in user (a job), the
        steps run unrestricted, as a job's do."""
        import dataclasses

        from liberty.auth.principal import Principal, current_principal
        from liberty.screens.actions import ActionInputError, run_shared_action

        ref = endpoint.action or ""
        url = f"action:{ref}"

        def failed(message: str, started: float | None = None) -> ApiResult:
            return ApiResult(connector=self.name, endpoint=endpoint.name, success=False, status_code=0, url=url, error=message,
                             duration_ms=(time.perf_counter() - started) * 1000.0 if started is not None else 0.0)

        state = getattr(self.registry, "state", None)
        key = (self.name, endpoint.name)
        serving = _serving.get()
        if key in serving:
            return failed(f"{endpoint.name}: the action '{ref}' calls the endpoint it serves — "
                          + " → ".join(f"{c}.{e}" for c, e in (*serving, key)))
        try:
            given = self._arguments(endpoint, merged, absent)
        except (ApiParamError, ValueError) as exc:
            return failed(f"{type(exc).__name__}: {exc}")
        given = {k: v for k, v in given.items() if v is not None}

        caller = current_principal.get()
        runner = (dataclasses.replace(caller, permissions=("*",)) if caller is not None
                  else Principal(id="system", username="system", is_superuser=True))
        started = time.perf_counter()
        token = _serving.set((*serving, key))
        try:
            result = await run_shared_action(state, self.registry, ref, given, runner, default_connector=self.name,
                                             origin=f"endpoint {self.name}.{endpoint.name}")
        except KeyError:
            return failed(f"{endpoint.name}: the shared action '{ref}' is not defined (actions.toml)")
        except ActionInputError as exc:
            return failed(str(exc), started)
        except Exception as exc:  # noqa: BLE001 — a step's failure outside the chain's own handling is the endpoint's
            return failed(str(exc) or type(exc).__name__, started)
        finally:
            _serving.reset(token)
        if not result.ok:
            return failed(result.error or "the action failed", started)
        parsed: dict[str, Any] = dict(result.returned)
        if result.messages:
            parsed.setdefault("messages", result.messages)
        if result.errors:
            parsed.setdefault("errors", result.errors)
        return self._answered(endpoint, parsed, url, started)

    # -- body construction ------------------------------------------------- #

    def _build_body(
        self, endpoint: EndpointDef, params: dict[str, Any], headers: dict[str, str], *, untrusted: bool = False
    ) -> dict[str, Any]:
        # GET / HEAD carry no body. A DELETE does when the endpoint declares one: some servers name what to
        # delete in a JSON body (JDE's Server Manager clears a cache that way), and HTTP allows it.
        if endpoint.method.upper() in ("GET", "HEAD") or not endpoint.body:
            return {}
        content_type = (endpoint.content_type or "application/json").lower()
        if content_type.startswith("multipart/form-data"):
            data, files = _parse_multipart(endpoint.body, params, untrusted=untrusted)
            # Drop any explicit Content-Type so httpx sets the multipart boundary.
            for h in [h for h in headers if h.lower() == "content-type"]:
                headers.pop(h)
            return {"data": data, "files": files} if files else {"data": data}
        if content_type.startswith("application/x-www-form-urlencoded"):
            return {"content": substitute_form(endpoint.body, params)}
        # JSON (the default): a value placed inside a string cannot leave it
        rendered = substitute_json(endpoint.body, params) if "json" in content_type else substitute(endpoint.body, params)
        # application/json (default) and anything else: send verbatim.
        headers.setdefault("Content-Type", endpoint.content_type or "application/json")
        return {"content": rendered}


def _parse_multipart(
    template: str, params: dict[str, Any], *, untrusted: bool = False
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
            p = file_part_path(segments[0].strip(), params, untrusted=untrusted)
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
            files[name] = (filename or p.name, p.read_bytes(), part_ct)
        else:
            data[name] = substitute(rest, params)
    return data, files


_ERROR_KEYS = ("TITLE", "title", "message", "MESSAGE", "alphaDescription", "DESC", "desc", "description", "error", "detail", "text")


def _error_text(item: Any) -> str:
    """One error of a response, as a line: its title / message when it is an object, else itself."""
    if isinstance(item, dict):
        for key in _ERROR_KEYS:
            if item.get(key) not in (None, ""):
                where = item.get("CONTROLTITLE") or item.get("field") or item.get("ALIAS")
                return f"{item[key]}" + (f" ({where})" if where and str(where) not in str(item[key]) else "")
        return json.dumps(item, ensure_ascii=False, default=str)[:300]
    return str(item)


_FILTERED = re.compile(r"^(?P<path>.*)\[(?P<key>[^=\[\]]+)=(?P<value>[^\[\]]*)\]$")


def _select(parsed: Any, path: str) -> Any:
    """:func:`extract_path`, with an optional filter on a list's items at the end:
    ``result.errors[errorLevel=1]`` keeps the items whose ``errorLevel`` is 1 (compared as text) —
    a server that puts its errors and its warnings in one list, told apart by a field."""
    m = _FILTERED.match(path.strip())
    if not m:
        return extract_path(parsed, path.strip())
    items = extract_path(parsed, m["path"])
    if not isinstance(items, list):
        return None
    return [i for i in items if isinstance(i, dict) and str(i.get(m["key"].strip())) == m["value"].strip()]


def _errors_in(parsed: Any, paths: list[str]) -> str | None:
    """What a response carries at *paths* (an endpoint's ``error_fields`` or ``warning_fields``),
    as one text — ``None`` when every path is empty, absent, false or zero."""
    found: list[str] = []
    for path in paths:
        value = _select(parsed, path)
        if value in (None, "", [], {}, False, 0):
            continue
        for item in value if isinstance(value, list) else [value]:
            text = _error_text(item).strip()
            if text and text not in found:
                found.append(text)
    return "; ".join(found)[:2000] if found else None


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)
