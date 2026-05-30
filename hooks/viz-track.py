#!/usr/bin/env python3
"""PostToolUse hook for Figma MCP tools.

When Claude calls a Figma MCP tool that creates or edits a file,
auto-record the file URL/key as the current viz target so the
comments hook can fetch its comments on subsequent prompts.

Silent / no-op (exit 0, no stdout) when:
- stdin is empty or unreadable
- no figma.com URL or "fileKey" is found in the payload
- viz-set-current.py fails for any reason
- any uncaught exception

A hook must never fail a tool result — top-level try/except catches
anything that escapes and exits 0.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

URL_RE = re.compile(r"figma\.com/(?:board|design|file)/([A-Za-z0-9_-]+)")
FILEKEY_RE = re.compile(r'"fileKey"\s*:\s*"([A-Za-z0-9_-]+)"')

SET_CURRENT = Path(__file__).resolve().parent.parent / "scripts" / "viz-set-current.py"


def _main() -> None:
    try:
        payload = sys.stdin.read()
    except (OSError, ValueError):
        return
    if not payload:
        return

    m = URL_RE.search(payload) or FILEKEY_RE.search(payload)
    if not m:
        return
    key = m.group(1)

    try:
        subprocess.run(
            [str(SET_CURRENT), key],
            timeout=2,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


if __name__ == "__main__":
    try:
        _main()
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
