"""``${NAME}`` in any TOML also resolves from ``NAME_FILE`` — Docker / Swarm / Kubernetes secrets
are mounted as files, not environment variables."""

from __future__ import annotations

from pathlib import Path

from liberty.config import substitute_env


def test_file_backed_secret(tmp_path: Path) -> None:
    f = tmp_path / "master_key"
    f.write_text("s3cret\n")
    assert substitute_env("${LIBERTY_MASTER_KEY}", env={"LIBERTY_MASTER_KEY_FILE": str(f)}) == "s3cret"
    assert substitute_env({"k": ["${LIBERTY_MASTER_KEY}"]}, env={"LIBERTY_MASTER_KEY_FILE": str(f)}) == {"k": ["s3cret"]}


def test_env_var_wins_over_file(tmp_path: Path) -> None:
    f = tmp_path / "k"
    f.write_text("from-file")
    assert substitute_env("${K}", env={"K": "from-env", "K_FILE": str(f)}) == "from-env"


def test_missing_file_falls_back_to_default(tmp_path: Path) -> None:
    assert substitute_env("${K:-dflt}", env={"K_FILE": str(tmp_path / "nope")}) == "dflt"
    assert substitute_env("${K}", env={"K_FILE": str(tmp_path / "nope")}) == ""
