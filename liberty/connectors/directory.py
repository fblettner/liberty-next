"""An LDAP directory as a pool.

A pool is where an installation says how to reach something: a database, and — with a URL that
starts with ``ldap://`` or ``ldaps://`` — a directory. It is set up in the same place (Liberty →
Pools), its password is kept the same way (the pool's ``password``, ``ENC:`` or ``${ENV}``), and
whatever reads a directory names the pool instead of carrying a host, an account and a password of
its own::

    [pools.corporate_directory]
    url = "ldaps://cn=liberty,ou=services,dc=example,dc=com@ad.example.com:636/dc=example,dc=com"
    password = "ENC:…"

The account is the URL's user part (a bind DN, ``DOMAIN\\user`` or ``user%40domain`` — an ``@`` is
written ``%40``), what follows the ``/`` is the base the searches start from (optional). A directory
pool has no SQL engine: :meth:`PoolRegistry.engine` says so, :meth:`PoolRegistry.directory` gives
the :class:`DirectoryTarget` to bind with.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from sqlalchemy.engine import URL, make_url

DIRECTORY_SCHEMES = ("ldap", "ldaps")
_DEFAULT_PORT = {"ldap": 389, "ldaps": 636}


def is_directory_url(url: str) -> bool:
    """Whether *url* names an LDAP directory (``ldap://`` / ``ldaps://``) and not a database."""
    return url.strip().split("://", 1)[0].lower() in DIRECTORY_SCHEMES


@dataclass(frozen=True, slots=True)
class DirectoryTarget:
    """What it takes to bind to a directory, read from a pool."""

    host: str
    port: int
    use_ssl: bool
    user: str | None
    password: str | None
    base: str | None

    @property
    def url(self) -> str:
        """``ldap(s)://host:port`` — the server as ldap3 takes it, without the account."""
        return f"{'ldaps' if self.use_ssl else 'ldap'}://{self.host}:{self.port}"


def directory_target(url: URL | str) -> DirectoryTarget:
    """The :class:`DirectoryTarget` of a directory pool's URL — its password already resolved by
    the caller (the pool registry decrypts it the way it does for a database)."""
    u = make_url(url) if isinstance(url, str) else url
    scheme = u.get_backend_name().lower()
    if scheme not in DIRECTORY_SCHEMES:
        raise ValueError(f"not a directory URL: {scheme}://…")
    if not u.host:
        raise ValueError("a directory pool needs a host (ldap://host:389)")
    base = unquote(u.database) if u.database else None
    return DirectoryTarget(
        host=u.host, port=int(u.port or _DEFAULT_PORT[scheme]), use_ssl=scheme == "ldaps",
        user=u.username or None, password=u.password or None, base=base or None,
    )


def _bind(target: DirectoryTarget, timeout: float) -> str:
    # ldap3 is the optional 'ldap' extra: imported only when a directory is actually reached
    from ldap3 import ALL, Connection, Server

    server = Server(target.url, get_info=ALL, connect_timeout=timeout)
    conn = Connection(server, user=target.user, password=target.password, auto_bind=True, receive_timeout=timeout)
    try:
        info = server.info
        vendor = getattr(info, "vendor_name", None) if info is not None else None
        return (vendor[0] if isinstance(vendor, (list, tuple)) and vendor else str(vendor or "")).strip()
    finally:
        conn.unbind()


async def try_directory(target: DirectoryTarget, *, timeout: float = 10.0) -> dict[str, Any]:
    """Bind once to *target* and let go — the directory's side of a pool's *Test connection*.
    Never raises: ``{ok, dialect, duration_ms, version | error}``, like a database's test."""
    started = time.perf_counter()

    def _answer(**extra: Any) -> dict[str, Any]:
        return {"dialect": "ldap", "duration_ms": round((time.perf_counter() - started) * 1000, 1), **extra}

    try:
        version = await asyncio.wait_for(asyncio.to_thread(_bind, target, timeout), timeout + 2)
        return _answer(ok=True, version=version)
    except ImportError as exc:
        return _answer(ok=False, error=f"a directory pool needs the ldap3 client — install the 'ldap' extra: pip install 'liberty-next[ldap]' ({exc})")
    except asyncio.TimeoutError:
        return _answer(ok=False, error=f"no answer after {timeout:g} s")
    except Exception as exc:  # noqa: BLE001 — a test reports the failure, whatever it is
        return _answer(ok=False, error=(" ".join(str(exc).split()) or type(exc).__name__)[:600])
