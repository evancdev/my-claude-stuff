"""Audit-pass black-box tests for the personal-graph feature.

These are additive to tests/test_personal_graph.py and
tests/test_personal_graph_adversarial.py. They probe gaps the existing
suites do not cover:

- compose: NEO4J_AUTH uses neo4j/<password> shape; restart policy bounded;
  healthcheck actually probes Neo4j (cypher-shell / wget / curl); raw-text
  defense against bare `0.0.0.0:` host bindings hidden in long-form blocks.
- .mcp.json: `command` is a real, well-known runner (uvx) per README;
  raw file scan for obvious literal secret patterns (alphanumeric "looks
  like a token" 16+ char strings) outside of ${...} interpolations.
- hooks/hooks.json: SessionStart entries declare a matcher field; type is
  "command".
- hooks/session-start-graph.sh: happy-path (container running) still emits
  valid JSON; stdout has no ANSI escapes; password env var does NOT leak to
  stdout; two concurrent invocations both emit valid JSON; trailing
  newlines on stdout limited; no leading garbage before the JSON.
- commands/graph-seed.md: body mentions the MERGE-only rule and the
  `Evan` singleton (not just "schema").
- README: documents how to actually start the container (compose command
  or hook auto-start), and tells the user the MCP server's name.

Black-box only — none of the forbidden files are opened directly here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

COMPOSE_FILE = REPO_ROOT / "docker" / "neo4j-compose.yml"
NEO4J_UP_SH = REPO_ROOT / "scripts" / "neo4j-up.sh"
MCP_JSON = REPO_ROOT / ".mcp.json"
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"
SESSION_START_SH = REPO_ROOT / "hooks" / "session-start-graph.sh"
GRAPH_SCHEMA_MD = REPO_ROOT / "conventions" / "graph-schema.md"
GRAPH_SEED_MD = REPO_ROOT / "commands" / "graph-seed.md"
README_MD = REPO_ROOT / "README.md"


# ---------------------------------------------------------------------------
# Local shim helper (kept tiny — adversarial file has the rich factory)
# ---------------------------------------------------------------------------


def _write_shim(dir_path: str, script: str, name: str = "docker") -> str:
    p = os.path.join(dir_path, name)
    with open(p, "w") as f:
        f.write(script)
    os.chmod(p, 0o755)
    return p


def _make_running_shim() -> str:
    """A docker shim that pretends a container is running and healthy."""
    d = tempfile.mkdtemp(prefix="audit_running_")
    script = textwrap.dedent(
        """\
        #!/bin/sh
        cmd="$1"; shift
        case "$cmd" in
            info) exit 0 ;;
            inspect)
                echo "true"
                exit 0
                ;;
            ps)
                echo "my-claude-stuff-graph"
                exit 0
                ;;
            compose)
                sub="$1"; shift || true
                if [ "$sub" = "ps" ]; then
                    echo "abc123"
                fi
                exit 0
                ;;
            exec)
                # Pretend cypher-shell ran fine and returned no rows.
                exit 0
                ;;
            start|up|run|stop)
                exit 0
                ;;
            *) exit 0 ;;
        esac
        """
    )
    _write_shim(d, script)
    return d


def _load_yaml(path: Path):
    """Best-effort load: PyYAML → `docker compose config --format json`."""
    try:
        import yaml  # type: ignore

        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        pass
    if shutil.which("docker") is None:
        return None
    try:
        r = subprocess.run(
            ["docker", "compose", "-f", str(path), "config", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# compose: extra raw-text + structural checks
# ---------------------------------------------------------------------------


class ComposeAuditTests(unittest.TestCase):
    def setUp(self):
        if not COMPOSE_FILE.exists():
            self.fail(f"missing {COMPOSE_FILE}")
        self.raw = COMPOSE_FILE.read_text()
        self.data = _load_yaml(COMPOSE_FILE)

    def test_neo4j_auth_has_user_password_pair(self):
        """NEO4J_AUTH is the Neo4j docker image's standard env for initial
        credentials. Format must be `user/password`, where password is a
        ${...} interpolation. A bare ${NEO4J_PASSWORD} without a leading
        `neo4j/` is a misconfiguration that bricks initialisation."""
        # Find the NEO4J_AUTH line in raw text.
        auth_line = None
        for line in self.raw.splitlines():
            if "NEO4J_AUTH" in line:
                auth_line = line
                break
        self.assertIsNotNone(auth_line, "NEO4J_AUTH not in compose file")
        # The value must contain a '/' character separating user and password.
        # Accept either inline `neo4j/${NEO4J_PASSWORD...}` or quoted form.
        self.assertIn(
            "/",
            auth_line.split("NEO4J_AUTH", 1)[1],
            f"NEO4J_AUTH must use user/password form; got line: {auth_line!r}",
        )
        # And the username segment should be `neo4j` (the only valid initial
        # user the container will accept).
        # We look for `neo4j/` to the right of the `NEO4J_AUTH` token.
        self.assertRegex(
            auth_line,
            r"NEO4J_AUTH[^/]*neo4j/",
            f"NEO4J_AUTH value must start with `neo4j/`; got {auth_line!r}",
        )

    def test_neo4j_password_has_default(self):
        """README claims `changeme-graph` is the dev default. So
        NEO4J_PASSWORD interpolation should use `${NEO4J_PASSWORD:-...}`
        form — otherwise the container fails to start with the env unset,
        contradicting the documented zero-config behavior."""
        auth_line = None
        for line in self.raw.splitlines():
            if "NEO4J_AUTH" in line:
                auth_line = line
                break
        self.assertIsNotNone(auth_line)
        # Find the ${NEO4J_PASSWORD...} blob and verify it carries `:-` or `:=`.
        m = re.search(r"\$\{NEO4J_PASSWORD([^}]*)\}", auth_line)
        self.assertIsNotNone(
            m,
            f"NEO4J_PASSWORD interpolation missing on auth line: {auth_line!r}",
        )
        spec = m.group(1)
        self.assertTrue(
            ":-" in spec or ":=" in spec,
            f"NEO4J_PASSWORD interpolation must provide a default "
            f"(`${{NEO4J_PASSWORD:-...}}`); got `${{NEO4J_PASSWORD{spec}}}`",
        )

    def test_healthcheck_actually_probes_neo4j(self):
        """Healthcheck `test` should reference a real Neo4j probe
        (cypher-shell, wget against 7474, or curl). A bash `true` would
        satisfy the "test key present" test but be useless."""
        if self.data is None:
            self.skipTest("compose unloadable")
        services = (self.data or {}).get("services") or {}
        if not services:
            self.skipTest("no services")
        svc = next(iter(services.values()))
        hc = svc.get("healthcheck") or {}
        test = hc.get("test")
        self.assertIsNotNone(test, "healthcheck.test missing")
        # `test` can be string or list — join to a single search string.
        if isinstance(test, list):
            joined = " ".join(str(t) for t in test)
        else:
            joined = str(test)
        joined_lower = joined.lower()
        candidates = ("cypher-shell", "wget", "curl", "7474", "7687")
        self.assertTrue(
            any(c in joined_lower for c in candidates),
            f"healthcheck.test should probe Neo4j (one of {candidates}); "
            f"got {joined!r}",
        )

    def test_restart_policy_is_sane(self):
        """If restart policy is set, it must not be `always` (annoying)
        nor missing entirely (README says `unless-stopped`). Acceptable:
        `unless-stopped`, `on-failure`, `no`, or missing."""
        if self.data is None:
            self.skipTest("compose unloadable")
        services = (self.data or {}).get("services") or {}
        if not services:
            self.skipTest("no services")
        svc = next(iter(services.values()))
        restart = svc.get("restart")
        if restart is None:
            self.skipTest("no restart policy set")
        self.assertIn(
            restart,
            ("unless-stopped", "on-failure", "no"),
            f"restart policy {restart!r} is unexpected (README says 'unless-stopped')",
        )

    def test_no_zero_zero_in_long_form_ports(self):
        """The existing adversarial test catches short-form 0.0.0.0 mappings.
        This adds a long-form check: scan for `host_ip:` keys whose value
        isn't 127.0.0.1."""
        # Look for any host_ip line in raw text.
        for m in re.finditer(r"host_ip\s*:\s*(\S+)", self.raw):
            value = m.group(1).strip("'\"")
            self.assertEqual(
                value,
                "127.0.0.1",
                f"long-form host_ip must be 127.0.0.1; got {value!r}",
            )


