#!/usr/bin/env python3
"""Idempotently registers the plugin's statusline in ~/.claude/settings.json.

Run once per machine: `python3 <plugin>/scripts/install-statusline.py`
(or directly, since it's executable).
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path


def _atomic_write(path: Path, text: str) -> None:
    # If `path` is a symlink (dotfile-manager setups), resolve through it so
    # we replace the real file rather than clobbering the symlink.
    target = path.resolve() if path.is_symlink() else path
    fd, tmp = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        # mkstemp creates files at 0600; preserve the original mode so we
        # don't silently tighten user perms on overwrite.
        try:
            prev_mode: int | None = stat.S_IMODE(target.stat().st_mode)
        except FileNotFoundError:
            prev_mode = None
        os.replace(tmp, target)
        if prev_mode is not None:
            os.chmod(target, prev_mode)
    except BaseException:
        # BaseException so KeyboardInterrupt mid-write still cleans up tmp.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    statusline = script_dir / "statusline.py"

    home = os.environ.get("HOME")
    if not home:
        print(
            "error: HOME environment variable is empty or unset",
            file=sys.stderr,
        )
        return 1
    if not os.path.isabs(home):
        # Invariant: home is absolute. Relative would silently write under cwd.
        print(
            f"error: HOME must be an absolute path (got: {home!r})",
            file=sys.stderr,
        )
        return 1
    settings_path = Path(home) / ".claude" / "settings.json"

    if not statusline.is_file():
        print(f"error: statusline.py not found at {statusline}", file=sys.stderr)
        return 1
    statusline.chmod(
        statusline.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if not settings_path.exists():
        _atomic_write(settings_path, "{}\n")

    try:
        raw = settings_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        print(
            f"error: {settings_path} is not valid JSON (encoding: {e})",
            file=sys.stderr,
        )
        return 1

    try:
        settings = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"error: {settings_path} is not valid JSON ({e})", file=sys.stderr)
        return 1

    if not isinstance(settings, dict):
        print(
            f"error: {settings_path} is not valid JSON "
            f"(top-level must be an object, got {type(settings).__name__})",
            file=sys.stderr,
        )
        return 1

    desired = {"type": "command", "command": str(statusline)}
    existing = settings.get("statusLine")

    if existing == desired:
        print(f"statusLine already points to {statusline} — no change")
        return 0

    if "statusLine" in settings:
        # JSON-render the prior value so the warning matches on-disk shape
        # across dicts/strings/scalars/null (vs. Python `repr`).
        print(
            f"warning: replacing existing statusLine command "
            f"(was: {json.dumps(existing, ensure_ascii=False)})",
            file=sys.stderr,
        )

    settings["statusLine"] = desired
    _atomic_write(
        settings_path,
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
    )
    print(f"wrote statusLine -> {statusline}")
    print(f"  into {settings_path}")
    print("done. restart Claude Code (or /reload-plugins) to see it.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Structured stderr line instead of a Python traceback. `Exception`
        # (not `BaseException`) so Ctrl-C still propagates normally.
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
