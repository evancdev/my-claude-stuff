---
name: viz
description: When user comments on a Figma file Claude has touched, they arrive in context as additionalContext. This skill explains how to respond — revise the diagram via use_figma, don't reflect comment text back.
---

## Handling injected comments

When `additionalContext` appears containing `--- N unread Figma comment(s) on file <key> ---`:

- Read each comment as actionable feedback on the diagram.
- Respond by updating the diagram via `use_figma` (move nodes, change labels, add/remove edges as the comment requests).
- Reply to the user briefly explaining what you changed.
