"""Tests for the viz workflow scripts.

Covers:
- scripts/viz-set-current.py — URL parsing, state file I/O, --clear, seen_comments
  preservation vs. reset across key switches.
- hooks/viz-comments.py — silent no-op contract (no state, no token, expired
  TTL, malformed state), and JSON output schema for Claude Code's
  UserPromptSubmit hook (must use hookSpecificOutput.additionalContext, not a
  bare additionalContext key).

The hook is exercised without network access by relying on its silent no-op
branches; tests requiring the Figma API to return canned data are not included
(would require an HTTP mock server — flagged as a remaining gap).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SET_CURRENT = REPO_ROOT / "scripts" / "viz-set-current.py"
SET_SECRET = REPO_ROOT / "scripts" / "set-secret.py"
HOOK = REPO_ROOT / "hooks" / "viz-comments.py"
TRACK = REPO_ROOT / "hooks" / "viz-track.py"
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"
MY_CLAUDE = REPO_ROOT / "scripts" / "my-claude"


def _run(cmd, *, env_extra=None, env_unset=(), stdin=""):
    """Run a script with a minimal env. HOME is set per-test."""
    env = os.environ.copy()
    for k in env_unset:
        env.pop(k, None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=10.0,
    )


class VizSetCurrentTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="viz_test_home_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.state_path = Path(self.tmpdir) / ".claude" / "viz-state.json"

    def _run_set(self, *args):
        return _run(
            [str(SET_CURRENT), *args],
            env_extra={"HOME": self.tmpdir},
        )

    def _state(self):
        return json.loads(self.state_path.read_text())

    # ---- usage / argument handling ----

    def test_no_args_prints_usage_and_exits_2(self):
        result = self._run_set()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)
        self.assertFalse(self.state_path.exists())

    def test_clear_removes_state_file(self):
        # Pre-populate
        self._run_set("ABC123")
        self.assertTrue(self.state_path.exists())
        result = self._run_set("--clear")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.state_path.exists())

    def test_clear_is_safe_when_no_state(self):
        result = self._run_set("--clear")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.state_path.exists())

    # ---- URL parsing ----

    def test_raw_key_accepted_as_is(self):
        result = self._run_set("ABC123XYZ")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "ABC123XYZ")

    def test_board_url_with_query_params(self):
        result = self._run_set(
            "https://www.figma.com/board/ABC123XYZ/My-Board?node-id=0%3A1"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "ABC123XYZ")

    def test_design_url(self):
        result = self._run_set("https://www.figma.com/design/DESIGNKEY1/Some-File")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "DESIGNKEY1")

    def test_file_url(self):
        result = self._run_set("https://www.figma.com/file/FILEKEY1/Some-File")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "FILEKEY1")

    def test_url_without_www(self):
        result = self._run_set("figma.com/board/BAREKEY/Foo")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "BAREKEY")

    def test_proto_slides_deck_urls_derive_key(self):
        # /proto/, /slides/, /deck/ are comment-bearing figma URL forms; the
        # key is derived just like /design/ and /board/.
        for form, key in [("proto", "PROTOKEY"), ("slides", "SLKEY"), ("deck", "DKEY")]:
            result = self._run_set(f"https://www.figma.com/{form}/{key}/x?node-id=1-2")
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(self._state()["current_file_key"], key)

    def test_http_scheme_url(self):
        result = self._run_set("http://www.figma.com/design/HTTPKEY/Name")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "HTTPKEY")

    def test_no_scheme_with_www(self):
        result = self._run_set("www.figma.com/design/NOSCHEMEWWW/x")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "NOSCHEMEWWW")

    def test_no_scheme_no_www(self):
        result = self._run_set("figma.com/design/NOSCHEMEKEY/x")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "NOSCHEMEKEY")

    def test_url_without_filename(self):
        result = self._run_set("https://www.figma.com/design/NOFILEKEY")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "NOFILEKEY")

    def test_url_with_trailing_slash(self):
        result = self._run_set("https://www.figma.com/design/TRAILKEY/")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "TRAILKEY")

    def test_design_url_with_node_id_query(self):
        result = self._run_set("https://www.figma.com/design/NODEKEY/Name?node-id=1-2")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "NODEKEY")

    def test_branch_url_uses_file_key_not_branch_key(self):
        # A branch URL keeps the first (file) key, not the branch segment.
        result = self._run_set(
            "https://www.figma.com/design/MAINKEY/branch/BRANCHKEY/Name?node-id=1-2"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "MAINKEY")

    def test_uppercase_and_mixed_case_host_path_parsed(self):
        # Host/path casing doesn't block key extraction.
        result = self._run_set("https://FIGMA.COM/design/UPPERHOST/Name")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "UPPERHOST")
        result = self._run_set("https://www.Figma.com/DESIGN/MixedKey/Name")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "MixedKey")

    def test_very_long_url_key_parsed(self):
        key = "VERYLONGKEY" + ("x" * 500)
        result = self._run_set(f"https://www.figma.com/design/{key}/Name")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], key)

    def test_non_figma_url_stored_verbatim(self):
        # A non-figma URL has no derivable key, so it's stored as-is (raw-key
        # fallback).
        result = self._run_set("https://example.com/foo/bar")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            self._state()["current_file_key"], "https://example.com/foo/bar"
        )

    # ---- raw-key value shapes ----

    def test_leading_dash_key_accepted(self):
        # A leading-dash value is a valid raw key, not an option flag.
        result = self._run_set("--", "-weirdkey")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(self._state()["current_file_key"], "-weirdkey")

    def test_key_with_spaces_preserved(self):
        result = self._run_set("key with spaces")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "key with spaces")

    def test_unicode_key_preserved(self):
        result = self._run_set("key\U0001f600emoji")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._state()["current_file_key"], "key\U0001f600emoji")

    def test_empty_string_rejected(self):
        result = self._run_set("")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.state_path.exists())

    # ---- state mutation: seen_comments preserved vs reset ----

    def test_re_affirming_same_key_preserves_seen_comments(self):
        # Pre-seed state with a few seen comments.
        os.makedirs(self.state_path.parent, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "current_file_key": "SAMEKEY",
                    "seen_comments": ["c1", "c2", "c3"],
                    "updated_at": "2026-05-26T00:00:00Z",
                }
            )
        )
        result = self._run_set("SAMEKEY")
        self.assertEqual(result.returncode, 0)
        state = self._state()
        self.assertEqual(state["current_file_key"], "SAMEKEY")
        self.assertEqual(state["seen_comments"], ["c1", "c2", "c3"])

    def test_switching_keys_resets_seen_comments(self):
        os.makedirs(self.state_path.parent, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "current_file_key": "OLDKEY",
                    "seen_comments": ["c1", "c2"],
                    "updated_at": "2026-05-26T00:00:00Z",
                }
            )
        )
        result = self._run_set("NEWKEY")
        self.assertEqual(result.returncode, 0)
        state = self._state()
        self.assertEqual(state["current_file_key"], "NEWKEY")
        self.assertEqual(state["seen_comments"], [])

    def test_state_has_updated_at_in_utc_z_format(self):
        result = self._run_set("ABCDEF")
        self.assertEqual(result.returncode, 0)
        ts = self._state()["updated_at"]
        # Must end with 'Z' (UTC), and parse as a valid timestamp.
        self.assertTrue(ts.endswith("Z"), f"updated_at must be UTC Z-suffixed: {ts}")
        # Just verify shape; not the exact wall-clock value.
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_corrupt_prior_state_does_not_crash(self):
        # If the existing state file is unreadable JSON, the script should
        # still succeed (it treats prior state as empty).
        os.makedirs(self.state_path.parent, exist_ok=True)
        self.state_path.write_text("{not valid json")
        result = self._run_set("FRESH")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(self._state()["current_file_key"], "FRESH")


class VizHookSilentNoopTests(unittest.TestCase):
    """The hook's contract is: silent no-op (exit 0, empty stderr, empty stdout)
    on every failure path, so it never disrupts a user prompt."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="viz_hook_home_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.state_path = Path(self.tmpdir) / ".claude" / "viz-state.json"

    def _run_hook(self, *, token=None):
        env_unset = () if token is not None else ("FIGMA_PERSONAL_ACCESS_TOKEN",)
        env_extra = {"HOME": self.tmpdir}
        if token is not None:
            env_extra["FIGMA_PERSONAL_ACCESS_TOKEN"] = token
        return _run([str(HOOK)], env_extra=env_extra, env_unset=env_unset)

    def _write_state(self, obj):
        os.makedirs(self.state_path.parent, exist_ok=True)
        self.state_path.write_text(obj if isinstance(obj, str) else json.dumps(obj))

    def _assert_silent(self, result):
        self.assertEqual(
            result.returncode,
            0,
            msg=f"hook crashed: stderr={result.stderr!r} stdout={result.stdout!r}",
        )
        self.assertEqual(
            result.stderr, "", msg=f"hook leaked stderr: {result.stderr!r}"
        )
        self.assertEqual(
            result.stdout, "", msg=f"hook leaked stdout: {result.stdout!r}"
        )

    # ---- no-op branches that already work ----

    def test_no_state_file_silent_noop(self):
        # No state file, no token.
        self._assert_silent(self._run_hook())

    def test_no_token_silent_noop_even_with_state(self):
        self._write_state(
            {
                "current_file_key": "ABC",
                "seen_comments": [],
                "updated_at": "2026-05-26T00:00:00Z",
            }
        )
        self._assert_silent(self._run_hook())

    def test_expired_ttl_silent_noop(self):
        # updated_at is way in the past — TTL has elapsed; must not call API.
        self._write_state(
            {
                "current_file_key": "ABC",
                "seen_comments": [],
                "updated_at": "2020-01-01T00:00:00Z",
            }
        )
        self._assert_silent(self._run_hook(token="fake-token"))

    def test_malformed_state_string_silent_noop(self):
        # Invalid JSON in state file → must silently no-op.
        self._write_state("not valid json at all{{{{")
        self._assert_silent(self._run_hook(token="fake-token"))

    # ---- the bug surfaced in QA: non-dict state crashes ----

    def test_non_dict_state_silent_noop(self):
        # State file contains *valid* JSON but is a list (or any non-dict).
        # The hook must not crash with AttributeError / leak a traceback.
        # Currently fails: 'list' object has no attribute 'get'.
        self._write_state("[]")
        self._assert_silent(self._run_hook(token="fake-token"))

    def test_state_is_a_json_number_silent_noop(self):
        self._write_state("42")
        self._assert_silent(self._run_hook(token="fake-token"))

    def test_state_is_a_json_string_silent_noop(self):
        self._write_state('"oops"')
        self._assert_silent(self._run_hook(token="fake-token"))

    def test_state_is_null_silent_noop(self):
        self._write_state("null")
        self._assert_silent(self._run_hook(token="fake-token"))

    def test_state_missing_updated_at_silent_noop(self):
        # No updated_at → can't tell if TTL elapsed → safest is to no-op
        # rather than risk an unbounded fetch window.
        self._write_state({"current_file_key": "ABC", "seen_comments": []})
        self._assert_silent(self._run_hook(token="fake-token"))

    def test_state_updated_at_unparseable_silent_noop(self):
        self._write_state(
            {
                "current_file_key": "ABC",
                "seen_comments": [],
                "updated_at": "not-a-real-timestamp",
            }
        )
        self._assert_silent(self._run_hook(token="fake-token"))

    def test_empty_state_file_silent_noop(self):
        # A zero-byte state file → unparseable → silent.
        self._write_state("")
        self._assert_silent(self._run_hook(token="fake-token"))

    def test_falsy_current_file_key_silent_noop(self):
        # A dict whose current_file_key is absent (`{}`) or null both resolve to
        # no key to track and hit the same falsy-key guard → silent.
        for state in ("{}", '{"current_file_key": null}'):
            with self.subTest(state=state):
                self._write_state(state)
                self._assert_silent(self._run_hook(token="fake-token"))

    def test_fresh_state_real_token_no_network_silent_noop(self):
        # Token present and TTL fresh, so the fetch path is reached, but the
        # file key is fake and there's no real network/auth — the hook must
        # still stay silent (and must not hang).
        self._write_state(
            {
                "current_file_key": "ABC",
                "seen_comments": [],
                "updated_at": "2099-01-01T00:00:00Z",
            }
        )
        self._assert_silent(self._run_hook(token="faketoken123"))


