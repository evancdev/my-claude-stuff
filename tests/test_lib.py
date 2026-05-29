"""Unit tests for scripts/_lib.py.

Focus on atomic_write's failure-cleanup branch: subprocess-based tests of the
callers can't reliably reach the `except BaseException` cleanup (they fail at
mkstemp before the try block), so these mock os.replace / os.fdopen to force a
failure mid-write and verify the temp file is removed and the target is left
byte-identical.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_PATH = REPO_ROOT / "scripts" / "_lib.py"


def _load_lib():
    """Load scripts/_lib.py as a module (scripts/ isn't on sys.path)."""
    spec = importlib.util.spec_from_file_location(
        "scripts_lib_under_test", str(LIB_PATH)
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AtomicWriteCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = _load_lib()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lib_atomic_write_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = self.tmp / "settings.json"

    def _leftover_tmp_files(self) -> list[str]:
        return sorted(p.name for p in self.tmp.iterdir() if p.name != self.target.name)

    def test_cleanup_on_replace_failure_when_target_exists(self):
        """os.replace raises → tmp file unlinked, original byte-identical."""
        self.target.write_text('{"foo": "bar"}\n')
        os.chmod(self.target, 0o644)
        original = self.target.read_bytes()

        with unittest.mock.patch(
            "os.replace", side_effect=OSError("simulated replace failure")
        ):
            with self.assertRaises(OSError):
                self.lib.atomic_write(self.target, '{"new": "data"}\n')

        self.assertEqual(
            self._leftover_tmp_files(),
            [],
            "temp file not cleaned up after replace failure",
        )
        self.assertEqual(self.target.read_bytes(), original)

    def test_cleanup_on_replace_failure_when_target_absent(self):
        """Bootstrap path: replace fails, no target file ever existed."""
        self.assertFalse(self.target.exists())

        with unittest.mock.patch(
            "os.replace", side_effect=OSError("simulated replace failure")
        ):
            with self.assertRaises(OSError):
                self.lib.atomic_write(self.target, "{}\n")

        self.assertEqual(
            self._leftover_tmp_files(),
            [],
            "temp file not cleaned up after bootstrap replace failure",
        )
        self.assertFalse(self.target.exists(), "target file should not exist")

    def test_cleanup_on_write_failure(self):
        """If the write call fails (mid-stream), temp file is still cleaned up."""
        self.target.write_text('{"foo": "bar"}\n')

        real_fdopen = os.fdopen

        class _FailingWriter:
            def __init__(self, fd, *args, **kwargs):
                # Close the real fd (release resources) then fail on write.
                self._inner = real_fdopen(fd, *args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._inner.close()
                return False

            def write(self, _data):
                raise OSError("simulated write failure")

        with unittest.mock.patch("os.fdopen", side_effect=_FailingWriter):
            with self.assertRaises(OSError):
                self.lib.atomic_write(self.target, "doesn't matter")

        self.assertEqual(
            self._leftover_tmp_files(),
            [],
            "temp file not cleaned up after write failure",
        )


if __name__ == "__main__":
    unittest.main()
