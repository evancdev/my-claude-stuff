<h1 align="center">my-claude-stuff</h1>
<p align="center">Evan's personal Claude Code plugin.</p>

## Install

In Claude Code:

```
/plugin marketplace add evancdev/my-claude-stuff
/plugin install my-claude-stuff@personal
```

## Viz (FigJam diagrams + comments loop)

The `viz` skill draws diagrams in FigJam via the Figma MCP and auto-injects any unresolved comments you leave on a diagram into Claude's next prompt. To enable on a new machine:

1. After plugin install, run `/mcp` to authenticate the Figma MCP server.
2. Save a Figma personal access token so the comments hook can read your comments:
   ```
   scripts/set-secret.py FIGMA_PERSONAL_ACCESS_TOKEN
   ```
   Generate at https://www.figma.com/settings → Personal access tokens (scope: `file_content:read`). Required only for the comments loop; drawing works without it.
3. (Optional) Put `scripts/` on your PATH so helpers are callable by name from any terminal:
   ```
   scripts/install-cli.sh
   ```
