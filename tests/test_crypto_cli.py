from __future__ import annotations

from liberty.crypto import is_encrypted
from liberty.crypto_cli import main

MK = "cli-test-master-key"


def test_encrypt_then_decrypt_round_trip(capsys) -> None:
    assert main(["encrypt", "my secret", "--master-key", MK]) == 0
    enc = capsys.readouterr().out.strip()
    assert is_encrypted(enc)
    assert main(["decrypt", enc, "--master-key", MK]) == 0
    assert capsys.readouterr().out.strip() == "my secret"


def test_encrypt_idempotent(capsys) -> None:
    main(["encrypt", "x", "--master-key", MK])
    enc = capsys.readouterr().out.strip()
    main(["encrypt", enc, "--master-key", MK])
    assert capsys.readouterr().out.strip() == enc  # ENC: in → ENC: out unchanged


def test_is_encrypted_exit_codes(capsys) -> None:
    assert main(["is-encrypted", "ENC:abc"]) == 0
    assert main(["is-encrypted", "plain"]) == 1
    capsys.readouterr()  # drain


def test_decrypt_plain_value_errors(capsys) -> None:
    assert main(["decrypt", "not-encrypted", "--master-key", MK]) == 2
    assert "not encrypted" in capsys.readouterr().err


def test_no_master_key_errors(capsys) -> None:
    # neither --master-key nor a configured one (config/app.toml's is ${LIBERTY_MASTER_KEY},
    # which is empty unless the env var is set in this test process)
    import os

    os.environ.pop("LIBERTY_MASTER_KEY", None)
    assert main(["encrypt", "x"]) == 2
    assert "no master key" in capsys.readouterr().err
