"""TDD test suite for the personal-context Neo4j graph feature.

Tests are written purely from the contract — the implementation files are
not read while writing this suite. Tests exercise:

- docker/neo4j-compose.yml          (validity, security, schema)
- scripts/neo4j-up.sh                (idempotency, no-op paths, stdout clean)
- .mcp.json                          (server definition, no literal secrets)
- hooks/hooks.json                   (SessionStart wiring)
- hooks/session-start-graph.sh       (hook JSON contract, timeouts)
- conventions/graph-schema.md        (content shape)
- commands/graph-seed.md              (frontmatter + body assertions)
- README.md                          (prereqs documented)

Style note: mirrors tests/test_statusline.py — black-box subprocess testing,
helpers on the TestCase, generous `timeout=` everywhere.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
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
GRAPH_SEED_MD = REPO_ROOT / "commands" / "graph-seed.md"
README_MD = REPO_ROOT / "README.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def docker_available() -> bool:
    """True if the `docker` CLI is on PATH (regardless of daemon state)."""
    return shutil.which("docker") is not None


def try_load_compose_via_yaml(path: Path):
    """Try to parse the compose file directly with PyYAML. Returns dict or None."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def try_load_compose_via_docker(path: Path):
    """Parse via `docker compose config --format json`. Returns dict or None."""
    if not docker_available():
        return None
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(path), "config", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def load_compose(path: Path):
    """Best-effort load: try YAML directly first (no daemon needed), then docker."""
    data = try_load_compose_via_yaml(path)
    if data is not None:
        return data, "pyyaml"
    data = try_load_compose_via_docker(path)
    if data is not None:
        return data, "docker"
    return None, None


def make_fake_docker_dir(behavior: str) -> str:
    """Create a tempdir with a fake `docker` shim. Returns the dir path.

    behavior:
      - "info_fails" : `docker info` exits 1, everything else exits 0
      - "all_fail"   : every invocation exits 1
      - "noop_ok"    : every invocation exits 0 with empty stdout
    """
    d = tempfile.mkdtemp(prefix="fake_docker_")
    shim_path = os.path.join(d, "docker")
    if behavior == "info_fails":
        script = textwrap.dedent(
            """\
            #!/bin/sh
            if [ "$1" = "info" ]; then
                echo "Cannot connect to the Docker daemon" 1>&2
                exit 1
            fi
            # `docker compose ps` etc — pretend nothing is running
            if [ "$1" = "compose" ]; then
                # Empty output, success — caller may interpret as "no services up"
                exit 0
            fi
            if [ "$1" = "ps" ]; then
                # Header only, no containers
                echo "CONTAINER ID   IMAGE   COMMAND   STATUS   PORTS   NAMES"
                exit 0
            fi
            exit 0
            """
        )
    elif behavior == "all_fail":
        script = textwrap.dedent(
            """\
            #!/bin/sh
            echo "fake docker: unavailable" 1>&2
            exit 1
            """
        )
    else:  # noop_ok
        script = textwrap.dedent(
            """\
            #!/bin/sh
            exit 0
            """
        )
    with open(shim_path, "w") as f:
        f.write(script)
    os.chmod(shim_path, 0o755)
    return d


# ---------------------------------------------------------------------------
# docker/neo4j-compose.yml
# ---------------------------------------------------------------------------


