---
description: Install the my-claude-stuff statusline into ~/.claude/settings.json (run once per machine).
allowed-tools: Bash
---

Run this exact command and show the stdout/stderr output:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/install-statusline.py"
```

Do not modify files, do not run other commands, do not add commentary. If the script exits 0, tell the user to restart Claude Code (or run `/reload-plugins`) for the statusline to take effect.
