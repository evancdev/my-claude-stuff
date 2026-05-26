"""Tests for the viz workflow scripts.

Covers:
- scripts/viz-set-current.py — URL parsing, state file I/O, --clear, seen_comments
  preservation vs. reset across key switches.
- hooks/viz-comments-poll.py — silent no-op contract (no state, no token, expired
  TTL, malformed state), and JSON output schema for Claude Code's
  UserPromptSubmit hook (must use hookSpecificOutput.additionalContext, not a
  bare additionalContext key).

The hook is exercised without network access by relying on its silent no-op
branches; tests requiring the Figma API to return canned data are not included
(would require an HTTP mock server — flagged as a remaining gap).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SET_CURRENT = REPO_ROOT / "scripts" / "viz-set-current.py"
HOOK = REPO_ROOT / "hooks" / "viz-comments-poll.py"
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"


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
        self.addCleanup(self._rm_tmpdir)
        self.state_path = Path(self.tmpdir) / ".claude" / "viz-state.json"

    def _rm_tmpdir(self):
        # Best-effort recursive cleanup. We don't import shutil to keep it lean.
        for root, dirs, files in os.walk(self.tmpdir, topdown=False):
            for f in files:
                try:
                    os.unlink(os.path.join(root, f))
                except OSError:
                    pass
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass
        try:
            os.rmdir(self.tmpdir)
        except OSError:
            pass

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
        result = self._run_set(
            "https://www.figma.com/design/DESIGNKEY1/Some-File"
        )
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
        self.addCleanup(self._rm_tmpdir)
        self.state_path = Path(self.tmpdir) / ".claude" / "viz-state.json"

    def _rm_tmpdir(self):
        for root, dirs, files in os.walk(self.tmpdir, topdown=False):
            for f in files:
                try:
                    os.unlink(os.path.join(root, f))
                except OSError:
                    pass
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass
        try:
            os.rmdir(self.tmpdir)
        except OSError:
            pass

    def _run_hook(self, *, token=None):
        env_unset = () if token is not None else ("FIGMA_PERSONAL_ACCESS_TOKEN",)
        env_extra = {"HOME": self.tmpdir}
        if token is not None:
            env_extra["FIGMA_PERSONAL_ACCESS_TOKEN"] = token
        return _run([str(HOOK)], env_extra=env_extra, env_unset=env_unset)

    def _write_state(self, obj):
        os.makedirs(self.state_path.parent, exist_ok=True)
        self.state_path.write_text(
            obj if isinstance(obj, str) else json.dumps(obj)
        )

    def _assert_silent(self, result):
        self.assertEqual(
            result.returncode, 0,
            msg=f"hook crashed: stderr={result.stderr!r} stdout={result.stdout!r}",
        )
        self.assertEqual(result.stderr, "", msg=f"hook leaked stderr: {result.stderr!r}")
        self.assertEqual(result.stdout, "", msg=f"hook leaked stdout: {result.stdout!r}")

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
        self._write_state(
            {"current_file_key": "ABC", "seen_comments": []}
        )
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
                "viz-comments-poll.py must emit JSON shaped as "
                "{'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', "
                "'additionalContext': ...}}; a bare 'additionalContext' key is "
                "ignored by the hook harness so injected comments would never "
                "reach the model."
            ),
        )
        self.assertIn("UserPromptSubmit", src)


class VizWiringTests(unittest.TestCase):
    """Sanity-check the on-disk wiring."""

    def test_hooks_json_is_valid_json_and_registers_userPromptSubmit(self):
        data = json.loads(HOOKS_JSON.read_text())
        self.assertIn("hooks", data)
        self.assertIn("UserPromptSubmit", data["hooks"])
        entries = data["hooks"]["UserPromptSubmit"]
        self.assertTrue(entries, "expected at least one UserPromptSubmit entry")
        inner = entries[0]["hooks"]
        self.assertTrue(any(h.get("command", "").endswith("viz-comments-poll.py") for h in inner))

    def test_set_current_script_is_executable_with_shebang(self):
        self.assertTrue(os.access(SET_CURRENT, os.X_OK), f"{SET_CURRENT} not executable")
        first = SET_CURRENT.read_text().splitlines()[0]
        self.assertTrue(first.startswith("#!"), f"missing shebang: {first!r}")

    def test_hook_script_is_executable_with_shebang(self):
        self.assertTrue(os.access(HOOK, os.X_OK), f"{HOOK} not executable")
        first = HOOK.read_text().splitlines()[0]
        self.assertTrue(first.startswith("#!"), f"missing shebang: {first!r}")


if __name__ == "__main__":
    unittest.main()
