#!/usr/bin/env python3
"""Answer two questions about cmux surfaces from one `cmux tree` parse.

Default: print the pane this agent's terminal is in, as ready-made flags.
`--tty <ttysNNN>`: print the UUID of the surface running on that tty.

`cmux new-surface` and `cmux list-pane-surfaces` default to the *focused* pane,
not the pane running the agent that called them. Both scripts that open a tab
call this and pass the result through.

The pane is resolved two ways, in order:

1. `cmux identify`, from $CMUX_SURFACE_ID. Exact, but only set when cmux itself
   launched the session.
2. The controlling tty, found by walking up from this process, matched against
   the tty on every surface in `cmux tree --json`.

Prints `--pane pane:N --window window:M` on success. Prints nothing and exits 1
otherwise, and callers fall back to the focused pane.

The --tty form exists because a `surface:N` ref only resolves inside the
caller's workspace, while `cmux tree --all` lists every workspace. A UUID needs
no context, so callers address a surface by that.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

PANE = re.compile(r"^pane:[0-9]+$")
WINDOW = re.compile(r"^window:[0-9]+$")
UUID = re.compile(r"^[0-9A-Fa-f-]{36}$")


def cmux(*args: str) -> dict:
    try:
        out = subprocess.run(
            ("cmux", "--id-format", "both", *args),
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    try:
        return json.loads(out.stdout)
    except (json.JSONDecodeError, ValueError):
        return {}


def agent_tty() -> str | None:
    """The first real tty at or above this process.

    The Bash tool and its shell have none; the `claude` process above them does.
    """
    pid = os.getpid()
    for _ in range(40):
        try:
            out = subprocess.run(
                ("ps", "-o", "ppid=,tty=", "-p", str(pid)),
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        fields = out.stdout.split()
        if len(fields) < 2:
            return None
        parent, tty = fields[0], fields[1]
        if tty != "??":
            return tty
        if parent in ("1", "0"):
            return None
        pid = int(parent) if parent.isdigit() else 0
        if not pid:
            return None
    return None


def surfaces() -> list[tuple[dict, dict]]:
    """Every surface in every window, paired with the window holding it."""
    tree = cmux("tree", "--all", "--json")
    out = []
    for window in tree.get("windows") or []:
        for workspace in window.get("workspaces") or []:
            for pane in workspace.get("panes") or []:
                for surface in pane.get("surfaces") or []:
                    out.append((surface, window))
    return out


def by_tty(tty: str) -> tuple[str, str] | None:
    for surface, window in surfaces():
        if surface.get("tty") == tty:
            return surface.get("pane_ref"), window.get("ref")
    return None


def uuid_for_tty(tty: str) -> str | None:
    for surface, _ in surfaces():
        if surface.get("tty") == tty and UUID.match(surface.get("id") or ""):
            return surface["id"]
    return None


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--tty":
        # hunk reports its session's tty as /dev/ttysNNN; the tree reports ttysNNN.
        found_id = uuid_for_tty(sys.argv[2].removeprefix("/dev/"))
        if not found_id:
            return 1
        print(found_id)
        return 0

    found = None

    caller = (cmux("identify") or {}).get("caller") or {}
    if caller.get("pane_ref"):
        found = (caller.get("pane_ref"), caller.get("window_ref"))

    if not found:
        tty = agent_tty()
        if tty:
            found = by_tty(tty)

    if not found:
        return 1

    pane, window = found
    if not (pane and PANE.match(pane)):
        return 1

    out = f"--pane {pane}"
    if window and WINDOW.match(window):
        out += f" --window {window}"
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
