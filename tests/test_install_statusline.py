"""Test suite for scripts/install-statusline.py.

Invokes the installer as a subprocess with HOME overridden to a temp directory
so the real ~/.claude/settings.json is never touched.

Contract under test (in order matching script flow):
- Locates sibling statusline.py next to the installer.
- Errors (exit 1, stderr) if sibling missing or is not a regular file.
- chmods sibling +x for u/g/o, preserving other bits.
- Bootstraps ~/.claude/ and ~/.claude/settings.json when absent.
- Reads settings.json; errors (exit 1, stderr "is not valid JSON ...") on
  parse failure or non-object root.
- Three statusLine cases: identical (no-op), absent (silent write), different
  (warning to stderr + write).
- Atomic writes via os.replace (no leftover temp files, no half-write).
- JSON formatted with indent=2 + trailing newline; other top-level keys preserved.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "scripts" / "install-statusline.py"
SIBLING = REPO_ROOT / "scripts" / "statusline.py"

SUCCESS_LINE_3 = "done. restart Claude Code (or /reload-plugins) to see it."


def run_installer(
    home: Path | str,
    *,
    installer: Path = INSTALLER,
    timeout: float = 15.0,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the installer with HOME overridden. Other env minimal."""
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(installer)],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def make_sandboxed_installer(
    sibling_content: str | None,
    sibling_mode: int | None = None,
    *,
    sibling_is_symlink_to: Path | None = None,
    sibling_is_dir: bool = False,
) -> tuple[Path, Path]:
    """Build a temp dir with the installer + optional sibling configurations.

    - sibling_content=None and sibling_is_dir=False and sibling_is_symlink_to=None
      → no sibling created (missing-sibling case).
    - sibling_is_dir=True → create statusline.py as a directory.
    - sibling_is_symlink_to=<path> → create symlink to that path.
    - else sibling_content=str → write a regular file with that body.

    Returns (installer_path, sandbox_dir).
    """
    sandbox = Path(tempfile.mkdtemp(prefix="installer_sandbox_"))
    installer_dst = sandbox / "install-statusline.py"
    shutil.copy2(INSTALLER, installer_dst)
    installer_dst.chmod(0o755)
    # The installer imports its sibling _lib; the whole scripts/ dir ships
    # together, so the sandbox must carry _lib alongside it.
    shutil.copy2(REPO_ROOT / "scripts" / "_lib.py", sandbox / "_lib.py")

    sibling = sandbox / "statusline.py"
    if sibling_is_dir:
        sibling.mkdir()
    elif sibling_is_symlink_to is not None:
        os.symlink(str(sibling_is_symlink_to), str(sibling))
    elif sibling_content is not None:
        sibling.write_text(sibling_content)
        if sibling_mode is not None:
            sibling.chmod(sibling_mode)
    return installer_dst, sandbox