class VizHookOutputSchemaTests(unittest.TestCase):
    """The hook's stdout, when it does emit context, must follow the
    Claude Code UserPromptSubmit JSON schema:

        {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                "additionalContext": "..."}}

    A bare {"additionalContext": "..."} is silently ignored by the harness,
    so injected comments would never reach the model.

    This test pins the schema by inspecting the source code, since the
    network-talking branch isn't tractable to exercise without an HTTP mock.
    """

    def test_hook_uses_hookSpecificOutput_envelope(self):
        src = HOOK.read_text()
        self.assertIn(
            "hookSpecificOutput",
            src,
            msg=(
                "viz-comments.py must emit JSON shaped as "
                "{'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', "
                "'additionalContext': ...}}; a bare 'additionalContext' key is "
                "ignored by the hook harness so injected comments would never "
                "reach the model."
            ),
        )
        self.assertIn("UserPromptSubmit", src)


class SetSecretTests(unittest.TestCase):
    """Generic secret setter: writes to ~/.claude/secrets.env in dotenv format,
    mode 0600. One file, many keys."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="viz_secret_home_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.secrets_path = Path(self.tmpdir) / ".claude" / "secrets.env"

    def _run(self, *args):
        return _run([str(SET_SECRET), *args], env_extra={"HOME": self.tmpdir})

    def test_non_tty_stdin_refuses_with_exit_2(self):
        result = self._run("FIGMA_PERSONAL_ACCESS_TOKEN")
        self.assertEqual(result.returncode, 2, msg=result.stderr)
        self.assertIn("not a tty", result.stderr)
        self.assertFalse(self.secrets_path.exists())

    def test_no_key_prints_usage_and_exits_2(self):
        result = self._run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)

    def test_list_when_no_secrets(self):
        result = self._run("--list")
        self.assertEqual(result.returncode, 0)
        self.assertIn("no secrets stored", result.stdout)

    def test_list_masks_values(self):
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets_path.write_text(
            "FIGMA_PERSONAL_ACCESS_TOKEN=figp_abc\nSENTRY_AUTH_TOKEN=sntrys_xyz\n"
        )
        result = self._run("--list")
        self.assertEqual(result.returncode, 0)
        self.assertIn("FIGMA_PERSONAL_ACCESS_TOKEN=****", result.stdout)
        self.assertIn("SENTRY_AUTH_TOKEN=****", result.stdout)
        # Values must NOT leak.
        self.assertNotIn("figp_abc", result.stdout)
        self.assertNotIn("sntrys_xyz", result.stdout)

    def test_clear_removes_key_preserves_others(self):
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets_path.write_text("FOO=one\nBAR=two\nBAZ=three\n")
        result = self._run("--clear", "BAR")
        self.assertEqual(result.returncode, 0)
        contents = self.secrets_path.read_text()
        self.assertIn("FOO=one", contents)
        self.assertIn("BAZ=three", contents)
        self.assertNotIn("BAR", contents)

    def test_clear_missing_key_is_a_noop(self):
        result = self._run("--clear", "NEVER_SET")
        self.assertEqual(result.returncode, 0)
        self.assertIn("not set", result.stdout)

    def test_list_masks_value_with_embedded_equals_and_empty(self):
        # The mask covers the whole RHS (even `a=b=c`), and an empty value is
        # still shown masked — raw value bytes never leak.
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets_path.write_text("WITH_EQ=a=b=c=secret\nEMPTYVAL=\n")
        result = self._run("--list")
        self.assertEqual(result.returncode, 0)
        self.assertIn("WITH_EQ", result.stdout)
        self.assertIn("EMPTYVAL", result.stdout)
        self.assertNotIn("a=b=c=secret", result.stdout)
        self.assertNotIn("secret", result.stdout)

    def test_list_skips_comments_blanks_and_no_equals_lines(self):
        # read_dotenv (via --list) must ignore `#` comments, blank/whitespace
        # lines, and lines lacking `=`, and strip whitespace around key/value.
        # Only real KEY=value entries surface as keys.
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets_path.write_text(
            "# a comment line\n"
            "\n"
            "   \n"
            "NOEQUALSLINE\n"
            "REAL_KEY=value\n"
            "  SPACED_KEY  =  spaced value  \n"
        )
        result = self._run("--list")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("REAL_KEY=****", result.stdout)
        self.assertIn("SPACED_KEY=****", result.stdout)
        # Junk lines never become keys.
        self.assertNotIn("a comment", result.stdout)
        self.assertNotIn("NOEQUALSLINE", result.stdout)
        # Exactly the two real keys are listed.
        keys = [ln for ln in result.stdout.splitlines() if ln]
        self.assertEqual(keys, ["REAL_KEY=****", "SPACED_KEY=****"])

    def test_clear_matches_full_key_not_prefix(self):
        # `--clear A=1` must not match key `A`; the key is the LHS of `=`, so
        # nothing is removed.
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets_path.write_text("A=1\nB=2\n")
        result = self._run("--clear", "A=1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.secrets_path.read_text(), "A=1\nB=2\n")


class VizHookTokenSourceTests(unittest.TestCase):
    """The hook reads the token from ~/.claude/secrets.env first, env var as
    fallback. Verified by inspecting the hook's source — we don't probe the
    real Figma API."""

    def test_hook_references_secrets_env_path(self):
        src = HOOK.read_text()
        self.assertIn(
            "secrets.env", src, msg="hook must load token from ~/.claude/secrets.env"
        )
        self.assertIn(
            "FIGMA_PERSONAL_ACCESS_TOKEN",
            src,
            msg="hook must look up the FIGMA_PERSONAL_ACCESS_TOKEN key",
        )


class VizTrackTests(unittest.TestCase):
    """PostToolUse hook that auto-records the current Figma file when Claude
    calls a Figma MCP tool. Extracts URL/key from stdin JSON, calls
    viz-set-current.py."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="viz_track_home_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.state_path = Path(self.tmpdir) / ".claude" / "viz-state.json"

    def _run_track(self, stdin):
        return _run([str(TRACK)], env_extra={"HOME": self.tmpdir}, stdin=stdin)

    def test_empty_stdin_silent_noop(self):
        r = self._run_track("")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertFalse(self.state_path.exists())

    def test_payload_without_figma_url_silent_noop(self):
        r = self._run_track(
            '{"tool_name": "Bash", "tool_response": {"output": "hello"}}'
        )
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.state_path.exists())

    def test_url_in_response_triggers_set_current(self):
        payload = json.dumps(
            {
                "tool_name": "mcp__figma__generate_diagram",
                "tool_response": {
                    "claimFileUrl": "https://www.figma.com/board/ABCKEY123/My-Diagram"
                },
            }
        )
        r = self._run_track(payload)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertTrue(self.state_path.exists())
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["current_file_key"], "ABCKEY123")

    def test_filekey_in_input_triggers_set_current(self):
        payload = json.dumps(
            {
                "tool_name": "mcp__figma__use_figma",
                "tool_input": {"fileKey": "XYZ456", "code": "figma.createSticky();"},
            }
        )
        r = self._run_track(payload)
        self.assertEqual(r.returncode, 0)
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["current_file_key"], "XYZ456")

    def test_malformed_stdin_silent_noop(self):
        r = self._run_track("not valid json {{{{")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")
        self.assertFalse(self.state_path.exists())


