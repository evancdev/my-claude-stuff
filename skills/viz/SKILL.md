---
name: viz
description: When user comments on a Figma file Claude has touched, they arrive in context as additionalContext. This skill explains how to respond — revise the diagram via use_figma, don't reflect comment text back.
---

# Viz

The viz workflow auto-tracks any Figma file Claude creates or edits (via `use_figma`, `generate_diagram`, or `create_new_file`) and surfaces unresolved comments on the next user prompt. No manual setup per drawing — the PostToolUse hook handles it.

## Handling injected comments

When `additionalContext` appears containing `--- N unread Figma comment(s) on file <key> ---`:

- Read each comment as actionable feedback on the diagram.
- Respond by updating the diagram via `use_figma` (move nodes, change labels, add/remove edges as the comment requests).
- Reply to the user briefly explaining what you changed.
- **Do not** reproduce the comment text back at the user — they wrote it.

The tracked file rotates automatically when Claude touches a different Figma file. State persists for 4h of inactivity; after that the comments hook silently no-ops until Claude next interacts with a Figma file.

## Prereqs (one-time per machine)

Save a Figma personal access token so the comments hook can read comments:

```
${CLAUDE_PLUGIN_ROOT}/scripts/set-secret.py FIGMA_PERSONAL_ACCESS_TOKEN
# (or just `set-secret.py FIGMA_PERSONAL_ACCESS_TOKEN` if you've run install-cli.sh)
```

Run from your own terminal (the setter refuses non-tty stdin so the value can't leak into chat history). Generate the Figma PAT at https://www.figma.com/settings → Personal access tokens → Generate new. Required scope: `file_content:read`. Free Figma seats can read comments on personal files; team-owned files may require a paid seat.

If the token isn't set, the comments hook silently no-ops — the rest of the viz workflow (drawing, auto-tracking) still works, just without auto-injected feedback.

## Anti-patterns

- **Don't paste injected comments back to the user.** They wrote them; reflecting them back wastes tokens.
- **Don't manually run `viz-set-current.py`** in your responses — it's already invoked automatically by the PostToolUse hook. Manual use is only for testing or recovery.
- **Don't try to "fix" the TTL** — if state expired, the next time you touch a Figma file the hook re-tracks it.
