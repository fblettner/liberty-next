"""``liberty-crypto`` — encrypt/decrypt values with the configured ``MASTER_KEY``
(the same v1-compatible scheme as :mod:`liberty.crypto`).

    liberty-crypto encrypt 'my secret'           # → ENC:…   (idempotent on an ENC: value)
    liberty-crypto decrypt 'ENC:…'               # → my secret
    liberty-crypto is-encrypted 'ENC:…'          # exit 0 if encrypted, 1 if not

The key comes from ``[crypto] master_key`` in ``config/app.toml`` / ``LIBERTY_MASTER_KEY``,
or ``--master-key VALUE``. Reads stdin when no value argument is given.
"""

from __future__ import annotations

import argparse
import sys

from liberty.config import load_settings
from liberty.crypto import CryptoError, decrypt, encrypt, is_encrypted


def _value(args: argparse.Namespace) -> str:
    if args.value is not None:
        return args.value
    return sys.stdin.read().rstrip("\n")


def _master_key(args: argparse.Namespace) -> str:
    if args.master_key:
        return args.master_key
    return (load_settings(args.config) if args.config else load_settings()).crypto.master_key


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--master-key", help="override [crypto] master_key")
    common.add_argument("--config", help="path to app.toml (default: config/app.toml)")
    parser = argparse.ArgumentParser(prog="liberty-crypto", description=__doc__, parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_ in [("encrypt", "encrypt a value → ENC:…"), ("decrypt", "decrypt an ENC:… value"), ("is-encrypted", "test for the ENC: marker")]:
        p = sub.add_parser(name, help=help_, parents=[common])
        p.add_argument("value", nargs="?", help="the value (read from stdin if omitted)")
    args = parser.parse_args(argv)

    value = _value(args)
    if args.command == "is-encrypted":
        return 0 if is_encrypted(value) else 1
    try:
        out = encrypt(value, _master_key(args)) if args.command == "encrypt" else decrypt(value, _master_key(args))
    except CryptoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(out + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
