from __future__ import annotations

import json
import textwrap

from liberty.cli import main


def _write_config(tmp_path) -> str:
    cfg = tmp_path / "connectors.toml"
    cfg.write_text(
        textwrap.dedent(
            """
            [pools.default]
            url = "sqlite+aiosqlite:///:memory:"

            [connectors.demo]
            type = "sql"
            pool = "default"

            [[connectors.demo.queries]]
            name = "answer"
            sql = "SELECT 42 AS answer"

            [connectors.svc]
            type = "api"
            base_url = "https://example.test"
            auth_type = "bearer"
            auth_token = "secret"

            [[connectors.svc.endpoints]]
            name = "ping"
            path = "/ping"
            """
        )
    )
    return str(cfg)


def test_cli_list(tmp_path, capsys) -> None:
    rc = main(["--config", _write_config(tmp_path), "list"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pools"] == ["default"]
    names = {c["name"]: c for c in out["connectors"]}
    assert names["demo"]["type"] == "sql"
    assert names["demo"]["items"] == ["answer"]
    assert names["svc"]["type"] == "api"
    assert names["svc"]["items"] == ["ping"]


def test_cli_describe_hides_secrets(tmp_path, capsys) -> None:
    rc = main(["--config", _write_config(tmp_path), "describe", "svc"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "secret" not in out
    assert json.loads(out)["type"] == "api"


def test_cli_run_sql(tmp_path, capsys) -> None:
    rc = main(["--config", _write_config(tmp_path), "run", "demo", "answer"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == [{"answer": 42}]


def test_cli_run_unknown_connector(tmp_path) -> None:
    rc = main(["--config", _write_config(tmp_path), "run", "ghost", "x"])
    assert rc == 2
