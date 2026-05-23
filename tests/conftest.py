"""Shared pytest fixtures for the annotate CLI test suite."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "annotate.py"


@pytest.fixture
def script_path() -> Path:
    return SCRIPT


@pytest.fixture
def run_cli(tmp_path, monkeypatch):
    """Invoke annotate.py as a subprocess from tmp_path.

    Returns a callable: run_cli(*args) -> subprocess.CompletedProcess
    """
    monkeypatch.chdir(tmp_path)

    def _run(*args: str, check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            check=check,
        )

    return _run


@pytest.fixture
def make_source(tmp_path):
    """Write a source file under tmp_path and return its Path."""

    def _make(name: str, content: str) -> Path:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    return _make


@pytest.fixture
def read_sidecar():
    """Read and parse a sidecar JSON file."""

    def _read(path) -> dict:
        with open(path) as f:
            return json.load(f)

    return _read
