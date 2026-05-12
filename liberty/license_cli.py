"""``liberty-license`` — inspect a license key (RS256 JWT — see :mod:`liberty.licensing`).

    liberty-license verify 'eyJ…'        # verify a key → JSON status (mode + claims); exit 0 if full, 1 if restricted
    liberty-license verify               # verify the configured key ([license] key / LIBERTY_LICENSE_KEY); reads stdin if none
    liberty-license status               # same as `verify` with no key — the configured key's status

The verification key is the one embedded in the build (``liberty/licensing/public.pem``);
``--public-key PATH`` overrides it (for testing against another key-pair).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from liberty.config import load_settings
from liberty.licensing import verify_license


def _key(args: argparse.Namespace) -> str:
    if getattr(args, "key", None):
        return args.key
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            return piped
    return (load_settings(args.config) if args.config else load_settings()).license.key


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="path to app.toml (default: config/app.toml)")
    common.add_argument("--public-key", help="path to a PEM public key to verify against (default: the embedded one)")
    parser = argparse.ArgumentParser(prog="liberty-license", description=__doc__, parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("verify", help="verify a license key (the argument, stdin, or the configured one)", parents=[common])
    p.add_argument("key", nargs="?", help="the license key (read from stdin, then [license] key, if omitted)")
    sub.add_parser("status", help="verify the configured [license] key", parents=[common])
    args = parser.parse_args(argv)

    pem = Path(args.public_key).read_bytes() if args.public_key else None
    result = verify_license(_key(args), public_key_pem=pem)
    print(json.dumps(result.public_dict(), indent=2))
    return 0 if result.valid else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
