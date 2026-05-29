"""Tests for scripts/install-cli.py.

Verifies: symlinking my-claude into ~/.local/bin, the marker-based idempotent
append/replace/uninstall of a PATH block in the user's shell rc file, the
skip-when-already-on-PATH guard, and rc-file selection per $SHELL.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL = REPO_ROOT / "scripts" / "install-cli.py"
ENTRYPOINT = REPO_ROOT / "scripts" / "my-claude"

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
        self.bin_dir = Path(self.tmpdir) / ".local" / "bin"
        self.link = self.bin_dir / "my-claude"

    def _install(self, *args, on_path=False):
        # Inherit the real PATH (needed for the python3 shebang); bin_dir lives
        # under tmpdir so it's never on it by default. The guard test prepends
        # bin_dir explicitly via on_path=True.
        env = {"HOME": self.tmpdir, "SHELL": "/bin/zsh"}
        if on_path:
            env["PATH"] = os.pathsep.join(
                [str(self.bin_dir), os.environ.get("PATH", "")]
            )
        return _run(*args, env_extra=env)

    # ---- symlink ----

    def test_creates_symlink_to_entrypoint(self):
        r = self._install()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertTrue(self.link.is_symlink(), "my-claude symlink not created")
        self.assertEqual(self.link.resolve(), ENTRYPOINT.resolve())

    def test_refuses_to_clobber_non_symlink(self):
        self.bin_dir.mkdir(parents=True)
        self.link.write_text("i am a real file\n")
        r = self._install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("refusing to overwrite", r.stderr)
        # The real file is left intact.
        self.assertEqual(self.link.read_text(), "i am a real file\n")

    # ---- rc PATH block ----

    def test_appends_block_when_rc_missing(self):
        self.assertFalse(self.rc.exists())
        r = self._install()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        body = self.rc.read_text()
        self.assertIn(MARKER_START, body)
        self.assertIn(MARKER_END, body)
        self.assertIn(f'export PATH="{self.bin_dir}:$PATH"', body)

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

    def test_skips_rc_edit_when_already_on_path(self):
        # bin_dir already on PATH and no block yet → leave rc untouched.
        r = self._install(on_path=True)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertTrue(self.link.is_symlink())
        self.assertIn("already on PATH", r.stdout)
        self.assertFalse(self.rc.exists(), "rc must not be created when on PATH")

    def test_refreshes_existing_block_even_when_on_path(self):
        # If our block already exists, refresh it regardless of PATH state.
        self._install()
        self.assertIn(MARKER_START, self.rc.read_text())
        r = self._install(on_path=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.rc.read_text().count(MARKER_START), 1)

    # ---- uninstall ----

    def test_uninstall_removes_symlink_and_block(self):
        self.rc.write_text("alias foo='bar'\n")
        self._install()
        self.assertTrue(self.link.is_symlink())
        self.assertIn(MARKER_START, self.rc.read_text())
        r = self._install("--uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.link.is_symlink(), "symlink must be removed")
        body = self.rc.read_text()
        self.assertNotIn(MARKER_START, body)
        self.assertNotIn(MARKER_END, body)
        self.assertIn("alias foo='bar'", body)

    def test_uninstall_is_safe_when_nothing_installed(self):
        self.rc.write_text("alias foo='bar'\n")
        r = self._install("--uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("alias foo='bar'", self.rc.read_text())

    def test_install_uninstall_install_cycle_ends_clean(self):
        # A full round-trip converges on exactly one managed block.
        self._install()
        self._install("--uninstall")
        r = self._install()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        body = self.rc.read_text()
        self.assertEqual(body.count(MARKER_START), 1)
        self.assertEqual(body.count(MARKER_END), 1)

    def test_empty_shell_uses_profile(self):
        # An empty $SHELL falls back to ~/.profile, like an unrecognized one.
        r = _run(env_extra={"HOME": self.tmpdir, "SHELL": ""})
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn(MARKER_START, (Path(self.tmpdir) / ".profile").read_text())
        self.assertFalse(self.rc.exists())

    def test_bash_picks_bash_profile_when_present(self):
        bp = Path(self.tmpdir) / ".bash_profile"
        bp.write_text("# existing\n")
        r = _run(env_extra={"HOME": self.tmpdir, "SHELL": "/bin/bash"})
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn(MARKER_START, bp.read_text())
        # zshrc should NOT have been touched.
        self.assertFalse(self.rc.exists())

    def test_default_shell_uses_profile(self):
        # An unrecognized $SHELL falls back to ~/.profile.
        r = _run(env_extra={"HOME": self.tmpdir, "SHELL": "/usr/bin/fish"})
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn(MARKER_START, (Path(self.tmpdir) / ".profile").read_text())
        self.assertFalse(self.rc.exists())

    def test_bash_without_bash_profile_uses_bashrc(self):
        r = _run(env_extra={"HOME": self.tmpdir, "SHELL": "/bin/bash"})
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn(MARKER_START, (Path(self.tmpdir) / ".bashrc").read_text())

    def test_missing_entrypoint_exits_1(self):
        # Copy install-cli.py + its _lib dep into an isolated dir with no
        # sibling `my-claude` entrypoint → the install must refuse (exit 1).
        isolated = Path(self.tmpdir) / "scripts"
        isolated.mkdir(parents=True)
        shutil.copy(INSTALL, isolated / "install-cli.py")
        shutil.copy(REPO_ROOT / "scripts" / "_lib.py", isolated / "_lib.py")
        (isolated / "install-cli.py").chmod(0o755)
        r = subprocess.run(
            [str(isolated / "install-cli.py")],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": self.tmpdir, "SHELL": "/bin/zsh"},
            timeout=10.0,
        )
        self.assertEqual(r.returncode, 1, msg=r.stdout + r.stderr)
        self.assertIn("entrypoint not found", r.stderr)


class InstallCliWiringTests(unittest.TestCase):
    def test_script_is_executable_with_shebang(self):
        self.assertTrue(os.access(INSTALL, os.X_OK))
        first = INSTALL.read_text().splitlines()[0]
        self.assertTrue(first.startswith("#!"), msg=first)


if __name__ == "__main__":
    unittest.main()
