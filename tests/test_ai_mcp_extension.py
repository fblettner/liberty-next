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

from liberty.ai.mcp_extension import build_extension, instance_names
from tests.test_auth_oauth import _app, _login


def test_the_file_holds_the_manifest_the_bridge_and_the_icon() -> None:
    zf = zipfile.ZipFile(io.BytesIO(build_extension("https://liberty.intra:8443/app/mcp", "7.0.80")))
    assert sorted(zf.namelist()) == ["icon.png", "manifest.json", "server/index.js"]
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["version"] == "7.0.80" and manifest["user_config"]["server_url"]["default"] == "https://liberty.intra:8443/app/mcp"
    assert manifest["name"] == "liberty-next-liberty-intra-8443" and manifest["display_name"] == "Liberty Next – liberty.intra:8443"
    assert manifest["server"]["entry_point"] == "server/index.js" and manifest["user_config"]["token"]["sensitive"] is True
    assert set(manifest["server"]["mcp_config"]["env"]) == {"LIBERTY_URL", "LIBERTY_TOKEN", "LIBERTY_INSECURE", "LIBERTY_FOLDER"}
    assert manifest["user_config"]["files_folder"]["type"] == "directory" and manifest["user_config"]["files_folder"]["required"] is False
    assert zf.read("icon.png")[:4] == b"\x89PNG" and b"LIBERTY_TOKEN" in zf.read("server/index.js")
    # a development build has no release number; an address that is not one is left for the user to type
    for address in (None, "", "javascript:alert(1)", 'https://x" , "y', "ftp://x"):
        m = json.loads(zipfile.ZipFile(io.BytesIO(build_extension(address, "0.0.0+dev"))).read("manifest.json"))
        assert m["user_config"]["server_url"]["default"] == "" and m["version"] == "0.0.0", address
    assert json.loads(zipfile.ZipFile(io.BytesIO(build_extension(None, None))).read("manifest.json"))["version"] == "1.0.0"


def test_each_instance_has_its_own_extension() -> None:
    """Claude Desktop tells extensions apart by name: named after its host and environment, the
    extension of a second server installs next to the first instead of replacing it."""
    def manifest(address, environment):
        return json.loads(zipfile.ZipFile(io.BytesIO(build_extension(address, "7.0.82", environment))).read("manifest.json"))

    prod = manifest("https://liberty.acme.com/mcp", "Production")
    test = manifest("https://liberty-test.acme.com/mcp", "Test")
    assert prod["name"] == "liberty-next-liberty-acme-com-production" and prod["display_name"] == "Liberty Next – Production · liberty.acme.com"
    assert test["name"] == "liberty-next-liberty-test-acme-com-test" and test["name"] != prod["name"]
    # the same host, two environments (a base path each): still two extensions
    assert manifest("https://host/a/mcp", "A")["name"] != manifest("https://host/b/mcp", "B")["name"]
    # an environment's odd characters never break the manifest
    odd = manifest("http://localhost:8000/mcp", 'Pré-prod "2" \\ x')
    assert odd["name"] == "liberty-next-localhost-8000-pr-prod-2-x" and odd["display_name"] == "Liberty Next – Pré-prod 2  x · localhost:8000"
    assert instance_names("http://localhost:8000/mcp", "Dev") == ("liberty-next-localhost-8000-dev", "Liberty Next – Dev · localhost:8000", "liberty-localhost-8000-dev.mcpb")
    # nothing known: the plain names
    assert instance_names(None, "") == ("liberty-next", "Liberty Next", "liberty.mcpb") and manifest(None, None)["name"] == "liberty-next"


def test_the_download_is_for_who_may_use_mcp(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        r = client.get("/auth/mcp/extension", params={"url": "http://liberty.local:8000/mcp"}, headers=_login(client, "bob", "bobpw-123"))
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        assert r.headers["content-disposition"] == 'attachment; filename="liberty-liberty-local-8000.mcpb"'
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
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path.split("?")[0].endswith("/files"):                 # a file sent by the extension
            type(self).seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "file": raw})
            self._send(200, {"job": "load-file", "file": "20260101-000000-items.csv", "size": len(raw)})
            return
        message = json.loads(raw)
        type(self).seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "message": message})
        if self.headers.get("Authorization") != "Bearer good":
            self._send(401, {"detail": "Invalid access token: token expired"})
        elif "id" not in message:
            self._send(202, None)
        elif message["method"] == "tools/list":
            self._send(200, {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [{"name": "job_run"}]}})
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


