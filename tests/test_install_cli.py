"""Tests for scripts/install-cli.sh.

Verifies the marker-based idempotent append/replace/uninstall of a PATH
export block in the user's shell rc file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL = REPO_ROOT / "scripts" / "install-cli.sh"

MARKER_START = "# >>> my-claude-stuff scripts >>>"
MARKER_END = "# <<< my-claude-stuff scripts <<<"


def _run(*args, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(INSTALL), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=10.0,
    )


class InstallCliTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="install_cli_home_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.rc = Path(self.tmpdir) / ".zshrc"

    def _install(self, *args):
        return _run(*args, env_extra={"HOME": self.tmpdir, "SHELL": "/bin/zsh"})

    def test_appends_block_when_rc_missing(self):
        self.assertFalse(self.rc.exists())
        r = self._install()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        body = self.rc.read_text()
        self.assertIn(MARKER_START, body)
        self.assertIn(MARKER_END, body)
        self.assertIn(f'export PATH="{REPO_ROOT}/scripts:$PATH"', body)

    def test_preserves_existing_rc_content(self):
        self.rc.write_text("# my existing alias\nalias ll='ls -la'\n")
        r = self._install()
        self.assertEqual(r.returncode, 0)
        body = self.rc.read_text()
        self.assertIn("alias ll='ls -la'", body)
        self.assertIn(MARKER_START, body)

    def test_re_run_replaces_in_place_no_dup(self):
        self._install()
        first = self.rc.read_text()
        self.assertEqual(first.count(MARKER_START), 1)
        self._install()
        second = self.rc.read_text()
        self.assertEqual(
            second.count(MARKER_START), 1, msg="re-run must replace, not duplicate"
        )
        self.assertEqual(second.count(MARKER_END), 1)

    def test_uninstall_removes_block_preserves_rest(self):
        self.rc.write_text("alias foo='bar'\n")
        self._install()
        self.assertIn(MARKER_START, self.rc.read_text())
        r = self._install("--uninstall")
        self.assertEqual(r.returncode, 0)
        body = self.rc.read_text()
        self.assertNotIn(MARKER_START, body)
        self.assertNotIn(MARKER_END, body)
        self.assertIn("alias foo='bar'", body)

    def test_uninstall_is_safe_when_nothing_installed(self):
        self.rc.write_text("alias foo='bar'\n")
        r = self._install("--uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("alias foo='bar'", self.rc.read_text())

    def test_bash_picks_bash_profile_when_present(self):
        bp = Path(self.tmpdir) / ".bash_profile"
        bp.write_text("# existing\n")
        r = _run(env_extra={"HOME": self.tmpdir, "SHELL": "/bin/bash"})
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn(MARKER_START, bp.read_text())
        # zshrc should NOT have been touched.
        self.assertFalse(self.rc.exists())


class InstallCliWiringTests(unittest.TestCase):
    def test_script_is_executable_with_shebang(self):
        self.assertTrue(os.access(INSTALL, os.X_OK))
        first = INSTALL.read_text().splitlines()[0]
        self.assertTrue(first.startswith("#!"), msg=first)


if __name__ == "__main__":
    unittest.main()
