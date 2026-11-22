"""An HTTP server as a pool.

A pool is where an installation says how to reach something: a database, an LDAP directory
(:mod:`liberty.connectors.directory`), and — with a URL that starts with ``http://`` or
``https://`` — the server an API connector calls. Everything that belongs to the installation
is there, in one place (Liberty → Pools), and nowhere on the connector::

    [pools.jde_ais]
    url = "https://JDE@ais.example.com:7077/jderest/v2"
    password = "ENC:…"            # the account's password — or the token, when the server takes one
    signing_key = "ENC:…"         # the key that signs a token per user (``user_jwt``), with its
    certificate = "/etc/liberty/ais.cer"
    verify_ssl = true

    [connectors.ais_connection]
    type = "api"
    server = "jde_ais"            # the address, the account, the secrets: the pool's
    auth_type = "basic"           # HOW it authenticates stays the connector's, with its endpoints

The connector as an application ships it then holds nothing of the installation: an upgrade
refreshes it whatever the address, and a credential is changed in one place. The address and
credential fields of a connector still work for a connector that names no server.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.engine import make_url

from liberty.connectors.config import ApiConnectorConfig, PoolConfig
from liberty.crypto import decrypt_or_keep

SERVER_SCHEMES = ("http", "https")


def is_server_url(url: str) -> bool:
    """Whether *url* names an HTTP server (``http://`` / ``https://``), not a database."""
    return url.strip().split("://", 1)[0].lower() in SERVER_SCHEMES


@dataclass(frozen=True, slots=True)
class ServerTarget:
    """What an API connector takes from a server pool. The secrets are as the pool holds them
    (``ENC:`` values included): the connector decrypts them, as it does its own."""

    base_url: str
    user: str | None
    secret: str | None          # the account's password, or the token
    signing_key: str | None
    certificate: str | None
    verify_ssl: bool


def server_target(cfg: PoolConfig) -> ServerTarget:
    """The :class:`ServerTarget` of a server pool: its URL without the account, and the rest."""
    if not cfg.url.strip():
        raise ValueError("it has no URL yet — set its address under Liberty → Pools (or the variable its URL refers to)")
    try:
        u = make_url(cfg.url)
    except Exception as exc:  # noqa: BLE001 — a URL that cannot be read is said, never raised at startup
        raise ValueError(f"its URL cannot be read ({type(exc).__name__})") from exc
    scheme = u.get_backend_name().lower()
    if scheme not in SERVER_SCHEMES:
        raise ValueError(f"it is not an HTTP server (its URL starts with {scheme}://, not http:// or https://)")
    if not u.host:
        raise ValueError("an HTTP server pool needs a host (https://host:port/path)")
    host = f"[{u.host}]" if ":" in u.host else u.host
    base = f"{scheme}://{host}{f':{u.port}' if u.port else ''}{f'/{u.database}' if u.database else ''}"
    return ServerTarget(
        base_url=base, user=u.username or None, secret=cfg.password if cfg.password is not None else (u.password or None),
        signing_key=cfg.signing_key or None, certificate=cfg.certificate or None, verify_ssl=cfg.verify_ssl,
    )


def with_servers(config: ApiConnectorConfig, pools: Mapping[str, PoolConfig]) -> tuple[ApiConnectorConfig, str | None]:
    """*config* as the connector runs it: where it (or one of its environments) names a
    ``server``, the address, the account, the secrets and the TLS check are the pool's. Returns
    the effective config and, when a server cannot be read, why — the connector is still built
    (the rest of the installation loads) and says it on each call."""
    problem: str | None = None

    def read(name: str, what: str) -> ServerTarget | None:
        nonlocal problem
        cfg = pools.get(name)
        try:
            if cfg is None:
                raise ValueError("there is no such pool — create it under Liberty → Pools (kind HTTP server)")
            return server_target(cfg)
        except ValueError as exc:
            problem = problem or f"{what} names the server pool {name!r}: {exc}"
            return None

    update: dict[str, Any] = {}
    if config.server:
        t = read(config.server, "the connector")
        if t is not None:
            update.update(base_url=t.base_url, auth_username=t.user, auth_password=t.secret, auth_token=t.secret,
                          auth_jwt_key=t.signing_key, auth_jwt_certificate=t.certificate, verify_ssl=t.verify_ssl)
    targets = dict(config.targets)
    for pool, target in config.targets.items():
        if target.server:
            t = read(target.server, f"its environment {pool!r}")
            if t is not None:
                targets[pool] = target.model_copy(update={"base_url": t.base_url, "auth_username": t.user, "auth_password": t.secret,
                                                          "auth_token": t.secret, "verify_ssl": t.verify_ssl})
    if targets != config.targets:
        update["targets"] = targets
    return (config.model_copy(update=update) if update else config), problem


async def try_server(target: ServerTarget, *, master_key: str = "", timeout: float = 10.0) -> dict[str, Any]:
    """One request to the server's address, with its account when it has one — the server's side
    of a pool's *Test connection*. It says the server answers (name, TLS, route): an HTTP status,
    whatever it is, is an answer; whether the account is accepted is the connector's own test,
    which knows how the server authenticates. Never raises: ``{ok, dialect, duration_ms,
    version | error}``."""
    started = time.perf_counter()

    def _answer(**extra: Any) -> dict[str, Any]:
        return {"dialect": "http", "duration_ms": round((time.perf_counter() - started) * 1000, 1), **extra}

    secret, _err = decrypt_or_keep(target.secret or "", master_key)
    auth = httpx.BasicAuth(target.user, secret) if target.user else None
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=target.verify_ssl, follow_redirects=True) as client:
            # the status is the answer: the body is not read (some servers send one that does not
            # decompress — JD Edwards AIS — which is the connector's business, not this test's)
            async with client.stream("GET", target.base_url, auth=auth, headers={"Accept-Encoding": "identity"}) as r:
                status = r.status_code
        return _answer(ok=True, version=f"HTTP {status}")
    except httpx.TimeoutException:
        return _answer(ok=False, error=f"no answer after {timeout:g} s")
    except Exception as exc:  # noqa: BLE001 — a test reports the failure, whatever it is
        return _answer(ok=False, error=(" ".join(str(exc).split()) or type(exc).__name__)[:600])
