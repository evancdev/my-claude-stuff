"""Gap-pass black-box tests for the personal-graph feature.

Additive to test_personal_graph.py, _adversarial.py, _audit.py. Focuses on
contract clauses the existing suites miss:

- compose: plugins volume mount path (`/var/lib/neo4j/plugins`); healthcheck
  references the password via container env (not a hardcoded default that
  silently ignores user overrides).
- .mcp.json: NEO4J_PASSWORD interpolation must NOT be passed via argv
  (process-table leak surface area); env-block delivery preferred.
- hooks/session-start-graph.sh:
  * **Critical:** password value must NOT appear in any subprocess's argv
    when the hook talks to a running container. Verified with a fake docker
    shim that logs every invocation's full argv to a file.
  * Stale stopped container branch (inspect returns "false").
  * Idempotent under repeated invocation (no growing stdout / log mess).
  * No CR / null bytes / non-UTF-8 on stdout.
- conventions/graph-schema.md: edge types are at least mentioned (spec
  calls out "allowed labels, edge types").

Black-box only — none of the forbidden files are opened.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_shim(dir_path: str, script: str, name: str = "docker") -> str:
    p = os.path.join(dir_path, name)
    with open(p, "w") as f:
        f.write(script)
    os.chmod(p, 0o755)
    return p


def _make_argv_logging_running_shim(log_path: str) -> str:
    """A docker shim that pretends the container is running, AND logs each
    invocation's full argv (NUL-delimited) to `log_path`.

    This is what we use to catch password-in-argv leaks: if the hook calls
    `docker exec ... cypher-shell -p $NEO4J_PASSWORD`, the password value
    will land in the log file.
    """
    d = tempfile.mkdtemp(prefix="argv_log_")
    # We use printf with %s and a NUL terminator so we can recover individual
    # argv values unambiguously (passwords could contain spaces).
    script = (
        "#!/bin/sh\n"
        # Log every argument (including $0) NUL-separated, then a record
        # separator newline.
        'for a in "$0" "$@"; do printf "%s\\0" "$a"; done >> ' + json.dumps(log_path) + "\n"
        'printf "\\n" >> ' + json.dumps(log_path) + "\n"
        + textwrap.dedent(
            """\
            cmd="$1"; shift
            case "$cmd" in
                info) exit 0 ;;
                inspect)
                    echo "true"
                    exit 0
                    ;;
                ps)
                    echo "neo4j-personal-graph"
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
                    # Pretend cypher-shell returned no rows.
                    exit 0
                    ;;
                start|up|run|stop)
                    exit 0
                    ;;
                *) exit 0 ;;
            esac
            """
        )
    )
    _write_shim(d, script)
    return d


def _make_stopped_shim() -> str:
    """A docker shim where container exists but is stopped (inspect returns
    "false"). Used to exercise the stale-stopped-container branch of the
    hook (mirroring up.sh coverage)."""
    d = tempfile.mkdtemp(prefix="stopped_")
    script = textwrap.dedent(
        """\
        #!/bin/sh
        cmd="$1"; shift
        case "$cmd" in
            info) exit 0 ;;
            inspect)
                echo "false"
                exit 0
                ;;
            ps)
                exit 0
                ;;
            compose)
                sub="$1"; shift || true
                exit 0
                ;;
            start|up|run) exit 0 ;;
            *) exit 0 ;;
        esac
        """
    )
    _write_shim(d, script)
    return d


def _load_compose():
    try:
        import yaml  # type: ignore
        with open(COMPOSE_FILE) as f:
            return yaml.safe_load(f)
    except Exception:
        pass
    if shutil.which("docker") is None:
        return None
    try:
        r = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Compose: plugins volume mount path + healthcheck password reference
# ---------------------------------------------------------------------------


class ComposeGapTests(unittest.TestCase):
    def setUp(self):
        if not COMPOSE_FILE.exists():
            self.fail(f"missing {COMPOSE_FILE}")
        self.raw = COMPOSE_FILE.read_text()
        self.data = _load_compose()

    def test_plugins_volume_mount_path_present_if_plugins_configured(self):
        """Contract: 'Volume mount paths match Neo4j 5.x layout
        (/var/lib/neo4j/plugins, /data).'

        If NEO4J_PLUGINS is configured (which it should be for apoc/etc.),
        a plugin volume must be mounted at the Neo4j-5.x layout path.
        If NEO4J_PLUGINS is unset, mark this as a skip rather than fail —
        plugins are optional. But the contract names the path explicitly,
        so we still require it to appear in raw text *if* any "plugins"
        mount appears at all.
        """
        if self.data is None:
            self.skipTest("compose unloadable")
        services = (self.data or {}).get("services") or {}
        if not services:
            self.skipTest("no services")
        svc = next(iter(services.values()))
        volumes = svc.get("volumes") or []
        targets = []
        for v in volumes:
            if isinstance(v, str):
                parts = v.split(":")
                if len(parts) >= 2:
                    targets.append(parts[1])
                elif len(parts) == 1:
                    targets.append(parts[0])
            elif isinstance(v, dict):
                t = v.get("target")
                if t:
                    targets.append(t)

        env = svc.get("environment") or {}
        if isinstance(env, list):
            env = dict(
                item.split("=", 1) for item in env if isinstance(item, str) and "=" in item
            )
        has_plugins_env = any(
            isinstance(k, str) and "PLUGINS" in k.upper() for k in env.keys()
        )

        # Any target string containing "plugin" qualifies as a plugins mount.
        plugin_targets = [t for t in targets if "plugin" in t.lower()]

        if not has_plugins_env and not plugin_targets:
            self.skipTest("no plugins env or mount declared")

        # Spec calls out `/var/lib/neo4j/plugins` exactly.
        self.assertTrue(
            any(t == "/var/lib/neo4j/plugins" for t in targets),
            f"plugins should be mounted at /var/lib/neo4j/plugins per Neo4j 5.x "
            f"layout; saw targets={targets!r}",
        )

    def test_healthcheck_uses_env_password_not_hardcoded_literal(self):
        """Spec dimension #10: 'healthcheck definition references the
        password through the container env (not a hardcoded default that
        ignores user overrides).'

        If the healthcheck shells out with `-p <password>`, that password
        must be `$NEO4J_PASSWORD` / `${NEO4J_PASSWORD}` (env-derived),
        NOT a literal like `changeme-graph`. If no `-p` flag appears,
        we accept that (no-auth probe is valid).
        """
        if self.data is None:
            self.skipTest("compose unloadable")
        services = (self.data or {}).get("services") or {}
        if not services:
            self.skipTest("no services")
        svc = next(iter(services.values()))
        hc = svc.get("healthcheck") or {}
        test_cmd = hc.get("test")
        if test_cmd is None:
            self.skipTest("no healthcheck.test")
        if isinstance(test_cmd, list):
            joined = " ".join(str(t) for t in test_cmd)
        else:
            joined = str(test_cmd)

        # Look for a -p flag whose value is NOT an env-var reference.
        # Patterns:
        #   -p PASSWORD          (separate token)
        #   -p PASSWORD          (with quotes)
        #   --password PASSWORD
        #   --password=PASSWORD
        # Accept $NEO4J_PASSWORD or ${NEO4J_PASSWORD} as env-derived.
        # Reject literals (anything else after the flag that doesn't reference env).
        for m in re.finditer(
            r"(?:^|\s)(?:-p|--password)(?:=|\s+)['\"]?([^\s'\"]+)['\"]?",
            joined,
        ):
            val = m.group(1)
            self.assertTrue(
                val.startswith("$") or "${" in val or "$NEO4J" in val,
                f"healthcheck uses literal password {val!r} — must reference "
                f"$NEO4J_PASSWORD via container env. Full test cmd: {joined!r}",
            )


# ---------------------------------------------------------------------------
# .mcp.json: NEO4J_PASSWORD interpolation MUST NOT be in argv
# ---------------------------------------------------------------------------


class McpJsonArgvLeakTests(unittest.TestCase):
    def setUp(self):
        if not MCP_JSON.exists():
            self.fail(f"missing {MCP_JSON}")
        with open(MCP_JSON) as f:
            self.data = json.load(f)
        self.server = self.data["mcpServers"]["personal-graph"]

    def test_password_interpolation_not_in_argv(self):
        """Even when an interpolation is used (so the literal isn't in the
        repo), passing it via argv still puts the resolved value into the
        host process table where any user can read it via `ps`.

        The MCP server's password should be passed via the `env` block
        (delivered via process env, never via argv).
        """
        args = self.server.get("args") or []
        password_in_args = False
        for a in args:
            if not isinstance(a, str):
                continue
            # Catch all common forms.
            if a in ("--password", "-p"):
                password_in_args = True
                break
            if a.startswith("--password="):
                password_in_args = True
                break
            # An arg that *is* the interpolation (e.g. "${NEO4J_PASSWORD}")
            # standalone — almost certainly the password value being passed
            # positionally or after a flag we already counted.
            if re.fullmatch(r"\s*\$\{NEO4J_PASSWORD[^}]*\}\s*", a):
                password_in_args = True
                break
        self.assertFalse(
            password_in_args,
            f"NEO4J_PASSWORD reaches the MCP server via argv, which exposes "
            f"the resolved value in the host process table (`ps -ef`). "
            f"Deliver via env instead. args={args!r}",
        )

    def test_neo4j_password_delivered_via_env(self):
        """Constructive complement: the password should be present in the
        server's `env` block as a ${NEO4J_PASSWORD...} interpolation."""
        env = self.server.get("env") or {}
        interp = re.compile(r"\$\{NEO4J_PASSWORD[^}]*\}")
        found = False
        for k, v in env.items():
            if not isinstance(v, str):
                continue
            if interp.search(v):
                found = True
                break
        self.assertTrue(
            found,
            f"expected an env entry referencing ${{NEO4J_PASSWORD...}}; "
            f"got env={env!r}",
        )


