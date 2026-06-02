"""Unit tests for the personal-graph MCP launcher (scripts/graph-mcp.py).

The launcher exists because .mcp.json's ${VAR} interpolation can only read
Claude Code's process environment — never ~/.claude/secrets.env. The launcher
bridges that gap: it loads the dotenv secrets, resolves the NEO4J_* connection
vars with precedence process env > secrets.env > default, and execs `uvx
mcp-neo4j-cypher` with them in the environment.

These tests exercise that resolution logic directly (build_env) plus the
file's executable/shebang contract. They never spawn uvx.
"""

from __future__ import annotations

import importlib.util
import stat
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "scripts" / "graph-mcp.py"


def _load():
    spec = importlib.util.spec_from_file_location("graph_mcp", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LauncherFileContractTests(unittest.TestCase):
    def test_exists(self):
        self.assertTrue(LAUNCHER.exists(), f"missing launcher {LAUNCHER}")

    def test_is_executable(self):
        mode = LAUNCHER.stat().st_mode
        self.assertTrue(
            mode & stat.S_IXUSR,
            "graph-mcp.py must be executable — .mcp.json runs it as `command`",
        )

    def test_has_python_shebang(self):
        first = LAUNCHER.read_text().splitlines()[0]
        self.assertTrue(
            first.startswith("#!") and "python" in first,
            f"expected a python shebang; got {first!r}",
        )

    def test_runner_is_uvx(self):
        mod = _load()
        self.assertEqual(mod.SERVER_ARGV[0], "uvx")
        self.assertIn("mcp-neo4j-cypher", " ".join(mod.SERVER_ARGV))


class BuildEnvPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_default_when_nothing_set(self):
        env = self.mod.build_env({}, {})
        for key, default in self.mod.DEFAULTS.items():
            self.assertEqual(env[key], default, f"{key} should fall back to default")

    def test_secrets_fill_when_env_absent(self):
        env = self.mod.build_env({}, {"NEO4J_PASSWORD": "from-secrets"})
        self.assertEqual(env["NEO4J_PASSWORD"], "from-secrets")
        # Untouched keys still get their defaults.
        self.assertEqual(env["NEO4J_USERNAME"], self.mod.DEFAULTS["NEO4J_USERNAME"])

    def test_process_env_wins_over_secrets(self):
        env = self.mod.build_env(
            {"NEO4J_PASSWORD": "from-env"},
            {"NEO4J_PASSWORD": "from-secrets"},
        )
        self.assertEqual(
            env["NEO4J_PASSWORD"],
            "from-env",
            "an explicitly-exported var must win over secrets.env",
        )

    def test_empty_process_env_value_does_not_shadow_secret(self):
        env = self.mod.build_env(
            {"NEO4J_PASSWORD": ""},
            {"NEO4J_PASSWORD": "from-secrets"},
        )
        self.assertEqual(
            env["NEO4J_PASSWORD"],
            "from-secrets",
            "an empty export should be treated as unset, not as an override",
        )

    def test_unrelated_env_preserved_and_input_not_mutated(self):
        process_env = {"PATH": "/usr/bin", "SOMETHING": "x"}
        env = self.mod.build_env(process_env, {})
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["SOMETHING"], "x")
        # build_env returns a new dict; the caller's env is untouched.
        self.assertNotIn("NEO4J_PASSWORD", process_env)

    def test_all_four_connection_vars_resolved(self):
        env = self.mod.build_env({}, {})
        for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"):
            self.assertIn(key, env)

    def test_secrets_path_is_user_dotclaude(self):
        # The launcher must read the same secrets file `my-claude secret set`
        # writes — ~/.claude/secrets.env.
        self.assertEqual(
            self.mod.SECRETS_PATH,
            Path.home() / ".claude" / "secrets.env",
        )


if __name__ == "__main__":
    unittest.main()