def _bridge(tmp_path, url: str, token: str, lines: list[dict], folder: str = "") -> tuple[list[dict], str]:
    zipfile.ZipFile(io.BytesIO(build_extension(url, "7.0.80"))).extractall(tmp_path / "ext")
    run = subprocess.run(
        [shutil.which("node"), str(tmp_path / "ext" / "server" / "index.js")],
        input="".join(json.dumps(m) + "\n" for m in lines), capture_output=True, text=True, timeout=30,
        env={"LIBERTY_URL": url, "LIBERTY_TOKEN": token, "LIBERTY_INSECURE": "false", "LIBERTY_FOLDER": folder, "PATH": "/usr/bin:/bin"},
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
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "describe_instance", "arguments": {}}},
        ])
        assert sorted(out, key=lambda m: m["id"]) == [
            {"jsonrpc": "2.0", "id": 1, "result": {"echo": "initialize"}}, {"jsonrpc": "2.0", "id": 2, "result": {"echo": "tools/call"}}]
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


@pytest.mark.skipif(shutil.which("node") is None, reason="the bridge runs on Node")
def test_the_bridge_sends_a_file_of_the_chosen_folder(tmp_path) -> None:
    """Only this program sees the user's disk: with a folder chosen in the extension's settings it
    adds two tools — the folder's files, and send one to Liberty for a job — and reads no other
    folder."""
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "items.csv").write_bytes(b"code,name\nA,Alpha\n")
    (folder / "notes.docx").write_bytes(b"not a file a job takes")
    (tmp_path / "secret.csv").write_bytes(b"outside the folder")
    server = HTTPServer(("127.0.0.1", 0), _Mcp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/mcp"
        call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}   # noqa: E731
        _Mcp.seen = []
        out, _err = _bridge(tmp_path, url, "good", [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            call(2, "local_files", {}),
            call(3, "send_file", {"job": "load-file", "file": "items.csv"}),
            call(4, "send_file", {"job": "load-file", "file": "../secret.csv"}),
            call(5, "send_file", {"job": "load-file", "file": "notes.docx"}),
            call(6, "send_file", {"file": "items.csv"}),
        ], folder=str(folder))
        by = {m["id"]: m["result"] for m in out}
        assert [t["name"] for t in by[1]["tools"]] == ["job_run", "local_files", "send_file"]          # the server's, then the two local ones
        listed = json.loads(by[2]["content"][0]["text"])
        assert [f["name"] for f in listed["files"]] == ["items.csv"]                                    # what a job takes, of this folder only
        assert by[3]["isError"] is False and json.loads(by[3]["content"][0]["text"])["file"].endswith("items.csv")
        sent = [x for x in _Mcp.seen if "file" in x]
        assert len(sent) == 1 and sent[0]["file"] == b"code,name\nA,Alpha\n" and sent[0]["auth"] == "Bearer good"
        assert sent[0]["path"] == "/mcp/files?job=load-file&filename=items.csv"
        assert by[4]["isError"] is True and by[5]["isError"] is True and by[6]["isError"] is True      # outside, not a kind taken, no job
        # no folder chosen: the two tools are not offered, and say why when called
        out, _err = _bridge(tmp_path, url, "good", [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, call(2, "local_files", {})])
        by = {m["id"]: m["result"] for m in out}
        assert [t["name"] for t in by[1]["tools"]] == ["job_run"] and by[2]["isError"] is True and "No files folder" in by[2]["content"][0]["text"]
    finally:
        server.shutdown()
        server.server_close()


def test_the_settings_are_in_french_for_a_french_profile() -> None:
    def manifest(language: str | None) -> dict:
        return json.loads(zipfile.ZipFile(io.BytesIO(build_extension("https://liberty.intra/mcp", "7.0.83", "Test", language))).read("manifest.json"))

    fr, en = manifest("fr-FR"), manifest("en")
    assert fr["user_config"]["server_url"]["title"] == "Adresse de Liberty" and en["user_config"]["server_url"]["title"] == "Liberty address"
    assert fr["user_config"]["files_folder"]["description"].startswith("Facultatif.") and fr["description"].startswith("Utilisez")
    # only the texts change: same name, same settings
    assert fr["name"] == en["name"] and set(fr["user_config"]) == set(en["user_config"])
    assert all(fr["user_config"][k]["title"] != en["user_config"][k]["title"] for k in en["user_config"])