# ---------------------------------------------------------------------------
# Critical: password must NOT leak into subprocess argv via the hook
# ---------------------------------------------------------------------------


class HookPasswordArgvLeakTests(unittest.TestCase):
    """Spec adversarial #9: 'Password must NOT leak into the host's process
    table. When the hook invokes docker exec against a running container,
    the password must not appear in any subprocess's argv.'

    We install a docker shim that logs every invocation's argv to a file,
    set NEO4J_PASSWORD to a unique sentinel, then assert the sentinel never
    appears in the captured argv log.
    """

    HOOK_TIMEOUT = 12.0

    def setUp(self):
        if not SESSION_START_SH.exists():
            self.fail(f"missing {SESSION_START_SH}")

    def _run(self, env, timeout=None):
        return subprocess.run(
            ["bash", str(SESSION_START_SH)],
            capture_output=True,
            text=True,
            timeout=timeout or self.HOOK_TIMEOUT,
            env=env,
        )

    def test_password_never_appears_in_docker_argv(self):
        sentinel = "SENTINEL-d09a3f7b-NEVER-LOG-ME"
        # Use a tempdir for the argv log so we can inspect it after the hook
        # exits.
        log_dir = tempfile.mkdtemp(prefix="argv_log_dir_")
        log_path = os.path.join(log_dir, "docker_argv.log")
        shim_dir = _make_argv_logging_running_shim(log_path)
        try:
            env = {
                "PATH": f"{shim_dir}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
                "NEO4J_PASSWORD": sentinel,
            }
            r = self._run(env)
            self.assertEqual(
                r.returncode, 0,
                f"hook must exit 0; stderr={r.stderr!r} stdout={r.stdout!r}",
            )

            if not os.path.exists(log_path):
                # The hook never invoked docker at all — which trivially
                # satisfies the "no argv leak" property but skips the test.
                self.skipTest(
                    "hook never invoked docker — argv leak path "
                    "untested but not reachable"
                )
            with open(log_path, "rb") as f:
                contents = f.read()
            self.assertNotIn(
                sentinel.encode(),
                contents,
                "NEO4J_PASSWORD value appeared in docker argv — host "
                "process table leak (anyone with `ps` access can read it). "
                f"argv log contents: {contents!r}",
            )
        finally:
            shutil.rmtree(shim_dir, ignore_errors=True)
            shutil.rmtree(log_dir, ignore_errors=True)

    def test_password_never_appears_in_stderr(self):
        """Stderr is unconstrained per the contract, but a leaked password
        in stderr ends up in transcripts and CI logs. Worth a defense check."""
        sentinel = "SENTINEL-stderr-leak-b14c9f7"
        log_dir = tempfile.mkdtemp(prefix="argv_log_stderr_")
        log_path = os.path.join(log_dir, "docker_argv.log")
        shim_dir = _make_argv_logging_running_shim(log_path)
        try:
            env = {
                "PATH": f"{shim_dir}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
                "NEO4J_PASSWORD": sentinel,
            }
            r = self._run(env)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn(
                sentinel, r.stderr,
                "NEO4J_PASSWORD value leaked to stderr — risks transcripts/logs",
            )
        finally:
            shutil.rmtree(shim_dir, ignore_errors=True)
            shutil.rmtree(log_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Hook: stale-stopped-container branch + idempotency + binary safety
# ---------------------------------------------------------------------------


class HookBranchAndSafetyTests(unittest.TestCase):
    HOOK_TIMEOUT = 12.0

    def setUp(self):
        if not SESSION_START_SH.exists():
            self.fail(f"missing {SESSION_START_SH}")

    def _run(self, env, timeout=None):
        return subprocess.run(
            ["bash", str(SESSION_START_SH)],
            capture_output=True,
            text=True,
            timeout=timeout or self.HOOK_TIMEOUT,
            env=env,
        )

    def _assert_hook_json(self, stdout):
        stripped = stdout.strip()
        self.assertTrue(stripped, f"empty stdout: {stdout!r}")
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            self.fail(f"stdout not valid JSON: {e}; stdout={stdout!r}")
        self.assertIsInstance(obj, dict)
        return obj

    def test_stale_stopped_container_branch(self):
        """Spec adversarial #8: a container exists but is stopped. Hook must
        not crash, must emit valid JSON envelope, exit 0, finish quickly."""
        d = _make_stopped_shim()
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
                r.returncode, 0,
                f"hook must exit 0 with stopped container; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self.assertLess(elapsed, 10.0, "hook exceeded 10s budget")
            self._assert_hook_json(r.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_hook_stdout_is_bytewise_identical_on_repeat_invocations(self):
        """The hook should be idempotent: invoking it twice in a row with
        the same environment should produce stdout of the same shape.
        We relax to 'same structural keys + same length-ish' rather than
        byte-identical, because additionalContext can contain a time-sensitive
        substring. The key invariant is that the envelope doesn't drift."""
        env = {
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        r1 = self._run(env)
        r2 = self._run(env)
        self.assertEqual(r1.returncode, 0)
        self.assertEqual(r2.returncode, 0)
        obj1 = self._assert_hook_json(r1.stdout)
        obj2 = self._assert_hook_json(r2.stdout)
        # Same top-level keys
        self.assertEqual(set(obj1.keys()), set(obj2.keys()))
        # Same hookSpecificOutput keys
        self.assertEqual(
            set(obj1["hookSpecificOutput"].keys()),
            set(obj2["hookSpecificOutput"].keys()),
        )
        # Same hookEventName
        self.assertEqual(
            obj1["hookSpecificOutput"]["hookEventName"],
            obj2["hookSpecificOutput"]["hookEventName"],
        )

    def test_stdout_has_no_carriage_return_or_null_byte(self):
        """A `\\r` or `\\x00` in stdout will trip up JSON consumers in odd
        ways even though python's json accepts the surrounding bytes."""
        env = {
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        # Capture stdout as bytes to inspect raw byte content.
        r = subprocess.run(
            ["bash", str(SESSION_START_SH)],
            capture_output=True,
            text=False,
            timeout=self.HOOK_TIMEOUT,
            env=env,
        )
        self.assertEqual(r.returncode, 0)
        self.assertNotIn(
            b"\x00", r.stdout, "stdout contains a NUL byte",
        )
        self.assertNotIn(
            b"\r", r.stdout,
            "stdout contains a carriage return — likely CRLF leak",
        )

    def test_stdout_is_valid_utf8(self):
        """Stdout is consumed by Claude Code as text; it must be valid UTF-8.
        Most shells emit ASCII, but if context includes any human text from
        the DB, it had better round-trip."""
        env = {
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
            "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        r = subprocess.run(
            ["bash", str(SESSION_START_SH)],
            capture_output=True,
            text=False,
            timeout=self.HOOK_TIMEOUT,
            env=env,
        )
        self.assertEqual(r.returncode, 0)
        try:
            r.stdout.decode("utf-8")
        except UnicodeDecodeError as e:
            self.fail(f"stdout is not valid UTF-8: {e}; raw={r.stdout!r}")


# ---------------------------------------------------------------------------
# Schema doc: edge types section
# ---------------------------------------------------------------------------


class GraphSchemaEdgeTypesTests(unittest.TestCase):
    """The feature description names 'allowed labels, edge types' as schema
    content. The existing suite verifies labels; this fills the edge gap."""

    def setUp(self):
        if not GRAPH_SCHEMA_MD.exists():
            self.fail(f"missing {GRAPH_SCHEMA_MD}")
        self.text = GRAPH_SCHEMA_MD.read_text()

    def test_mentions_edges_or_relationships(self):
        """At least the word 'edge', 'edges', or 'relationship(s)' should
        appear somewhere — otherwise the doc only documents nodes."""
        lower = self.text.lower()
        self.assertTrue(
            "edge" in lower or "relationship" in lower or "->" in self.text,
            "graph-schema.md should document edge types / relationships",
        )

    def test_lists_at_least_three_uppercase_rel_types(self):
        """Neo4j convention: relationship types are SCREAMING_SNAKE_CASE.
        A meaningful schema doc lists several. Heuristic: at least 3 distinct
        SCREAMING tokens of length >= 4 appear."""
        # Capture all "uppercase + underscore" tokens >= 4 chars.
        candidates = set(re.findall(r"\b[A-Z][A-Z_]{3,}\b", self.text))
        # Filter out obvious non-types: node labels (we know those are
        # CamelCase, not all caps) and well-known noise.
        noise = {
            "MERGE", "CREATE", "MATCH", "RETURN", "DELETE", "SET", "WITH",
            "UNWIND", "WHERE", "ORDER", "BY", "LIMIT", "EVAN", "TODO",
            "FIXME", "NOTE", "WARNING", "INFO", "BUT", "AND", "ALL",
            "URL", "API", "JSON", "YAML", "MCP", "ISO",
            "NEO4J_PASSWORD", "NEO4J_AUTH", "NEO4J_PLUGINS", "NEO4J_URL",
            "CLAUDE_PLUGIN_ROOT",
        }
        edge_like = {c for c in candidates if c not in noise and "_" in c or len(c) >= 4 and c not in noise}
        # Stronger filter: only tokens that *contain* an underscore are most
        # likely Neo4j rel types (KNOWS, WORKS_AT, ATTENDED, etc.). We also
        # accept single-word ALL_CAPS tokens but require >=5 chars to dodge
        # noise.
        edge_like = {
            c for c in candidates
            if c not in noise and ("_" in c or len(c) >= 5)
        }
        self.assertGreaterEqual(
            len(edge_like), 3,
            f"graph-schema.md should declare at least 3 relationship/edge "
            f"types; saw candidate set: {sorted(edge_like)!r}",
        )


if __name__ == "__main__":
    unittest.main()
