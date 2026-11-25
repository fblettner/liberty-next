"""A directory pool's *Test connection*: what is handed to the LDAP client."""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from liberty.connectors.directory import directory_target, try_directory


@pytest.mark.asyncio
async def test_timeouts_are_whole_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """ldap3 packs the read timeout as integers: ``10.0`` made every test answer
    "required argument is not an integer" before the directory was even asked."""
    seen: dict[str, Any] = {}

    class Server:
        info = None

        def __init__(self, url: str, *, get_info: Any, connect_timeout: Any) -> None:
            seen.update(url=url, connect=connect_timeout)

    class Connection:
        def __init__(self, server: Server, *, user: Any, password: Any, auto_bind: bool, receive_timeout: Any) -> None:
            seen.update(user=user, receive=receive_timeout)

        def unbind(self) -> None:
            seen["unbound"] = True

    monkeypatch.setitem(sys.modules, "ldap3", types.SimpleNamespace(ALL="ALL", Server=Server, Connection=Connection))
    out = await try_directory(directory_target("ldap://cn=reader@directory.example.com:7001/dc=example"), timeout=2.5)
    assert out["ok"] and out["dialect"] == "ldap"
    assert seen["url"] == "ldap://directory.example.com:7001" and seen["user"] == "cn=reader" and seen["unbound"]
    assert (seen["connect"], seen["receive"]) == (3, 3) and all(type(seen[k]) is int for k in ("connect", "receive"))
