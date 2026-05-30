---
name: viz
description: When user comments on a Figma file Claude has touched, they arrive in context as additionalContext. This skill explains how to respond — revise the diagram via use_figma, then reply in-thread via `my-claude viz reply`; don't reflect comment text back.
---

## Handling injected comments

When `additionalContext` appears containing `--- N unread Figma comment(s) on file <key> ---`:

- Read each comment as actionable feedback on the diagram.
- Respond by updating the diagram via `use_figma` (move nodes, change labels, add/remove edges as the comment requests).
- Then close the loop in Figma: reply in the comment's thread with
  `my-claude viz reply <id> "<one-line summary of what you changed>"`, using the
  `id=...` shown in the injected comment. Keep it short and specific (what
  changed) — not a restatement of the comment.
- Reply to the user briefly explaining what you changed.

The reply posts under Claude's own Figma account and is recorded so it won't be
re-surfaced to you as new feedback. If `viz reply` fails (e.g. the account isn't
invited to the file), report that to the user instead of retrying blindly.
