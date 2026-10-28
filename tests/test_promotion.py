"""Promote to another instance (C11 step 2): targets in app.toml, sign-in, the target's preview and
apply of a config package, the history — with the target a second app run in-process."""

from __future__ import annotations

import tomllib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_web_admin_ops import _h, _write, make_env


class _ClientTransport(httpx.AsyncBaseTransport):
    """Hands the source's outgoing calls to the target's TestClient (its own loop and lifespan)."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        r = self.client.request(request.method, path, content=request.read(), headers=dict(request.headers))
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)


@pytest.fixture
def pair(tmp_path: Path, monkeypatch):
    src_dir, tgt_dir = tmp_path / "src", tmp_path / "tgt"
    src_dir.mkdir()
    tgt_dir.mkdir()
    source, _ = make_env(src_dir)
    target, _ = make_env(tgt_dir)
    app_toml = src_dir / "app.toml"
    app_toml.write_text("# the source's app.toml\n[app]\nname = \"dev\"\n")
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(app_toml))
    with TestClient(target) as tc, TestClient(source) as sc:
        source.state.promotion_transport = _ClientTransport(tc)
        yield sc, tc, src_dir, tgt_dir, app_toml


def _save_target(sc: TestClient, h: dict[str, str], password: str = "adminpw") -> None:
    r = sc.put("/admin/promotion/targets", headers=h, json={"targets": {"prod": {
        "label": "Production", "url": "http://target", "username": "admin", "password": password}}})
    assert r.status_code == 200, r.text


def test_targets_are_saved_encrypted_and_tested(pair) -> None:
    sc, _, _, _, app_toml = pair
    h = _h(sc, "admin")
    _save_target(sc, h)
    text = app_toml.read_text()
    assert "# the source's app.toml" in text and "[promotion.targets.prod]" in text and "adminpw" not in text
    stored = tomllib.loads(text)["promotion"]["targets"]["prod"]
    assert stored["password"].startswith("ENC:")
    assert sc.get("/admin/promotion/targets", headers=h).json()["targets"]["prod"]["label"] == "Production"
    t = sc.post("/admin/promotion/targets/prod/test", headers=h)
    assert t.status_code == 200, t.text
    assert t.json()["ok"] is True and t.json()["version"]
    assert sc.post("/admin/promotion/targets/nope/test", headers=h).status_code == 404
    assert sc.put("/admin/promotion/targets", headers=h, json={"targets": {"Bad Name": {"url": "x"}}}).status_code == 422


def test_a_wrong_password_is_a_clear_502(pair) -> None:
    sc, *_ = pair
    h = _h(sc, "admin")
    _save_target(sc, h, password="wrong")
    r = sc.post("/admin/promotion/targets/prod/test", headers=h)
    assert r.status_code == 502 and "sign-in on http://target failed" in r.json()["detail"]


def test_push_previews_then_applies_a_config_package(pair) -> None:
    sc, tc, src_dir, tgt_dir, _ = pair
    h = _h(sc, "admin")
    _save_target(sc, h)
    # A change on the source the target doesn't have yet: the screen's label.
    screens = (src_dir / "screens.toml").read_text().replace('connector = "foo"', 'connector = "foo"\nlabel = "Users (v2)"', 1)
    _write(src_dir / "screens.toml", screens)
    assert sc.post("/admin/reload", headers=h).status_code == 200
    body = {"target": "prod", "payload": "config", "seeds": [{"kind": "screen", "name": "users", "scope": "foo"}]}

    prev = sc.post("/admin/promotion/push", headers=h, json=body)
    assert prev.status_code == 200, prev.text
    out = prev.json()
    assert out["applied"] is False and out["target"]["label"] == "Production" and "history_id" not in out
    changed = [e for f in out["report"]["files"] for e in f["entities"] if e["state"] == "changed"]
    assert [(e["kind"], e["name"]) for e in changed] == [("screen/foo", "users")]
    assert changed[0]["diff"] == [{"key": "label", "from": None, "to": "Users (v2)"}]
    assert "Users (v2)" not in (tgt_dir / "screens.toml").read_text()       # a preview writes nothing

    done = sc.post("/admin/promotion/push", headers=h, json={**body, "apply": True, "comment": "release 12"})
    assert done.status_code == 200, done.text
    assert done.json()["report"]["reloaded"] is True
    assert tomllib.loads((tgt_dir / "screens.toml").read_text())["screens"]["foo"]["users"]["label"] == "Users (v2)"

    hist = sc.get("/admin/promotion/history", headers=h).json()["entries"]
    assert len(hist) == 1 and hist[0]["target"] == "prod" and hist[0]["status"] == "applied" and hist[0]["comment"] == "release 12"
    assert "report" not in hist[0]
    full = sc.get(f"/admin/promotion/history/{hist[0]['id']}", headers=h).json()
    assert full["report"]["summary"]["changed"] == 1
    assert sc.get("/admin/promotion/history/../x", headers=h).status_code == 404


def test_push_refuses_an_unapproved_change_package(pair) -> None:
    sc, *_ = pair
    h = _h(sc, "admin")
    _save_target(sc, h)
    pkg = sc.post("/admin/changesets", headers=h, json={"application": "foo", "name": "p"}).json()
    r = sc.post("/admin/promotion/push", headers=h, json={"target": "prod", "payload": "changes", "package_id": pkg["id"]})
    assert r.status_code == 409 and "approved" in r.json()["detail"]


def test_promotion_routes_are_superuser_only(pair) -> None:
    sc, *_ = pair
    for method, path in (("get", "/admin/promotion/targets"), ("post", "/admin/promotion/push"), ("get", "/admin/promotion/history")):
        assert getattr(sc, method)(path).status_code == 401
        assert getattr(sc, method)(path, headers=_h(sc, "reader")).status_code == 403


def test_push_replays_an_approved_change_package(pair) -> None:
    import sqlite3

    from liberty.changesets import store
    sc, tc, _, tgt_dir, _ = pair
    h = _h(sc, "admin")
    _save_target(sc, h)
    db = sc.app.state.changesets_db
    # The target runs the captured write through its own connector: a harmless one here.
    with sqlite3.connect(tgt_dir / "default.db") as c:
        c.execute("CREATE TABLE promoted (x INTEGER)")
    with (tgt_dir / "connectors.toml").open("a") as f:
        f.write('\n[[connectors.foo.queries]]\nname = "promoted_put"\nsql = "INSERT INTO promoted (x) VALUES (1)"\nwritable = true\n')
    assert tc.post("/admin/reload", headers=_h(tc, "admin")).status_code == 200

    async def approved() -> str:
        await store.capture(db, "foo", connector="foo", query="promoted_put", operation="UPDATE",
                            entity="users", new_values={"USR_NAME": "bob"}, user="admin")
        async with db.session() as s:
            pkg = await store.get_active_package(s, "foo")
            await store.submit_package(s, pkg.id, user="admin")
            await store.approve_package(s, pkg.id, user="boss")
            return pkg.id

    pkg_id = sc.portal.call(approved)
    body = {"target": "prod", "payload": "changes", "package_id": pkg_id}
    prev = sc.post("/admin/promotion/push", headers=h, json=body)
    assert prev.status_code == 200, prev.text
    rep = prev.json()["report"]
    assert rep["dry_run"] is True and rep["summary"]["unverified"] == 1     # no read query: drift can't be checked
    done = sc.post("/admin/promotion/push", headers=h, json={**body, "apply": True, "force": [0]})
    assert done.status_code == 200, done.text
    assert done.json()["report"]["summary"].get("applied") == 1, done.json()["report"]
    with sqlite3.connect(tgt_dir / "default.db") as c:
        assert c.execute("SELECT count(*) FROM promoted").fetchone()[0] == 1
    status = sc.get(f"/admin/changesets/{pkg_id}", headers=h).json()["status"]
    assert status == "exported"
    hist = sc.get("/admin/promotion/history", headers=h).json()["entries"]
    assert hist[0]["payload"] == "changes" and hist[0]["status"] == "applied"
