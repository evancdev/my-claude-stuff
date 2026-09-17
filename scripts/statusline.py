#!/usr/bin/env python3
"""Minimal statusline: reads Claude Code hook JSON from stdin, prints
`cwd | model (effort) | NNk tok` from the last transcript usage row, with
the token count colored by how full the window is.

Reverse-scans the transcript from EOF — cost is O(K) where K is the
distance from EOF to the last usage row (typically the last few KB).

Spec resolutions:
- `transcript_path` must be absolute; relative paths are treated as missing.
- `model.display_name` falls back to `"claude"` when missing or empty.
- Effort comes from the transcript, not the hook payload. `perTurnEffort`
  wins over the session `effort`; with neither, the model name prints as
  the hook gave it, parenthetical and all.
- `cwd="/"` renders as `"/"`; empty cwd renders empty.
- Half-up rounding: `1450` → `"1.5k"`, `1449` → `"1.4k"`.
- Colors are unconditional. stdout is a pipe here, never a tty, so a tty
  check would mean no color ever. Claude Code renders the SGR codes.
"""

import json
import os
import sys
from typing import TypeGuard


def _as_dict(x) -> dict:
    return x if isinstance(x, dict) else {}


def _as_str(x) -> str:
    return x if isinstance(x, str) else ""


def _is_numeric(v) -> TypeGuard[int | float]:
    """Real int or float — bool excluded (it's an int subclass in Python)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _row_usage(row) -> dict:
    """Extract a usage block with a usable input_tokens from a row, or {}.

    `message.usage` is preferred; falls back to top-level `usage` if the
    preferred block doesn't have a valid input_tokens.
    """
    if not isinstance(row, dict):
        return {}
    msg_usage = _as_dict(_as_dict(row.get("message")).get("usage"))
    if _is_numeric(msg_usage.get("input_tokens")):
        return msg_usage
    top_usage = _as_dict(row.get("usage"))
    if _is_numeric(top_usage.get("input_tokens")):
        return top_usage
    return {}


def _row_effort(row) -> str:
    """Effort level from a transcript row. A per-turn override wins."""
    if not isinstance(row, dict):
        return ""
    return _as_str(row.get("perTurnEffort")) or _as_str(row.get("effort"))


def _scan_transcript(path: str) -> tuple[dict, str]:
    """Reverse-scan for the last usage block and the last effort level.

    Either can be missing. The scan stops as soon as both are in hand.
    """
    usage: dict = {}
    effort = ""

    try:
        f = open(path, "rb")
    except OSError:
        return usage, effort

    chunk_size = 16384

    with f:
        f.seek(0, 2)
        pos = f.tell()
        carry = b""  # partial line at the start of the previously-read region

        while pos > 0:
            read = min(chunk_size, pos)
            pos -= read
            f.seek(pos)
            block = f.read(read) + carry

            if pos > 0:
                # The first line might still be partial — its start is in a
                # chunk we haven't read yet. Save it for the next iteration.
                idx = block.find(b"\n")
                if idx == -1:
                    # No newline in the whole block — entire block is one
                    # partial line.
                    carry = block
                    continue
                carry = block[:idx]
                block = block[idx + 1 :]
            else:
                carry = b""

            for line in reversed(block.split(b"\n")):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line.decode("utf-8", errors="replace"))
                except Exception:
                    continue
                if not usage:
                    usage = _row_usage(row)
                if not effort:
                    effort = _row_effort(row)
                if usage and effort:
                    return usage, effort

    return usage, effort


def main() -> None:
    try:
        hook = _as_dict(json.loads(sys.stdin.read()))
    except Exception:
        print("claude json parsing error o7")
        return

    transcript = _as_str(hook.get("transcript_path"))
    if transcript and not os.path.isabs(transcript):
        transcript = ""
    model = _as_str(_as_dict(hook.get("model")).get("display_name")) or "claude"

    cwd_raw = _as_str(hook.get("cwd"))
    # rstrip("/") on root ("/") collapses to "" → basename "" → empty.
    # Fall back to the original input so root renders as "/".
    cwd = os.path.basename(cwd_raw.rstrip("/")) or cwd_raw

    last_usage, effort = (
        _scan_transcript(transcript)
        if transcript and os.path.exists(transcript)
        else ({}, "")
    )
    if effort:
        model = f"{_base_model(model)} ({effort})"

    total = 0
    for k in (
        "input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "output_tokens",
    ):
        v = last_usage.get(k)
        if _is_numeric(v) and v >= 0:
            # int(10.9) truncates to 10. Use half-up to match the display
            # formatter's rounding convention. Safe because v >= 0 here.
            total += int(v + 0.5)

    print(f"{cwd} | {model} | {_color(total, fmt(total) + ' tok')}")


# Upper bound of each band and its 256-color code. Past the last one, red.
_BANDS = ((200_000, 2), (300_000, 3), (400_000, 208))
_OVER = 9


def _color(total: int, text: str) -> str:
    """Wrap the count and unit in the SGR color for its band."""
    code = next((c for limit, c in _BANDS if total < limit), _OVER)
    return f"\x1b[38;5;{code}m{text}\x1b[0m"


def _base_model(name: str) -> str:
    """Drop a trailing parenthetical: `Opus 5 (1M context)` → `Opus 5`."""
    cut = name.rfind(" (")
    if cut > 0 and name.endswith(")"):
        return name[:cut]
    return name


def fmt(n: int) -> str:
    # Promote to M once the rounded display would otherwise show "1000.0k".
    if n >= 999_950:
        return _fmt_unit(n, 1_000_000, "M")
    if n >= 1_000:
        return _fmt_unit(n, 1_000, "k")
    return str(n)


def _fmt_unit(n: int, divisor: int, suffix: str) -> str:
    """Format `n / divisor` to one decimal place with half-up rounding.

    Pure integer arithmetic — avoids both float precision quirks and
    Python's `round()` / f-string banker's rounding.
    """
    scaled = (n * 10 + divisor // 2) // divisor
    return f"{scaled // 10}.{scaled % 10}{suffix}"


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
