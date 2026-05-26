#!/usr/bin/env python3
"""Store the Figma personal access token used by the viz comments hook.

Run from a real terminal (the script refuses non-tty stdin so the token
can't be accidentally captured by a parent process / chat history).

Usage:
    viz-set-token.py            # interactive prompt (hidden input)
    viz-set-token.py --clear    # remove stored token
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

TOKEN_PATH = Path.home() / ".claude" / "figma-token"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save a Figma personal access token for the viz hook.",
        usage="%(prog)s [--clear]",
    )
    parser.add_argument("--clear", action="store_true", help="Remove stored token")
    args = parser.parse_args()

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)

    if args.clear:
        try:
            TOKEN_PATH.unlink()
        except FileNotFoundError:
            pass
        print(f"viz: removed {TOKEN_PATH}")
        return 0

    if not sys.stdin.isatty():
        print(
            "viz-set-token: stdin is not a tty; cannot prompt for hidden input.\n"
            "Run this directly from your terminal, or write the token manually:\n"
            f"  printf '%s\\n' '<token>' > {TOKEN_PATH}\n"
            f"  chmod 600 {TOKEN_PATH}",
            file=sys.stderr,
        )
        return 2

    token = getpass.getpass("Figma personal access token: ").strip()
    if not token:
        print("viz-set-token: empty token, aborting.", file=sys.stderr)
        return 2

    TOKEN_PATH.write_text(token + "\n")
    os.chmod(TOKEN_PATH, 0o600)
    print(f"viz: token saved to {TOKEN_PATH} (mode 0600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