class VizWiringTests(unittest.TestCase):
    """Sanity-check the on-disk wiring."""

    def test_hooks_json_is_valid_json_and_registers_userPromptSubmit(self):
        data = json.loads(HOOKS_JSON.read_text())
        self.assertIn("hooks", data)
        self.assertIn("UserPromptSubmit", data["hooks"])
        entries = data["hooks"]["UserPromptSubmit"]
        self.assertTrue(entries, "expected at least one UserPromptSubmit entry")
        inner = entries[0]["hooks"]
        self.assertTrue(
            any(h.get("command", "").endswith("viz-comments.py") for h in inner)
        )

    def test_hooks_json_registers_PostToolUse_for_figma_tools(self):
        data = json.loads(HOOKS_JSON.read_text())
        self.assertIn("PostToolUse", data["hooks"])
        entries = data["hooks"]["PostToolUse"]
        self.assertTrue(entries)
        matcher = entries[0]["matcher"]
        # Matcher is a regex; should cover every Figma MCP tool, not just a
        # hand-maintained subset. (`mcp__figma__.*` is the canonical form.)
        self.assertIn("mcp__figma__", matcher)
        inner = entries[0]["hooks"]
        self.assertTrue(
            any(h.get("command", "").endswith("viz-track.py") for h in inner)
        )

    def test_track_script_is_executable_with_shebang(self):
        self.assertTrue(os.access(TRACK, os.X_OK), f"{TRACK} not executable")
        first = TRACK.read_text().splitlines()[0]
        self.assertTrue(first.startswith("#!"), f"missing shebang: {first!r}")

    def test_set_current_script_is_executable_with_shebang(self):
        self.assertTrue(
            os.access(SET_CURRENT, os.X_OK), f"{SET_CURRENT} not executable"
        )
        first = SET_CURRENT.read_text().splitlines()[0]
        self.assertTrue(first.startswith("#!"), f"missing shebang: {first!r}")

    def test_set_secret_script_is_executable_with_shebang(self):
        self.assertTrue(os.access(SET_SECRET, os.X_OK), f"{SET_SECRET} not executable")
        first = SET_SECRET.read_text().splitlines()[0]
        self.assertTrue(first.startswith("#!"), f"missing shebang: {first!r}")

    def test_hook_script_is_executable_with_shebang(self):
        self.assertTrue(os.access(HOOK, os.X_OK), f"{HOOK} not executable")
        first = HOOK.read_text().splitlines()[0]
        self.assertTrue(first.startswith("#!"), f"missing shebang: {first!r}")


