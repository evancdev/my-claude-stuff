#!/usr/bin/env python3
"""UserPromptSubmit hook: surface unread Figma comments as additionalContext.

Silent / no-op (exit 0, no stdout) when:
- no state file, or state file isn't a dict
- no FIGMA_PERSONAL_ACCESS_TOKEN
- state older than TTL (default 4h)
- no unseen comments (comments authored by our own account are skipped, so
  Claude's posted replies never come back as fresh feedback)
- any error talking to Figma OR any uncaught exception

A hook must never fail a user prompt. The top-level try/except catches
anything that escapes the inner handlers and exits 0.
"""

from __future__ import annotations

import calendar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Hooks live in hooks/; shared helpers live in the sibling scripts/ dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _lib import atomic_write, read_dotenv  # noqa: E402

STATE_PATH = Path.home() / ".claude" / "viz-state.json"
SECRETS_PATH = Path.home() / ".claude" / "secrets.env"
TOKEN_KEY = "FIGMA_PERSONAL_ACCESS_TOKEN"
TTL_SECONDS = 4 * 3600


def _read_token() -> str | None:
    """Dotenv file first (set once, works from any launch context), real env
    var as a fallback for power users."""
    v = read_dotenv(SECRETS_PATH).get(TOKEN_KEY)
    if v:
        return v
    env = os.environ.get(TOKEN_KEY)
    return env or None


def _figma_user_id(token: str) -> str | None:
    """The token account's own Figma user id, used to filter out Claude's own
    replies (the token reads and writes as the same account). Returns None if
    /v1/me is unreachable or the token lacks current_user:read — self-filtering
    is then simply disabled, never fatal."""
    req = urllib.request.Request(
        "https://api.figma.com/v1/me", headers={"X-Figma-Token": token}
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            me = json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    if isinstance(me, dict):
        uid = me.get("id")
        if isinstance(uid, str) and uid:
            return uid
    return None


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

    # Our own (Claude's) user id, so the bot's replies aren't re-surfaced as
    # fresh feedback. Cached in state after the first lookup to avoid an extra
    # request every prompt; None disables filtering (never fatal).
    self_id = state.get("self_user_id")
    if not isinstance(self_id, str) or not self_id:
        self_id = _figma_user_id(token)
        if self_id:
            state["self_user_id"] = self_id
            try:
                atomic_write(STATE_PATH, json.dumps(state, indent=2))
            except OSError:
                pass

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

    current_ids: set[str] = set()
    unseen = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        if not isinstance(cid, str):
            continue
        cuser = c.get("user")
        if self_id and isinstance(cuser, dict) and cuser.get("id") == self_id:
            continue  # Claude's own reply — never surface it as feedback
        current_ids.add(cid)
        if cid in seen or c.get("resolved_at"):
            continue
        unseen.append(c)
    if not unseen:
        # Even on no-op turns, prune seen_comments to ids the API still returns
        # so resolved/deleted comments fall off and the list stays bounded.
        pruned = sorted(seen & current_ids)
        if pruned != sorted(seen):
            state["seen_comments"] = pruned
            try:
                atomic_write(STATE_PATH, json.dumps(state, indent=2))
            except OSError:
                pass
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

    # Intersect with current_ids so resolved/deleted comments drop out — keeps
    # seen_comments bounded by the file's actual comment count.
    state["seen_comments"] = sorted((seen & current_ids) | {c["id"] for c in unseen})
    try:
        atomic_write(STATE_PATH, json.dumps(state, indent=2))
    except OSError:
        pass


if __name__ == "__main__":
    try:
        _main()
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
