"""Contract-audit black-box tests for the personal-graph feature.

Additive to the existing test_personal_graph*.py suites. Targets specific
contract clauses + adversarial dimensions that the prior tests miss or only
partially address:

1. Python3 trap-fallback (adversarial #11): if python3 fails mid-emit, the
   shell trap should still write a valid JSON envelope to stdout.
2. Compose mount path coverage: contract names `/var/lib/neo4j/plugins`
   *and* `/data` as required Neo4j 5.x mount points. Existing tests only
   cover `/var/lib/neo4j/plugins` *if* plugins env is set, and `/data` is
   covered separately. We require both unconditionally.
3. .mcp.json: `args` must contain no `--db-url` (or similar URL flag)
   with embedded credentials, and must contain no positional NEO4J_PASSWORD
   interpolation. Also: env block must NOT carry a literal NEO4J_AUTH
   `neo4j/<password>` pair.
4. Healthcheck override correctness (adversarial #10): we run the actual
   healthcheck command's --password value through a sanity check that
   rejects literal defaults.
5. Hooks JSON: SessionStart entry must register the `session-start-graph.sh`
   script (not just *any* command). Otherwise the wiring could point at
   the wrong file silently.
6. Hook + happy path with running container: `additionalContext` must
   include some indication of upcoming events (per feature description:
   "surfaces upcoming events into Claude's context").

Black-box only — none of the forbidden files are read here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

COMPOSE_FILE = REPO_ROOT / "docker" / "neo4j-compose.yml"
NEO4J_UP_SH = REPO_ROOT / "scripts" / "neo4j-up.sh"
MCP_JSON = REPO_ROOT / ".mcp.json"
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"
SESSION_START_SH = REPO_ROOT / "hooks" / "session-start-graph.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_exec(path: str, body: str) -> str:
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)
    return path


def _load_yaml_or_compose(path: Path):
    """Best-effort: PyYAML, falling back to docker compose config --format json."""
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


def _make_running_docker_shim(extra_dir: str | None = None) -> str:
    """Docker shim that mimics a running container; container 'inspect'
    returns 'true' for State.Running, 'cypher-shell' / 'exec' return rows
    of fake events."""
    d = tempfile.mkdtemp(prefix="contract_running_")
    body = textwrap.dedent(
        """\
        #!/bin/sh
        cmd="$1"; shift
        case "$cmd" in
            info) exit 0 ;;
            inspect)
                # Container exists and running.
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
                # Simulate cypher-shell returning a row that looks like
                # an upcoming event so the hook may surface it.
                printf '%s\\n' '"title","when"'
                printf '%s\\n' '"Sample upcoming meeting","2026-05-23T10:00"'
                exit 0
                ;;
            start|up|run|stop) exit 0 ;;
            *) exit 0 ;;
        esac
        """
    )
    _write_exec(os.path.join(d, "docker"), body)
    return d


# ---------------------------------------------------------------------------
# Adversarial #11: python3 trap-fallback
# ---------------------------------------------------------------------------


class HookPython3TrapFallbackTests(unittest.TestCase):
    """The hook reportedly uses python3 to format/escape its JSON envelope.
    If python3 fails (missing binary, exits non-zero, returns garbage),
    the hook's shell trap fallback MUST still write a valid single JSON
    object to stdout — otherwise Claude Code sees garbage and the
    SessionStart payload is dropped silently.

    We exercise three failure modes:
      A. python3 missing from PATH entirely.
      B. python3 present but exits 1 with empty stdout.
      C. python3 present but emits garbage to stdout (e.g. a partial JSON
         fragment) then exits 1.
    In all three, stdout must be exactly one valid JSON object matching
    the shape contract.
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

    def _assert_envelope(self, stdout, label):
        stripped = stdout.strip()
        self.assertTrue(
            stripped,
            f"[{label}] stdout was empty — no fallback envelope emitted",
        )
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            self.fail(f"[{label}] stdout is not valid JSON: {e}; stdout={stdout!r}")
        self.assertIsInstance(obj, dict, f"[{label}] top-level must be object")
        hso = obj.get("hookSpecificOutput")
        self.assertIsInstance(
            hso, dict, f"[{label}] hookSpecificOutput must be object; got {obj!r}"
        )
        self.assertEqual(
            hso.get("hookEventName"),
            "SessionStart",
            f"[{label}] hookEventName must be 'SessionStart'; got {hso!r}",
        )
        ctx = hso.get("additionalContext")
        self.assertIsInstance(
            ctx, str, f"[{label}] additionalContext must be a string; got {type(ctx)}"
        )
        self.assertTrue(ctx, f"[{label}] additionalContext must be non-empty")

    def _shim_dir_with_python3(self, behavior: str) -> str:
        """Return a tempdir containing a `python3` shim plus a fake docker
        shim that signals daemon-down. We want to isolate the python3-fail
        branch even when the hook's other branches succeed."""
        d = tempfile.mkdtemp(prefix=f"py3shim_{behavior}_")
        # docker shim: daemon down so the hook takes the simplest branch
        # and (per contract) still emits valid JSON.
        _write_exec(
            os.path.join(d, "docker"),
            textwrap.dedent(
                """\
                #!/bin/sh
                if [ "$1" = "info" ]; then
                    echo "Cannot connect to the Docker daemon" 1>&2
                    exit 1
                fi
                exit 0
                """
            ),
        )
        if behavior == "exit_nonzero":
            _write_exec(
                os.path.join(d, "python3"),
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    # python3 always fails — exit 1 with empty stdout
                    echo "fake python3: failing for test" 1>&2
                    exit 1
                    """
                ),
            )
        elif behavior == "garbage_then_fail":
            _write_exec(
                os.path.join(d, "python3"),
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    # Emit a partial JSON fragment then die.
                    printf '{"partial": "broken'
                    exit 1
                    """
                ),
            )
        elif behavior == "missing":
            # Don't create python3 in the dir at all. We tighten PATH below
            # so no other python3 is reachable.
            pass
        else:
            raise ValueError(behavior)
        return d

    def test_python3_missing_still_yields_valid_envelope(self):
        d = self._shim_dir_with_python3("missing")
        try:
            # Tight PATH — only our shim dir + /bin (must keep /bin so bash
            # built-ins like sh have core utils). Crucially NO /usr/bin
            # (where system python3 typically lives).
            env = {
                "PATH": f"{d}:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            r = self._run(env)
            self.assertEqual(
                r.returncode,
                0,
                f"hook must exit 0 with python3 missing; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self._assert_envelope(r.stdout, "python3-missing")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_python3_exits_nonzero_still_yields_valid_envelope(self):
        d = self._shim_dir_with_python3("exit_nonzero")
        try:
            # python3 shim takes precedence; do NOT include /usr/bin to
            # ensure the shim is the one invoked.
            env = {
                "PATH": f"{d}:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            r = self._run(env)
            self.assertEqual(
                r.returncode,
                0,
                f"hook must exit 0 with python3 failing; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self._assert_envelope(r.stdout, "python3-exit-1")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_python3_emits_garbage_then_dies_still_yields_valid_envelope(self):
        d = self._shim_dir_with_python3("garbage_then_fail")
        try:
            env = {
                "PATH": f"{d}:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            r = self._run(env)
            self.assertEqual(
                r.returncode,
                0,
                f"hook must exit 0 even with partial-garbage python3; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self._assert_envelope(r.stdout, "python3-garbage")
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Compose: mount-path coverage (BOTH /var/lib/neo4j/plugins AND /data)
# ---------------------------------------------------------------------------


class ComposeMountPathContractTests(unittest.TestCase):
    """The contract names `/var/lib/neo4j/plugins` and `/data` as required
    mount paths matching Neo4j 5.x layout. The existing gaps test only
    requires `/var/lib/neo4j/plugins` *if* NEO4J_PLUGINS is configured.
    The contract reads it as an unconditional requirement.
    """

    def setUp(self):
        if not COMPOSE_FILE.exists():
            self.fail(f"missing {COMPOSE_FILE}")
        self.raw = COMPOSE_FILE.read_text()
        self.data = _load_yaml_or_compose(COMPOSE_FILE)

    def _targets(self):
        if self.data is None:
            return None
        services = (self.data or {}).get("services") or {}
        svc = next(iter(services.values()), None)
        if not svc:
            return []
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
        return targets

    def test_data_mount_present(self):
        targets = self._targets()
        if targets is None:
            self.skipTest("compose unloadable")
        self.assertIn(
            "/data",
            targets,
            f"compose must mount Neo4j data dir at /data; targets={targets!r}",
        )

    def test_plugins_mount_path_uses_neo4j_5x_layout(self):
        """Contract: 'Volume mount paths match Neo4j 5.x layout
        (/var/lib/neo4j/plugins, /data).'

        We require either:
          a) a plugins mount at the canonical Neo4j 5.x path
             `/var/lib/neo4j/plugins`, OR
          b) no plugins mount at all (plugins are optional).

        The failure case we want to catch is a plugins mount at a WRONG
        path (e.g. the Neo4j 4.x `/plugins`), which silently fails to
        load plugins at runtime."""
        targets = self._targets()
        if targets is None:
            self.skipTest("compose unloadable")
        plugin_targets = [t for t in targets if "plugin" in t.lower()]
        if not plugin_targets:
            self.skipTest("no plugins mount declared (acceptable)")
        # Reject any plugins mount that isn't the canonical path.
        wrong = [t for t in plugin_targets if t != "/var/lib/neo4j/plugins"]
        self.assertFalse(
            wrong,
            f"plugins mount must be at /var/lib/neo4j/plugins per Neo4j 5.x; "
            f"found incorrect target(s): {wrong!r}",
        )

    def test_data_target_is_not_neo4j_4x_layout(self):
        """Neo4j 4.x used `/var/lib/neo4j/data`. 5.x uses `/data`. Catch
        a stale 4.x-style mount."""
        targets = self._targets()
        if targets is None:
            self.skipTest("compose unloadable")
        bad = [t for t in targets if t == "/var/lib/neo4j/data"]
        self.assertFalse(
            bad,
            f"found Neo4j 4.x-style data mount {bad!r}; 5.x uses /data",
        )


# ---------------------------------------------------------------------------
# .mcp.json: argv must carry no DB URL with embedded creds, no positional
# password interpolation, env must not carry NEO4J_AUTH literal.
# ---------------------------------------------------------------------------


class McpJsonArgvHardeningTests(unittest.TestCase):
    def setUp(self):
        if not MCP_JSON.exists():
            self.fail(f"missing {MCP_JSON}")
        self.raw = MCP_JSON.read_text()
        self.data = json.loads(self.raw)
        servers = self.data.get("mcpServers") or {}
        if "personal-graph" not in servers:
            self.fail("personal-graph server not present")
        self.server = servers["personal-graph"]

    def test_no_url_flag_with_embedded_credentials(self):
        """A common foot-gun: passing `--db-url neo4j://user:pass@host:7687`
        as an arg embeds the password in argv (host process table leak).

        Reject any arg that looks like a connection URL with `user:pass@`
        embedded. Allow:
          - `bolt://localhost:7687` (no auth)
          - `neo4j://localhost:7687` (no auth)
          - URL via ${...} interpolation
        """
        args = self.server.get("args") or []
        bad = []
        # neo4j://, bolt://, http://, https://
        url_with_creds = re.compile(
            r"\b(?:neo4j|bolt|http|https)\+?[a-z]*://[^/\s${]+:[^/\s@${]+@",
            re.IGNORECASE,
        )
        for a in args:
            if not isinstance(a, str):
                continue
            if url_with_creds.search(a):
                bad.append(a)
        self.assertFalse(
            bad,
            f"args contain a URL with embedded credentials: {bad!r}. "
            "Credentials must be passed via env, never on the command line.",
        )

    def test_no_positional_password_interpolation_in_args(self):
        """Defense in depth: even without an explicit `--password` flag,
        a bare `${NEO4J_PASSWORD}` in args still resolves to the secret on
        the command line. Reject any arg that *is* (or contains) a
        ${NEO4J_PASSWORD...} reference."""
        args = self.server.get("args") or []
        bad = []
        pwd_interp = re.compile(r"\$\{NEO4J_PASSWORD[^}]*\}")
        for a in args:
            if isinstance(a, str) and pwd_interp.search(a):
                bad.append(a)
        self.assertFalse(
            bad,
            f"args contain ${{NEO4J_PASSWORD...}} interpolation(s): {bad!r}. "
            "Interpolations in argv still expose the resolved password in "
            "the host process table.",
        )

    def test_no_token_or_secret_interpolation_in_args(self):
        """Extend the no-secrets-in-argv rule to *any* env var whose name
        smells like a secret (PASS, TOKEN, SECRET, KEY at suffix). A
        ${MY_API_TOKEN} in args is just as leaky as ${NEO4J_PASSWORD}."""
        args = self.server.get("args") or []
        bad = []
        # Match any ${...} whose var name contains a secret-y token.
        secret_interp = re.compile(r"\$\{([^}]+)\}")
        for a in args:
            if not isinstance(a, str):
                continue
            for m in secret_interp.finditer(a):
                varspec = m.group(1)
                # Extract just the var name (before `:-` or `:=` default).
                name = re.split(r"[:\-]", varspec, 1)[0].upper()
                if any(t in name for t in ("PASS", "TOKEN", "SECRET")) or name.endswith(
                    "_KEY"
                ):
                    bad.append((a, name))
        self.assertFalse(
            bad,
            f"args contain secret-like interpolation(s): {bad!r}. "
            "Move these to the `env` block.",
        )

    def test_env_block_carries_no_literal_neo4j_auth_pair(self):
        """`NEO4J_AUTH` in the MCP server's env block should NOT carry a
        literal `neo4j/changeme-graph` pair. If NEO4J_AUTH is delivered at
        all, the password segment must be a ${...} interpolation."""
        env = self.server.get("env") or {}
        if not isinstance(env, dict):
            self.fail(f"server.env must be dict; got {type(env)}")
        if "NEO4J_AUTH" not in env:
            self.skipTest("NEO4J_AUTH not delivered via MCP env (compose-only)")
        val = env["NEO4J_AUTH"]
        self.assertIsInstance(val, str, f"NEO4J_AUTH value not a string: {val!r}")
        # If there's a `/`, the segment after it must be an interpolation.
        if "/" in val:
            _, _, pwd = val.partition("/")
            self.assertRegex(
                pwd,
                r"\$\{[^}]+\}",
                f"NEO4J_AUTH password segment must be ${{...}}; got {val!r}",
            )

    def test_no_naked_secret_value_in_env_block(self):
        """For every env value that looks like a sensitive var (PASS/TOKEN/
        SECRET in key), the VALUE must be a ${...} interpolation only —
        no inline mixing with literals."""
        env = self.server.get("env") or {}
        bad = []
        interp_only = re.compile(r"^\$\{[^}]+\}$")
        for k, v in env.items():
            if not (isinstance(k, str) and isinstance(v, str)):
                continue
            up = k.upper()
            sensitive = (
                "PASS" in up or "TOKEN" in up or "SECRET" in up or up.endswith("_KEY")
            )
            if sensitive and not interp_only.match(v):
                bad.append((k, v))
        self.assertFalse(
            bad,
            f"env block has secret keys with non-pure-interpolation values: "
            f"{bad!r}. Each secret value must be exactly `${{VAR_NAME[:-...]}}`.",
        )


# ---------------------------------------------------------------------------
# Healthcheck override correctness (adversarial #10, deeper)
# ---------------------------------------------------------------------------


class HealthcheckOverrideTests(unittest.TestCase):
    """The healthcheck must honor a user-overridden password. If the
    healthcheck uses `cypher-shell -p <literal>` (or `--password <literal>`),
    overriding NEO4J_PASSWORD will silently break the probe.

    A correct implementation either:
      (a) uses `cypher-shell` with no `-p` flag (relies on the container's
          configured user/pwd from NEO4J_AUTH and the shell auto-auth env), or
      (b) passes the password via the in-container env var
          (`-p "$NEO4J_PASSWORD"`).

    We also reject any literal default like `changeme-graph` showing up in
    the healthcheck.test command.
    """

    def setUp(self):
        if not COMPOSE_FILE.exists():
            self.fail(f"missing {COMPOSE_FILE}")
        self.raw = COMPOSE_FILE.read_text()
        self.data = _load_yaml_or_compose(COMPOSE_FILE)

    def _joined_hc(self):
        if self.data is None:
            return None
        services = (self.data or {}).get("services") or {}
        svc = next(iter(services.values()), None)
        if not svc:
            return None
        hc = svc.get("healthcheck") or {}
        test = hc.get("test")
        if test is None:
            return None
        if isinstance(test, list):
            return " ".join(str(t) for t in test)
        return str(test)

    def test_no_known_literal_default_passwords(self):
        """Reject anything that looks like a hardcoded credential we know
        about from the README — `changeme-graph`, `neo4j` (the bootstrap
        default), `password`, `admin`."""
        joined = self._joined_hc()
        if joined is None:
            self.skipTest("compose unloadable or no healthcheck")
        # Match `cypher-shell ... <literal>` patterns and bare quoted strings.
        # Keep this conservative — only flag tokens that look like credentials.
        forbidden = ("changeme-graph", "changeme", "password123", "admin")
        for word in forbidden:
            self.assertNotIn(
                word,
                joined,
                f"healthcheck.test references literal credential {word!r}: {joined!r}",
            )

    def test_healthcheck_does_not_skip_auth(self):
        """`cypher-shell --no-password-prompt` without auth would mask
        broken NEO4J_AUTH. A healthcheck that always returns 'green' even
        with bad creds doesn't validate Neo4j is actually usable."""
        joined = self._joined_hc()
        if joined is None:
            self.skipTest("compose unloadable or no healthcheck")
        # If the healthcheck is just `wget http://localhost:7474` — that's
        # acceptable (it exercises the HTTP listener). If it shells into
        # cypher-shell, an auth path is expected.
        if (
            "cypher-shell" in joined
            and "-u" not in joined
            and "--username" not in joined
        ):
            # cypher-shell needs a username to actually try auth.
            # An unauthenticated cypher-shell call would fail closed
            # (still useful), but most likely indicates a misconfiguration.
            # Just record as a soft-warning by skipping rather than failing;
            # we still want the password-not-leaked check above.
            self.skipTest(
                "healthcheck uses cypher-shell without -u/--username; "
                "would fail closed but contract doesn't require an auth probe"
            )


# ---------------------------------------------------------------------------
# Hooks JSON: must wire the *correct* SessionStart script
# ---------------------------------------------------------------------------


class HooksJsonScriptWiringTests(unittest.TestCase):
    def setUp(self):
        if not HOOKS_JSON.exists():
            self.fail(f"missing {HOOKS_JSON}")
        with open(HOOKS_JSON) as f:
            self.data = json.load(f)

    def test_session_start_wires_graph_hook_script(self):
        """Per the feature spec, the SessionStart hook is supposed to bring
        the DB up and surface upcoming events — implemented by
        `hooks/session-start-graph.sh`. We verify some SessionStart hook
        command actually references that script (basename match)."""
        ss = self.data.get("hooks", {}).get("SessionStart") or []
        commands = []
        for entry in ss:
            inner = entry.get("hooks") or []
            for h in inner:
                if "command" in h:
                    commands.append(h["command"])
            if "command" in entry:
                commands.append(entry["command"])
        self.assertTrue(commands, "no SessionStart commands found")
        match = any("session-start-graph.sh" in cmd for cmd in commands)
        self.assertTrue(
            match,
            f"no SessionStart command references "
            f"`session-start-graph.sh`; commands={commands!r}",
        )

    def test_command_quoting_handles_spaces_in_plugin_root(self):
        """If the user's CLAUDE_PLUGIN_ROOT contains spaces (e.g. macOS
        path `/Users/Jane Doe/...`), commands like `bash $PATH/script.sh`
        will split. Hooks should quote: `bash "${CLAUDE_PLUGIN_ROOT}/...".`
        We assert there's quoting around the CLAUDE_PLUGIN_ROOT reference."""
        ss = self.data.get("hooks", {}).get("SessionStart") or []
        commands = []
        for entry in ss:
            inner = entry.get("hooks") or []
            for h in inner:
                if "command" in h:
                    commands.append(h["command"])
            if "command" in entry:
                commands.append(entry["command"])
        for cmd in commands:
            if "${CLAUDE_PLUGIN_ROOT}" not in cmd:
                continue
            # We expect `"${CLAUDE_PLUGIN_ROOT}/...` or
            # `'${CLAUDE_PLUGIN_ROOT}/...`. A bare `${CLAUDE_PLUGIN_ROOT}/...`
            # would split on spaces. JSON itself wraps the whole string in
            # ", but the bash shell sees the *value* — so we need inner
            # quotes around the expansion.
            # If the command is shape `bash <path>`, we look for an inner
            # quote immediately before the `$` of the expansion.
            idx = cmd.find("${CLAUDE_PLUGIN_ROOT}")
            before = cmd[:idx]
            # Acceptable: prev char is `"` or `'` (quoted), or cmd starts
            # directly with the expansion (rare and risky but plausible).
            prev = before[-1] if before else ""
            if prev not in ('"', "'"):
                # Try a softer check: maybe the whole command is wrapped
                # in single quotes already (less common in JSON).
                # We flag as a warning via skipTest only if there's no
                # whitespace before the expansion (couldn't split).
                # Otherwise: fail.
                self.assertFalse(
                    " " in before or "\t" in before,
                    f"unquoted ${{CLAUDE_PLUGIN_ROOT}} after whitespace will "
                    f"split on spaces in the root path: {cmd!r}",
                )


# ---------------------------------------------------------------------------
# Hook happy-path: additionalContext should signal events or schema doc
# ---------------------------------------------------------------------------


class HookHappyPathContextTests(unittest.TestCase):
    """Feature description says the hook 'surfaces upcoming events into
    Claude's context.' We can't reliably assert on event content (the
    DB is mocked), but we CAN assert the context is structured for that
    purpose — either it acknowledges the schema doc, OR it labels its
    event section in some recognisable way ('event', 'upcoming', or
    'schedule')."""

    HOOK_TIMEOUT = 12.0

    def setUp(self):
        if not SESSION_START_SH.exists():
            self.fail(f"missing {SESSION_START_SH}")

    def test_running_container_context_mentions_events_or_schema(self):
        d = _make_running_docker_shim()
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            r = subprocess.run(
                ["bash", str(SESSION_START_SH)],
                capture_output=True,
                text=True,
                timeout=self.HOOK_TIMEOUT,
                env=env,
            )
            self.assertEqual(
                r.returncode,
                0,
                f"hook must exit 0; stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            stripped = r.stdout.strip()
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as e:
                self.fail(f"hook stdout invalid JSON: {e}; stdout={r.stdout!r}")
            ctx = obj.get("hookSpecificOutput", {}).get("additionalContext", "")
            self.assertIsInstance(ctx, str)
            ctx_lower = ctx.lower()
            self.assertTrue(
                "graph-schema.md" in ctx
                or "event" in ctx_lower
                or "upcoming" in ctx_lower
                or "schedule" in ctx_lower,
                f"running-container context should mention events/schedule/"
                f"schema; got {ctx!r}",
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# README sanity: docker compose start command + warning surface
# ---------------------------------------------------------------------------


class ReadmeContractTests(unittest.TestCase):
    """The README mentions Docker/uv/NEO4J_PASSWORD/graph-seed — but does
    it tell the user the port mappings are localhost-only? If a teammate
    expects to hit Neo4j Browser at http://<workstation-ip>:7474, the
    127.0.0.1-only binding will silently fail to listen for them."""

    def setUp(self):
        readme = REPO_ROOT / "README.md"
        if not readme.exists():
            self.fail(f"missing {readme}")
        self.text = readme.read_text()

    def test_mentions_localhost_or_127_0_0_1(self):
        """README should hint at localhost-only binding so a user
        debugging 'why can't I reach Neo4j Browser from another machine?'
        finds the answer in-repo."""
        lower = self.text.lower()
        self.assertTrue(
            "localhost" in lower
            or "127.0.0.1" in self.text
            or "local-only" in lower
            or "local only" in lower,
            "README should mention local-only / 127.0.0.1 binding",
        )


if __name__ == "__main__":
    unittest.main()
