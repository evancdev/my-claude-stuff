# Annotate Tool — Features & Improvements

Backlog of improvements for the annotate tool (Python CLI + VS Code extension). We dogfood the tool by leaving annotations on real files in this repo; ideas that surface during review land here.

## Open

- **`prune` / `delete` subcommands.** No way to remove threads from a sidecar today — "closing" currently requires editing the JSON directly, which bypasses the author-tagging the rest of the CLI enforces. Two shapes:
  - `python3 scripts/annotate.py prune <sidecar>` — drop everything marked `resolved: true`
  - `python3 scripts/annotate.py delete --id <id> <sidecar>` — drop a single thread by ID

- **Inter-thread linking.** Comments can't reference other threads. Painful when a reply is "see thread N" and there's no way to jump there from the VS Code panel. Proposed syntax: `[[<id>]]` in comment body; the VS Code extension renders these as clickable links that scroll to and highlight the target annotation.

## Conventions

- **Append, don't overwrite.** Add new ideas with a `-` bullet under the relevant section.
- **Lead with what's painful.** Each entry should say *why* it's worth doing (what dogfooding moment surfaced the need), not just *what* to build.
- **Resolved items get deleted, not crossed out.** Once shipped, remove the entry — `git log` is the history of what we built.