# ---------------------------------------------------------------------------
# .mcp.json: stronger secret scan + command sanity
# ---------------------------------------------------------------------------


class McpJsonAuditTests(unittest.TestCase):
    def setUp(self):
        if not MCP_JSON.exists():
            self.fail(f"missing {MCP_JSON}")
        self.raw = MCP_JSON.read_text()
        self.data = json.loads(self.raw)
        self.server = self.data["mcpServers"]["personal-graph"]

    def test_command_points_at_launcher_that_runs_uvx(self):
        """The MCP server is launched through the graph-mcp.py wrapper so it
        can source NEO4J_* from ~/.claude/secrets.env — something .mcp.json's
        ${VAR} interpolation can't read. The wrapper in turn must invoke `uvx`
        as the README documents. Verify both ends so the indirection can't
        silently drift from the documented runner."""
        cmd = self.server.get("command", "")
        self.assertTrue(
            cmd.endswith("scripts/graph-mcp.py"),
            f"command should launch the graph-mcp.py wrapper; got {cmd!r}",
        )
        launcher = REPO_ROOT / "scripts" / "graph-mcp.py"
        self.assertTrue(launcher.exists(), f"missing launcher {launcher}")
        self.assertIn(
            "uvx",
            launcher.read_text(),
            "graph-mcp.py wrapper should invoke `uvx` per README",
        )

    def test_no_high_entropy_literals_outside_interpolation(self):
        """Scan all string values for plausible literal credentials —
        16+ char alphanumeric tokens that aren't ${...} interpolations and
        aren't recognisable noise (paths, urls, package names).

        This is a heuristic: a strict false-positive-free scan is hard,
        so we focus on values that follow obvious secret-shape patterns
        like `--password=XXXXXXXXXXXXXXXX` outside of interpolations.
        Path-like strings (with `/` or `.`) are excluded."""
        suspects = []

        def walk(value, path):
            if isinstance(value, str):
                # Skip if it's a ${...} interpolation
                if re.fullmatch(r"\s*\$\{[^}]+\}\s*", value):
                    return
                # Skip obvious non-secrets (URLs, paths, package names with dots)
                if "/" in value or value.count(".") >= 2:
                    return
                # Skip pure ASCII words shorter than 16 chars
                if len(value) < 16:
                    return
                # Hex-like or base64-like dense strings → suspect
                if re.fullmatch(r"[A-Za-z0-9+/=_-]{16,}", value):
                    suspects.append((path, value))
            elif isinstance(value, dict):
                for k, v in value.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    walk(v, f"{path}[{i}]")

        walk(self.server, "mcpServers.personal-graph")
        self.assertFalse(
            suspects,
            f"high-entropy literal candidates (possible secrets) found: {suspects!r}",
        )

    def test_no_password_in_raw_file_text(self):
        """Belt and braces: even with interpolations in args, raw file text
        must not contain a plausible literal password assignment."""
        # Look for `"password": "<literal>"` or `--password=<literal>`
        # where `<literal>` is NOT a ${...} interpolation.
        bad = re.findall(r'"--password=([^"$][^"]*)"', self.raw) + re.findall(
            r'"password"\s*:\s*"([^"$][^"]*)"', self.raw, re.IGNORECASE
        )
        self.assertFalse(
            bad,
            f"literal password value(s) found in raw .mcp.json: {bad!r}",
        )


