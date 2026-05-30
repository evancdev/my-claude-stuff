#!/usr/bin/env python3
"""Post a reply into the tracked Figma file's comment thread.

Usage:
    viz-reply.py <comment-id> <message...>

Reads the tracked file key from ~/.claude/viz-state.json and the token from
~/.claude/secrets.env (FIGMA_PERSONAL_ACCESS_TOKEN). Figma only allows replying
to a *root* comment, so <comment-id> is resolved to its thread root before
posting. The new reply's id is recorded in seen_comments so the comments hook
never re-surfaces Claude's own reply as fresh feedback.

Unlike the comments hook (which must stay silent), this is a user/agent-invoked
command and reports failures on stderr with a non-zero exit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from _lib import atomic_write, read_dotenv

STATE_PATH = Path.home() / ".claude" / "viz-state.json"
SECRETS_PATH = Path.home() / ".claude" / "secrets.env"
TOKEN_KEY = "FIGMA_PERSONAL_ACCESS_TOKEN"
API_BASE = "https://api.figma.com/v1/files"


def _read_token() -> str | None:
    v = read_dotenv(SECRETS_PATH).get(TOKEN_KEY)
    if v:
        return v
    env = os.environ.get(TOKEN_KEY)
    return env or None


def _api(url: str, token: str, *, data: dict | None = None) -> dict:
    """GET (data=None) or POST (data=dict) JSON against the Figma API."""
    headers = {"X-Figma-Token": token}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=body, headers=headers, method="POST" if data is not None else "GET"
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _resolve_root(comments: list, comment_id: str) -> str | None:
    """Map the target comment to its thread root id. Figma forbids replying to
    a reply, so a reply's `parent_id` (the root) is the real target. Returns
    None if the id isn't found on the file."""
    for c in comments:
        if isinstance(c, dict) and c.get("id") == comment_id:
            parent = c.get("parent_id")
            return parent if isinstance(parent, str) and parent else comment_id
    return None


def _record_seen(reply_id: str) -> None:
    """Add the just-posted reply id to seen_comments so the comments hook
    won't treat Claude's own reply as new feedback. Best-effort."""
    try:
        state = json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(state, dict):
        return
    seen = state.get("seen_comments")
    seen = list(seen) if isinstance(seen, list) else []
    if reply_id not in seen:
        seen.append(reply_id)
        state["seen_comments"] = sorted(seen)
        try:
            atomic_write(STATE_PATH, json.dumps(state, indent=2))
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reply to a Figma comment thread on the tracked file.",
        usage="%(prog)s <comment-id> <message...>",
    )
    parser.add_argument("comment_id", help="id of a comment in the thread to reply to")
    parser.add_argument("message", nargs="+", help="reply text (one or more words)")
    args = parser.parse_args(argv)

    message = " ".join(args.message).strip()
    if not message:
        print("viz: empty reply message, aborting.", file=sys.stderr)
        return 2

    token = _read_token()
    if not token:
        print(
            "viz: no FIGMA_PERSONAL_ACCESS_TOKEN set; run "
            "`my-claude secret set FIGMA_PERSONAL_ACCESS_TOKEN`",
            file=sys.stderr,
        )
        return 2

    try:
        state = json.loads(STATE_PATH.read_text())
        key = state.get("current_file_key") if isinstance(state, dict) else None
    except (OSError, json.JSONDecodeError):
        key = None
    if not isinstance(key, str) or not key:
        print(
            "viz: no Figma file tracked; run `my-claude viz set <url-or-key>` first",
            file=sys.stderr,
        )
        return 2

    try:
        listing = _api(f"{API_BASE}/{key}/comments", token)
        comments = listing.get("comments", []) if isinstance(listing, dict) else []
        root_id = _resolve_root(
            comments if isinstance(comments, list) else [], args.comment_id
        )
        if root_id is None:
            print(
                f"viz: comment {args.comment_id} not found on file {key}",
                file=sys.stderr,
            )
            return 1
        posted = _api(
            f"{API_BASE}/{key}/comments",
            token,
            data={"message": message, "comment_id": root_id},
        )
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print(
                f"viz: Figma denied the request (403) — is the token's account "
                f"invited to file {key} with comment access, and does the token "
                f"have the file_comments:write scope?",
                file=sys.stderr,
            )
        elif e.code == 404:
            print(f"viz: file {key} not found (404)", file=sys.stderr)
        elif e.code == 429:
            retry = e.headers.get("Retry-After", "?") if e.headers else "?"
            print(f"viz: rate limited by Figma; retry after {retry}s", file=sys.stderr)
        else:
            print(f"viz: Figma API error ({e.code})", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        print(f"viz: could not reach Figma ({e})", file=sys.stderr)
        return 1

    reply_id = posted.get("id") if isinstance(posted, dict) else None
    if isinstance(reply_id, str):
        _record_seen(reply_id)

    print(f"viz: replied in thread {root_id} on file {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