class InstallerTestBase(unittest.TestCase):
    """Shared helpers + per-test temp HOME."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="install_statusline_test_"))
        self.addCleanup(self._cleanup_tmp, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.claude_dir = self.home / ".claude"
        self.settings_path = self.claude_dir / "settings.json"

    @staticmethod
    def _cleanup_tmp(path: Path) -> None:
        # Restore perms so files we chmodded restrictively can still be removed.
        for root, dirs, files in os.walk(path, topdown=False):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o700)
                except OSError:
                    pass
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), 0o600)
                except OSError:
                    pass
        shutil.rmtree(path, ignore_errors=True)

    def write_settings(self, data) -> None:
        """JSON-encode `data` into settings.json (creates parent dir)."""
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(data, indent=2) + "\n")

    def write_settings_raw(self, raw: str) -> None:
        """Write arbitrary text as settings.json (for malformed JSON tests)."""
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(raw)

    def read_settings(self):
        return json.loads(self.settings_path.read_text())

    def desired_command(self) -> str:
        return str(SIBLING.resolve())

    def desired_entry(self) -> dict:
        return {"type": "command", "command": self.desired_command()}

    def assert_clean_success(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(
            result.returncode,
            0,
            f"expected exit 0, got {result.returncode}; stderr={result.stderr!r}",
        )

    def assert_three_line_success(
        self,
        result: subprocess.CompletedProcess,
    ) -> None:
        expected_lines = [
            f"wrote statusLine -> {self.desired_command()}",
            f"  into {self.settings_path}",
            SUCCESS_LINE_3,
        ]
        actual = result.stdout.splitlines()
        self.assertEqual(
            actual,
            expected_lines,
            f"stdout did not match 3-line success block: {result.stdout!r}",
        )

    def assert_no_temp_leftovers(self) -> None:
        if not self.claude_dir.exists():
            return
        names = sorted(p.name for p in self.claude_dir.iterdir())
        self.assertEqual(
            names,
            ["settings.json"],
            f"unexpected leftover files in {self.claude_dir}: {names}",
        )


# =============================================================================
# Missing / invalid sibling
# =============================================================================


class MissingSiblingTests(InstallerTestBase):
    """Spec: missing or non-file statusline.py → stderr error + exit 1."""

    def test_missing_sibling_exits_1_with_stderr_error(self):
        installer, sandbox = make_sandboxed_installer(sibling_content=None)
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        result = run_installer(self.home, installer=installer)
        self.assertEqual(result.returncode, 1)
        expected_missing = sandbox.resolve() / "statusline.py"
        self.assertIn(
            f"error: statusline.py not found at {expected_missing}",
            result.stderr,
        )

    def test_missing_sibling_no_success_on_stdout(self):
        installer, sandbox = make_sandboxed_installer(sibling_content=None)
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        result = run_installer(self.home, installer=installer)
        self.assertNotIn("wrote statusLine", result.stdout)
        self.assertNotIn("done.", result.stdout)

    def test_missing_sibling_does_not_create_claude_dir(self):
        # mkdir comes AFTER the sibling check; failure must not touch ~/.claude.
        installer, sandbox = make_sandboxed_installer(sibling_content=None)
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        run_installer(self.home, installer=installer)
        self.assertFalse(self.claude_dir.exists())

    def test_sibling_is_a_directory_treated_as_missing(self):
        installer, sandbox = make_sandboxed_installer(
            sibling_content=None, sibling_is_dir=True
        )
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        result = run_installer(self.home, installer=installer)
        self.assertEqual(result.returncode, 1)
        self.assertIn("error: statusline.py not found at", result.stderr)


# =============================================================================
# Executable bit
# =============================================================================


class ExecutableBitTests(InstallerTestBase):
    """Spec: set u+x, g+x, o+x on statusline.py, preserve other bits."""

    def _with_sibling(self, mode: int) -> tuple[Path, Path]:
        installer, sandbox = make_sandboxed_installer(
            sibling_content="#!/usr/bin/env python3\nprint('hi')\n",
            sibling_mode=mode,
        )
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        return installer, sandbox

    def test_adds_x_bits_when_starting_from_0644(self):
        installer, sandbox = self._with_sibling(0o644)
        result = run_installer(self.home, installer=installer)
        self.assert_clean_success(result)
        m = stat.S_IMODE(os.stat(sandbox / "statusline.py").st_mode)
        self.assertTrue(m & stat.S_IXUSR)
        self.assertTrue(m & stat.S_IXGRP)
        self.assertTrue(m & stat.S_IXOTH)
        # Original read/write bits preserved.
        self.assertTrue(m & stat.S_IRUSR)
        self.assertTrue(m & stat.S_IWUSR)
        self.assertTrue(m & stat.S_IRGRP)
        self.assertTrue(m & stat.S_IROTH)

    def test_idempotent_when_already_0755(self):
        installer, sandbox = self._with_sibling(0o755)
        result = run_installer(self.home, installer=installer)
        self.assert_clean_success(result)
        self.assertEqual(
            stat.S_IMODE(os.stat(sandbox / "statusline.py").st_mode), 0o755
        )

    def test_preserves_unusual_other_bits(self):
        # 0640: owner rw, group r, others none. After install:
        # - u/g/o x bits SET
        # - existing r/w bits preserved
        # - o-r still NOT set (spec only adds x bits, not r bits)
        installer, sandbox = self._with_sibling(0o640)
        result = run_installer(self.home, installer=installer)
        self.assert_clean_success(result)
        m = stat.S_IMODE(os.stat(sandbox / "statusline.py").st_mode)
        self.assertTrue(m & stat.S_IXUSR)
        self.assertTrue(m & stat.S_IXGRP)
        self.assertTrue(m & stat.S_IXOTH)
        self.assertTrue(m & stat.S_IRUSR)
        self.assertTrue(m & stat.S_IWUSR)
        self.assertTrue(m & stat.S_IRGRP)
        self.assertFalse(m & stat.S_IROTH)

    def test_sibling_is_symlink_to_external_target(self):
        # The sibling is a symlink; the real file lives elsewhere.
        target_dir = Path(tempfile.mkdtemp(prefix="installer_target_"))
        self.addCleanup(shutil.rmtree, target_dir, ignore_errors=True)
        target = target_dir / "real_statusline.py"
        target.write_text("#!/usr/bin/env python3\nprint('hi')\n")
        target.chmod(0o644)

        installer, sandbox = make_sandboxed_installer(
            sibling_content=None, sibling_is_symlink_to=target
        )
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)

        result = run_installer(self.home, installer=installer)
        self.assert_clean_success(result)
        data = self.read_settings()
        self.assertIn("statusLine", data)
        cmd = data["statusLine"]["command"]
        # Recorded path is the SIBLING location (the symlink itself), not the
        # resolved target. script_dir is resolved but the join `/ "statusline.py"`
        # doesn't re-resolve the final component — pin this so a future
        # refactor that adds .resolve() to the final path is a deliberate change.
        expected = str(sandbox.resolve() / "statusline.py")
        self.assertEqual(cmd, expected)
        self.assertNotEqual(cmd, str(target))
        # Path.chmod follows symlinks by default → target gets x bits.
        m = stat.S_IMODE(os.stat(target).st_mode)
        self.assertTrue(m & stat.S_IXUSR)
        self.assertTrue(m & stat.S_IXGRP)
        self.assertTrue(m & stat.S_IXOTH)


# =============================================================================
# Bootstrap: missing dir / missing file
# =============================================================================


class BootstrapTests(InstallerTestBase):
    """Spec: mkdir -p ~/.claude and create settings.json={} as needed."""

    def test_creates_missing_claude_directory(self):
        self.assertFalse(self.claude_dir.exists())
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assertTrue(self.claude_dir.is_dir())

    def test_creates_settings_file_when_completely_absent(self):
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assertTrue(self.settings_path.is_file())
        self.assertEqual(self.read_settings(), {"statusLine": self.desired_entry()})

    def test_creates_settings_when_dir_exists_but_file_does_not(self):
        self.claude_dir.mkdir(parents=True)
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assertEqual(self.read_settings().get("statusLine"), self.desired_entry())

    def test_first_install_prints_three_line_success(self):
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assert_three_line_success(result)

    def test_first_install_emits_nothing_on_stderr(self):
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assertEqual(result.stderr, "")

    def test_empty_object_settings_gets_entry(self):
        self.write_settings({})
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assert_three_line_success(result)
        self.assertEqual(self.read_settings(), {"statusLine": self.desired_entry()})

    def test_claude_dir_path_is_a_file_errors_out(self):
        # ~/.claude exists but is a regular file → mkdir fails.
        (self.home / ".claude").write_text("not a dir")
        result = run_installer(self.home)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("error:", result.stderr)
        # The path must still be the file we wrote (no replacement).
        self.assertTrue((self.home / ".claude").is_file())

    def test_settings_path_is_a_directory_errors_out(self):
        # settings.json exists as a directory → read/write fails.
        self.claude_dir.mkdir(parents=True)
        self.settings_path.mkdir()
        result = run_installer(self.home)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("error:", result.stderr)
        self.assertTrue(self.settings_path.is_dir())


# =============================================================================
# HOME env var validation
# =============================================================================


class HomeEnvTests(InstallerTestBase):
    """Installer must handle missing/empty/invalid HOME gracefully."""

    def test_empty_home_emits_structured_error(self):
        result = run_installer("")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)
        self.assertIn("HOME", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_relative_home_is_rejected(self):
        # Relative HOME would silently write to cwd-relative paths — reject.
        result = run_installer("relative/path")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)
        self.assertIn("HOME", result.stderr)
        self.assertIn("absolute", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_dot_home_is_rejected(self):
        # `HOME=.` is also relative.
        result = run_installer(".")
        self.assertEqual(result.returncode, 1)
        self.assertIn("absolute", result.stderr)

    def test_non_directory_home_emits_structured_error(self):
        # HOME pointing at a non-directory file (e.g. /dev/null) — mkdir
        # fails. Result must be a structured 'error:' line, not a traceback.
        result = run_installer("/dev/null")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


# =============================================================================
# JSON parse failures
# =============================================================================


class JsonParseFailureTests(InstallerTestBase):
    """Spec: parse failure → stderr 'is not valid JSON', exit 1, file untouched."""

    def _expect_error(self, raw: str) -> subprocess.CompletedProcess:
        self.write_settings_raw(raw)
        original = self.settings_path.read_text()
        result = run_installer(self.home)
        self.assertEqual(result.returncode, 1, f"stderr={result.stderr!r}")
        self.assertIn(
            f"error: {self.settings_path} is not valid JSON",
            result.stderr,
        )
        self.assertNotIn("wrote statusLine", result.stdout)
        self.assertNotIn("done.", result.stdout)
        # Existing file must be untouched on the error path.
        self.assertEqual(self.settings_path.read_text(), original)
        return result

    def test_malformed_json(self):
        self._expect_error("{this is not json")

    def test_truncated_json(self):
        self._expect_error('{"foo": ')

    def test_trailing_comma(self):
        self._expect_error('{"foo": 1,}')

    def test_empty_file(self):
        self._expect_error("")

    def test_bom_at_start_of_file(self):
        # UTF-8 BOM bytes prefixing valid JSON → json.loads rejects.
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_bytes(b"\xef\xbb\xbf" + b'{"foo": 1}')
        original = self.settings_path.read_bytes()
        result = run_installer(self.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            f"error: {self.settings_path} is not valid JSON",
            result.stderr,
        )
        self.assertEqual(self.settings_path.read_bytes(), original)

    def test_leading_whitespace_is_allowed(self):
        # JSON RFC permits leading whitespace; this is a happy path.
        self.write_settings_raw('   \n\t  {"foo": "bar"}\n')
        result = run_installer(self.home)
        self.assert_clean_success(result)
        data = self.read_settings()
        self.assertEqual(data.get("foo"), "bar")
        self.assertEqual(data.get("statusLine"), self.desired_entry())

    def test_non_utf8_bytes_rejected_with_structured_error(self):
        # Binary garbage that isn't valid UTF-8 → graceful error, no traceback.
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_bytes(b"\xff\xfe\x00invalid\xff")
        original = self.settings_path.read_bytes()
        result = run_installer(self.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)
        self.assertIn("is not valid JSON", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.settings_path.read_bytes(), original)


# =============================================================================
# Non-object top-level → rejected
# =============================================================================


class NonObjectTopLevelTests(InstallerTestBase):
    """Spec: top-level must be a JSON object; otherwise error like parse failure."""

    def _expect_rejected(self, raw: str) -> None:
        self.write_settings_raw(raw)
        original = self.settings_path.read_text()
        result = run_installer(self.home)
        self.assertEqual(result.returncode, 1, f"stderr={result.stderr!r}")
        self.assertIn(
            f"error: {self.settings_path} is not valid JSON",
            result.stderr,
        )
        self.assertNotIn("wrote statusLine", result.stdout)
        # Source file untouched.
        self.assertEqual(self.settings_path.read_text(), original)

    def test_array_rejected(self):
        self._expect_rejected("[1, 2, 3]")

    def test_string_rejected(self):
        self._expect_rejected('"hello"')

    def test_number_rejected(self):
        self._expect_rejected("42")

    def test_true_rejected(self):
        self._expect_rejected("true")

    def test_false_rejected(self):
        self._expect_rejected("false")

    def test_null_rejected(self):
        self._expect_rejected("null")


# =============================================================================
# Case (a): existing entry already matches desired → no-op
# =============================================================================


class CaseAAlreadyCorrectTests(InstallerTestBase):
    """Spec rule #9(a): identical entry → print no-op message, do NOT rewrite."""

    def _seed(self, extra: dict | None = None) -> None:
        data: dict = {"statusLine": self.desired_entry()}
        if extra:
            data.update(extra)
        self.write_settings(data)

    def test_prints_no_change_message_to_stdout(self):
        self._seed()
        result = run_installer(self.home)
        self.assert_clean_success(result)
        expected = f"statusLine already points to {self.desired_command()} — no change"
        self.assertIn(expected, result.stdout)

    def test_no_op_emits_nothing_on_stderr(self):
        self._seed()
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assertEqual(result.stderr, "")

    def test_no_op_does_not_print_success_write_block(self):
        self._seed()
        result = run_installer(self.home)
        self.assertNotIn("wrote statusLine ->", result.stdout)
        self.assertNotIn("done.", result.stdout)

    def test_no_op_mtime_unchanged(self):
        self._seed({"permissions": {"allow": ["Bash(ls:*)"]}})
        # Push mtime far into the past so any rewrite would be detectable.
        old_time = time.time() - 3600
        os.utime(self.settings_path, (old_time, old_time))
        before = self.settings_path.stat().st_mtime_ns
        result = run_installer(self.home)
        self.assert_clean_success(result)
        after = self.settings_path.stat().st_mtime_ns
        self.assertEqual(before, after, "settings.json must not be rewritten on no-op")

    def test_no_op_preserves_exact_byte_content(self):
        self._seed({"permissions": {"allow": ["Bash(ls:*)"]}})
        original_bytes = self.settings_path.read_bytes()
        run_installer(self.home)
        self.assertEqual(self.settings_path.read_bytes(), original_bytes)

    def test_no_op_no_temp_leftovers(self):
        self._seed()
        run_installer(self.home)
        self.assert_no_temp_leftovers()

    def test_no_op_with_extra_keys_keeps_them(self):
        self._seed({"theme": "dark", "permissions": {"allow": ["Bash(ls:*)"]}})
        run_installer(self.home)
        data = self.read_settings()
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls:*)"]})
        self.assertEqual(data["statusLine"], self.desired_entry())