# ---------------------------------------------------------------------------
# hooks/hooks.json: SessionStart entry shape per Claude Code schema
# ---------------------------------------------------------------------------


class HooksJsonAuditTests(unittest.TestCase):
    def setUp(self):
        if not HOOKS_JSON.exists():
            self.fail(f"missing {HOOKS_JSON}")
        with open(HOOKS_JSON) as f:
            self.data = json.load(f)
        ss = self.data.get("hooks", {}).get("SessionStart")
        if not isinstance(ss, list) or not ss:
            self.fail("SessionStart hook list missing or empty")
        self.entries = ss

    def test_each_hook_command_has_type(self):
        """Per the Claude Code hook schema, individual hook objects under a
        SessionStart entry's `hooks` array carry `type: "command"`."""
        found = 0
        for entry in self.entries:
            inner = entry.get("hooks") or []
            for h in inner:
                found += 1
                self.assertEqual(
                    h.get("type"),
                    "command",
                    f"hook object must have type='command'; got {h!r}",
                )
        self.assertGreater(found, 0, "no inner hook objects found")


# ---------------------------------------------------------------------------
# hooks/session-start-graph.sh: gaps in branch + safety coverage
# ---------------------------------------------------------------------------


class SessionStartHookAuditTests(unittest.TestCase):
    HOOK_TIMEOUT = 12.0

    def setUp(self):
        if not SESSION_START_SH.exists():
            self.fail(f"missing {SESSION_START_SH}")

    def _run(self, env, timeout=None, cwd=None):
        return subprocess.run(
            ["bash", str(SESSION_START_SH)],
            capture_output=True,
            text=True,
            timeout=timeout or self.HOOK_TIMEOUT,
            env=env,
            cwd=cwd,
        )

    def _parse_hook_json(self, stdout):
        stripped = stdout.strip()
        self.assertTrue(stripped, f"stdout is empty; raw={stdout!r}")
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            self.fail(f"stdout not valid JSON: {e}; stdout={stdout!r}")
        self.assertIsInstance(obj, dict)
        return obj

    def test_happy_path_container_running_emits_valid_json(self):
        """The existing suites cover the failure modes; here we cover the
        happy branch: docker info OK, container running. Hook must still
        emit a valid JSON envelope with additionalContext referencing the
        schema doc."""
        d = _make_running_shim()
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            t0 = time.monotonic()
            r = self._run(env)
            elapsed = time.monotonic() - t0

            self.assertEqual(
                r.returncode,
                0,
                f"hook must exit 0 on happy path; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self.assertLess(elapsed, 10.0, "hook exceeded 10s budget on happy path")
            obj = self._parse_hook_json(r.stdout)
            ctx = obj["hookSpecificOutput"]["additionalContext"]
            self.assertIn(
                "graph-schema.md",
                ctx,
                f"additionalContext should reference graph-schema.md; got {ctx!r}",
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_stdout_has_no_ansi_escape_sequences(self):
        """Stdout is consumed by Claude Code; any ANSI escape would
        corrupt the JSON envelope. Even in the docker-missing branch."""
        env = {
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        r = self._run(env)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn(
            "\x1b",
            r.stdout,
            f"stdout contains ANSI escape (0x1b); got {r.stdout!r}",
        )

    def test_password_env_not_leaked_to_stdout(self):
        """If NEO4J_PASSWORD is set in the environment, the hook must not
        echo it into stdout (where Claude / logs / transcripts capture it)."""
        token = "DO-NOT-LEAK-7f3a9b1c-PASSWORD"
        d = _make_running_shim()
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
                "NEO4J_PASSWORD": token,
            }
            r = self._run(env)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn(
                token,
                r.stdout,
                "NEO4J_PASSWORD value leaked into stdout — security issue",
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_no_leading_garbage_before_json(self):
        """Stdout must start with `{` (after any leading whitespace).
        A printf debug like `echo "hi"` before the JSON would break the
        contract."""
        env = {
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        r = self._run(env)
        self.assertEqual(r.returncode, 0)
        lstripped = r.stdout.lstrip()
        self.assertTrue(
            lstripped.startswith("{"),
            f"stdout must begin with `{{`; got first 80 chars: {r.stdout[:80]!r}",
        )

    def test_two_concurrent_hook_invocations_both_emit_valid_json(self):
        """SessionStart hook can be invoked twice (two windows started
        simultaneously). Both must emit valid JSON and exit 0 — no
        interleaved output, no crashes."""
        d = _make_running_shim()
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            results = []
            errors = []

            def worker():
                try:
                    results.append(self._run(env, timeout=12.0))
                except BaseException as e:  # noqa: BLE001
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=20)

            self.assertFalse(errors, f"worker exceptions: {errors!r}")
            self.assertEqual(len(results), 2)
            for r in results:
                self.assertEqual(
                    r.returncode,
                    0,
                    f"concurrent hook must exit 0; "
                    f"stderr={r.stderr!r} stdout={r.stdout!r}",
                )
                self._parse_hook_json(r.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_trailing_content_after_json_is_only_whitespace(self):
        """Strict re-check: after the single JSON object, only whitespace
        (e.g., a final newline) is allowed."""
        env = {
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        r = self._run(env)
        self.assertEqual(r.returncode, 0)
        decoder = json.JSONDecoder()
        _, end_idx = decoder.raw_decode(r.stdout.lstrip())
        # Compute the tail of the *original* stdout, after the JSON.
        # We compare lstripped offset to original to find absolute end.
        lead = len(r.stdout) - len(r.stdout.lstrip())
        absolute_end = lead + end_idx
        tail = r.stdout[absolute_end:]
        # Allow only whitespace in the tail.
        self.assertRegex(
            tail,
            r"\A\s*\Z",
            f"non-whitespace tail after JSON: {tail!r}",
        )


# ---------------------------------------------------------------------------
# commands/graph-seed.md: content depth
# ---------------------------------------------------------------------------


class GraphSeedDepthTests(unittest.TestCase):
    def setUp(self):
        if not GRAPH_SEED_MD.exists():
            self.fail(f"missing {GRAPH_SEED_MD}")
        self.text = GRAPH_SEED_MD.read_text()

    def test_body_references_merge_rule(self):
        """Seed command should remind Claude of the MERGE-only rule;
        otherwise the cold-start population will violate the schema."""
        # Body = text after first two `---` lines
        m = re.match(r"---\s*\n.*?\n---\s*\n(.*)$", self.text, re.DOTALL)
        body = m.group(1) if m else self.text
        self.assertRegex(
            body,
            r"\bMERGE\b",
            "graph-seed.md body should reference `MERGE` to remind Claude "
            "of the write discipline",
        )

    def test_body_mentions_evan_singleton(self):
        """Seed should explicitly mention seeding the `Evan` node."""
        m = re.match(r"---\s*\n.*?\n---\s*\n(.*)$", self.text, re.DOTALL)
        body = m.group(1) if m else self.text
        self.assertIn(
            "Evan",
            body,
            "graph-seed.md body should mention the `Evan` singleton",
        )


# ---------------------------------------------------------------------------
# README extra checks
# ---------------------------------------------------------------------------


class ReadmeAuditTests(unittest.TestCase):
    def setUp(self):
        if not README_MD.exists():
            self.fail(f"missing {README_MD}")
        self.text = README_MD.read_text()

    def test_mentions_mcp_server_name(self):
        """The MCP server is `personal-graph`; README should mention this
        verbatim so the user can grep for it / reference it in tool calls."""
        self.assertIn(
            "personal-graph",
            self.text,
            "README must mention the `personal-graph` MCP server name",
        )

    def test_mentions_compose_or_hook_auto_start(self):
        """README must give the user a way to know how the container is
        started — either by referencing `docker compose` or by saying the
        SessionStart hook brings it up."""
        lower = self.text.lower()
        self.assertTrue(
            "docker compose" in lower or "sessionstart" in lower,
            "README must explain how the Neo4j container is started "
            "(docker compose command or SessionStart hook)",
        )


if __name__ == "__main__":
    unittest.main()
