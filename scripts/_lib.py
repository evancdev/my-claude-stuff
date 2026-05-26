"""Shared helpers for plugin scripts and hooks.

Internal — leading underscore signals not a public CLI. Imported by
sibling scripts directly; hooks insert the parent /scripts/ dir into
sys.path first (see hooks/viz-comments-poll.py for the pattern).
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def atomic_write(path: Path, text: str, *, mode: int | None = None) -> None:
    """Atomically write `text` to `path` (tmp in same dir, then rename).

    - If `path` is a symlink, writes through it so dotfile-manager setups
      replace the target file, not the link.
    - If `mode` is given, the final file is chmod'd to that mode.
    - If `mode` is omitted and the file exists, the prior mode is preserved.
      If it doesn't exist, the OS default applies (mkstemp uses 0600).
    """
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is None:
            try:
                mode = stat.S_IMODE(target.stat().st_mode)
            except FileNotFoundError:
                mode = None
        os.replace(tmp, target)
        if mode is not None:
            os.chmod(target, mode)
    except BaseException:
        # BaseException so KeyboardInterrupt mid-write still cleans up tmp.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE dotenv file. Skips blanks and `#` comments,
    skips lines without `=`. Returns {} on missing/unreadable file."""
    if not path.is_file():
        return {}
    try:
        text = path.read_text()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env
