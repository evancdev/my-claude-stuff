# Annotate Tool — Features & Improvements

Backlog of improvements for the annotate tool (Python CLI + VS Code extension). We dogfood the tool by leaving annotations on real files in this repo; ideas that surface during review land here.

## Open

- **`prune` / `delete` subcommands.** No way to remove threads from a sidecar today — "closing" currently requires editing the JSON directly, which bypasses the author-tagging the rest of the CLI enforces. Two shapes:
  - `python3 scripts/annotate.py prune <sidecar>` — drop everything marked `resolved: true`
  - `python3 scripts/annotate.py delete --id <id> <sidecar>` — drop a single thread by ID

- **Inter-thread linking.** Comments can't reference other threads. Painful when a reply is "see thread N" and there's no way to jump there from the VS Code panel. Proposed syntax: `[[<id>]]` in comment body; the VS Code extension renders these as clickable links that scroll to and highlight the target annotation.

- **Anchor migration on file edits.** Anchors are coordinate-only (`line_start`/`char_start`/...), so any edit shifts the highlight to whatever text now occupies that range. Caught dogfooding: renaming `## Hard constraints` → `## Constraints` made the "Hard" annotation drift onto characters inside "Constraints". The sidecar already stores the original `quote`; the extension should re-search for it on file load and update coordinates. If not found, surface as **orphaned** (greyed, "anchor lost — was: <quote>") rather than silently mis-highlighting. Add short prefix/suffix context (~32 chars each) to disambiguate when the quote appears multiple times. W3C Web Annotation's `TextQuoteSelector` is the standard model.

- **Differential / incremental list reads.** `list` always dumps the full sidecar — every comment from every thread on every invocation. During active iteration with an LLM dispatcher, the same comments get re-ingested into the context window on every refresh; over a long thread this wastes a meaningful slice of context. Three flags fix it:
  - `list --since <iso-timestamp>` — only comments newer than X (dispatcher tracks last-seen timestamp, asks for delta next turn). This is the load-bearing one.
  - `list --id <id>` — single-thread view, for "I just replied on `bb4510`, what changed?"
  - `list --compact` — strip JSON padding to `[<id>] <author>: <body>` one-liners. Same information, ~3x fewer tokens.

## Conventions

- **Append, don't overwrite.** Add new ideas with a `-` bullet under the relevant section.
- **Lead with what's painful.** Each entry should say *why* it's worth doing (what dogfooding moment surfaced the need), not just *what* to build.
- **Resolved items get deleted, not crossed out.** Once shipped, remove the entry — `git log` is the history of what we built.
