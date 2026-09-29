#!/usr/bin/env python3
"""Annotation harness for the my-claude-stuff plugin.

Subcommands:
  list     <path>     [--unresolved]                          List annotations. <path> can be a sidecar file or a directory (recursive glob of *.annotations.json).
  reply    <sidecar>  --id <id> --body "<text>"               Append a Claude reply to an annotation.
  create   <source>   --quote "<text>" --body "<text>"        Create a new Claude-authored annotation on a source file.
  resolve  <sidecar>  --id <id> [--unresolve]                 Mark an annotation resolved (or unresolved with --unresolve).

Author is hard-coded to "Claude" for every write — the script never accepts an --author flag, so
Claude cannot impersonate the user.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from typing import Tuple

CLAUDE_AUTHOR = "Claude"


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def new_id() -> str:
    return secrets.token_hex(3)  # 6 hex chars


def load_sidecar(path: str) -> Tuple[dict, bool]:
    """Load a sidecar. Returns (data, migrated) — migrated=True if any fields were backfilled."""
    if not os.path.exists(path):
        return ({"annotations": []}, False)
    if not os.path.isfile(path):
        sys.exit(f"sidecar path is not a regular file: {path}")
    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            sys.exit(
                f"could not parse sidecar {path}: {e.msg} (line {e.lineno} col {e.colno})"
            )
    if not isinstance(data, dict):
        sys.exit(
            f"sidecar {path} must contain a JSON object, got {type(data).__name__}"
        )
    if "annotations" not in data:
        annotations = []
    else:
        annotations = data["annotations"]
        if not isinstance(annotations, list):
            sys.exit(
                f"sidecar {path}: 'annotations' must be a list, got {type(annotations).__name__}"
            )
    data["annotations"] = annotations
    migrated = False
    for i, ann in enumerate(annotations):
        if not isinstance(ann, dict):
            sys.exit(
                f"sidecar {path}: annotations[{i}] must be an object, got {type(ann).__name__}"
            )
        comments = ann.get("comments")
        if comments is not None:
            if not isinstance(comments, list):
                sys.exit(
                    f"sidecar {path}: annotations[{i}].comments must be a list, got {type(comments).__name__}"
                )
            for j, c in enumerate(comments):
                if not isinstance(c, dict):
                    sys.exit(
                        f"sidecar {path}: annotations[{i}].comments[{j}] must be an object, got {type(c).__name__}"
                    )
        if not ann.get("id"):
            ann["id"] = new_id()
            migrated = True
        if "resolved" not in ann:
            ann["resolved"] = False
            migrated = True
    return data, migrated


def atomic_write(path: str, data: dict) -> None:
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".annotate-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def find_quote_anchor(source_path: str, quote: str) -> dict:
    if not os.path.exists(source_path):
        sys.exit(f"source file not found: {source_path}")
    if not os.path.isfile(source_path):
        sys.exit(f"source path is not a regular file: {source_path}")
    with open(source_path) as f:
        text = f.read()
    occurrences = []
    start = 0
    while True:
        idx = text.find(quote, start)
        if idx == -1:
            break
        occurrences.append(idx)
        start = idx + 1
    if not occurrences:
        sys.exit(f"quote not found in {source_path}: {quote!r}")
    if len(occurrences) > 1:
        sys.exit(
            f"quote appears {len(occurrences)} times in {source_path}; "
            "provide a more unique substring"
        )
    idx = occurrences[0]
    before = text[:idx]
    line_start = before.count("\n") + 1
    last_nl = before.rfind("\n")
    char_start = len(before) - (last_nl + 1) if last_nl >= 0 else len(before)
    quote_newlines = quote.count("\n")
    line_end = line_start + quote_newlines
    if quote_newlines == 0:
        char_end = char_start + len(quote)
    else:
        last_nl_in_quote = quote.rfind("\n")
        char_end = len(quote) - (last_nl_in_quote + 1)
    return {
        "line_start": line_start,
        "line_end": line_end,
        "char_start": char_start,
        "char_end": char_end,
    }


def sidecar_path(source_path: str) -> str:
    return f"{source_path}.annotations.json"


IGNORED_DIRS = {
    ".git",
    "node_modules",
    ".worktrees",
    "out",
    "dist",
    "__pycache__",
    ".venv",
    "venv",
}


def collect_sidecars(path: str) -> Tuple[list[str], bool]:
    """Returns (sidecars, is_dir)."""
    if os.path.isfile(path):
        return ([path], False)
    if os.path.isdir(path):
        found = []
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for f in files:
                if f.endswith(".annotations.json"):
                    found.append(os.path.join(root, f))
        found.sort()
        return (found, True)
    sys.exit(f"path not found: {path}")


def cmd_list(args):
    sidecars, is_dir = collect_sidecars(args.path)
    base = args.path if is_dir else os.path.dirname(args.path) or "."
    grouped = len(sidecars) > 1
    any_printed = False
    for sidecar in sidecars:
        if is_dir:
            try:
                data, migrated = load_sidecar(sidecar)
            except SystemExit as e:
                rel = os.path.relpath(sidecar, base)
                print(f"!! skipping {rel}: {e}", file=sys.stderr)
                continue
        else:
            data, migrated = load_sidecar(sidecar)
        if migrated:
            atomic_write(sidecar, data)
        annotations = data.get("annotations", [])
        if args.unresolved:
            annotations = [a for a in annotations if not a.get("resolved")]
        if not annotations:
            continue
        if grouped:
            rel = os.path.relpath(sidecar, base)
            print(f"\n=== {rel} ===" if any_printed else f"=== {rel} ===")
        for ann in annotations:
            marker = "✓" if ann.get("resolved") else "○"
            comments = ann.get("comments") or []
            first_author = comments[0].get("author", "?") if comments else "?"
            quote_preview = (ann.get("quote", "") or "").replace("\n", " ")
            if len(quote_preview) > 60:
                quote_preview = quote_preview[:57] + "…"
            comment_count = len(comments)
            author_field = f"[{first_author}]".ljust(8)
            print(
                f"{marker} {ann.get('id', '?'):8}  {author_field}  ({comment_count}c)  {quote_preview}"
            )
        any_printed = True
    if not any_printed:
        state = "unresolved " if args.unresolved else ""
        print(f"(no {state}annotations found under {args.path})")


def cmd_reply(args):
    data, _ = load_sidecar(args.sidecar)
    target = next(
        (a for a in data.get("annotations", []) if a.get("id") == args.id), None
    )
    if not target:
        sys.exit(f"no annotation with id={args.id} in {args.sidecar}")
    comments = target.get("comments") or []
    if not isinstance(comments, list):
        sys.exit(
            f"annotation {args.id} in {args.sidecar}: 'comments' must be a list, got {type(comments).__name__}"
        )
    comments.append(
        {
            "author": CLAUDE_AUTHOR,
            "body": args.body,
            "timestamp": now_iso(),
        }
    )
    target["comments"] = comments
    atomic_write(args.sidecar, data)
    print(f"replied to {args.id} in {args.sidecar}")


def cmd_create(args):
    side = sidecar_path(args.source)
    data, _ = load_sidecar(side)
    anchor = find_quote_anchor(args.source, args.quote)
    ann = {
        "id": new_id(),
        "anchor": anchor,
        "quote": args.quote,
        "resolved": False,
        "comments": [
            {
                "author": CLAUDE_AUTHOR,
                "body": args.body,
                "timestamp": now_iso(),
            }
        ],
    }
    data.setdefault("annotations", []).append(ann)
    atomic_write(side, data)
    print(f"created {ann['id']} in {side}")


def cmd_resolve(args):
    data, _ = load_sidecar(args.sidecar)
    target = next(
        (a for a in data.get("annotations", []) if a.get("id") == args.id), None
    )
    if not target:
        sys.exit(f"no annotation with id={args.id} in {args.sidecar}")
    target["resolved"] = not args.unresolve
    atomic_write(args.sidecar, data)
    state = "resolved" if target["resolved"] else "unresolved"
    print(f"{args.id} marked {state} in {args.sidecar}")


def main():
    p = argparse.ArgumentParser(
        prog="annotate", description=__doc__.strip().splitlines()[0]
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser(
        "list",
        help="List annotations. <path> can be a sidecar file or a directory (recursive).",
    )
    ls.add_argument("path")
    ls.add_argument(
        "--unresolved", action="store_true", help="Show only unresolved annotations"
    )
    ls.set_defaults(func=cmd_list)

    r = sub.add_parser("reply", help="Append a Claude reply to an existing annotation")
    r.add_argument("sidecar")
    r.add_argument("--id", required=True)
    r.add_argument("--body", required=True)
    r.set_defaults(func=cmd_reply)

    c = sub.add_parser(
        "create",
        help="Create a new Claude-authored annotation anchored to a quote in source",
    )
    c.add_argument("source")
    c.add_argument("--quote", required=True)
    c.add_argument("--body", required=True)
    c.set_defaults(func=cmd_create)

    rv = sub.add_parser(
        "resolve", help="Mark an annotation resolved (use --unresolve to flip back)"
    )
    rv.add_argument("sidecar")
    rv.add_argument("--id", required=True)
    rv.add_argument("--unresolve", action="store_true")
    rv.set_defaults(func=cmd_resolve)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
