#!/usr/bin/env python3
"""Minimal statusline: reads Claude Code hook JSON from stdin, prints
`cwd | model | NNk tok` based on the last transcript usage row.

Reverse-scans the transcript from EOF — cost is O(K) where K is the
distance from EOF to the last usage row (typically the last few KB).

Spec resolutions:
- `transcript_path` must be absolute; relative paths are treated as missing.
- `model.display_name` falls back to `"claude"` when missing or empty.
- `cwd="/"` renders as `"/"`; empty cwd renders empty.
- Half-up rounding: `1450` → `"1.5k"`, `1449` → `"1.4k"`.
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


def _last_usage(path: str) -> dict:
    """Reverse-scan transcript for the last usage row. Returns {} if none."""
    try:
        f = open(path, "rb")
    except OSError:
        return {}

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
                usage = _row_usage(row)
                if usage:
                    return usage

    return {}


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

    last_usage = (
        _last_usage(transcript) if transcript and os.path.exists(transcript) else {}
    )

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

    print(f"{cwd} | {model} | {fmt(total)} tok")


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
