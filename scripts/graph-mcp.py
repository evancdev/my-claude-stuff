#!/usr/bin/env python3
"""Launch the personal-graph MCP server (mcp-neo4j-cypher) with Neo4j
connection settings sourced from ~/.claude/secrets.env.

`.mcp.json`'s ${VAR} interpolation only sees Claude Code's own process
environment — it never reads ~/.claude/secrets.env. This wrapper bridges that
gap: it loads the dotenv secrets the same way the viz hooks do (read_dotenv),
layers in defaults, and execs `uvx mcp-neo4j-cypher` so the server inherits the
NEO4J_* vars in its environment. Store the password once with

    my-claude secret set NEO4J_PASSWORD

instead of exporting it in a shell rc.

Per-key precedence (highest first): existing process env > secrets.env > default.
An already-exported var always wins, so power users can still override via the
real environment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Shared helpers live alongside this script in scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import read_dotenv  # noqa: E402

SECRETS_PATH = Path.home() / ".claude" / "secrets.env"

# uvx fetches and runs the package by name — no local path, so it resolves on
# any machine. Pinned to @latest like the rest of the plugin's uvx usage.
SERVER_ARGV = ["uvx", "mcp-neo4j-cypher@latest", "--transport", "stdio"]

DEFAULTS = {
    "NEO4J_URI": "bolt://localhost:7687",
    "NEO4J_USERNAME": "neo4j",
    "NEO4J_PASSWORD": "changeme-graph",
    "NEO4J_DATABASE": "neo4j",
}


def build_env(process_env, secrets):
    """Resolve the NEO4J_* connection vars with precedence
    process env > secrets.env > built-in default, and return a new env dict
    (a copy of process_env with the resolved NEO4J_* keys layered in).

    A var present-but-empty in process_env is treated as unset so an empty
    export can't silently shadow the stored secret.
    """
    env = dict(process_env)
    for key, default in DEFAULTS.items():
        if env.get(key):
            continue  # an explicitly-exported, non-empty value always wins
        env[key] = secrets.get(key, default)
    return env


def main() -> int:
    env = build_env(os.environ, read_dotenv(SECRETS_PATH))
    try:
        os.execvpe(SERVER_ARGV[0], SERVER_ARGV, env)
    except FileNotFoundError:
        sys.stderr.write(
            "[graph] `uvx` not found on PATH — install uv "
            "(https://docs.astral.sh/uv/) so the personal-graph MCP server "
            "can run.\n"
        )
        return 127
    return 127  # unreachable on a successful exec


if __name__ == "__main__":
    raise SystemExit(main())
