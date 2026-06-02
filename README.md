<h1 align="center">my-claude-stuff</h1>
<p align="center">Evan's personal Claude Code plugin.</p>

## Install

In Claude Code:

```
/plugin marketplace add evancdev/my-claude-stuff
/plugin install my-claude-stuff@personal
```

## CLI

The plugin's helper scripts are exposed through a single `my-claude` command.
Install it once per machine — this symlinks `my-claude` into `~/.local/bin` and
ensures that dir is on your `PATH`:

```
scripts/install-cli.py
```

Open a new shell (or `source` your rc file), then:

```
my-claude help                                  # list commands
my-claude secret set FIGMA_PERSONAL_ACCESS_TOKEN # store a secret (hidden prompt)
my-claude secret list                            # list stored keys (values masked)
my-claude viz set <figma-url-or-key>             # track a Figma file for the comments loop
my-claude viz reply <comment-id> "<message>"     # reply in a comment thread
my-claude statusline install                     # register the statusline
```

To remove the command: `scripts/install-cli.py --uninstall`.

The Figma token (`my-claude secret set FIGMA_PERSONAL_ACCESS_TOKEN`) is only
needed for the viz comments loop; generate one at
https://www.figma.com/settings → Personal access tokens. Reading comments needs
the `file_comments:read` scope; `viz reply` also needs `file_comments:write`
(and the token's account must be invited to the file with comment access).
Drawing diagrams works without any token.

## Personal graph (Neo4j)

A time-aware personal-context graph Claude reads and writes during sessions — people, projects, events, decisions, preferences. Backed by a local Neo4j container; reached via the `personal-graph` MCP server.

### Prereqs

- **Docker Desktop** (or any Docker daemon). The Neo4j container binds to `127.0.0.1:7474` (browser) and `127.0.0.1:7687` (Bolt) only.
- **uvx** ([`uv`](https://docs.astral.sh/uv/)) to run the MCP server: `brew install uv` or `pipx install uv`.

### First-time setup

1. Pick a password and store it with the plugin's secrets CLI (lands in `~/.claude/secrets.env`, mode 0600):
   ```bash
   my-claude secret set NEO4J_PASSWORD     # hidden prompt
   ```
   The dev default `changeme-graph` is fine on a trusted single-user machine, but **set your own** on any host where other processes you don't trust can reach `localhost` (the port is loopback-only, but every process on the box can connect). The container, the SessionStart hook, and the MCP server all read this one value — no shell-rc `export` needed. (An exported `NEO4J_PASSWORD` in the real environment still wins, for power users.)
2. Start Claude Code in any project. The `SessionStart` hook spawns the container in the background — on the first ever launch the ~500MB Neo4j image is pulled (takes minutes on typical broadband), so the graph will report **"configured but not running"** until the next session. After the image is cached, the container starts in ~30s. Subsequent launches are instant once Docker itself is up.
3. Run `/graph-seed` once to seed baseline facts (the `Evan` node, current projects, key people, standing meetings).

### Naming

Three names refer to the same system; useful when grepping:
- Docker container: `my-claude-stuff-graph`
- MCP server (as Claude sees it): `personal-graph`
- Bolt port: `localhost:7687`, Browser: `localhost:7474`

### Customizing

Four connection vars (all optional, all with sensible defaults). Each is resolved with precedence **process env > `~/.claude/secrets.env` > default**, so store them with `my-claude secret set <VAR>` (or export them to override):

| Var              | Default                  | Notes |
|------------------|--------------------------|-------|
| `NEO4J_URI`      | `bolt://localhost:7687`  | Override to point at an external Neo4j. |
| `NEO4J_USERNAME` | `neo4j`                  | |
| `NEO4J_PASSWORD` | `changeme-graph`         | **Set this before the first `compose up`** — Neo4j hashes the password into the data volume on first startup. Changing it later has no effect unless you also run `docker compose down -v` (which deletes all graph data). |
| `NEO4J_DATABASE` | `neo4j`                  | |

### Daily use

- Claude reads the graph proactively at SessionStart (upcoming events surface automatically) and on demand via the `personal-graph` MCP server.
- Claude writes to the graph when something biographical, relational, or time-sensitive comes up. Write rule: `MERGE` only, never `CREATE`. See [`conventions/graph-schema.md`](conventions/graph-schema.md).
- Neo4j Browser at `http://localhost:7474` (user `neo4j`, password = the `NEO4J_PASSWORD` you stored) if you want to poke around manually.

### Stopping / cleanup

The container uses `restart: unless-stopped`, so it will auto-restart when Docker starts (e.g., after reboot). To actually keep it down, use `down` (not just `stop`).

```bash
docker compose -f docker/neo4j-compose.yml down       # stop, keep data
docker compose -f docker/neo4j-compose.yml down -v    # nuke data volumes
```
