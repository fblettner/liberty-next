"""release/install.sh: what the end of an install says about the admin account.

The summary used to decide from one fact — "this run generated the .env" — and so told an install
whose .env was already there (``./install.sh prepare`` first, a first run stopped early, volumes
removed by hand) that the password was "unchanged from the original install", while the container
had just created the account with a password of its own, said only in its log.

The script now asks the container's log (``liberty-admin init-db`` answers ``admin_created`` and,
when it chose the password, ``generated_password``). Its two helpers are run here as they are
written in the script.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "release" / "install.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _function(name: str) -> str:
    """The definition of *name* as the install script writes it."""
    found = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", SCRIPT.read_text(encoding="utf-8"), re.M | re.S)
    assert found, f"{name}() not found in {SCRIPT.name}"
    return found.group(0)


def _run(call: str, *args: str) -> str:
    source = "set -euo pipefail\n" + _function("admin_created") + _function("say_admin") + call + "\n"
    done = subprocess.run(["bash", "-c", source, "install", *args], capture_output=True, text=True, check=True)
    return done.stdout


def _boot_log(answer: dict) -> str:
    """A container's start as it is logged: the entrypoint's lines around what init-db prints."""
    return (
        "[entrypoint] bootstrapping auth + nomaflow schema (liberty-admin init-db)…\n"
        + json.dumps(answer, indent=2)                      # liberty.admin_cli._print
        + "\n[entrypoint] starting FastAPI + Socket.IO on 0.0.0.0:8000\n"
    )


BASE = {"backend": "db", "ready": True, "nomaflow_schema": "ready"}
USER = {"username": "admin", "is_superuser": True}


def admin_created(log: str) -> str:
    return _run('admin_created "$1"', log).strip()


def say_admin(created: str, password: str) -> str:
    return _run('say_admin "Sign in to Liberty" "$1" "$2"', created, password)


def test_an_account_created_with_a_password_of_its_own_gives_that_password():
    log = _boot_log({**BASE, "admin_created": True, "user": USER, "generated_password": "aB3_x-9ZkQ7mN2pLr5Tt"})
    assert admin_created(log) == "yes:aB3_x-9ZkQ7mN2pLr5Tt"


def test_an_account_created_with_the_password_handed_over_says_only_yes():
    assert admin_created(_boot_log({**BASE, "admin_created": True, "user": USER})) == "yes"


def test_an_account_already_there_is_said():
    assert admin_created(_boot_log({**BASE, "admin_created": False})) == "no"


def test_a_log_that_says_nothing_is_unknown():
    assert admin_created("") == "unknown"
    assert admin_created("[entrypoint] LIBERTY_SKIP_INITDB=1 — skipping bootstrap.\n") == "unknown"


def test_created_then_restarted_in_the_same_run_is_still_created():
    first = _boot_log({**BASE, "admin_created": True, "user": USER, "generated_password": "first-One_1"})
    again = _boot_log({**BASE, "admin_created": False})
    assert admin_created(first + again) == "yes:first-One_1"


def test_the_password_of_an_account_this_run_created_is_shown():
    said = say_admin("yes", "aB3_x-9ZkQ7mN2pLr5Tt")
    assert "username:  admin" in said
    assert "password:  aB3_x-9ZkQ7mN2pLr5Tt" in said


def test_a_password_is_never_shown_for_an_account_that_was_already_there():
    # a fresh .env (so a password was generated and handed over) on a database that already has users
    said = say_admin("no", "never-Used_1")
    assert "never-Used_1" not in said
    assert "already there" in said
    assert "liberty-admin set-password admin" in said


def test_nothing_known_and_nothing_handed_over_gives_the_way_to_set_one():
    said = say_admin("unknown", "")
    assert "password:" not in said
    assert "liberty-admin set-password admin" in said


def test_an_unreadable_log_keeps_the_password_this_run_handed_over():
    assert "password:  handed-Over_1" in say_admin("unknown", "handed-Over_1")
