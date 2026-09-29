# Claude Annotate

Companion VS Code extension for the `my-claude-stuff` plugin's `/annotate` slash command. Adds a Google-Docs-style highlight-and-comment surface to plan files Claude writes under `.claude/annotate/plan-*.md`, and persists threads to a sidecar `.annotations.json` that `/annotate review` reads back.

## What it does

- Activates on any markdown file whose path matches `**/.claude/annotate/plan-*.md`.
- Enables the gutter "+" / hover "Add Comment" UI on every line of those files.
- Persists annotations to `<plan>.annotations.json` next to the plan file.
- Hydrates threads from the sidecar when a plan file is reopened.
- Re-hydrates when the sidecar changes on disk (e.g. Claude writes a new file).

## Sidecar format

```json
{
  "annotations": [
    {
      "anchor": { "line_start": 12, "line_end": 14, "char_start": 0, "char_end": 42 },
      "quote": "the exact plan text being annotated",
      "comments": [
        { "author": "You", "body": "your note", "timestamp": "2026-05-19T22:11:00.000Z" }
      ]
    }
  ]
}
```

Lines are 1-indexed in the sidecar (so they line up with what editors show). `quote` is the source of truth — if the plan text shifts, the slash command uses `quote` to relocate.

## Develop

```bash
cd vscode-extension
npm install
npm run compile
```

Open `vscode-extension/` in VS Code and press `F5`. An Extension Development Host opens with the parent workspace; navigate to `.claude/annotate/plan-*.md` and the gutter "+" should appear.

## Package

```bash
npx @vscode/vsce package
code --install-extension my-claude-stuff-annotate-0.0.1.vsix
```