class ComposeFileTests(unittest.TestCase):
    def setUp(self):
        if not COMPOSE_FILE.exists():
            self.fail(f"compose file missing: {COMPOSE_FILE}")

    @unittest.skipUnless(docker_available(), "docker CLI not on PATH")
    def test_docker_compose_config_validates(self):
        result = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "-q"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"compose config -q failed: stderr={result.stderr!r}",
        )

    def test_compose_loads(self):
        data, source = load_compose(COMPOSE_FILE)
        self.assertIsNotNone(
            data,
            "could not load compose file via PyYAML or `docker compose config`",
        )
        self.assertIsInstance(data, dict)

    def test_exactly_one_service(self):
        data, _ = load_compose(COMPOSE_FILE)
        if data is None:
            self.skipTest("compose unloadable in this env")
        services = data.get("services") or {}
        self.assertEqual(
            len(services),
            1,
            f"expected exactly 1 service, got {list(services.keys())!r}",
        )

    def test_image_is_neo4j_5x(self):
        data, _ = load_compose(COMPOSE_FILE)
        if data is None:
            self.skipTest("compose unloadable in this env")
        services = data.get("services") or {}
        self.assertTrue(services, "no services defined")
        svc = next(iter(services.values()))
        image = svc.get("image", "")
        self.assertTrue(
            image.startswith("neo4j:5."),
            f"image must start with neo4j:5., got {image!r}",
        )

    def test_ports_bind_to_localhost_only(self):
        """Security-critical: 7474 and 7687 must bind 127.0.0.1 only."""
        data, source = load_compose(COMPOSE_FILE)
        if data is None:
            self.skipTest("compose unloadable in this env")
        services = data.get("services") or {}
        svc = next(iter(services.values()))
        ports = svc.get("ports") or []
        self.assertTrue(ports, "service must declare port mappings")

        seen = set()
        for entry in ports:
            host_ip, container_port = self._extract_port_binding(entry, source)
            self.assertEqual(
                host_ip,
                "127.0.0.1",
                f"port mapping {entry!r} must bind 127.0.0.1, got host_ip={host_ip!r}. "
                "Binding to 0.0.0.0 / unspecified exposes Neo4j to the LAN.",
            )
            if container_port is not None:
                seen.add(str(container_port))

        # Spec calls out both 7474 (HTTP) and 7687 (Bolt). Both must be present.
        self.assertIn("7474", seen, f"missing 7474 mapping; saw {seen!r}")
        self.assertIn("7687", seen, f"missing 7687 mapping; saw {seen!r}")

    @staticmethod
    def _extract_port_binding(entry, source):
        """Return (host_ip, container_port_str) from a port entry."""
        # Long-form dict (docker compose normalizes to this)
        if isinstance(entry, dict):
            host_ip = entry.get("host_ip")
            target = entry.get("target")
            published = entry.get("published")
            # Default `docker compose config` fills host_ip="" when missing,
            # which we treat as "not localhost-bound" → fail.
            return host_ip, str(target) if target is not None else (
                str(published) if published is not None else None
            )

        # Short-form string: "[HOST_IP:]HOST_PORT:CONTAINER_PORT[/PROTO]"
        if isinstance(entry, str):
            # Strip /proto suffix
            base = entry.split("/", 1)[0]
            parts = base.split(":")
            if len(parts) == 3:
                host_ip = parts[0]
                container_port = parts[2]
                return host_ip, container_port
            elif len(parts) == 2:
                # No host_ip → unspecified (i.e. 0.0.0.0). Treat as not-localhost.
                return "", parts[1]
            elif len(parts) == 1:
                return "", parts[0]
        return None, None

    def test_neo4j_auth_uses_password_interpolation(self):
        # We assert against the RAW compose source text, not against
        # `docker compose config` output: the latter materializes
        # `${NEO4J_PASSWORD:-default}` to its default, destroying the
        # signal we care about (i.e. "is the interpolation written down?").
        raw = COMPOSE_FILE.read_text()
        # NEO4J_AUTH must appear and must reference ${NEO4J_PASSWORD...}.
        self.assertIn(
            "NEO4J_AUTH",
            raw,
            "compose file must define NEO4J_AUTH env var",
        )
        # The interpolation must appear in the same physical line as NEO4J_AUTH
        # (compose-yaml puts env vars on a single line).
        auth_line = None
        for line in raw.splitlines():
            if "NEO4J_AUTH" in line:
                auth_line = line
                break
        self.assertIsNotNone(auth_line, "NEO4J_AUTH line not found in compose")
        self.assertIn(
            "${NEO4J_PASSWORD",
            auth_line,
            f"NEO4J_AUTH line must reference ${{NEO4J_PASSWORD...}}; got: {auth_line!r}",
        )

    def test_volume_for_data(self):
        """Named volume must cover at least /data so DB state persists."""
        data, _ = load_compose(COMPOSE_FILE)
        if data is None:
            self.skipTest("compose unloadable in this env")
        services = data.get("services") or {}
        svc = next(iter(services.values()))
        volumes = svc.get("volumes") or []
        self.assertTrue(volumes, "service must declare at least one volume")

        # Mounts may be short-form "name:/data" or long-form dicts.
        targets = []
        for v in volumes:
            if isinstance(v, str):
                parts = v.split(":")
                # short form: [source:]target[:mode]
                if len(parts) >= 2:
                    targets.append(parts[1])
                elif len(parts) == 1:
                    targets.append(parts[0])
            elif isinstance(v, dict):
                t = v.get("target")
                if t:
                    targets.append(t)
        self.assertIn(
            "/data",
            targets,
            f"a volume must mount /data; saw targets={targets!r}",
        )

        # And the top-level volumes block should declare the named volume(s).
        # Required only if any short-form used a named source.
        top_volumes = data.get("volumes")
        # Best-effort: if a short-form like "neo4j_data:/data" appears,
        # `neo4j_data` should be a key under top-level `volumes`.
        for v in volumes:
            if isinstance(v, str) and ":" in v:
                source, _, _ = v.partition(":")
                # Skip bind mounts (begin with . or /)
                if source and not source.startswith((".", "/")):
                    self.assertIsInstance(top_volumes, dict)
                    self.assertIn(source, top_volumes or {})

    def test_healthcheck_defined(self):
        data, _ = load_compose(COMPOSE_FILE)
        if data is None:
            self.skipTest("compose unloadable in this env")
        services = data.get("services") or {}
        svc = next(iter(services.values()))
        self.assertIn(
            "healthcheck", svc, "service must define a healthcheck block"
        )
        hc = svc["healthcheck"]
        self.assertIsInstance(hc, dict)
        # Don't over-specify shape; just require a `test` key (compose spec).
        self.assertIn("test", hc, "healthcheck must specify a 'test' command")