class MyClaudeDispatchTests(unittest.TestCase):
    """The `my-claude` entrypoint routes `<group> <command> [args]` to the
    underlying scripts, with a built-in help and exit-2 on bad usage."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="my_claude_home_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.claude_dir = Path(self.tmpdir) / ".claude"

    def _run(self, *args, stdin=""):
        return _run(
            [str(MY_CLAUDE), *args],
            env_extra={"HOME": self.tmpdir},
            stdin=stdin,
        )

    # ---- help / usage ----

    def test_no_args_prints_help_exit_0(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("Usage: my-claude", r.stdout)

    def test_help_subcommand(self):
        r = self._run("help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("secret", r.stdout)
        self.assertIn("viz", r.stdout)
        self.assertIn("statusline", r.stdout)

    def test_help_flag_aliases(self):
        for flag in ("-h", "--help"):
            r = self._run(flag)
            self.assertEqual(r.returncode, 0, msg=f"{flag}: {r.stderr}")
            self.assertIn("Usage: my-claude", r.stdout)

    def test_unknown_group_exits_2_with_help(self):
        r = self._run("bogus")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown group", r.stderr)
        self.assertIn("Usage: my-claude", r.stderr)

    def test_group_without_command_exits_2(self):
        r = self._run("secret")
        self.assertEqual(r.returncode, 2)
        self.assertIn("needs a command", r.stderr)
        # Lists the valid verbs for that group.
        self.assertIn("set", r.stderr)

    def test_unknown_command_exits_2(self):
        r = self._run("secret", "frobnicate")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown command", r.stderr)

    def test_missing_positional_shows_clean_usage_not_internal_name(self):
        # A command that needs a positional, invoked with none, must show
        # "my-claude <group> <command>" usage — never the underlying script
        # filename (viz-set-current.py / set-secret.py).
        for group, command in [("viz", "set"), ("secret", "set"), ("secret", "clear")]:
            r = self._run(group, command)
            self.assertEqual(r.returncode, 2, msg=f"{group} {command}: {r.stderr}")
            self.assertIn(f"usage: my-claude {group} {command}", r.stderr)
            self.assertNotIn(".py", r.stderr, msg=f"leaked internal name: {r.stderr}")

    def test_zero_arg_command_rejects_extra_args(self):
        r = self._run("viz", "clear", "UNEXPECTED")
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage: my-claude viz clear", r.stderr)
        self.assertNotIn(".py", r.stderr)

    def test_all_help_forms_emit_identical_text(self):
        # `help`, `-h`, `--help`, and no-args must all print the same usage.
        outs = {self._run(a).stdout for a in ("help", "-h", "--help")}
        outs.add(self._run().stdout)
        self.assertEqual(len(outs), 1, msg=f"help forms diverged: {outs}")

    def test_errors_go_to_stderr_with_empty_stdout(self):
        # Human-facing errors are written to stderr; stdout stays empty so the
        # CLI is safe to pipe.
        for args in (("bogus",), ("secret", "frobnicate"), ("viz", "set")):
            r = self._run(*args)
            self.assertEqual(r.returncode, 2, msg=f"{args}: {r.stderr}")
            self.assertEqual(r.stdout, "", msg=f"{args} leaked stdout: {r.stdout!r}")
            self.assertNotEqual(r.stderr, "", msg=f"{args}: empty stderr")

    def test_every_group_without_command_exits_2(self):
        # The "needs a command" guard applies to each group, listing its verbs.
        for group in ("viz", "statusline"):
            r = self._run(group)
            self.assertEqual(r.returncode, 2, msg=f"{group}: {r.stderr}")
            self.assertIn("needs a command", r.stderr)
            self.assertIn(group, r.stderr)

    def test_one_arg_commands_reject_extra_args(self):
        # Commands taking exactly one positional reject a second, with clean
        # usage (never the underlying script filename).
        for group, command in [
            ("viz", "set"),
            ("secret", "set"),
            ("secret", "clear"),
        ]:
            r = self._run(group, command, "A", "B")
            self.assertEqual(r.returncode, 2, msg=f"{group} {command}: {r.stderr}")
            self.assertIn(f"usage: my-claude {group} {command}", r.stderr)
            self.assertNotIn(".py", r.stderr)

    def test_zero_arg_commands_reject_extra_args(self):
        # `secret list` and `statusline install` take no positional.
        for group, command in [("secret", "list"), ("statusline", "install")]:
            r = self._run(group, command, "extra")
            self.assertEqual(r.returncode, 2, msg=f"{group} {command}: {r.stderr}")
            self.assertIn(f"usage: my-claude {group} {command}", r.stderr)

    def test_viz_set_rejects_whitespace_only_value(self):
        # The dispatcher rejects an all-whitespace value before routing (the
        # underlying script would otherwise accept it verbatim).
        r = self._run("viz", "set", "   ")
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.claude_dir / "viz-state.json").exists())

    def test_flag_looking_value_is_tracked_not_parsed_as_option(self):
        # A value that looks like an option (`--clear`, or a leading-dash key
        # behind an explicit `--`) is passed through as the file key, not
        # interpreted as a flag.
        r = self._run("viz", "set", "--clear")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        state = json.loads((self.claude_dir / "viz-state.json").read_text())
        self.assertEqual(state["current_file_key"], "--clear")
        r = self._run("viz", "set", "--", "-weirdkey")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        state = json.loads((self.claude_dir / "viz-state.json").read_text())
        self.assertEqual(state["current_file_key"], "-weirdkey")

    def test_relative_home_resolves_under_cwd(self):
        # A relative $HOME still works: state lands under cwd-relative HOME,
        # never escaping the sandbox (cwd pinned to tmpdir).
        rel = Path(self.tmpdir) / "relhome"
        r = subprocess.run(
            [str(MY_CLAUDE), "viz", "set", "K"],
            input="",
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": "relhome"},
            cwd=self.tmpdir,
            timeout=10.0,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertTrue((rel / ".claude" / "viz-state.json").exists())

    # ---- routing to underlying scripts ----

    def test_viz_set_routes_and_writes_state(self):
        r = self._run("viz", "set", "https://www.figma.com/board/ABCKEY/Foo")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        state = json.loads((self.claude_dir / "viz-state.json").read_text())
        self.assertEqual(state["current_file_key"], "ABCKEY")

    def test_viz_clear_routes(self):
        self._run("viz", "set", "ABCKEY")
        r = self._run("viz", "clear")
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.claude_dir / "viz-state.json").exists())

    def test_secret_list_routes(self):
        r = self._run("secret", "list")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("no secrets stored", r.stdout)

    def test_secret_clear_routes_with_key_arg(self):
        secrets = self.claude_dir / "secrets.env"
        secrets.parent.mkdir(parents=True, exist_ok=True)
        secrets.write_text("FOO=one\nBAR=two\n")
        r = self._run("secret", "clear", "BAR")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        contents = secrets.read_text()
        self.assertIn("FOO=one", contents)
        self.assertNotIn("BAR", contents)

    def test_secret_set_non_tty_refuses_via_route(self):
        # The trailing KEY arg must reach set-secret.py; non-tty stdin → exit 2.
        r = self._run("secret", "set", "FIGMA_PERSONAL_ACCESS_TOKEN")
        self.assertEqual(r.returncode, 2, msg=r.stderr)
        self.assertIn("not a tty", r.stderr)

    def test_statusline_install_routes(self):
        # Routes to install-statusline.py, which writes statusLine into the
        # isolated-HOME settings.json (idempotent; safe under temp HOME).
        r = self._run("statusline", "install")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        settings = json.loads((self.claude_dir / "settings.json").read_text())
        self.assertIn("statusLine", settings)
        self.assertTrue(
            settings["statusLine"]["command"].endswith("statusline.py"),
            msg=settings["statusLine"],
        )

    # ---- wiring ----

    def test_my_claude_is_executable_with_shebang(self):
        self.assertTrue(os.access(MY_CLAUDE, os.X_OK), f"{MY_CLAUDE} not executable")
        first = MY_CLAUDE.read_text().splitlines()[0]
        self.assertTrue(first.startswith("#!"), f"missing shebang: {first!r}")

    def test_missing_target_script_exits_1(self):
        # Copy only the entrypoint into an isolated dir (no sibling target
        # scripts). A valid route then resolves to a non-existent file → exit 1.
        isolated = Path(self.tmpdir) / "bin"
        isolated.mkdir(parents=True)
        copy = isolated / "my-claude"
        shutil.copy(MY_CLAUDE, copy)
        copy.chmod(0o755)
        r = _run([str(copy), "viz", "clear"], env_extra={"HOME": self.tmpdir})
        self.assertEqual(r.returncode, 1, msg=r.stdout + r.stderr)
        self.assertIn("missing target script", r.stderr)


def _load_module(path: Path, name: str):
    """Import a script by file path (hyphenated names can't use `import`).

    The module's `if __name__ == "__main__"` guard keeps main()/_main() from
    running on load.

    The scripts do a bare `from _lib import ...`; when run normally their own
    dir is sys.path[0]. importlib doesn't add it, so put scripts/ on the path
    (idempotent) — otherwise the import fails under `unittest discover`, which,
    unlike pytest, doesn't seed sys.path with the repo dirs.
    """
    scripts_dir = str(REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeFigmaResponse:
    """Minimal stand-in for urlopen()'s context-managed response.

    json.load(r) calls r.read(); we return canned JSON bytes.
    """

    def __init__(self, body: dict):
        self._bytes = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *_args):
        return self._bytes


class VizHookNetworkTests(unittest.TestCase):
    """Exercise viz-comments.py's network -> filter -> emit -> persist path
    in-process with a mocked Figma API (the suite's long-standing gap).

    Loaded as a module so we can patch STATE_PATH/SECRETS_PATH/TTL and stub
    urlopen; the subprocess tests cover the silent-no-op guards separately.
    """

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module(HOOK, "viz_comments_under_test")

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="viz_hook_net_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.state_path = Path(self.tmpdir) / ".claude" / "viz-state.json"
        self.secrets_path = Path(self.tmpdir) / ".claude" / "secrets.env"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets_path.write_text("FIGMA_PERSONAL_ACCESS_TOKEN=figp_test\n")
        # Point the module at our temp paths; neutralize TTL so a fixed
        # timestamp always counts as fresh.
        self._patches = [
            unittest.mock.patch.object(self.mod, "STATE_PATH", self.state_path),
            unittest.mock.patch.object(self.mod, "SECRETS_PATH", self.secrets_path),
            unittest.mock.patch.object(self.mod, "TTL_SECONDS", 10**12),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _write_state(self, *, key="KEY1", seen=None):
        self.state_path.write_text(
            json.dumps(
                {
                    "current_file_key": key,
                    "seen_comments": seen or [],
                    "updated_at": "2026-05-01T00:00:00Z",
                }
            )
        )

    def _run_with_body(self, body: dict) -> str:
        """Run _main() with urlopen stubbed to return `body`; return stdout."""
        buf = io.StringIO()
        with unittest.mock.patch(
            "urllib.request.urlopen", return_value=_FakeFigmaResponse(body)
        ):
            with contextlib.redirect_stdout(buf):
                self.mod._main()
        return buf.getvalue()

    def _state(self):
        return json.loads(self.state_path.read_text())

    def test_emits_envelope_and_filters_resolved_and_seen(self):
        self._write_state(seen=["c0"])
        body = {
            "comments": [
                {"id": "c0", "message": "old", "user": {"handle": "bob"}},  # seen
                {"id": "c1", "message": "needs work", "user": {"handle": "alice"}},
                {"id": "c2", "message": "done", "resolved_at": "2026-05-02T00:00:00Z"},
                {"id": "c3", "message": "no user", "user": "not-a-dict"},  # -> unknown
                "not-a-comment-dict",  # non-dict entry -> skipped
                {"id": 123, "message": "numeric id"},  # non-str id -> skipped
            ]
        }
        out = self._run_with_body(body)
        payload = json.loads(out)
        # Exact Claude Code UserPromptSubmit envelope.
        self.assertEqual(
            set(payload), {"hookSpecificOutput"}, msg=f"unexpected top-level: {payload}"
        )
        hso = payload["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "UserPromptSubmit")
        ctx = hso["additionalContext"]
        # Unseen, unresolved comments present with author + id + message.
        self.assertIn("[alice] (id=c1): needs work", ctx)
        self.assertIn("[unknown] (id=c3): no user", ctx)
        self.assertIn("2 unread", ctx)
        # Seen (c0) and resolved (c2) excluded.
        self.assertNotIn("c0", ctx)
        self.assertNotIn("c2", ctx)
        # Persisted seen = (old seen ∩ current ids) ∪ newly surfaced ids.
        self.assertEqual(self._state()["seen_comments"], ["c0", "c1", "c3"])

    def test_second_run_same_body_is_silent_noop(self):
        self._write_state()
        body = {"comments": [{"id": "c1", "message": "hi", "user": {"handle": "a"}}]}
        first = self._run_with_body(body)
        self.assertIn("hookSpecificOutput", first)
        self.assertEqual(self._state()["seen_comments"], ["c1"])
        # Same body again → already seen → no output.
        second = self._run_with_body(body)
        self.assertEqual(second, "", msg=f"expected silent re-run, got: {second!r}")

    def test_noop_turn_prunes_stale_seen_ids(self):
        # All comments already seen, plus a stale id the API no longer returns.
        self._write_state(seen=["c1", "stale"])
        body = {"comments": [{"id": "c1", "message": "hi", "user": {"handle": "a"}}]}
        out = self._run_with_body(body)
        self.assertEqual(out, "", "no unseen comments → no stdout")
        # 'stale' pruned (not in current_ids); 'c1' kept.
        self.assertEqual(self._state()["seen_comments"], ["c1"])

    def test_non_dict_api_body_is_noop(self):
        self._write_state()
        out = self._run_with_body(["not", "a", "dict"])  # body isn't a dict
        self.assertEqual(out, "")

    def test_comments_not_a_list_is_noop(self):
        self._write_state()
        out = self._run_with_body({"comments": "nope"})
        self.assertEqual(out, "")

    def test_urlopen_error_is_silent_noop(self):
        self._write_state()
        buf = io.StringIO()
        import urllib.error

        with unittest.mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("boom"),
        ):
            with contextlib.redirect_stdout(buf):
                self.mod._main()
        self.assertEqual(buf.getvalue(), "")
        # State untouched on network failure.
        self.assertEqual(self._state()["seen_comments"], [])

    def test_token_read_from_dotenv(self):
        # No env var set; token must come from the secrets.env we wrote.
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FIGMA_PERSONAL_ACCESS_TOKEN", None)
            self.assertEqual(self.mod._read_token(), "figp_test")


class SetSecretWritePathTests(unittest.TestCase):
    """The getpass success path (and empty-value abort) can't be driven via
    subprocess — set-secret.py refuses non-tty stdin. Exercise in-process with
    getpass + isatty mocked."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module(SET_SECRET, "set_secret_under_test")

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="set_secret_write_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.secrets_path = Path(self.tmpdir) / ".claude" / "secrets.env"
        p = unittest.mock.patch.object(self.mod, "SECRETS_PATH", self.secrets_path)
        p.start()
        self.addCleanup(p.stop)

    def _run_set(self, key, *, value):
        with unittest.mock.patch("sys.argv", ["set-secret.py", key]):
            with unittest.mock.patch("sys.stdin.isatty", return_value=True):
                with unittest.mock.patch.object(
                    self.mod.getpass, "getpass", return_value=value
                ):
                    return self.mod.main()

    def test_stores_value_with_mode_0600(self):
        rc = self._run_set("FIGMA_PERSONAL_ACCESS_TOKEN", value="figp_secret")
        self.assertEqual(rc, 0)
        env = self.mod._read()
        self.assertEqual(env["FIGMA_PERSONAL_ACCESS_TOKEN"], "figp_secret")
        mode = self.secrets_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, f"expected 0600, got 0o{mode:o}")

    def test_empty_value_aborts_without_writing(self):
        rc = self._run_set("FIGMA_PERSONAL_ACCESS_TOKEN", value="   ")
        self.assertEqual(rc, 2)
        self.assertFalse(self.secrets_path.exists())

    def test_upsert_preserves_other_keys(self):
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets_path.write_text("OTHER=keepme\n")
        rc = self._run_set("NEWKEY", value="newval")
        self.assertEqual(rc, 0)
        env = self.mod._read()
        self.assertEqual(env["OTHER"], "keepme")
        self.assertEqual(env["NEWKEY"], "newval")


if __name__ == "__main__":
    unittest.main()
