#!/usr/bin/env python3
"""UserPromptSubmit hook: surface unread Figma comments as additionalContext.

Silent / no-op (exit 0, no stdout) when:
- no state file, or state file isn't a dict
- no FIGMA_PERSONAL_ACCESS_TOKEN
- state older than TTL (default 4h)
- no unseen comments
- any error talking to Figma OR any uncaught exception

A hook must never fail a user prompt. The top-level try/except catches
anything that escapes the inner handlers and exits 0.
"""
from __future__ import annotations

import calendar
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

STATE_PATH = Path.home() / ".claude" / "viz-state.json"
TOKEN_PATH = Path.home() / ".claude" / "figma-token"
TTL_SECONDS = 4 * 3600


def _read_token() -> str | None:
    """Token file first (set once, works from any launch context), env var as
    a fallback for power users."""
    if TOKEN_PATH.is_file():
        try:
            t = TOKEN_PATH.read_text().strip()
            if t:
                return t
        except OSError:
            pass
    env = os.environ.get("FIGMA_PERSONAL_ACCESS_TOKEN")
    return env or None


def _main() -> None:
    if not STATE_PATH.is_file():
        return
    token = _read_token()
    if not token:
        return

    try:
        loaded = json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(loaded, dict):
        return
    state = loaded

    key = state.get("current_file_key")
    if not isinstance(key, str) or not key:
        return

    updated = state.get("updated_at")
    if isinstance(updated, str):
        try:
            t = time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return
        if time.time() - calendar.timegm(t) > TTL_SECONDS:
            return

    req = urllib.request.Request(
        f"https://api.figma.com/v1/files/{key}/comments?as_md=true",
        headers={"X-Figma-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            body = json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return
    if not isinstance(body, dict):
        return

    seen_list = state.get("seen_comments")
    seen = set(seen_list) if isinstance(seen_list, list) else set()
    raw = body.get("comments", [])
    if not isinstance(raw, list):
        return

    unseen = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        if not isinstance(cid, str) or cid in seen or c.get("resolved_at"):
            continue
        unseen.append(c)
    if not unseen:
        return

    lines = [f"--- {len(unseen)} unread Figma comment(s) on file {key} ---"]
    for c in unseen:
        user = c.get("user") if isinstance(c.get("user"), dict) else {}
        author = user.get("handle") or "unknown"
        text = (c.get("message") or "").strip()
        lines.append(f"[{author}] (id={c['id']}): {text}")
    lines.append("--- end ---")

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n".join(lines),
        }
    }
    # Print first, persist after. If the persist fails or the harness drops
    # stdout, the next prompt re-injects the same comments — annoying but
    # never silently loses feedback. Swapping these would do the opposite.
    print(json.dumps(payload))

    state["seen_comments"] = sorted(seen | {c["id"] for c in unseen})
    tmp = STATE_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(STATE_PATH)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    try:
        _main()
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
