---
description: Walk unresolved annotations across the project (or a specific file) and respond via the harness. Reads annotations, replies, optionally resolves. Never edits sidecars by hand.
argument-hint: "[. | path-to-source-or-annotations-json]"
allowed-tools: Read, Bash, Glob
---

Walk user-written annotations and respond to them. **All writes go through the harness at `$CLAUDE_PLUGIN_ROOT/scripts/annotate.py`** — never edit sidecars by hand. The harness enforces schema, atomicity, and hard-codes `author: "Claude"` so you can't impersonate the user.

## Resolve the scope

The sidecar convention: for any source file at `<path>`, annotations live at `<path>.annotations.json`.

1. **`$ARGUMENTS` is empty or `.`**: workspace mode. Walk every **unresolved** annotation under the current working directory.

2. **`$ARGUMENTS` is a path to a `.md`/source/sidecar file**: file mode. Walk every annotation (resolved + unresolved) on that file.
   - If it ends in `.annotations.json`, the source is the path with `.annotations.json` stripped.
   - Otherwise the source is `$ARGUMENTS` and the sidecar is `$ARGUMENTS.annotations.json`.

3. **Missing files**: surface the path and stop.

## Inventory

Run the harness `list` to discover scope:

```bash
# workspace mode
$CLAUDE_PLUGIN_ROOT/scripts/annotate.py list . --unresolved

# file mode (single sidecar)
$CLAUDE_PLUGIN_ROOT/scripts/annotate.py list <sidecar-path>
```

The output groups annotations by file (when listing multiple) and shows `id`, state marker (`○` open / `✓` resolved), authoring info, comment count, and a quote preview. For each annotation you'll act on, also `Read` the sidecar JSON to get full comment bodies and anchor coordinates.

## Harness commands

```bash
# Reply to an existing annotation. Body can be multi-line; quote it carefully.
$CLAUDE_PLUGIN_ROOT/scripts/annotate.py reply <sidecar> --id <id> --body "<text>"

# Create a new Claude-authored annotation anchored to a quote in a source file.
# Fails if the quote isn't found or appears more than once — pick a more unique substring.
$CLAUDE_PLUGIN_ROOT/scripts/annotate.py create <source> --quote "<exact substring>" --body "<text>"

# Toggle resolved state. Default flips to resolved; pass --unresolve to flip back.
$CLAUDE_PLUGIN_ROOT/scripts/annotate.py resolve <sidecar> --id <id> [--unresolve]
```

The harness writes atomically. The VS Code extension's file watcher picks up changes and re-hydrates threads automatically.

## How to respond

Walk annotations in document order. For each one:
- Treat the latest comment as a question (answer it), a critique (revise the relevant section or function), or an instruction (apply it).
- **Reply via `annotate.py reply`** — that's the primary surface. Keep replies focused and addressed to that specific quote.
- **If your reply fully addresses the comment, mark it resolved** with `annotate.py resolve`. Don't resolve prematurely; if there's an open question, leave it unresolved.
- If the source is code, your reply may include a proposed diff. Apply it only if the user explicitly asks; otherwise let them decide.
- Ground load-bearing claims by dispatching the `researcher` subagent and citing returned Sources inline.

## Leaving review annotations (Claude as reviewer)

When the user asks for a code/doc review, use `annotate.py create` to anchor each finding to a specific quote in the source. The user sees them as orange highlights in VS Code (Claude-authored, unresolved) and can reply or resolve from the thread panel.

## Output in chat

Print a short summary per annotation: file, id, action taken, one-line rationale. The actual discussion lives in the threads — chat is the digest.

## Rules

- **Source files are off-limits.** Never edit the annotated file itself unless the user explicitly asks. Sidecar mutations go through the harness only.
- **Don't hand-edit sidecars.** If the harness can't do what you need, surface that — don't reach for raw `Write` on a `.annotations.json`.
- **Don't fabricate annotations.** If `list` returns nothing for the requested scope, stop.
- **Don't reorder, edit, or delete the user's comments.** The harness won't let you, but don't try.
- **Respect `quote` over `anchor`** when locating annotated text — line offsets drift, quoted text is stable.