# =============================================================================
# Case (b): statusLine absent → silent write + success block
# =============================================================================


class CaseBAbsentTests(InstallerTestBase):
    """Spec rule #9(b): absent key → silent write, 3-line success on stdout."""

    def test_writes_entry_and_prints_success(self):
        self.write_settings({"foo": "bar"})
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assert_three_line_success(result)
        data = self.read_settings()
        self.assertEqual(data["statusLine"], self.desired_entry())
        self.assertEqual(data["foo"], "bar")

    def test_absent_emits_no_stderr(self):
        self.write_settings({"foo": "bar"})
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assertEqual(result.stderr, "")

    def test_absent_from_empty_object(self):
        self.write_settings({})
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assert_three_line_success(result)
        self.assertEqual(self.read_settings(), {"statusLine": self.desired_entry()})

    def test_absent_from_bootstrap(self):
        # No file at all → bootstrap → write.
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assert_three_line_success(result)
        self.assertEqual(self.read_settings(), {"statusLine": self.desired_entry()})


# =============================================================================
# Case (c): present but different → warning to stderr, overwrite
# =============================================================================


class CaseCDifferentTests(InstallerTestBase):
    """Spec rule #9(c): different entry → 'warning: replacing...' + overwrite."""

    def _assert_warning_and_overwrite(
        self,
        existing: object,
        old_string_in_warning: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Seed settings with `existing` under statusLine; assert warn+overwrite."""
        self.write_settings({"statusLine": existing})
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assertIn("warning:", result.stderr)
        self.assertIn("replacing existing statusLine command", result.stderr)
        if old_string_in_warning is not None:
            self.assertIn(old_string_in_warning, result.stderr)
        self.assertEqual(self.read_settings()["statusLine"], self.desired_entry())
        return result

    def test_different_command_warns_and_overwrites(self):
        result = self._assert_warning_and_overwrite(
            {"type": "command", "command": "/old/path/to/statusline.py"},
            old_string_in_warning="/old/path/to/statusline.py",
        )
        self.assert_three_line_success(result)

    def test_different_type_warns_and_overwrites(self):
        # When only `type` differs, the warning should still show enough to
        # convey what's being replaced (not just our own command path).
        result = self._assert_warning_and_overwrite(
            {"type": "shell", "command": self.desired_command()},
        )
        self.assertIn("shell", result.stderr)

    def test_extra_subkeys_warns_and_overwrites(self):
        # Same command, but extra "padding" key → not deep-equal → case (c).
        self._assert_warning_and_overwrite(
            {"type": "command", "command": self.desired_command(), "padding": True},
        )

    def test_nested_matching_command_with_extra_keys_overwrites(self):
        self._assert_warning_and_overwrite(
            {"type": "command", "command": self.desired_command(), "icon": "robot"},
        )

    def test_statusline_is_array(self):
        self._assert_warning_and_overwrite(["not", "a", "dict"])

    def test_statusline_is_string(self):
        # String form: the string itself appears in the warning (it's the "command").
        self._assert_warning_and_overwrite(
            "/legacy/string/form",
            old_string_in_warning="/legacy/string/form",
        )

    def test_statusline_is_number(self):
        self._assert_warning_and_overwrite(42)

    def test_statusline_is_null(self):
        self._assert_warning_and_overwrite(None)

    def test_warning_not_on_stdout(self):
        self.write_settings({"statusLine": {"type": "command", "command": "/some/old"}})
        result = run_installer(self.home)
        self.assertNotIn("warning:", result.stdout)


# =============================================================================
# Atomic write
# =============================================================================


class AtomicWriteTests(InstallerTestBase):
    """Spec rule #10: writes use temp file + os.replace; no leftovers; no half-write."""

    def test_no_temp_files_after_fresh_install(self):
        run_installer(self.home)
        self.assert_no_temp_leftovers()

    def test_no_temp_files_after_overwrite(self):
        self.write_settings({"statusLine": {"type": "command", "command": "/x"}})
        run_installer(self.home)
        self.assert_no_temp_leftovers()

    def test_no_temp_files_after_no_op(self):
        self.write_settings({"statusLine": self.desired_entry()})
        run_installer(self.home)
        self.assert_no_temp_leftovers()

    def test_no_temp_files_after_bootstrap(self):
        self.assertFalse(self.claude_dir.exists())
        run_installer(self.home)
        self.assert_no_temp_leftovers()

    def test_inode_changes_on_overwrite(self):
        # Atomic replace yields a new inode (vs in-place truncate-write).
        self.write_settings({"statusLine": {"type": "command", "command": "/x"}})
        old_inode = self.settings_path.stat().st_ino
        run_installer(self.home)
        new_inode = self.settings_path.stat().st_ino
        self.assertNotEqual(
            old_inode, new_inode, "atomic write should produce a new inode"
        )

    def test_write_failure_leaves_settings_intact(self):
        """If write fails, installer must exit non-zero AND leave file intact.

        Force failure by making file AND parent dir read-only. With atomic
        write, the original file is byte-identical on failure.
        """
        original = {
            "foo": "bar",
            "statusLine": {"type": "command", "command": "/old"},
        }
        self.write_settings(original)
        original_bytes = self.settings_path.read_bytes()

        os.chmod(self.settings_path, 0o400)
        os.chmod(self.claude_dir, 0o500)
        self.addCleanup(lambda: os.chmod(self.claude_dir, 0o700))
        self.addCleanup(lambda: os.chmod(self.settings_path, 0o600))

        result = run_installer(self.home)
        self.assertNotEqual(
            result.returncode,
            0,
            "installer should exit non-zero when settings dir/file are read-only",
        )

        # File must still be readable + valid JSON.
        try:
            content = self.settings_path.read_text()
        except OSError:
            self.fail("settings.json disappeared after failed write")
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            self.fail(f"settings.json was left in invalid-JSON state: {e}")

        # Atomic-write invariant: original file byte-identical on failure.
        self.assertEqual(
            self.settings_path.read_bytes(),
            original_bytes,
            "atomic-write invariant: original file modified despite failure",
        )

    def test_no_python_traceback_on_write_failure(self):
        """Failure path must emit structured 'error:' line, not a raw traceback."""
        self.write_settings({"statusLine": {"type": "command", "command": "/old"}})
        os.chmod(self.settings_path, 0o400)
        os.chmod(self.claude_dir, 0o500)
        self.addCleanup(lambda: os.chmod(self.claude_dir, 0o700))
        self.addCleanup(lambda: os.chmod(self.settings_path, 0o600))

        result = run_installer(self.home)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("error:", result.stderr)

    def test_no_temp_file_leftover_after_failed_write(self):
        """A failed atomic write must not leave a .tmp file in .claude/."""
        self.write_settings({"statusLine": {"type": "command", "command": "/old"}})
        os.chmod(self.settings_path, 0o400)
        os.chmod(self.claude_dir, 0o500)
        self.addCleanup(lambda: os.chmod(self.claude_dir, 0o700))
        self.addCleanup(lambda: os.chmod(self.settings_path, 0o600))

        result = run_installer(self.home)
        self.assertNotEqual(result.returncode, 0)

        # Restore perms so we can list the directory.
        os.chmod(self.claude_dir, 0o700)
        leftovers = sorted(
            p.name for p in self.claude_dir.iterdir() if p.name != "settings.json"
        )
        self.assertEqual(
            leftovers, [], f"unexpected temp files after failed write: {leftovers}"
        )

    def test_preserves_file_mode_on_overwrite(self):
        """Overwriting an existing settings.json must preserve its mode bits."""
        self.write_settings({"statusLine": {"type": "command", "command": "/old"}})
        os.chmod(self.settings_path, 0o644)
        result = run_installer(self.home)
        self.assert_clean_success(result)
        final_mode = stat.S_IMODE(os.stat(self.settings_path).st_mode)
        self.assertEqual(
            final_mode,
            0o644,
            f"expected mode 0o644 preserved, got 0o{final_mode:o}",
        )

    def test_bootstrap_settings_file_mode_is_0o600(self):
        """Fresh install: settings.json is created at mkstemp's 0o600 default.

        Pinning this — it's defensible (private by default) and avoids
        depending on the user's umask. A widen-to-0o644 change would be
        deliberate and would update this test.
        """
        result = run_installer(self.home)
        self.assert_clean_success(result)
        mode = stat.S_IMODE(os.stat(self.settings_path).st_mode)
        self.assertEqual(mode, 0o600, f"expected 0o600 bootstrap, got 0o{mode:o}")

    def test_atomic_write_follows_settings_json_symlink(self):
        """If settings.json is a symlink, write through it — do not clobber.

        Dotfile managers (chezmoi, yadm, manual stow) often symlink
        ~/.claude/settings.json into a repo. os.replace replaces the symlink
        itself unless we resolve through it first.
        """
        real_dir = self.tmp / "real_dotfiles"
        real_dir.mkdir()
        real_settings = real_dir / "real_settings.json"
        real_settings.write_text(json.dumps({"foo": "bar"}, indent=2) + "\n")
        os.chmod(real_settings, 0o644)

        self.claude_dir.mkdir(parents=True)
        os.symlink(str(real_settings), str(self.settings_path))

        result = run_installer(self.home)
        self.assert_clean_success(result)

        # The symlink must still be a symlink, still pointing at real_settings.
        self.assertTrue(
            self.settings_path.is_symlink(),
            "symlink was clobbered into a regular file",
        )
        self.assertEqual(os.readlink(str(self.settings_path)), str(real_settings))
        # The real file (behind the symlink) got the update.
        data = json.loads(real_settings.read_text())
        self.assertEqual(data["statusLine"], self.desired_entry())
        self.assertEqual(data["foo"], "bar")
        # And its mode bits were preserved.
        real_mode = stat.S_IMODE(os.stat(real_settings).st_mode)
        self.assertEqual(real_mode, 0o644)

    def test_dangling_settings_symlink_bootstraps_through(self):
        """Settings.json is a symlink to a non-existent file in an existing dir.

        Installer should follow the symlink, bootstrap a new file at the
        target, and write through it — leaving the symlink intact.
        """
        real_dir = self.tmp / "dotfiles"
        real_dir.mkdir()
        target = real_dir / "real_settings.json"
        # Do NOT create target — the symlink is dangling.

        self.claude_dir.mkdir(parents=True)
        os.symlink(str(target), str(self.settings_path))

        # Sanity: symlink exists, target doesn't.
        self.assertTrue(self.settings_path.is_symlink())
        self.assertFalse(target.exists())

        result = run_installer(self.home)
        self.assert_clean_success(result)

        # Symlink intact.
        self.assertTrue(self.settings_path.is_symlink())
        self.assertEqual(os.readlink(str(self.settings_path)), str(target))
        # Target now exists with our content.
        self.assertTrue(target.is_file())
        data = json.loads(target.read_text())
        self.assertEqual(data, {"statusLine": self.desired_entry()})


# =============================================================================
# Preserving other top-level keys
# =============================================================================


class PreserveOtherKeysTests(InstallerTestBase):
    """Spec rule #11: keys other than statusLine round-trip exactly."""

    def test_unrelated_keys_survive_fresh_install(self):
        original = {
            "permissions": {"allow": ["Bash(ls:*)", "Bash(git status:*)"]},
            "effortLevel": "high",
            "model": "opus",
            "env": {"FOO": "bar", "BAZ": "qux"},
        }
        self.write_settings(original)
        result = run_installer(self.home)
        self.assert_clean_success(result)
        data = self.read_settings()
        for key, value in original.items():
            self.assertEqual(data.get(key), value, f"key {key!r} mutated")
        self.assertEqual(data["statusLine"], self.desired_entry())

    def test_unrelated_keys_survive_overwrite(self):
        original = {
            "statusLine": {"type": "command", "command": "/old/cmd"},
            "permissions": {"allow": ["Bash(ls:*)"]},
            "effortLevel": "medium",
            "nested": {"deep": {"value": [1, 2, {"a": "b"}]}},
        }
        self.write_settings(original)
        result = run_installer(self.home)
        self.assert_clean_success(result)
        data = self.read_settings()
        self.assertEqual(data["permissions"], original["permissions"])
        self.assertEqual(data["effortLevel"], original["effortLevel"])
        self.assertEqual(data["nested"], original["nested"])
        self.assertEqual(data["statusLine"], self.desired_entry())

    def test_deeply_nested_values_round_trip(self):
        nested = {
            "permissions": {
                "allow": ["Bash(git status)", "Bash(ls *)"],
                "deny": [],
                "ask": [{"tool": "WebFetch", "scope": "*"}],
            },
            "theme": "dark",
            "env": {"DEBUG": "1", "PATH_OVERRIDE": "/usr/local/bin"},
        }
        self.write_settings(nested)
        run_installer(self.home)
        data = self.read_settings()
        self.assertEqual(data["permissions"], nested["permissions"])
        self.assertEqual(data["theme"], nested["theme"])
        self.assertEqual(data["env"], nested["env"])

    def test_unicode_and_special_chars_preserved(self):
        weird = {
            "emoji_key": "café résumé naïve",
            "nested": {"path": "/Users/me/Some Path/with spaces & symbols!"},
        }
        self.write_settings(weird)
        run_installer(self.home)
        data = self.read_settings()
        self.assertEqual(data["emoji_key"], weird["emoji_key"])
        self.assertEqual(data["nested"], weird["nested"])

    def test_no_extra_keys_introduced(self):
        self.write_settings({"effortLevel": "high"})
        run_installer(self.home)
        data = self.read_settings()
        self.assertEqual(set(data.keys()), {"effortLevel", "statusLine"})


# =============================================================================
# File format
# =============================================================================


class FileFormatTests(InstallerTestBase):
    """Spec rule #12: indent=2, exactly one trailing newline, no tabs."""

    def test_ends_with_single_trailing_newline(self):
        run_installer(self.home)
        text = self.settings_path.read_text()
        self.assertTrue(text.endswith("\n"), f"no trailing newline: {text[-10:]!r}")
        self.assertFalse(text.endswith("\n\n"), "extra trailing newlines")

    def test_uses_two_space_indent_no_tabs(self):
        self.write_settings({"effortLevel": "high"})
        run_installer(self.home)
        text = self.settings_path.read_text()
        self.assertIn('\n  "effortLevel"', text)
        self.assertIn('\n  "statusLine"', text)
        self.assertNotIn("\t", text)

    def test_output_is_valid_json(self):
        run_installer(self.home)
        json.loads(self.settings_path.read_text())

    def test_desired_command_path_is_absolute(self):
        self.write_settings({})
        run_installer(self.home)
        cmd = self.read_settings()["statusLine"]["command"]
        self.assertTrue(os.path.isabs(cmd), f"not absolute: {cmd!r}")

    def test_desired_entry_shape_exact(self):
        # Exactly {"type": "command", "command": <path>}, no extra keys.
        self.write_settings({})
        run_installer(self.home)
        entry = self.read_settings()["statusLine"]
        self.assertEqual(set(entry.keys()), {"type", "command"})
        self.assertEqual(entry["type"], "command")


# =============================================================================
# stdout vs stderr split
# =============================================================================


class OutputStreamTests(InstallerTestBase):
    """Success → stdout. Warnings/errors → stderr."""

    def test_first_install_no_warning_or_error_on_stderr(self):
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assertNotIn("warning:", result.stderr)
        self.assertNotIn("error:", result.stderr)

    def test_no_op_stderr_clean(self):
        self.write_settings({"statusLine": self.desired_entry()})
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assertEqual(result.stderr, "")

    def test_overwrite_has_warning_no_error(self):
        self.write_settings({"statusLine": {"type": "command", "command": "/old"}})
        result = run_installer(self.home)
        self.assert_clean_success(result)
        self.assertIn("warning:", result.stderr)
        self.assertNotIn("error:", result.stderr)


# =============================================================================
# Idempotency across multiple runs
# =============================================================================


class IdempotencyTests(InstallerTestBase):
    """Repeated runs converge: first writes, subsequent are no-ops."""

    def test_second_run_is_no_op(self):
        first = run_installer(self.home)
        self.assert_clean_success(first)
        mtime_after_first = self.settings_path.stat().st_mtime_ns
        # Sleep so a rewrite would move mtime measurably.
        time.sleep(0.05)
        second = run_installer(self.home)
        self.assert_clean_success(second)
        self.assertIn("already points to", second.stdout)
        self.assertEqual(
            mtime_after_first,
            self.settings_path.stat().st_mtime_ns,
            "second run rewrote the file",
        )

    def test_three_runs_converge_no_leftovers(self):
        for _ in range(3):
            r = run_installer(self.home)
            self.assert_clean_success(r)
        self.assert_no_temp_leftovers()


# =============================================================================
# Gap-filling: installer-invoked-via-symlink, on-disk UTF-8, concurrent runs
# =============================================================================


class InstallerInvokedViaSymlinkTests(InstallerTestBase):
    """`Path(__file__).resolve()` must locate the sibling at the REAL script dir.

    Dotfile-manager users may symlink the plugin (or just the installer) into
    a shared scripts dir on PATH. If `.resolve()` weren't applied, the sibling
    lookup would happen relative to the symlink and miss `statusline.py`.
    """

    def test_installer_symlink_finds_real_sibling(self):
        # Real plugin dir with both files; symlink-only path to the installer.
        real_dir = self.tmp / "real_plugin_scripts"
        real_dir.mkdir()
        real_installer = real_dir / "install-statusline.py"
        shutil.copy2(INSTALLER, real_installer)
        real_installer.chmod(0o755)
        # _lib must live beside the real installer (it imports it); the
        # resolve()-based sibling lookup should find both at the real dir.
        shutil.copy2(REPO_ROOT / "scripts" / "_lib.py", real_dir / "_lib.py")
        real_sibling = real_dir / "statusline.py"
        real_sibling.write_text("#!/usr/bin/env python3\nprint('hi')\n")
        real_sibling.chmod(0o644)

        # Symlink the installer (only) into an unrelated dir with no sibling.
        link_dir = self.tmp / "elsewhere"
        link_dir.mkdir()
        installer_link = link_dir / "install-statusline.py"
        os.symlink(str(real_installer), str(installer_link))

        result = run_installer(self.home, installer=installer_link)
        self.assert_clean_success(result)
        # The settings file should point to the REAL sibling, not next to the link.
        cmd = self.read_settings()["statusLine"]["command"]
        # Compare resolved paths (macOS /tmp → /private/tmp symlink wrapping).
        self.assertEqual(Path(cmd).resolve(), real_sibling.resolve())
        self.assertNotEqual(Path(cmd).resolve(), (link_dir / "statusline.py").resolve())


class OnDiskUtf8EncodingTests(InstallerTestBase):
    """`ensure_ascii=False` is part of the contract: non-ASCII stays as UTF-8.

    Round-tripping via `json.loads` would pass either way; this pins the
    on-disk byte form so a switch to `ensure_ascii=True` is a deliberate
    decision that updates this test.
    """

    def test_non_ascii_values_kept_as_utf8_bytes(self):
        self.write_settings({"name": "café résumé"})
        run_installer(self.home)
        raw = self.settings_path.read_bytes()
        # Literal UTF-8 bytes present.
        self.assertIn("café résumé".encode("utf-8"), raw)
        # And not escaped to ASCII (e.g. "café").
        self.assertNotIn(b"caf\\u00e9", raw)
        self.assertNotIn(b"r\\u00e9sum\\u00e9", raw)

    def test_non_ascii_keys_kept_as_utf8_bytes(self):
        self.write_settings({"café": "value"})
        run_installer(self.home)
        raw = self.settings_path.read_bytes()
        self.assertIn("café".encode("utf-8"), raw)
        self.assertNotIn(b"caf\\u00e9", raw)


class ConcurrentInvocationTests(InstallerTestBase):
    """Two installer processes racing must leave settings.json valid.

    `os.replace` is atomic on POSIX, so the final file content must be one of
    the two writers' outputs (not a half-written mix), and the final
    `statusLine` must be the desired entry from either writer. No temp-file
    leftovers either.
    """

    def test_two_parallel_runs_both_succeed_and_file_is_valid(self):
        # Bootstrap once so the bootstrap path is out of the race window.
        result = run_installer(self.home)
        self.assert_clean_success(result)
        # Seed a non-matching state so each parallel run takes the write branch.
        self.write_settings({"statusLine": {"type": "command", "command": "/old/path"}})

        env = {
            "HOME": str(self.home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        procs = [
            subprocess.Popen(
                [sys.executable, str(INSTALLER)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results = [p.communicate(timeout=15) for p in procs]
        rcs = [p.returncode for p in procs]
        # Both must exit 0; we never expect a race to corrupt exit status.
        self.assertEqual(rcs, [0, 0], f"results: {results}")

        # File must parse and statusLine must match the desired entry.
        data = self.read_settings()
        self.assertEqual(data["statusLine"], self.desired_entry())

        # No stray temp files left in .claude/.
        self.assert_no_temp_leftovers()


if __name__ == "__main__":
    unittest.main()
