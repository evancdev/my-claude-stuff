"""Tests for scripts/annotate.py.

Organized by subject (subcommand / helper), not by methodology. Tests use
two access modes:

- **Unit** — import the module directly to exercise internal helpers
  (`now_iso`, `new_id`, `load_sidecar`, `atomic_write`, `find_quote_anchor`,
  `sidecar_path`, `collect_sidecars`) and drive the `cmd_*` handlers by
  constructing argparse Namespaces.
- **CLI** — invoke `python3 scripts/annotate.py …` as a subprocess (via the
  `run_cli` fixture in conftest.py) and assert against observable behavior:
  stdout, stderr, exit code, and the on-disk sidecar JSON.

Both modes appear together inside each subcommand class so a future maintainer
sees the full behavioral spec for `create`, `reply`, `resolve`, `list` in one
place.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "annotate.py"
spec = importlib.util.spec_from_file_location("annotate", SCRIPT)
annotate = importlib.util.module_from_spec(spec)
sys.modules["annotate"] = annotate
spec.loader.exec_module(annotate)


# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

HEX6_RE = re.compile(r"^[0-9a-f]{6}$")
ISO_MS_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _ns(**kw):
    """Build an argparse.Namespace for direct cmd_* invocations."""
    return argparse.Namespace(**kw)


def _create(run_cli, source: str, quote: str, body: str = "note"):
    """Convenience: invoke `create` via the CLI and assert it succeeded."""
    result = run_cli("create", source, "--quote", quote, "--body", body)
    assert result.returncode == 0, (
        f"create failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    return result


def _only_annotation(sidecar_data: dict) -> dict:
    anns = sidecar_data["annotations"]
    assert len(anns) == 1, f"expected exactly one annotation, got {len(anns)}"
    return anns[0]


def _line_for_id(stdout: str, ann_id: str) -> str:
    """Return the single output line containing the given id."""
    matches = [line for line in stdout.splitlines() if ann_id in line]
    assert len(matches) == 1, (
        f"expected exactly one line containing id={ann_id!r}, got {matches!r}"
    )
    return matches[0]


# ===========================================================================
# Internal helpers (no public CLI surface — unit tests only)
# ===========================================================================


class TestNowIso:
    def test_ends_in_Z(self):
        assert annotate.now_iso().endswith("Z")

    def test_has_millisecond_precision(self):
        ts = annotate.now_iso()
        # 2026-05-22T12:34:56.789Z — exactly 3 digits after the dot.
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", ts), ts

    def test_parses_back(self):
        ts = annotate.now_iso()
        parsed = datetime.fromisoformat(ts[:-1])  # strip trailing Z
        assert parsed.year >= 2024


class TestNewId:
    def test_is_six_hex_chars(self):
        nid = annotate.new_id()
        assert len(nid) == 6
        assert HEX6_RE.match(nid)

    def test_returns_distinct_values(self):
        ids = {annotate.new_id() for _ in range(50)}
        # Birthday-style collision in 16^6 space for 50 draws is astronomically
        # unlikely; this is a fine smoke check.
        assert len(ids) == 50


class TestLoadSidecar:
    def test_nonexistent_path(self, tmp_path):
        data, migrated = annotate.load_sidecar(str(tmp_path / "nope.json"))
        assert data == {"annotations": []}
        assert migrated is False

    def test_full_annotations_not_migrated(self, tmp_path):
        p = tmp_path / "s.annotations.json"
        payload = {
            "annotations": [
                {"id": "abc123", "resolved": False, "quote": "x", "comments": []},
                {"id": "def456", "resolved": True, "quote": "y", "comments": []},
            ]
        }
        p.write_text(json.dumps(payload))
        data, migrated = annotate.load_sidecar(str(p))
        assert migrated is False
        assert data == payload

    def test_missing_id_is_backfilled(self, tmp_path, monkeypatch):
        p = tmp_path / "s.annotations.json"
        p.write_text(json.dumps({"annotations": [{"resolved": False, "quote": "x"}]}))
        monkeypatch.setattr(annotate, "new_id", lambda: "deadbe")
        data, migrated = annotate.load_sidecar(str(p))
        assert migrated is True
        assert data["annotations"][0]["id"] == "deadbe"

    def test_missing_resolved_is_backfilled(self, tmp_path):
        p = tmp_path / "s.annotations.json"
        p.write_text(json.dumps({"annotations": [{"id": "aaaaaa", "quote": "x"}]}))
        data, migrated = annotate.load_sidecar(str(p))
        assert migrated is True
        assert data["annotations"][0]["resolved"] is False

    def test_both_missing_single_migrated_flag(self, tmp_path, monkeypatch):
        p = tmp_path / "s.annotations.json"
        p.write_text(json.dumps({"annotations": [{"quote": "x"}]}))
        monkeypatch.setattr(annotate, "new_id", lambda: "feedda")
        data, migrated = annotate.load_sidecar(str(p))
        assert migrated is True  # one flag, not two
        assert data["annotations"][0]["id"] == "feedda"
        assert data["annotations"][0]["resolved"] is False

    def test_empty_annotations_list(self, tmp_path):
        p = tmp_path / "s.annotations.json"
        p.write_text(json.dumps({"annotations": []}))
        data, migrated = annotate.load_sidecar(str(p))
        assert migrated is False
        assert data == {"annotations": []}

    def test_missing_annotations_key(self, tmp_path):
        p = tmp_path / "s.annotations.json"
        p.write_text(json.dumps({"meta": "no annotations key"}))
        data, migrated = annotate.load_sidecar(str(p))
        assert migrated is False
        assert data["meta"] == "no annotations key"
        assert data["annotations"] == []

    def test_malformed_json_exits(self, tmp_path):
        p = tmp_path / "s.annotations.json"
        p.write_text("{not valid json")
        with pytest.raises(SystemExit) as excinfo:
            annotate.load_sidecar(str(p))
        assert "could not parse sidecar" in str(excinfo.value)


class TestAtomicWrite:
    def test_indent_and_trailing_newline(self, tmp_path):
        p = tmp_path / "out.json"
        annotate.atomic_write(str(p), {"a": 1, "b": [1, 2]})
        raw = p.read_text()
        expected = json.dumps({"a": 1, "b": [1, 2]}, indent=2) + "\n"
        assert raw == expected
        assert raw.endswith("\n")
        # 2-space indent indicator: a child key after exactly two spaces.
        assert "\n  " in raw

    def test_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "x.json"
        annotate.atomic_write(str(nested), {"k": "v"})
        assert nested.exists()
        assert json.loads(nested.read_text()) == {"k": "v"}

    def test_cleans_up_tmp_on_exception(self, tmp_path, monkeypatch):
        p = tmp_path / "target.json"
        p.write_text('{"original": true}')
        original_bytes = p.read_bytes()

        def boom(*a, **kw):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(annotate.json, "dump", boom)
        with pytest.raises(RuntimeError, match="kaboom"):
            annotate.atomic_write(str(p), {"will": "not write"})

        # Target file unchanged; no leftover .annotate-*.tmp files.
        assert p.read_bytes() == original_bytes
        leftovers = [
            f
            for f in os.listdir(tmp_path)
            if f.startswith(".annotate-") and f.endswith(".tmp")
        ]
        assert leftovers == [], f"leftover tmp files: {leftovers}"

    def test_cleans_up_tmp_on_baseexception(self, tmp_path, monkeypatch):
        """atomic_write's except clause is BaseException — catches things like
        KeyboardInterrupt too. Verify the cleanup runs and the target is not
        created."""
        p = tmp_path / "target.json"

        def boom(*a, **kw):
            raise KeyboardInterrupt()

        monkeypatch.setattr(annotate.json, "dump", boom)
        with pytest.raises(KeyboardInterrupt):
            annotate.atomic_write(str(p), {"x": 1})

        leftovers = [
            f
            for f in os.listdir(tmp_path)
            if f.startswith(".annotate-") and f.endswith(".tmp")
        ]
        assert leftovers == []
        assert not p.exists()

    def test_replaces_existing_file(self, tmp_path):
        p = tmp_path / "target.json"
        p.write_text('"old"')
        annotate.atomic_write(str(p), {"new": True})
        assert json.loads(p.read_text()) == {"new": True}


class TestFindQuoteAnchor:
    def test_at_file_start(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("hello\nworld\n")
        assert annotate.find_quote_anchor(str(src), "hello") == {
            "line_start": 1,
            "line_end": 1,
            "char_start": 0,
            "char_end": 5,
        }

    def test_mid_line(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("abc DEF ghi\n")
        assert annotate.find_quote_anchor(str(src), "DEF") == {
            "line_start": 1,
            "line_end": 1,
            "char_start": 4,
            "char_end": 7,
        }

    def test_end_of_line(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("abc def\nnext\n")
        # "def" at idx 4..7 (before the \n).
        assert annotate.find_quote_anchor(str(src), "def") == {
            "line_start": 1,
            "line_end": 1,
            "char_start": 4,
            "char_end": 7,
        }

    def test_on_second_line(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("line1\nline2\n")
        # idx=6 ("l" of line2); before="line1\n"; last_nl=5; char_start=0;
        # char_end=5 (end of "line2" on line 2).
        assert annotate.find_quote_anchor(str(src), "line2") == {
            "line_start": 2,
            "line_end": 2,
            "char_start": 0,
            "char_end": 5,
        }

    def test_multi_line(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("alpha\nbeta\ngamma\n")
        # Quote spans end of line 1 → middle of line 3.
        # idx=2 ("p" of alpha); end_idx=14; quote has 2 newlines → line_end=3;
        # last_nl in quote at index 8 (\n after beta) → char_end = 12-9 = 3.
        assert annotate.find_quote_anchor(str(src), "pha\nbeta\ngam") == {
            "line_start": 1,
            "line_end": 3,
            "char_start": 2,
            "char_end": 3,
        }

    def test_first_char_of_file(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("X then rest\n")
        assert annotate.find_quote_anchor(str(src), "X") == {
            "line_start": 1,
            "line_end": 1,
            "char_start": 0,
            "char_end": 1,
        }

    def test_last_char_no_trailing_newline(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("hello")
        assert annotate.find_quote_anchor(str(src), "o") == {
            "line_start": 1,
            "line_end": 1,
            "char_start": 4,
            "char_end": 5,
        }

    def test_trailing_newline_as_quote(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("hello\n")
        # Quote IS the trailing newline. line_end advances; char_end wraps to 0.
        assert annotate.find_quote_anchor(str(src), "\n") == {
            "line_start": 1,
            "line_end": 2,
            "char_start": 5,
            "char_end": 0,
        }

    def test_not_found(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("abc\n")
        with pytest.raises(SystemExit) as exc:
            annotate.find_quote_anchor(str(src), "zzz")
        msg = str(exc.value)
        assert "quote not found" in msg
        assert str(src) in msg
        assert "zzz" in msg

    def test_duplicate(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("foo bar foo\n")
        with pytest.raises(SystemExit) as exc:
            annotate.find_quote_anchor(str(src), "foo")
        assert "2 times" in str(exc.value)
        assert str(src) in str(exc.value)

    def test_overlapping_occurrences_counted_separately(self, tmp_path):
        """Verify `start = idx + 1` advances by exactly one char.
        For "aaaa" searching "aa": matches at idx 0, 1, 2 → three hits."""
        src = tmp_path / "a.txt"
        src.write_text("aaaa")
        with pytest.raises(SystemExit) as exc:
            annotate.find_quote_anchor(str(src), "aa")
        assert "3 times" in str(exc.value)

    def test_source_not_found(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            annotate.find_quote_anchor(str(tmp_path / "missing.txt"), "x")
        assert "source file not found" in str(exc.value)


class TestSidecarPath:
    def test_appends_suffix(self):
        assert annotate.sidecar_path("foo/bar.py") == "foo/bar.py.annotations.json"

    def test_path_with_no_extension(self):
        assert annotate.sidecar_path("README") == "README.annotations.json"

    def test_empty_string(self):
        assert annotate.sidecar_path("") == ".annotations.json"


class TestCollectSidecars:
    def test_single_file(self, tmp_path):
        p = tmp_path / "x.annotations.json"
        p.write_text("{}")
        assert annotate.collect_sidecars(str(p)) == ([str(p)], False)

    def test_single_file_accepts_non_sidecar_name(self, tmp_path):
        # Only checks isfile, not suffix, when given a file.
        p = tmp_path / "anything.txt"
        p.write_text("hi")
        assert annotate.collect_sidecars(str(p)) == ([str(p)], False)

    def test_directory_recursive_and_sorted(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "b").mkdir()
        f1 = tmp_path / "a" / "alpha.py.annotations.json"
        f2 = tmp_path / "a" / "b" / "beta.py.annotations.json"
        f3 = tmp_path / "zeta.py.annotations.json"
        for f in (f1, f2, f3):
            f.write_text("{}")
        (tmp_path / "a" / "not_a_sidecar.json").write_text("{}")

        found, is_dir = annotate.collect_sidecars(str(tmp_path))
        assert found == sorted([str(f1), str(f2), str(f3)])
        assert is_dir is True

    def test_skips_ignored_dirs(self, tmp_path):
        for ignored in annotate.IGNORED_DIRS:
            d = tmp_path / ignored
            d.mkdir()
            (d / "hidden.py.annotations.json").write_text("{}")
        visible = tmp_path / "visible.py.annotations.json"
        visible.write_text("{}")
        found, _ = annotate.collect_sidecars(str(tmp_path))
        assert found == [str(visible)]

    def test_nonexistent_path(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            annotate.collect_sidecars(str(tmp_path / "does_not_exist"))
        assert "path not found" in str(exc.value)

    def test_empty_directory_returns_empty(self, tmp_path):
        assert annotate.collect_sidecars(str(tmp_path)) == ([], True)

    def test_ignored_dir_nested(self, tmp_path):
        """Sidecar buried inside an ignored dir nested under a normal dir is
        still skipped — prune happens at every walk level."""
        normal = tmp_path / "src"
        normal.mkdir()
        (normal / "real.py.annotations.json").write_text("{}")
        nested_ignored = normal / "node_modules" / "deep"
        nested_ignored.mkdir(parents=True)
        (nested_ignored / "buried.py.annotations.json").write_text("{}")
        found, _ = annotate.collect_sidecars(str(tmp_path))
        assert found == [str(normal / "real.py.annotations.json")]


# ===========================================================================
# Subcommands — each class mixes unit (cmd_*) and CLI (subprocess) tests
# ===========================================================================


class TestCreate:
    # ---- unit ----
    def test_unit_writes_sidecar_with_expected_structure(self, tmp_path, monkeypatch):
        src = tmp_path / "thing.py"
        src.write_text("print('hi')\n")
        monkeypatch.setattr(annotate, "new_id", lambda: "abcdef")
        monkeypatch.setattr(annotate, "now_iso", lambda: "2026-05-22T00:00:00.000Z")

        annotate.cmd_create(_ns(source=str(src), quote="print", body="why?"))

        side = tmp_path / "thing.py.annotations.json"
        assert side.exists()
        data = json.loads(side.read_text())
        assert data["annotations"][0] == {
            "id": "abcdef",
            "anchor": {"line_start": 1, "line_end": 1, "char_start": 0, "char_end": 5},
            "quote": "print",
            "resolved": False,
            "comments": [
                {
                    "author": "Claude",
                    "body": "why?",
                    "timestamp": "2026-05-22T00:00:00.000Z",
                }
            ],
        }

    def test_unit_appends_to_existing_sidecar(self, tmp_path, monkeypatch):
        src = tmp_path / "thing.py"
        src.write_text("alpha beta\n")
        side = tmp_path / "thing.py.annotations.json"
        side.write_text(
            json.dumps(
                {
                    "annotations": [
                        {
                            "id": "old001",
                            "resolved": False,
                            "quote": "alpha",
                            "comments": [],
                        }
                    ]
                }
            )
        )
        monkeypatch.setattr(annotate, "new_id", lambda: "new002")
        monkeypatch.setattr(annotate, "now_iso", lambda: "2026-05-22T00:00:00.000Z")
        annotate.cmd_create(_ns(source=str(src), quote="beta", body="hm"))

        data = json.loads(side.read_text())
        ids = [a["id"] for a in data["annotations"]]
        assert ids == ["old001", "new002"]

    # ---- CLI ----
    def test_cli_creates_sidecar_with_expected_shape(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("sample.txt", "line one\nline two has TARGET here\nline three\n")
        result = _create(run_cli, "sample.txt", "TARGET", "hello")
        assert "sample.txt.annotations.json" in result.stdout

        sidecar = tmp_path / "sample.txt.annotations.json"
        assert sidecar.exists()
        ann = _only_annotation(read_sidecar(sidecar))

        assert HEX6_RE.match(ann["id"]), f"id not 6 hex chars: {ann['id']!r}"
        assert ann["resolved"] is False
        assert ann["quote"] == "TARGET"
        # "T" of TARGET on line 2 starts at column 13; quote length 6.
        assert ann["anchor"] == {
            "line_start": 2,
            "line_end": 2,
            "char_start": 13,
            "char_end": 19,
        }
        assert len(ann["comments"]) == 1
        c = ann["comments"][0]
        assert c["author"] == "Claude"
        assert c["body"] == "hello"
        assert ISO_MS_UTC_RE.match(c["timestamp"]), (
            f"timestamp not ISO-8601 ms UTC: {c['timestamp']!r}"
        )

    def test_cli_anchor_for_quote_on_first_line(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("a.txt", "hello world\nsecond line\n")
        _create(run_cli, "a.txt", "hello", "b")
        ann = _only_annotation(read_sidecar(tmp_path / "a.txt.annotations.json"))
        assert ann["anchor"] == {
            "line_start": 1,
            "line_end": 1,
            "char_start": 0,
            "char_end": 5,
        }

    def test_cli_anchor_for_multiline_quote(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("multi.txt", "hello\nworld\nbye\n")
        _create(run_cli, "multi.txt", "hello\nworld", "b")
        ann = _only_annotation(read_sidecar(tmp_path / "multi.txt.annotations.json"))
        # Spans lines 1..2; char_end=5 is the column AFTER "world".
        assert ann["anchor"] == {
            "line_start": 1,
            "line_end": 2,
            "char_start": 0,
            "char_end": 5,
        }

    def test_cli_author_is_hardcoded_claude(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("auth.txt", "alpha beta gamma\n")
        _create(run_cli, "auth.txt", "beta", "x")
        ann = _only_annotation(read_sidecar(tmp_path / "auth.txt.annotations.json"))
        assert ann["comments"][0]["author"] == "Claude"

    def test_cli_no_author_flag_accepted(self, run_cli, make_source):
        make_source("auth.txt", "alpha beta gamma\n")
        # Spec: never accepts --author. CLI must reject it.
        result = run_cli(
            "create", "auth.txt", "--quote", "beta", "--body", "x", "--author", "evan"
        )
        assert result.returncode != 0

    def test_cli_quote_not_found_errors(self, run_cli, make_source):
        make_source("miss.txt", "absent\n")
        result = run_cli("create", "miss.txt", "--quote", "NOT THERE", "--body", "x")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "miss.txt" in combined
        assert "NOT THERE" in combined

    def test_cli_quote_must_be_unique(self, run_cli, make_source):
        make_source("dup.txt", "duplicate\nduplicate\nduplicate\n")
        result = run_cli("create", "dup.txt", "--quote", "duplicate", "--body", "x")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "3" in combined  # error states count
        assert "unique" in combined.lower()  # nudges toward more unique substring

    def test_cli_quote_not_found_does_not_create_sidecar(
        self, run_cli, make_source, tmp_path
    ):
        make_source("miss.txt", "absent\n")
        run_cli("create", "miss.txt", "--quote", "NOPE", "--body", "x")
        assert not (tmp_path / "miss.txt.annotations.json").exists()

    def test_cli_multiple_creates_append_with_unique_ids(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("multi.txt", "a\nb\nc\nd\n")
        _create(run_cli, "multi.txt", "a", "first")
        _create(run_cli, "multi.txt", "c", "second")

        data = read_sidecar(tmp_path / "multi.txt.annotations.json")
        assert len(data["annotations"]) == 2
        ids = [a["id"] for a in data["annotations"]]
        for i in ids:
            assert HEX6_RE.match(i), f"id not hex6: {i!r}"
        assert len(set(ids)) == 2, f"ids not unique: {ids}"

    def test_cli_sidecar_is_pretty_printed_with_trailing_newline(
        self, run_cli, make_source, tmp_path
    ):
        make_source("pp.txt", "alpha\n")
        _create(run_cli, "pp.txt", "alpha", "b")
        raw = (tmp_path / "pp.txt.annotations.json").read_bytes()
        assert raw.endswith(b"\n")
        # 2-space indent indicator: a child key after exactly two spaces.
        assert '\n  "annotations"' in raw.decode()


class TestReply:
    # ---- unit ----
    def test_unit_appends_comment_with_claude_author(self, tmp_path, monkeypatch):
        sidecar = tmp_path / "s.annotations.json"
        sidecar.write_text(
            json.dumps(
                {
                    "annotations": [
                        {
                            "id": "aaaaaa",
                            "resolved": False,
                            "comments": [
                                {
                                    "author": "Evan",
                                    "body": "first",
                                    "timestamp": "2026-01-01T00:00:00.000Z",
                                }
                            ],
                        }
                    ]
                }
            )
        )
        monkeypatch.setattr(annotate, "now_iso", lambda: "2026-05-22T00:00:00.000Z")
        annotate.cmd_reply(_ns(sidecar=str(sidecar), id="aaaaaa", body="thx"))
        after = json.loads(sidecar.read_text())
        assert after["annotations"][0]["comments"][-1] == {
            "author": "Claude",
            "body": "thx",
            "timestamp": "2026-05-22T00:00:00.000Z",
        }

    def test_unit_initializes_comments_when_missing(self, tmp_path, monkeypatch):
        sidecar = tmp_path / "s.annotations.json"
        sidecar.write_text(
            json.dumps({"annotations": [{"id": "aaaaaa", "resolved": False}]})
        )
        monkeypatch.setattr(annotate, "now_iso", lambda: "2026-05-22T00:00:00.000Z")
        annotate.cmd_reply(_ns(sidecar=str(sidecar), id="aaaaaa", body="hello"))
        after = json.loads(sidecar.read_text())
        assert after["annotations"][0]["comments"] == [
            {
                "author": "Claude",
                "body": "hello",
                "timestamp": "2026-05-22T00:00:00.000Z",
            }
        ]

    def test_unit_against_nonexistent_sidecar_exits(self, tmp_path):
        # load_sidecar returns empty list; next(...) is None → SystemExit.
        missing = tmp_path / "nope.annotations.json"
        with pytest.raises(SystemExit) as exc:
            annotate.cmd_reply(_ns(sidecar=str(missing), id="abcdef", body="hi"))
        assert "no annotation with id=abcdef" in str(exc.value)

    # ---- CLI ----
    def test_cli_appends_comment_with_claude_author(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("r.txt", "hello world\n")
        _create(run_cli, "r.txt", "hello", "first")
        ann_id = _only_annotation(read_sidecar(tmp_path / "r.txt.annotations.json"))[
            "id"
        ]

        result = run_cli(
            "reply", "r.txt.annotations.json", "--id", ann_id, "--body", "follow-up"
        )
        assert result.returncode == 0

        ann = _only_annotation(read_sidecar(tmp_path / "r.txt.annotations.json"))
        assert len(ann["comments"]) == 2
        appended = ann["comments"][-1]
        assert appended["author"] == "Claude"
        assert appended["body"] == "follow-up"
        assert ISO_MS_UTC_RE.match(appended["timestamp"])

    def test_cli_unknown_id_errors_and_does_not_modify_file(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("r.txt", "hello world\n")
        _create(run_cli, "r.txt", "hello", "first")
        sidecar = tmp_path / "r.txt.annotations.json"
        before = sidecar.read_bytes()

        result = run_cli(
            "reply", "r.txt.annotations.json", "--id", "zzzzzz", "--body", "x"
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "zzzzzz" in combined
        assert "r.txt.annotations.json" in combined
        assert sidecar.read_bytes() == before  # unchanged


class TestResolve:
    # ---- unit ----
    def test_unit_flips_resolved_flag(self, tmp_path):
        sidecar = tmp_path / "s.annotations.json"
        sidecar.write_text(
            json.dumps({"annotations": [{"id": "aaaaaa", "resolved": False}]})
        )
        annotate.cmd_resolve(_ns(sidecar=str(sidecar), id="aaaaaa", unresolve=False))
        assert json.loads(sidecar.read_text())["annotations"][0]["resolved"] is True

        annotate.cmd_resolve(_ns(sidecar=str(sidecar), id="aaaaaa", unresolve=True))
        assert json.loads(sidecar.read_text())["annotations"][0]["resolved"] is False

    # ---- CLI ----
    def test_cli_resolve_then_unresolve_toggles_field(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("s.txt", "alpha beta\n")
        _create(run_cli, "s.txt", "alpha", "x")
        sidecar = tmp_path / "s.txt.annotations.json"
        ann_id = _only_annotation(read_sidecar(sidecar))["id"]

        r1 = run_cli("resolve", "s.txt.annotations.json", "--id", ann_id)
        assert r1.returncode == 0
        assert _only_annotation(read_sidecar(sidecar))["resolved"] is True

        r2 = run_cli("resolve", "s.txt.annotations.json", "--id", ann_id, "--unresolve")
        assert r2.returncode == 0
        assert _only_annotation(read_sidecar(sidecar))["resolved"] is False

    def test_cli_unknown_id_errors(self, run_cli, make_source, tmp_path):
        make_source("s.txt", "alpha beta\n")
        _create(run_cli, "s.txt", "alpha", "x")
        result = run_cli("resolve", "s.txt.annotations.json", "--id", "zzzzzz")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "zzzzzz" in combined
        assert "s.txt.annotations.json" in combined


class TestList:
    # ---- unit ----
    def test_unit_truncates_long_quote_preview(self, tmp_path, capsys):
        sidecar = tmp_path / "x.py.annotations.json"
        long_q = "x" * 100
        sidecar.write_text(
            json.dumps(
                {
                    "annotations": [
                        {
                            "id": "aaaaaa",
                            "resolved": False,
                            "quote": long_q,
                            "comments": [
                                {"author": "Evan", "body": "b", "timestamp": "t"}
                            ],
                        }
                    ]
                }
            )
        )
        annotate.cmd_list(_ns(path=str(sidecar), unresolved=False))
        out = capsys.readouterr().out
        # 57 x's then the single-char ellipsis — pin the exact boundary.
        assert ("x" * 57 + "…") in out
        assert ("x" * 100) not in out

    # ---- CLI ----
    def test_cli_single_sidecar_shows_unresolved_marker(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("l.txt", "hello world\n")
        _create(run_cli, "l.txt", "hello", "note")
        ann_id = _only_annotation(read_sidecar(tmp_path / "l.txt.annotations.json"))[
            "id"
        ]

        result = run_cli("list", "l.txt.annotations.json")
        assert result.returncode == 0
        line = _line_for_id(result.stdout, ann_id)
        assert line.startswith("○"), f"unresolved marker missing: {line!r}"
        assert "[Claude]" in line
        assert "(1c)" in line
        assert "hello" in line

    def test_cli_shows_resolved_marker_after_resolve(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("l.txt", "hello world\n")
        _create(run_cli, "l.txt", "hello", "note")
        ann_id = _only_annotation(read_sidecar(tmp_path / "l.txt.annotations.json"))[
            "id"
        ]
        run_cli("resolve", "l.txt.annotations.json", "--id", ann_id)

        result = run_cli("list", "l.txt.annotations.json")
        assert result.returncode == 0
        line = _line_for_id(result.stdout, ann_id)
        assert line.startswith("✓"), f"resolved marker missing: {line!r}"

    def test_cli_comment_count_grows_after_reply(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("l.txt", "hello world\n")
        _create(run_cli, "l.txt", "hello", "first")
        ann_id = _only_annotation(read_sidecar(tmp_path / "l.txt.annotations.json"))[
            "id"
        ]
        run_cli("reply", "l.txt.annotations.json", "--id", ann_id, "--body", "second")

        result = run_cli("list", "l.txt.annotations.json")
        assert "(2c)" in _line_for_id(result.stdout, ann_id)

    def test_cli_unresolved_filter_hides_resolved(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("l.txt", "alpha beta gamma\n")
        _create(run_cli, "l.txt", "alpha", "a")
        _create(run_cli, "l.txt", "gamma", "g")
        data = read_sidecar(tmp_path / "l.txt.annotations.json")
        alpha_id = next(a["id"] for a in data["annotations"] if a["quote"] == "alpha")
        gamma_id = next(a["id"] for a in data["annotations"] if a["quote"] == "gamma")
        run_cli("resolve", "l.txt.annotations.json", "--id", alpha_id)

        all_out = run_cli("list", "l.txt.annotations.json").stdout
        assert alpha_id in all_out and gamma_id in all_out

        un_out = run_cli("list", "l.txt.annotations.json", "--unresolved").stdout
        assert alpha_id not in un_out
        assert gamma_id in un_out

    def test_cli_long_quote_is_truncated_with_ellipsis(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        long_quote = "a" * 80
        make_source("long.txt", f"intro\n{long_quote}\nend\n")
        _create(run_cli, "long.txt", long_quote, "b")
        ann_id = _only_annotation(read_sidecar(tmp_path / "long.txt.annotations.json"))[
            "id"
        ]

        result = run_cli("list", "long.txt.annotations.json")
        line = _line_for_id(result.stdout, ann_id)
        assert "…" in line
        assert long_quote not in line

    def test_cli_short_quote_is_not_truncated(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("short.txt", "tiny piece here\n")
        _create(run_cli, "short.txt", "tiny piece", "b")
        ann_id = _only_annotation(
            read_sidecar(tmp_path / "short.txt.annotations.json")
        )["id"]
        result = run_cli("list", "short.txt.annotations.json")
        line = _line_for_id(result.stdout, ann_id)
        assert "tiny piece" in line
        assert "…" not in line

    def test_cli_newlines_in_quote_collapsed_to_spaces_in_preview(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("nl.txt", "hello\nworld\nend\n")
        _create(run_cli, "nl.txt", "hello\nworld", "b")
        ann_id = _only_annotation(read_sidecar(tmp_path / "nl.txt.annotations.json"))[
            "id"
        ]
        result = run_cli("list", "nl.txt.annotations.json")
        line = _line_for_id(result.stdout, ann_id)
        assert "hello world" in line
        assert "hello\nworld" not in line

    def test_cli_empty_directory_shows_no_annotations_message(self, run_cli):
        result = run_cli("list", ".")
        assert result.returncode == 0
        assert "(no annotations found under" in result.stdout

    def test_cli_empty_unresolved_message(
        self, run_cli, make_source, read_sidecar, tmp_path
    ):
        make_source("e.txt", "hello\n")
        _create(run_cli, "e.txt", "hello", "b")
        ann_id = _only_annotation(read_sidecar(tmp_path / "e.txt.annotations.json"))[
            "id"
        ]
        run_cli("resolve", "e.txt.annotations.json", "--id", ann_id)

        result = run_cli("list", ".", "--unresolved")
        assert result.returncode == 0
        assert "(no unresolved annotations found under" in result.stdout

    def test_cli_directory_recursion_with_multiple_sidecars_shows_headers(
        self, run_cli, make_source, tmp_path
    ):
        make_source("a/one.txt", "alpha line\n")
        make_source("b/two.txt", "beta line\n")
        _create(run_cli, "a/one.txt", "alpha", "x")
        _create(run_cli, "b/two.txt", "beta", "y")

        result = run_cli("list", ".")
        assert result.returncode == 0
        assert "=== a/one.txt.annotations.json ===" in result.stdout
        assert "=== b/two.txt.annotations.json ===" in result.stdout
        # Sorted-path order.
        assert result.stdout.index("a/one.txt.annotations.json") < result.stdout.index(
            "b/two.txt.annotations.json"
        )

    @pytest.mark.parametrize(
        "ignored_dir",
        [
            ".git",
            "node_modules",
            ".worktrees",
            "out",
            "dist",
            "__pycache__",
            ".venv",
            "venv",
        ],
    )
    def test_cli_ignored_directories_are_skipped(self, run_cli, tmp_path, ignored_dir):
        # Annotations in an ignored directory shouldn't surface in dir-recursive list.
        ignored = tmp_path / ignored_dir
        ignored.mkdir(parents=True, exist_ok=True)
        (ignored / "skip.annotations.json").write_text(
            json.dumps(
                {
                    "annotations": [
                        {
                            "id": "deadbe",
                            "quote": "hidden",
                            "resolved": False,
                            "comments": [
                                {
                                    "author": "Claude",
                                    "body": "x",
                                    "timestamp": "2026-01-01T00:00:00.000Z",
                                }
                            ],
                        }
                    ]
                }
            )
            + "\n"
        )
        # A visible annotation outside the ignored dir, so the empty-result
        # message can't be the reason the bad id isn't found.
        visible_dir = tmp_path / "visible"
        visible_dir.mkdir()
        (visible_dir / "v.annotations.json").write_text(
            json.dumps(
                {
                    "annotations": [
                        {
                            "id": "abc123",
                            "quote": "shown",
                            "resolved": False,
                            "comments": [
                                {
                                    "author": "Claude",
                                    "body": "y",
                                    "timestamp": "2026-01-01T00:00:00.000Z",
                                }
                            ],
                        }
                    ]
                }
            )
            + "\n"
        )

        result = run_cli("list", ".")
        assert result.returncode == 0
        assert "abc123" in result.stdout
        assert "deadbe" not in result.stdout
        assert ignored_dir not in result.stdout


# ===========================================================================
# Backfill (cross-cutting: triggered when load_sidecar finds missing fields)
# ===========================================================================


class TestBackfill:
    def test_unit_list_migrates_and_rewrites_sidecar(
        self, tmp_path, monkeypatch, capsys
    ):
        sidecar = tmp_path / "x.py.annotations.json"
        sidecar.write_text(
            json.dumps(
                {
                    "annotations": [
                        {
                            "quote": "hello",
                            "comments": [
                                {"author": "Evan", "body": "?", "timestamp": "t"}
                            ],
                        }
                    ]
                }
            )
        )
        monkeypatch.setattr(annotate, "new_id", lambda: "back01")
        annotate.cmd_list(_ns(path=str(sidecar), unresolved=False))
        capsys.readouterr()
        after = json.loads(sidecar.read_text())
        assert after["annotations"][0]["id"] == "back01"
        assert after["annotations"][0]["resolved"] is False

    def test_cli_list_backfills_missing_id_and_resolved(
        self, tmp_path, run_cli, read_sidecar
    ):
        sidecar = tmp_path / "legacy.txt.annotations.json"
        original = {
            "annotations": [
                {
                    "quote": "legacy quote",
                    "comments": [
                        {
                            "author": "human",
                            "body": "old",
                            "timestamp": "2020-01-01T00:00:00.000Z",
                        }
                    ],
                }
            ]
        }
        sidecar.write_text(json.dumps(original))

        result = run_cli("list", "legacy.txt.annotations.json")
        assert result.returncode == 0
        out_lines = [ln for ln in result.stdout.splitlines() if "legacy quote" in ln]
        assert len(out_lines) == 1
        line = out_lines[0]
        assert line.startswith("○")
        m = re.search(r"\b([0-9a-f]{6})\b", line)
        assert m, f"no 6-hex id in line: {line!r}"
        stdout_id = m.group(1)

        data = read_sidecar(sidecar)
        ann = _only_annotation(data)
        assert HEX6_RE.match(ann["id"])
        assert ann["id"] == stdout_id
        assert ann["resolved"] is False
        # Original data preserved.
        assert ann["quote"] == "legacy quote"
        assert ann["comments"] == original["annotations"][0]["comments"]

    def test_cli_backfilled_file_is_pretty_printed(self, tmp_path, run_cli):
        sidecar = tmp_path / "legacy.txt.annotations.json"
        sidecar.write_text(
            '{"annotations":[{"quote":"q","comments":[{"author":"Claude","body":"x","timestamp":"2020-01-01T00:00:00.000Z"}]}]}'
        )
        result = run_cli("list", "legacy.txt.annotations.json")
        assert result.returncode == 0
        text = sidecar.read_text()
        # Re-pretty-printed and ends with a newline.
        assert text.endswith("\n")
        assert '\n  "annotations"' in text
