#!/usr/bin/env python3
"""Install the `my-claude` command.

Symlinks scripts/my-claude into ~/.local/bin and ensures that dir is on your
PATH via your shell rc file. One command, clean namespace — the raw script
filenames stay private. Idempotent; re-runs refresh in place.

    install-cli.py             # install / refresh the symlink + PATH block
    install-cli.py --uninstall # remove the symlink + PATH block
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

from _lib import atomic_write

SCRIPTS_DIR = Path(__file__).resolve().parent
ENTRYPOINT = SCRIPTS_DIR / "my-claude"

MARKER_START = "# >>> my-claude-stuff scripts >>>"
MARKER_END = "# <<< my-claude-stuff scripts <<<"


def _rc_path() -> Path:
    """Pick the shell rc file to edit, mirroring common login-shell defaults."""
    home = Path.home()
    shell = os.environ.get("SHELL", "")
    if shell.endswith("/zsh"):
        return home / ".zshrc"
    if shell.endswith("/bash"):
        bash_profile = home / ".bash_profile"
        return bash_profile if bash_profile.is_file() else home / ".bashrc"
    return home / ".profile"


def _strip_block(lines: list[str]) -> list[str]:
    """Return `lines` with any existing marker block removed."""
    out: list[str] = []
    in_block = False
    for line in lines:
        if line == MARKER_START:
            in_block = True
            continue
        if line == MARKER_END and in_block:
            in_block = False
            continue
        if not in_block:
            out.append(line)
    return out


def _on_path(bin_dir: Path) -> bool:
    entries = os.environ.get("PATH", "").split(os.pathsep)
    return any(p and os.path.abspath(p) == str(bin_dir) for p in entries)


def _read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _install() -> int:
    if not ENTRYPOINT.is_file():
        print(f"install-cli: entrypoint not found at {ENTRYPOINT}", file=sys.stderr)
        return 1
    ENTRYPOINT.chmod(
        ENTRYPOINT.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )

    bin_dir = Path.home() / ".local" / "bin"
    link = bin_dir / "my-claude"
    bin_dir.mkdir(parents=True, exist_ok=True)

    if link.is_symlink():
        link.unlink()
    elif link.exists():
        # A real file/dir here isn't ours — refuse to clobber it.
        print(f"install-cli: refusing to overwrite non-symlink {link}", file=sys.stderr)
        return 1
    link.symlink_to(ENTRYPOINT)
    print(f"install-cli: linked {link} -> {ENTRYPOINT}")

    rc = _rc_path()
    lines = _read_lines(rc)
    block = [MARKER_START, f'export PATH="{bin_dir}:$PATH"', MARKER_END]

    if MARKER_START in lines:
        # Refresh the existing block in place (keeps bin_dir correct if moved).
        out: list[str] = []
        in_block = False
        for line in lines:
            if line == MARKER_START:
                out.extend(block)
                in_block = True
                continue
            if line == MARKER_END and in_block:
                in_block = False
                continue
            if not in_block:
                out.append(line)
        atomic_write(rc, "\n".join(out) + "\n")
        print(f"install-cli: updated PATH block in {rc}")
    elif _on_path(bin_dir):
        # Already reachable some other way — don't touch the user's dotfiles.
        print(f"install-cli: {bin_dir} already on PATH — left {rc} untouched")
        print("install-cli: ready — run: my-claude help")
        return 0
    else:
        prefix = "\n".join(lines)
        if prefix:
            prefix += "\n"
        atomic_write(rc, prefix + "\n" + "\n".join(block) + "\n")
        print(f"install-cli: appended PATH block to {rc}")

    print(f"install-cli: open a new shell, or run 'source {rc}', then: my-claude help")
    return 0


def _uninstall() -> int:
    bin_dir = Path.home() / ".local" / "bin"
    link = bin_dir / "my-claude"
    if link.is_symlink():
        link.unlink()
        print(f"install-cli: removed symlink {link}")

    rc = _rc_path()
    lines = _read_lines(rc)
    if MARKER_START in lines:
        atomic_write(rc, "\n".join(_strip_block(lines)) + "\n")
        print(f"install-cli: removed PATH block from {rc}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Install the my-claude command.",
        usage="%(prog)s [--uninstall]",
    )
    parser.add_argument(
        "--uninstall", action="store_true", help="Remove the symlink and PATH block"
    )
    args = parser.parse_args(argv)
    return _uninstall() if args.uninstall else _install()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
