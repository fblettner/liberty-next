"""The Claude Desktop extension (liberty.ai.mcp_extension): the file a user downloads to reach
the MCP endpoint from their own machine, and its bridge played against a server."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from liberty.ai.mcp_extension import build_extension
from tests.test_auth_oauth import _app, _login


def test_the_file_holds_the_manifest_the_bridge_and_the_icon() -> None:
    zf = zipfile.ZipFile(io.BytesIO(build_extension("https://liberty.intra:8443/app/mcp", "7.0.80")))
    assert sorted(zf.namelist()) == ["icon.png", "manifest.json", "server/index.js"]
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["version"] == "7.0.80" and manifest["user_config"]["server_url"]["default"] == "https://liberty.intra:8443/app/mcp"
    assert manifest["server"]["entry_point"] == "server/index.js" and manifest["user_config"]["token"]["sensitive"] is True
    assert set(manifest["server"]["mcp_config"]["env"]) == {"LIBERTY_URL", "LIBERTY_TOKEN", "LIBERTY_INSECURE"}
    assert zf.read("icon.png")[:4] == b"\x89PNG" and b"LIBERTY_TOKEN" in zf.read("server/index.js")
    # a development build has no release number; an address that is not one is left for the user to type
    for address in (None, "", "javascript:alert(1)", 'https://x" , "y', "ftp://x"):
        m = json.loads(zipfile.ZipFile(io.BytesIO(build_extension(address, "0.0.0+dev"))).read("manifest.json"))
        assert m["user_config"]["server_url"]["default"] == "" and m["version"] == "0.0.0", address
    assert json.loads(zipfile.ZipFile(io.BytesIO(build_extension(None, None))).read("manifest.json"))["version"] == "1.0.0"


def test_the_download_is_for_who_may_use_mcp(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        r = client.get("/auth/mcp/extension", params={"url": "http://liberty.local:8000/mcp"}, headers=_login(client, "bob", "bobpw-123"))
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        assert r.headers["content-disposition"] == 'attachment; filename="liberty.mcpb"'
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("manifest.json"))
        assert manifest["user_config"]["server_url"]["default"] == "http://liberty.local:8000/mcp"
        assert client.get("/auth/mcp/extension", headers=_login(client, "carol", "carolpw-123")).status_code == 403
        assert client.get("/auth/mcp/extension").status_code == 401
    (tmp_path / "off").mkdir()
    with TestClient(_app(tmp_path / "off", mcp_enabled=False)) as client:
        assert client.get("/auth/mcp/extension", headers=_login(client, "bob", "bobpw-123")).status_code == 404


class _Mcp(BaseHTTPRequestHandler):
    """A stand-in for /mcp: answers a request, accepts a notification, refuses a bad token."""

    seen: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "message": message})
        if self.headers.get("Authorization") != "Bearer good":
            self._send(401, {"detail": "Invalid access token: token expired"})
        elif "id" not in message:
            self._send(202, None)
        else:
            self._send(200, {"jsonrpc": "2.0", "id": message["id"], "result": {"echo": message["method"]}})

    def _send(self, status: int, body: dict | None) -> None:
        data = json.dumps(body, indent=1).encode() if body is not None else b""      # several lines: the bridge writes one
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


def _bridge(tmp_path, url: str, token: str, lines: list[dict]) -> tuple[list[dict], str]:
    zipfile.ZipFile(io.BytesIO(build_extension(url, "7.0.80"))).extractall(tmp_path / "ext")
    run = subprocess.run(
        [shutil.which("node"), str(tmp_path / "ext" / "server" / "index.js")],
        input="".join(json.dumps(m) + "\n" for m in lines), capture_output=True, text=True, timeout=30,
        env={"LIBERTY_URL": url, "LIBERTY_TOKEN": token, "LIBERTY_INSECURE": "false", "PATH": "/usr/bin:/bin"},
    )
    return [json.loads(line) for line in run.stdout.splitlines() if line.strip()], run.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="the bridge runs on Node")
def test_the_bridge_relays_each_message_with_the_token(tmp_path) -> None:
    server = HTTPServer(("127.0.0.1", 0), _Mcp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        _Mcp.seen = []
        out, _err = _bridge(tmp_path, f"{base}/app", "good", [                           # the address without /mcp: added
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        assert sorted(out, key=lambda m: m["id"]) == [
            {"jsonrpc": "2.0", "id": 1, "result": {"echo": "initialize"}}, {"jsonrpc": "2.0", "id": 2, "result": {"echo": "tools/list"}}]
        assert {s["path"] for s in _Mcp.seen} == {"/app/mcp"} and {s["auth"] for s in _Mcp.seen} == {"Bearer good"} and len(_Mcp.seen) == 3
        # a refused token: an error the user can act on, with the server's reason
        out, _err = _bridge(tmp_path, f"{base}/mcp", "Bearer bad", [{"jsonrpc": "2.0", "id": 7, "method": "tools/list"}])
        assert out[0]["id"] == 7 and "personal access token" in out[0]["error"]["message"] and "token expired" in out[0]["error"]["message"]
        # nothing set, a server that is not there
        out, _err = _bridge(tmp_path, f"{base}/mcp", "", [{"jsonrpc": "2.0", "id": 8, "method": "tools/list"}])
        assert "token is missing" in out[0]["error"]["message"]
    finally:
        server.shutdown()
        server.server_close()               # the port refuses from here on
    out, _err = _bridge(tmp_path, f"http://127.0.0.1:{server.server_port}/mcp", "good", [{"jsonrpc": "2.0", "id": 9, "method": "tools/list"}])
    assert "cannot be reached" in out[0]["error"]["message"]
