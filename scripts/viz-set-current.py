#!/usr/bin/env python3
"""Record the current Figma file so the comments hook polls it.

Usage:
    viz-set-current.py <figma-url-or-file-key>
    viz-set-current.py --clear
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

STATE_PATH = Path.home() / ".claude" / "viz-state.json"
URL_RE = re.compile(r"figma\.com/(?:board|design|file)/([A-Za-z0-9_-]+)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Track current Figma file for the comments hook.",
        usage="%(prog)s <figma-url-or-file-key> | --clear",
    )
    parser.add_argument("input", nargs="?", help="Figma URL or file key")
    parser.add_argument("--clear", action="store_true", help="Clear current state")
    args = parser.parse_args()

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if args.clear:
        try:
            STATE_PATH.unlink()
        except FileNotFoundError:
            pass
        print("viz: state cleared")
        return 0

    if not args.input:
        parser.print_usage(sys.stderr)
        return 2

    m = URL_RE.search(args.input)
    key = m.group(1) if m else args.input

    prev: dict = {}
    if STATE_PATH.exists():
        try:
            loaded = json.loads(STATE_PATH.read_text())
            prev = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            prev = {}

    seen = prev.get("seen_comments", []) if prev.get("current_file_key") == key else []
    state = {
        "current_file_key": key,
        "seen_comments": seen,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)
    print(f"viz: tracking Figma {key} for comments (TTL 4h)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
