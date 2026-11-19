"""The Claude Desktop extension (``liberty.mcpb``) — the MCP endpoint for a server that is not
on the internet.

A custom connector in Claude is called from Anthropic's servers: it needs a public address (and
is where the OAuth sign-in of :mod:`liberty.auth.oauth` applies). An extension runs on the
user's machine instead: Claude Desktop starts the small bridge of ``mcpb/server/index.js``,
which posts each message to ``/mcp`` with the user's personal access token. The server then only
has to be reachable from that machine — an internal address, a VPN.

The file is a zip built at download: the manifest with this server's address and version filled
in, the bridge (Node's own modules, nothing to install) and the icon. The user opens it, pastes
a token created in their profile, and that is all.
"""

from __future__ import annotations

import io
import re
import zipfile
from importlib import resources

FILE_NAME = "liberty.mcpb"
_ADDRESS = re.compile(r"https?://[^\s\"\\]+")


def _resource(name: str) -> bytes:
    return resources.files("liberty.ai").joinpath("mcpb", *name.split("/")).read_bytes()


def _version(release: str | None) -> str:
    """*release* as the ``X.Y.Z`` an extension manifest wants (a development build has none)."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", release or "")
    return ".".join(str(int(g)) for g in m.groups()) if m else "1.0.0"


def build_extension(address: str | None, release: str | None) -> bytes:
    """The extension for this server: *address* is where the user's machine reaches ``/mcp``
    (pre-filled in the extension's settings; anything that is not an http(s) address is left
    blank for the user to type)."""
    url = (address or "").strip()
    if not _ADDRESS.fullmatch(url):
        url = ""
    manifest = _resource("manifest.json").decode("utf-8").replace("{{version}}", _version(release)).replace("{{url}}", url)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr("server/index.js", _resource("server/index.js"))
        zf.writestr("icon.png", _resource("icon.png"))
    return out.getvalue()