# ---------------------------------------------------------------------------
# scripts/neo4j-up.sh
# ---------------------------------------------------------------------------


class Neo4jUpScriptTests(unittest.TestCase):
    def setUp(self):
        if not NEO4J_UP_SH.exists():
            self.fail(f"missing script: {NEO4J_UP_SH}")

    def test_executable_bit_set(self):
        self.assertTrue(
            os.access(NEO4J_UP_SH, os.X_OK),
            f"{NEO4J_UP_SH} is not executable",
        )

    def test_bash_syntax_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(NEO4J_UP_SH)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"bash -n failed: stderr={result.stderr!r}",
        )

    def test_no_op_when_docker_missing(self):
        # env -i strips PATH; restore to minimal toolchain. `which docker`
        # under /usr/bin:/bin should return nothing on macOS/Linux test runners.
        result = subprocess.run(
            ["env", "-i", "PATH=/usr/bin:/bin", "bash", str(NEO4J_UP_SH)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"expected exit 0 when docker is missing; "
            f"stderr={result.stderr!r} stdout={result.stdout!r}",
        )
        # Spec: stdout reserved for the hook contract; this script must keep
        # stdout clean even in the no-op path.
        self.assertEqual(
            result.stdout,
            "",
            f"stdout must be empty in no-op path; got {result.stdout!r}",
        )

    def test_no_op_when_docker_daemon_down(self):
        fake_dir = make_fake_docker_dir("info_fails")
        try:
            env = {
                "PATH": f"{fake_dir}:/usr/bin:/bin",
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            result = subprocess.run(
                ["bash", str(NEO4J_UP_SH)],
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"expected exit 0 when daemon down; "
                f"stderr={result.stderr!r} stdout={result.stdout!r}",
            )
            self.assertEqual(
                result.stdout,
                "",
                f"stdout must be empty when daemon down; got {result.stdout!r}",
            )
        finally:
            shutil.rmtree(fake_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# .mcp.json
# ---------------------------------------------------------------------------


class McpJsonTests(unittest.TestCase):
    def setUp(self):
        if not MCP_JSON.exists():
            self.fail(f"missing: {MCP_JSON}")
        with open(MCP_JSON) as f:
            self.data = json.load(f)  # also asserts valid JSON

    def test_valid_json(self):
        # If setUp() got here, json.load() succeeded
        self.assertIsInstance(self.data, dict)

    def test_personal_graph_server_present(self):
        servers = self.data.get("mcpServers")
        self.assertIsInstance(servers, dict, ".mcp.json must have 'mcpServers' dict")
        self.assertIn(
            "personal-graph",
            servers,
            f"missing 'personal-graph' server; saw {list(servers)!r}",
        )

    def test_personal_graph_has_command_and_args(self):
        server = self.data["mcpServers"]["personal-graph"]
        self.assertIn("command", server)
        self.assertIn("args", server)
        self.assertIsInstance(server["args"], list)

    def test_no_literal_password(self):
        """No literal secrets — only ${...} interpolations may appear near password fields."""
        server = self.data["mcpServers"]["personal-graph"]
        args = server.get("args") or []
        env = server.get("env") or {}

        interp_pat = re.compile(r"^\$\{[^}]+\}$")

        # Walk args looking for "--password" / "-p" followed by a literal.
        for i, a in enumerate(args):
            if not isinstance(a, str):
                continue
            if a in ("--password", "-p"):
                self.assertLess(
                    i + 1,
                    len(args),
                    f"arg {a!r} has no following value",
                )
                nxt = args[i + 1]
                self.assertIsInstance(nxt, str, f"value after {a!r} not a string")
                self.assertRegex(
                    nxt,
                    interp_pat,
                    f"arg following {a!r} must be a ${{...}} interpolation, "
                    f"got literal {nxt!r}",
                )

        # Also defend the combined form: "--password=<value>"
        for a in args:
            if isinstance(a, str) and a.startswith("--password="):
                value = a.split("=", 1)[1]
                self.assertRegex(
                    value,
                    interp_pat,
                    f"--password= must use ${{...}} interpolation; got {value!r}",
                )

        # Env: any key containing PASS or PASSWORD must be an interpolation.
        for k, v in env.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            if "PASSWORD" in k.upper() or "PASS" in k.upper():
                self.assertRegex(
                    v,
                    interp_pat,
                    f"env {k}={v!r} must be a ${{...}} interpolation",
                )


# ---------------------------------------------------------------------------
# hooks/hooks.json
# ---------------------------------------------------------------------------


class HooksJsonTests(unittest.TestCase):
    def setUp(self):
        if not HOOKS_JSON.exists():
            self.fail(f"missing: {HOOKS_JSON}")
        with open(HOOKS_JSON) as f:
            self.data = json.load(f)

    def test_valid_json_with_session_start(self):
        hooks = self.data.get("hooks")
        self.assertIsInstance(hooks, dict, "top-level 'hooks' must be a dict")
        self.assertIn("SessionStart", hooks)
        self.assertIsInstance(hooks["SessionStart"], list)
        self.assertTrue(
            len(hooks["SessionStart"]) >= 1,
            "SessionStart must declare at least one hook",
        )

    def test_hook_commands_reference_plugin_root(self):
        """No absolute paths — must use ${CLAUDE_PLUGIN_ROOT}."""
        ss_hooks = self.data["hooks"]["SessionStart"]
        # Spec allows the matcher/hooks shape. Walk all hooks and pull commands.
        commands = []
        for entry in ss_hooks:
            inner = entry.get("hooks") or []
            for h in inner:
                if "command" in h:
                    commands.append(h["command"])
            # tolerate flat shape too
            if "command" in entry:
                commands.append(entry["command"])

        self.assertTrue(commands, "no commands found in SessionStart hooks")
        for cmd in commands:
            self.assertIn(
                "${CLAUDE_PLUGIN_ROOT}",
                cmd,
                f"hook command must reference ${{CLAUDE_PLUGIN_ROOT}}; got {cmd!r}",
            )
            # No absolute /Users/... or /home/... leaked through
            self.assertNotRegex(
                cmd,
                r"^/(Users|home)/",
                f"hook command starts with hardcoded absolute path: {cmd!r}",
            )


# ---------------------------------------------------------------------------
# hooks/session-start-graph.sh
# ---------------------------------------------------------------------------


class SessionStartHookTests(unittest.TestCase):
    def setUp(self):
        if not SESSION_START_SH.exists():
            self.fail(f"missing: {SESSION_START_SH}")

    def _run_hook(self, env: dict, timeout: float = 10.0) -> subprocess.CompletedProcess:
        # Always run via `bash` explicitly so we don't depend on exec bit alone.
        return subprocess.run(
            ["bash", str(SESSION_START_SH)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

    def _assert_hook_json_shape(self, stdout: str):
        # Stdout must be exactly one JSON object.
        stripped = stdout.strip()
        self.assertTrue(stripped, "stdout was empty")
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            self.fail(f"stdout is not valid JSON: {e}; stdout={stdout!r}")
        self.assertIsInstance(obj, dict, "top-level JSON must be an object")

        # Reject any non-whitespace tail after the JSON object — i.e. only
        # one JSON value on stdout, possibly with a trailing newline.
        decoder = json.JSONDecoder()
        _, end_idx = decoder.raw_decode(stripped)
        tail = stripped[end_idx:].strip()
        self.assertEqual(tail, "", f"unexpected trailing content after JSON: {tail!r}")

        # Required shape
        hso = obj.get("hookSpecificOutput")
        self.assertIsInstance(
            hso,
            dict,
            f"missing 'hookSpecificOutput' object; got {obj!r}",
        )
        self.assertEqual(
            hso.get("hookEventName"),
            "SessionStart",
            f"hookEventName must be 'SessionStart'; got {hso.get('hookEventName')!r}",
        )
        ctx = hso.get("additionalContext")
        self.assertIsInstance(
            ctx, str, f"additionalContext must be a string; got {type(ctx)}"
        )
        self.assertTrue(ctx, "additionalContext must be non-empty")
        return obj, ctx

    def test_docker_missing_path(self):
        # PATH without docker
        env = {
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        t0 = time.monotonic()
        result = self._run_hook(env, timeout=10.0)
        elapsed = time.monotonic() - t0

        self.assertEqual(
            result.returncode,
            0,
            f"hook must exit 0; stderr={result.stderr!r} stdout={result.stdout!r}",
        )
        self.assertLess(elapsed, 10.0, "hook took too long with docker missing")
        _, ctx = self._assert_hook_json_shape(result.stdout)
        # Schema doc reference
        self.assertIn(
            "graph-schema.md",
            ctx,
            f"additionalContext should reference graph-schema.md; got {ctx!r}",
        )

    def test_docker_daemon_down_signals_dormant_state(self):
        fake_dir = make_fake_docker_dir("info_fails")
        try:
            env = {
                "PATH": f"{fake_dir}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            t0 = time.monotonic()
            result = self._run_hook(env, timeout=10.0)
            elapsed = time.monotonic() - t0

            self.assertEqual(
                result.returncode,
                0,
                f"hook must exit 0 when daemon down; "
                f"stderr={result.stderr!r} stdout={result.stdout!r}",
            )
            self.assertLess(
                elapsed, 10.0, "hook took too long with daemon down"
            )
            _, ctx = self._assert_hook_json_shape(result.stdout)

            # Spec: assert the context signals dormant state. The spec
            # offered "not running", "dormant", or "configured" as candidate
            # substrings — accept any of them (case-insensitive). If the
            # implementation phrased it differently, this is a flaggable miss.
            ctx_lower = ctx.lower()
            candidates = ("not running", "dormant", "configured", "unavailable", "down")
            self.assertTrue(
                any(c in ctx_lower for c in candidates),
                f"additionalContext should signal dormant state "
                f"(expected one of {candidates}); got {ctx!r}",
            )
        finally:
            shutil.rmtree(fake_dir, ignore_errors=True)

    def test_stdout_is_one_json_object_only(self):
        # Sanity re-check on the missing-docker path: no extra lines / output.
        env = {
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        result = self._run_hook(env, timeout=10.0)
        self.assertEqual(result.returncode, 0)
        self._assert_hook_json_shape(result.stdout)


# ---------------------------------------------------------------------------
# conventions/graph-schema.md
# ---------------------------------------------------------------------------


class GraphSchemaDocTests(unittest.TestCase):
    NODE_LABELS = ["Person", "Org", "Project", "Event", "Decision", "Preference", "Topic"]

    def setUp(self):
        if not GRAPH_SCHEMA_MD.exists():
            self.fail(f"missing: {GRAPH_SCHEMA_MD}")
        self.text = GRAPH_SCHEMA_MD.read_text()

    def test_lists_all_node_labels(self):
        for label in self.NODE_LABELS:
            with self.subTest(label=label):
                # Word-boundary, case-sensitive
                self.assertRegex(
                    self.text,
                    rf"\b{re.escape(label)}\b",
                    f"node label {label!r} not mentioned in graph-schema.md",
                )

    def test_merge_only_rule(self):
        # Accept either "MERGE only" or "never CREATE" (case-insensitive).
        lower = self.text.lower()
        self.assertTrue(
            ("merge only" in lower) or ("never create" in lower),
            "graph-schema.md must state the MERGE-only / never-CREATE write rule",
        )

    def test_singleton_evan_person(self):
        # Find a quoted Evan (single or double quotes) near "singleton" or "implicit".
        # We use a fuzzy window: within 200 chars of each other.
        quoted = list(re.finditer(r"""['"]Evan['"]""", self.text))
        self.assertTrue(
            quoted,
            "graph-schema.md must mention 'Evan' (or \"Evan\") as a quoted identifier",
        )
        keyword = re.compile(r"\b(singleton|implicit)\b", re.IGNORECASE)
        kw_matches = list(keyword.finditer(self.text))
        self.assertTrue(
            kw_matches,
            "graph-schema.md must use 'singleton' or 'implicit' near the Evan node",
        )

        def close(a_match, b_match, window=200):
            return abs(a_match.start() - b_match.start()) <= window

        close_pairs = [
            (q, k) for q in quoted for k in kw_matches if close(q, k)
        ]
        self.assertTrue(
            close_pairs,
            "quoted 'Evan' must appear near 'singleton' or 'implicit' "
            "(within 200 chars)",
        )


# ---------------------------------------------------------------------------
# commands/graph-seed.md
# ---------------------------------------------------------------------------


class GraphSeedCommandTests(unittest.TestCase):
    def setUp(self):
        if not GRAPH_SEED_MD.exists():
            self.fail(f"missing: {GRAPH_SEED_MD}")
        self.text = GRAPH_SEED_MD.read_text()

    def test_has_frontmatter(self):
        self.assertTrue(
            self.text.startswith("---"),
            "graph-seed.md must start with YAML frontmatter (`---`)",
        )

    def _frontmatter(self) -> str:
        # Extract everything between the first two `---` lines.
        m = re.match(r"---\s*\n(.*?)\n---\s*\n", self.text, re.DOTALL)
        if not m:
            self.fail("could not locate closing `---` of YAML frontmatter")
        return m.group(1)

    def test_frontmatter_has_description(self):
        fm = self._frontmatter()
        self.assertRegex(
            fm,
            r"(?m)^description\s*:",
            "frontmatter missing `description:` key",
        )

    def test_frontmatter_has_allowed_tools_with_mcp_personal_graph(self):
        fm = self._frontmatter()
        self.assertRegex(
            fm,
            r"(?m)^allowed-tools\s*:",
            "frontmatter missing `allowed-tools:` key",
        )
        # The MCP tool naming convention is mcp__personal-graph__<tool>.
        self.assertRegex(
            fm,
            r"mcp__personal-graph__\w+",
            "allowed-tools must list at least one mcp__personal-graph__* tool",
        )

    def test_body_references_schema(self):
        # Body = text after closing `---`
        m = re.match(r"---\s*\n.*?\n---\s*\n(.*)$", self.text, re.DOTALL)
        body = m.group(1) if m else self.text
        ok = (
            "graph-schema.md" in body
            or re.search(r"read[^\n]*schema", body, re.IGNORECASE) is not None
        )
        self.assertTrue(
            ok,
            "graph-seed.md body must reference reading the schema "
            "(either 'graph-schema.md' or a phrase like 'read ... schema')",
        )


# ---------------------------------------------------------------------------
# README.md
# ---------------------------------------------------------------------------


class ReadmeTests(unittest.TestCase):
    def setUp(self):
        if not README_MD.exists():
            self.fail(f"missing: {README_MD}")
        self.text = README_MD.read_text()

    def test_mentions_docker_prereq(self):
        self.assertRegex(
            self.text,
            r"\bDocker\b",
            "README should mention Docker as a prereq",
        )

    def test_mentions_uv_or_uvx(self):
        self.assertRegex(
            self.text,
            r"\buvx?\b",
            "README should mention `uv` or `uvx` as a prereq",
        )

    def test_mentions_neo4j_password_env_var(self):
        self.assertIn(
            "NEO4J_PASSWORD",
            self.text,
            "README must mention NEO4J_PASSWORD",
        )

    def test_mentions_graph_seed_command(self):
        self.assertIn(
            "/graph-seed",
            self.text,
            "README must mention the /graph-seed command",
        )


if __name__ == "__main__":
    unittest.main()
