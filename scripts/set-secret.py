#!/usr/bin/env python3
"""Manage secrets in ~/.claude/secrets.env (dotenv format, mode 0600).

Generic primitive used by any plugin/hook that needs an auth token or
API key. Future services drop into the same file as new KEY=value lines.

Usage:
    set-secret.py <KEY>            # prompt for value (hidden input), upsert
    set-secret.py --clear <KEY>    # remove this key
    set-secret.py --list           # list keys (values masked)
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from _lib import atomic_write, read_dotenv

SECRETS_PATH = Path.home() / ".claude" / "secrets.env"


def _read() -> dict[str, str]:
    return read_dotenv(SECRETS_PATH)


def _write(env: dict[str, str]) -> None:
    body = "".join(f"{k}={v}\n" for k, v in env.items())
    atomic_write(SECRETS_PATH, body, mode=0o600)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage secrets in ~/.claude/secrets.env.",
        usage="%(prog)s <KEY> | --clear <KEY> | --list",
    )
    parser.add_argument(
        "key", nargs="?", help="Env-var-style key (e.g. FIGMA_PERSONAL_ACCESS_TOKEN)"
    )
    parser.add_argument("--clear", action="store_true", help="Remove the given key")
    parser.add_argument(
        "--list", action="store_true", help="List stored keys (values masked)"
    )
    args = parser.parse_args()

    if args.list:
        env = _read()
        if not env:
            print("(no secrets stored)")
            return 0
        for k in sorted(env):
            print(f"{k}=****")
        return 0

    if not args.key:
        parser.print_usage(sys.stderr)
        return 2

    if args.clear:
        env = _read()
        if args.key in env:
            del env[args.key]
            _write(env)
            print(f"set-secret: removed {args.key}")
        else:
            print(f"set-secret: {args.key} not set; nothing to do")
        return 0

    if not sys.stdin.isatty():
        print(
            "set-secret: stdin is not a tty; cannot prompt for hidden input.\n"
            f"Run this directly from your terminal, or edit {SECRETS_PATH} manually:\n"
            f"  printf '%s=%s\\n' '{args.key}' '<value>' >> {SECRETS_PATH}\n"
            f"  chmod 600 {SECRETS_PATH}",
            file=sys.stderr,
        )
        return 2

    value = getpass.getpass(f"Value for {args.key}: ").strip()
    if not value:
        print("set-secret: empty value, aborting.", file=sys.stderr)
        return 2

    env = _read()
    env[args.key] = value
    _write(env)
    print(f"set-secret: {args.key} saved to {SECRETS_PATH} (mode 0600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
