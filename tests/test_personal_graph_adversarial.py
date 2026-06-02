"""Adversarial / contract-gap tests for the personal-graph feature.

These extend tests/test_personal_graph.py by exercising the parts of the
contract the existing suite doesn't cover:

- compose: APOC bonus / heap memory sanity / interpolation in raw text
- neo4j-up.sh: idempotency when container is already running, stale stopped
  container restart, concurrent invocations (race), CLAUDE_PLUGIN_ROOT-less
  invocation, stderr-only on failure paths.
- .mcp.json: token literals, MCP env vars with no default.
- hooks/hooks.json: SessionStart entry shape (matcher/type/command).
- hooks/session-start-graph.sh: partial failure (info ok, inspect errors),
  container-not-running branch, slow-docker timeout, special-char JSON
  escaping in additionalContext, invocation from a CWD outside plugin root,
  invocation with CLAUDE_PLUGIN_ROOT unset.

Black-box only: forbidden source files are NEVER read here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
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


# ---------------------------------------------------------------------------
# Fake-docker shim factory
# ---------------------------------------------------------------------------


def _write_shim(dir_path: str, script: str, name: str = "docker") -> str:
    p = os.path.join(dir_path, name)
    with open(p, "w") as f:
        f.write(script)
    os.chmod(p, 0o755)
    return p


def make_shim(behavior: str, log_path: str | None = None) -> str:
    """Build a tempdir with a configurable `docker` shim and return the dir.

    Behaviors:
      - "running":   `docker info` ok, `docker inspect` returns "true" / "running"
                     (i.e. container is running and healthy).
      - "stopped":   `docker info` ok, `docker inspect` for State.Running returns
                     "false" (container exists but stopped).
      - "missing":   `docker info` ok, `docker inspect` errors (no such object).
      - "inspect_errors": `docker info` ok but `docker inspect` exits non-zero
                          with stderr (the "partial failure" case from the spec).
      - "slow":      Every invocation sleeps 30s before returning.
      - "info_fails":`docker info` fails (daemon down); other calls succeed.
    """
    d = tempfile.mkdtemp(prefix=f"shim_{behavior}_")
    log_clause = ""
    if log_path:
        # Append the full argv to a log file so tests can assert call sequences.
        log_clause = f'printf "%s\\n" "$*" >> {json.dumps(log_path)}\n'

    if behavior == "running":
        script = (
            "#!/bin/sh\n"
            + log_clause
            + textwrap.dedent(
                """\
                cmd="$1"; shift
                case "$cmd" in
                    info) exit 0 ;;
                    inspect)
                        # Caller likely does: docker inspect -f '{{.State.Running}}' <name>
                        echo "true"
                        exit 0
                        ;;
                    ps)
                        # Mimic "container is running"
                        echo "neo4j-personal-graph"
                        exit 0
                        ;;
                    compose)
                        # `docker compose ps -q` etc — return a fake container id
                        sub="$1"; shift || true
                        if [ "$sub" = "ps" ]; then
                            echo "abc123"
                        fi
                        exit 0
                        ;;
                    start|up|run)
                        exit 0
                        ;;
                    *)
                        exit 0
                        ;;
                esac
                """
            )
        )
    elif behavior == "stopped":
        script = (
            "#!/bin/sh\n"
            + log_clause
            + textwrap.dedent(
                """\
                cmd="$1"; shift
                case "$cmd" in
                    info) exit 0 ;;
                    inspect)
                        # Container exists but not running.
                        echo "false"
                        exit 0
                        ;;
                    ps)
                        # No running containers
                        exit 0
                        ;;
                    compose)
                        sub="$1"; shift || true
                        if [ "$sub" = "ps" ]; then
                            : # empty
                        fi
                        exit 0
                        ;;
                    start|up|run)
                        exit 0
                        ;;
                    *) exit 0 ;;
                esac
                """
            )
        )
    elif behavior == "missing":
        # Container does not exist at all.
        script = (
            "#!/bin/sh\n"
            + log_clause
            + textwrap.dedent(
                """\
                cmd="$1"; shift
                case "$cmd" in
                    info) exit 0 ;;
                    inspect)
                        echo "Error: No such object" 1>&2
                        exit 1
                        ;;
                    ps) exit 0 ;;
                    compose)
                        sub="$1"; shift || true
                        # compose ps -q → empty
                        exit 0
                        ;;
                    start|up|run) exit 0 ;;
                    *) exit 0 ;;
                esac
                """
            )
        )
    elif behavior == "inspect_errors":
        # info OK, but inspect blows up with a permission / dial error,
        # mimicking the spec's "partial failure" dimension.
        script = (
            "#!/bin/sh\n"
            + log_clause
            + textwrap.dedent(
                """\
                cmd="$1"; shift
                case "$cmd" in
                    info) exit 0 ;;
                    inspect)
                        echo "permission denied while trying to connect" 1>&2
                        exit 1
                        ;;
                    ps) echo "permission denied" 1>&2 ; exit 1 ;;
                    compose) echo "permission denied" 1>&2 ; exit 1 ;;
                    *) exit 1 ;;
                esac
                """
            )
        )
    elif behavior == "slow":
        # All commands hang for 30s — used to verify hook enforces timeouts.
        script = (
            "#!/bin/sh\n"
            + log_clause
            + textwrap.dedent(
                """\
                sleep 30
                exit 0
                """
            )
        )
    elif behavior == "info_fails":
        script = (
            "#!/bin/sh\n"
            + log_clause
            + textwrap.dedent(
                """\
                cmd="$1"; shift
                if [ "$cmd" = "info" ]; then
                    echo "Cannot connect to the Docker daemon" 1>&2
                    exit 1
                fi
                exit 0
                """
            )
        )
    else:
        raise ValueError(f"unknown shim behavior: {behavior}")

    _write_shim(d, script, name="docker")
    return d


# ---------------------------------------------------------------------------
# Compose: bonus & raw-text checks
# ---------------------------------------------------------------------------


def _load_yaml(path: Path):
    """Best-effort compose load: PyYAML if available, else `docker compose config --format json`."""
    try:
        import yaml  # type: ignore
        try:
            with open(path) as f:
                return yaml.safe_load(f)
        except Exception:
            pass
    except ImportError:
        pass
    # Fallback: docker compose config --format json
    if shutil.which("docker") is None:
        return None
    try:
        r = subprocess.run(
            ["docker", "compose", "-f", str(path), "config", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        return None
    return None


class ComposeAdversarialTests(unittest.TestCase):
    def setUp(self):
        if not COMPOSE_FILE.exists():
            self.fail(f"missing {COMPOSE_FILE}")
        self.raw = COMPOSE_FILE.read_text()
        self.data = _load_yaml(COMPOSE_FILE)

    def test_no_zero_bind_in_raw_text(self):
        """Defense in depth: scan raw YAML for any 0.0.0.0 binding or bare
        '7474:' / '7687:' short-form (which would mean 0.0.0.0)."""
        self.assertNotRegex(
            self.raw,
            r"\b0\.0\.0\.0\b",
            "compose must not bind any port to 0.0.0.0",
        )
        # Short-form 'PORT:PORT' without a host IP is also 0.0.0.0.
        # Match lines like "- 7474:7474" or "- '7474:7474'" with no leading IP.
        bad = re.findall(
            r"-\s*['\"]?(\d{2,5}:\d{2,5}(?:/\w+)?)['\"]?\s*$",
            self.raw,
            re.MULTILINE,
        )
        # In our compose, only mappings using "127.0.0.1:HOST:CONTAINER" form
        # would have at least 3 colon-separated tokens, so the above regex
        # only matches the bad 2-token form.
        # Filter out empty matches and report.
        bad = [b for b in bad if b]
        self.assertFalse(
            bad,
            f"found bare HOST:CONTAINER port mappings (implicit 0.0.0.0): {bad!r}. "
            "All mappings should use 127.0.0.1:HOST:CONTAINER.",
        )

    def test_heap_memory_sane_if_set(self):
        """Bonus: if NEO4J_server_memory_heap_max__size (or similar) is set,
        it must not exceed 2G. Skip if not configured."""
        if self.data is None:
            self.skipTest("compose unloadable")
        services = (self.data or {}).get("services") or {}
        svc = next(iter(services.values()), {})
        env = svc.get("environment") or {}
        # `environment` may be dict or list of "KEY=VALUE" strings.
        if isinstance(env, list):
            env = dict(
                item.split("=", 1) for item in env if isinstance(item, str) and "=" in item
            )
        heap_keys = [
            k for k in env
            if isinstance(k, str)
            and "heap" in k.lower()
            and "max" in k.lower()
        ]
        if not heap_keys:
            self.skipTest("no heap-max env var set in compose")
        for k in heap_keys:
            v = str(env[k]).strip()
            m = re.match(r"^(\d+(?:\.\d+)?)\s*([gGmMkK]?)$", v)
            if not m:
                continue  # opaque value (e.g. interpolation) — skip
            num = float(m.group(1))
            unit = m.group(2).lower()
            gb = num if unit == "g" else (num / 1024.0 if unit == "m" else 0)
            self.assertLessEqual(
                gb,
                2.0,
                f"{k}={v} → {gb}GB exceeds 2G sane-ceiling for a local dev DB",
            )

    def test_apoc_bonus_if_present(self):
        """Bonus: if NEO4J_PLUGINS appears, it should list apoc."""
        if "NEO4J_PLUGINS" not in self.raw:
            self.skipTest("NEO4J_PLUGINS not configured")
        # Look for the literal token 'apoc' (any case) near the plugins line.
        for line in self.raw.splitlines():
            if "NEO4J_PLUGINS" in line:
                self.assertRegex(
                    line.lower(),
                    r"apoc",
                    f"NEO4J_PLUGINS present but does not include apoc: {line!r}",
                )


# ---------------------------------------------------------------------------
# .mcp.json: extended secret scanning + env-var defaults
# ---------------------------------------------------------------------------


class McpJsonAdversarialTests(unittest.TestCase):
    def setUp(self):
        if not MCP_JSON.exists():
            self.fail(f"missing {MCP_JSON}")
        with open(MCP_JSON) as f:
            self.data = json.load(f)
        self.server = self.data["mcpServers"]["personal-graph"]
        self.raw = MCP_JSON.read_text()

    def test_no_token_or_secret_literal(self):
        """Spec: any --password / *PASS* / *TOKEN* must be ${...} interpolation.
        We extend to TOKEN too (not just PASSWORD)."""
        interp = re.compile(r"^\$\{[^}]+\}$")
        env = self.server.get("env") or {}
        for k, v in env.items():
            if not (isinstance(k, str) and isinstance(v, str)):
                continue
            up = k.upper()
            if "TOKEN" in up or "PASS" in up or "SECRET" in up or "KEY" in up:
                # Allow obvious non-secrets like USERKEY etc by being a bit loose:
                # only fail if the value looks like a literal (no ${...}).
                if not interp.match(v):
                    self.fail(
                        f"env {k}={v!r} smells secret-like and is not a ${{...}} interpolation"
                    )

        # Scan args for any '=...' flag where flag-name suggests a secret.
        for a in self.server.get("args") or []:
            if not isinstance(a, str) or "=" not in a:
                continue
            flag, _, val = a.partition("=")
            up = flag.upper()
            if any(t in up for t in ("PASSWORD", "TOKEN", "SECRET")):
                self.assertRegex(
                    val,
                    interp,
                    f"flag {a!r} carries a literal secret value",
                )

    def test_env_vars_have_defaults_or_are_documented(self):
        """If the MCP server references env vars in args/env values, each
        ${VAR} should ideally have a default (${VAR:-...}). Otherwise an unset
        var crashes the server at first session start. We test that NEO4J_URL,
        if referenced, has a default (since spec calls it out)."""
        # Collect every ${...} reference from all string values in the server.
        refs = []

        def walk(x):
            if isinstance(x, str):
                refs.extend(re.findall(r"\$\{([^}]+)\}", x))
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)

        walk(self.server)
        # Group by name (text before ':-' or ':' or '-')
        names_to_specs = {}
        for r in refs:
            name = re.split(r"[:\-]", r, 1)[0]
            names_to_specs.setdefault(name, []).append(r)

        # NEO4J_URL is the spec-flagged one. If used, must have a default.
        if "NEO4J_URL" in names_to_specs:
            specs = names_to_specs["NEO4J_URL"]
            has_default = any(":-" in s or ":=" in s for s in specs)
            self.assertTrue(
                has_default,
                f"NEO4J_URL is referenced ({specs!r}) without a default — "
                "an unset var would crash the MCP server.",
            )


# ---------------------------------------------------------------------------
# hooks/hooks.json shape
# ---------------------------------------------------------------------------


class HooksJsonShapeTests(unittest.TestCase):
    def setUp(self):
        if not HOOKS_JSON.exists():
            self.fail(f"missing {HOOKS_JSON}")
        with open(HOOKS_JSON) as f:
            self.data = json.load(f)

    def test_session_start_hook_has_executable_command(self):
        """The actual command must invoke a real, executable file."""
        ss = self.data["hooks"]["SessionStart"]
        commands = []
        for entry in ss:
            inner = entry.get("hooks") or []
            for h in inner:
                if "command" in h:
                    commands.append(h["command"])
            if "command" in entry:
                commands.append(entry["command"])

        # Substitute ${CLAUDE_PLUGIN_ROOT} → REPO_ROOT and confirm the path exists.
        plugin_root = str(REPO_ROOT)
        for cmd in commands:
            expanded = cmd.replace("${CLAUDE_PLUGIN_ROOT}", plugin_root)
            # Pull the first token as the script path.
            first = expanded.strip().split()[0]
            # `bash <path>` form: first token might be "bash".
            if first == "bash" or first.endswith("/bash"):
                parts = expanded.strip().split()
                self.assertGreaterEqual(len(parts), 2, f"bash with no arg: {cmd!r}")
                script = parts[1]
            else:
                script = first
            self.assertTrue(
                Path(script).exists(),
                f"hook command references non-existent script: {script!r} (from {cmd!r})",
            )


# ---------------------------------------------------------------------------
# scripts/neo4j-up.sh: adversarial dimensions
# ---------------------------------------------------------------------------


class Neo4jUpAdversarialTests(unittest.TestCase):
    def setUp(self):
        if not NEO4J_UP_SH.exists():
            self.fail(f"missing {NEO4J_UP_SH}")

    def _run(self, env, timeout=20.0):
        return subprocess.run(
            ["bash", str(NEO4J_UP_SH)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

    def test_idempotent_when_container_running(self):
        """If the container is already running, up.sh must not error and
        must not relaunch (we can't directly observe the latter, but we
        verify exit 0 and clean stdout)."""
        d = make_shim("running")
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            r = self._run(env)
            self.assertEqual(
                r.returncode, 0,
                f"up.sh must exit 0 when container is running; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self.assertEqual(
                r.stdout, "",
                f"stdout must be clean when container running; got {r.stdout!r}",
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_handles_stale_stopped_container(self):
        """If the container exists but is stopped, up.sh should bring it back
        (or at minimum: exit 0, clean stdout, not crash)."""
        d = make_shim("stopped")
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            r = self._run(env)
            self.assertEqual(
                r.returncode, 0,
                f"up.sh must exit 0 when container is stopped; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self.assertEqual(
                r.stdout, "",
                f"stdout must be clean; got {r.stdout!r}",
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_concurrent_invocations_both_exit_zero(self):
        """Race: two sessions might invoke up.sh at the same time. Both
        invocations must exit 0 and produce clean stdout. We use the
        'missing' shim so each invocation attempts to start the container."""
        d = make_shim("missing")
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            results: list[subprocess.CompletedProcess] = []
            errors: list[BaseException] = []

            def worker():
                try:
                    results.append(self._run(env, timeout=20.0))
                except BaseException as e:  # noqa: BLE001
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            self.assertFalse(errors, f"worker exceptions: {errors!r}")
            self.assertEqual(len(results), 2)
            for r in results:
                self.assertEqual(
                    r.returncode, 0,
                    f"concurrent up.sh must exit 0; "
                    f"stderr={r.stderr!r} stdout={r.stdout!r}",
                )
                self.assertEqual(
                    r.stdout, "",
                    f"stdout must stay clean under concurrency; got {r.stdout!r}",
                )
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# hooks/session-start-graph.sh: adversarial
# ---------------------------------------------------------------------------


class SessionStartHookAdversarialTests(unittest.TestCase):
    HOOK_TIMEOUT = 12.0  # spec calls out 10s wallclock; allow slop for slow CI

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

    def _assert_json(self, stdout, must_include_schema=True):
        stripped = stdout.strip()
        self.assertTrue(stripped, "stdout was empty")
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            self.fail(f"stdout not valid JSON: {e}; stdout={stdout!r}")
        self.assertIsInstance(obj, dict)
        # No trailing junk
        decoder = json.JSONDecoder()
        _, end_idx = decoder.raw_decode(stripped)
        tail = stripped[end_idx:].strip()
        self.assertEqual(tail, "", f"trailing junk after JSON: {tail!r}")

        hso = obj.get("hookSpecificOutput")
        self.assertIsInstance(hso, dict)
        self.assertEqual(hso.get("hookEventName"), "SessionStart")
        ctx = hso.get("additionalContext")
        self.assertIsInstance(ctx, str)
        self.assertTrue(ctx, "additionalContext must be non-empty")
        if must_include_schema:
            self.assertIn(
                "graph-schema.md", ctx,
                f"additionalContext should reference graph-schema.md; got {ctx!r}",
            )
        return obj, ctx

    # ---- container-not-running branch -------------------------------------

    def test_container_missing_branch(self):
        """Docker daemon up, but container does not exist (inspect errors out).
        Hook must still emit valid JSON, exit 0, finish quickly."""
        d = make_shim("missing")
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
                f"hook must exit 0; stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self.assertLess(elapsed, 10.0, "hook exceeded 10s wallclock budget")
            self._assert_json(r.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ---- partial failure: info OK but inspect errors ---------------------

    def test_partial_failure_inspect_errors(self):
        """`docker info` returns 0 but `docker inspect` fails (permissions,
        socket race, etc). Hook must NOT emit malformed JSON, must exit 0."""
        d = make_shim("inspect_errors")
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
                f"hook must exit 0 on partial failure; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self.assertLess(elapsed, 10.0)
            self._assert_json(r.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ---- slow docker → must enforce timeout ------------------------------

    def test_slow_docker_does_not_block_hook(self):
        """If `docker` hangs, the hook must enforce a timeout and still emit
        valid JSON within the 10s wallclock budget."""
        d = make_shim("slow")
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            t0 = time.monotonic()
            try:
                r = self._run(env, timeout=12.0)
            except subprocess.TimeoutExpired:
                self.fail(
                    "hook did not enforce its own docker timeout; "
                    "wallclock exceeded 12s with a hanging docker shim"
                )
            elapsed = time.monotonic() - t0

            self.assertLess(
                elapsed, 10.0,
                f"hook wallclock {elapsed:.2f}s exceeded 10s budget "
                "with slow docker — internal timeout not enforced",
            )
            self.assertEqual(
                r.returncode, 0,
                f"hook must exit 0 even when docker hangs; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self._assert_json(r.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ---- wrong CWD --------------------------------------------------------

    def test_invocation_from_unrelated_cwd(self):
        """Hook may be invoked from any project directory; it must not depend
        on cwd to locate its own files."""
        d = make_shim("info_fails")
        cwd = tempfile.mkdtemp(prefix="unrelated_cwd_")
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            r = self._run(env, cwd=cwd)
            self.assertEqual(
                r.returncode, 0,
                f"hook must exit 0 from unrelated cwd; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self._assert_json(r.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(cwd, ignore_errors=True)

    # ---- CLAUDE_PLUGIN_ROOT unset → must derive from script location -----

    def test_no_plugin_root_env_var(self):
        """If CLAUDE_PLUGIN_ROOT is unset, the hook should derive its location
        from $0 (script path). Should still emit valid JSON, exit 0."""
        d = make_shim("info_fails")
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            # Notably: no CLAUDE_PLUGIN_ROOT
            r = self._run(env)
            self.assertEqual(
                r.returncode, 0,
                f"hook must exit 0 with CLAUDE_PLUGIN_ROOT unset; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            self._assert_json(r.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ---- stderr unconstrained but stdout must be exactly one JSON --------

    def test_stderr_is_not_mixed_into_stdout(self):
        """Even when stderr is noisy (info_fails shim writes to stderr),
        stdout must remain exactly one JSON object."""
        d = make_shim("info_fails")
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            r = self._run(env)
            self.assertEqual(r.returncode, 0)
            # stderr can have anything; stdout must be a single JSON value.
            self._assert_json(r.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# JSON escaping: invoke the hook via a shim that injects a hostile event
# title via a known channel (env var) IFF the hook reads such a thing.
# This is best-effort: if the hook builds context purely from literals,
# the test won't catch real escaping issues. We still validate that *any*
# event-like content present in context is valid JSON.
# ---------------------------------------------------------------------------


class HookJsonEscapingTests(unittest.TestCase):
    """Treat the JSON-validity guarantee as the *contract*. Even if we can't
    inject hostile titles directly, we can still assert that the JSON shape
    holds across multiple shim states with non-ASCII / control-char-laden
    stderr (the hook must not accidentally leak stderr → stdout)."""

    def setUp(self):
        if not SESSION_START_SH.exists():
            self.fail(f"missing {SESSION_START_SH}")

    def test_json_holds_when_shim_emits_special_chars_on_stderr(self):
        d = tempfile.mkdtemp(prefix="shim_specials_")
        try:
            script = (
                "#!/bin/sh\n"
                "# Emit a hostile-looking string to stderr for every call.\n"
                'printf %s "{\\"evil\\": \\"line1\\nline2\\t\\\\u00e9\\"}" 1>&2\n'
                'echo " trailing stderr garbage \\\"with quotes\\\"" 1>&2\n'
                "if [ \"$1\" = info ]; then exit 1; fi\n"
                "exit 0\n"
            )
            _write_shim(d, script)
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            r = subprocess.run(
                ["bash", str(SESSION_START_SH)],
                capture_output=True,
                text=True,
                timeout=10.0,
                env=env,
            )
            self.assertEqual(
                r.returncode, 0,
                f"hook must exit 0 with hostile stderr; stderr={r.stderr!r}",
            )
            # stdout must still parse cleanly
            stripped = r.stdout.strip()
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as e:
                self.fail(
                    f"hook leaked stderr into stdout / produced invalid JSON: {e}; "
                    f"stdout={r.stdout!r}"
                )
            self.assertIsInstance(obj, dict)
            self.assertIn("hookSpecificOutput", obj)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
