---
name: viz
description: Track the current FigJam file you create/edit so the comments hook can surface user feedback on subsequent prompts. Loads only when you've just used the Figma MCP.
---

# Viz

When you create or edit a Figma file (FigJam `figma.com/board/...`, Design `figma.com/design/...`, or legacy `figma.com/file/...`) via the Figma MCP and get back a URL, run:

```
${CLAUDE_PLUGIN_ROOT}/scripts/viz-set-current.py <url-or-key>
```

That registers the file with the comments hook. For the next 4 hours, every user prompt will surface any unresolved, unseen comments the user has left on that file.

## Handling injected comments

When `additionalContext` appears containing `--- N unread Figma comment(s) ---`:
- Read each comment as actionable feedback on the diagram.
- Respond by updating the diagram via `use_figma` (move nodes, change labels, add/remove edges as the comment requests).
- Reply to the user briefly explaining what you changed. Do not reproduce the comment text back at them — they wrote it.

## Prereqs (one-time per machine)

Save a Figma personal access token so the hook can read comments:

```
${CLAUDE_PLUGIN_ROOT}/scripts/viz-set-token.py
```

Run from your own terminal (the script refuses non-tty stdin so the token can't leak into chat history). It prompts via hidden input and writes `~/.claude/figma-token` with mode 0600. Generate the token at https://www.figma.com/settings (Personal access tokens → Generate new). Required scope: `file_content:read`. Free Figma seats can read comments on personal files; team-owned files may require a paid seat.

The hook falls back to the `FIGMA_PERSONAL_ACCESS_TOKEN` env var if the file isn't present. If neither is set, the hook silently no-ops — the rest of the viz workflow still works, just without auto-injected feedback.

## Anti-patterns

- **Don't paste injected comments back to the user.** They wrote them; reflecting them back wastes tokens.
- **Don't fight the TTL.** If the state expires, just re-run `viz-set-current.py` when you next touch the file.
