"""TDD test suite for scripts/statusline.py.

Tests invoke the script as a subprocess and assert on its contract:
- Reads JSON from stdin
- Prints exactly one line to stdout: "<cwd-basename> | <model-name> | <token-total> tok"
- Tolerates malformed inputs; on success paths, stderr is empty and exit code is 0
- On malformed stdin, prints "claude json parsing error o7" and exits 0
"""

from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "statusline.py"
BAD_INPUT_LINE = "claude json parsing error o7"


def run_script(stdin_text: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Invoke the statusline script with the given stdin text."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def make_stdin(
    transcript_path: str | None = None,
    model_display_name: str | None = "opus",
    cwd: str | None = "/Users/me/widget",
    *,
    include_transcript: bool = True,
    include_model: bool = True,
    include_cwd: bool = True,
    model_override=None,
    cwd_override=None,
) -> str:
    """Build a well-formed JSON stdin payload."""
    payload: dict = {}
    if include_transcript and transcript_path is not None:
        payload["transcript_path"] = transcript_path
    if include_model:
        if model_override is not None:
            payload["model"] = model_override
        else:
            payload["model"] = {"display_name": model_display_name}
    if include_cwd:
        if cwd_override is not None:
            payload["cwd"] = cwd_override
        else:
            payload["cwd"] = cwd
    return json.dumps(payload)


def write_transcript(lines: list[str]) -> str:
    """Write a transcript file with the provided raw lines. Returns its path."""
    fd, path = tempfile.mkstemp(prefix="transcript_", suffix=".jsonl")
    os.close(fd)
    with open(path, "w") as f:
        for line in lines:
            f.write(line)
            if not line.endswith("\n"):
                f.write("\n")
    return path


def usage_row(
    *,
    input_tokens=None,
    cache_read_input_tokens=None,
    cache_creation_input_tokens=None,
    output_tokens=None,
    location: str = "message",
    extra: dict | None = None,
) -> str:
    """Build a JSONL line containing a usage block.

    location: "message" → .message.usage; "top" → .usage; "none" → no usage block
    """
    usage: dict = {}
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
    if cache_read_input_tokens is not None:
        usage["cache_read_input_tokens"] = cache_read_input_tokens
    if cache_creation_input_tokens is not None:
        usage["cache_creation_input_tokens"] = cache_creation_input_tokens
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
    if extra:
        usage.update(extra)

    row: dict = {}
    if location == "message":
        row["message"] = {"usage": usage}
    elif location == "top":
        row["usage"] = usage
    elif location == "none":
        pass
    return json.dumps(row)


class StatuslineTestBase(unittest.TestCase):
    """Helpers shared across all test cases."""

    def assert_clean_success(self, result: subprocess.CompletedProcess) -> None:
        """Assert exit 0 and empty stderr."""
        self.assertEqual(
            result.returncode,
            0,
            f"expected exit 0, got {result.returncode}; stderr={result.stderr!r}",
        )
        self.assertEqual(
            result.stderr, "", f"expected empty stderr, got {result.stderr!r}"
        )

    def assert_line(self, result: subprocess.CompletedProcess, expected: str) -> None:
        """Assert clean exit AND stdout equals expected + newline."""
        self.assert_clean_success(result)
        self.assertEqual(result.stdout, expected + "\n")

    def _track_for_cleanup(self, path: str) -> None:
        self.addCleanup(self._safe_unlink, path)

    @staticmethod
    def _safe_unlink(path: str) -> None:
        try:
            # Restore permissions in case test chmodded 000
            os.chmod(path, 0o600)
        except OSError:
            pass
        try:
            os.unlink(path)
        except (OSError, IsADirectoryError):
            try:
                os.rmdir(path)
            except OSError:
                pass

    def make_transcript(self, lines: list[str]) -> str:
        path = write_transcript(lines)
        self._track_for_cleanup(path)
        return path

    def empty_transcript(self) -> str:
        return self.make_transcript([])


# ---------------------------------------------------------------------------
# Basic happy path
# ---------------------------------------------------------------------------


class BasicHappyPathTests(StatuslineTestBase):
    def test_basic_line_format(self):
        path = self.empty_transcript()
        stdin = make_stdin(
            transcript_path=path, model_display_name="opus", cwd="/Users/me/widget"
        )
        result = run_script(stdin)
        self.assert_line(result, "widget | opus | 0 tok")

    def test_basic_line_with_usage(self):
        path = self.make_transcript(
            [
                usage_row(input_tokens=100, output_tokens=50),
            ]
        )
        stdin = make_stdin(
            transcript_path=path, model_display_name="sonnet", cwd="/tmp/proj"
        )
        result = run_script(stdin)
        self.assert_line(result, "proj | sonnet | 150 tok")

    def test_exactly_one_line_terminated_by_newline(self):
        path = self.empty_transcript()
        stdin = make_stdin(transcript_path=path)
        result = run_script(stdin)
        self.assert_clean_success(result)
        # exactly one trailing newline
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)


# ---------------------------------------------------------------------------
# cwd-basename behavior
# ---------------------------------------------------------------------------


class CwdBasenameTests(StatuslineTestBase):
    def test_simple_basename(self):
        path = self.empty_transcript()
        result = run_script(make_stdin(transcript_path=path, cwd="/Users/me/widget"))
        self.assert_line(result, "widget | opus | 0 tok")

    def test_trailing_slash_does_not_produce_empty(self):
        path = self.empty_transcript()
        result = run_script(make_stdin(transcript_path=path, cwd="/Users/me/widget/"))
        self.assert_line(result, "widget | opus | 0 tok")

    def test_cwd_root_slash_renders_as_slash(self):
        # cwd="/" should render as "/", not as empty string. rstrip("/") on
        # "/" leaves "" → os.path.basename("") → "". Need to fall back.
        path = self.empty_transcript()
        result = run_script(make_stdin(transcript_path=path, cwd="/"))
        self.assert_line(result, "/ | opus | 0 tok")

    def test_trailing_multiple_slashes(self):
        path = self.empty_transcript()
        result = run_script(make_stdin(transcript_path=path, cwd="/Users/me/widget///"))
        self.assert_line(result, "widget | opus | 0 tok")

    def test_cwd_missing(self):
        path = self.empty_transcript()
        stdin = make_stdin(transcript_path=path, include_cwd=False)
        result = run_script(stdin)
        self.assert_line(result, " | opus | 0 tok")

    def test_cwd_null(self):
        path = self.empty_transcript()
        payload = {
            "transcript_path": path,
            "model": {"display_name": "opus"},
            "cwd": None,
        }
        result = run_script(json.dumps(payload))
        self.assert_line(result, " | opus | 0 tok")

    def test_cwd_empty_string(self):
        path = self.empty_transcript()
        result = run_script(make_stdin(transcript_path=path, cwd=""))
        self.assert_line(result, " | opus | 0 tok")

    def test_cwd_wrong_type_integer(self):
        path = self.empty_transcript()
        payload = {
            "transcript_path": path,
            "model": {"display_name": "opus"},
            "cwd": 12345,
        }
        result = run_script(json.dumps(payload))
        self.assert_line(result, " | opus | 0 tok")

    def test_cwd_wrong_type_list(self):
        path = self.empty_transcript()
        payload = {
            "transcript_path": path,
            "model": {"display_name": "opus"},
            "cwd": ["a", "b"],
        }
        result = run_script(json.dumps(payload))
        self.assert_line(result, " | opus | 0 tok")


# ---------------------------------------------------------------------------
# model-name fallback behavior
# ---------------------------------------------------------------------------


class ModelNameTests(StatuslineTestBase):
    def test_model_display_name_present(self):
        path = self.empty_transcript()
        result = run_script(
            make_stdin(transcript_path=path, model_display_name="haiku")
        )
        self.assert_line(result, "widget | haiku | 0 tok")

    def test_model_missing_falls_back_to_claude(self):
        path = self.empty_transcript()
        stdin = make_stdin(transcript_path=path, include_model=False)
        result = run_script(stdin)
        self.assert_line(result, "widget | claude | 0 tok")

    def test_model_null_falls_back_to_claude(self):
        path = self.empty_transcript()
        payload = {"transcript_path": path, "model": None, "cwd": "/Users/me/widget"}
        result = run_script(json.dumps(payload))
        self.assert_line(result, "widget | claude | 0 tok")

    def test_model_wrong_type_string(self):
        path = self.empty_transcript()
        stdin = make_stdin(transcript_path=path, model_override="opus")
        result = run_script(stdin)
        self.assert_line(result, "widget | claude | 0 tok")

    def test_model_object_missing_display_name(self):
        path = self.empty_transcript()
        stdin = make_stdin(transcript_path=path, model_override={"name": "opus"})
        result = run_script(stdin)
        self.assert_line(result, "widget | claude | 0 tok")

    def test_model_object_display_name_null(self):
        path = self.empty_transcript()
        stdin = make_stdin(transcript_path=path, model_override={"display_name": None})
        result = run_script(stdin)
        self.assert_line(result, "widget | claude | 0 tok")

    def test_model_object_display_name_wrong_type(self):
        path = self.empty_transcript()
        stdin = make_stdin(transcript_path=path, model_override={"display_name": 42})
        result = run_script(stdin)
        self.assert_line(result, "widget | claude | 0 tok")

    def test_model_empty_object(self):
        path = self.empty_transcript()
        stdin = make_stdin(transcript_path=path, model_override={})
        result = run_script(stdin)
        self.assert_line(result, "widget | claude | 0 tok")


# ---------------------------------------------------------------------------
# Token formatting
# ---------------------------------------------------------------------------


class TokenFormattingTests(StatuslineTestBase):
    def _run_with_total(self, total: int) -> subprocess.CompletedProcess:
        """Build a transcript whose last usage row sums to `total` and run."""
        path = self.make_transcript(
            [
                usage_row(input_tokens=total, output_tokens=0),
            ]
        )
        return run_script(
            make_stdin(transcript_path=path, cwd="/x/dir", model_display_name="m")
        )

    def _token_part(self, total: int) -> str:
        result = self._run_with_total(total)
        self.assert_clean_success(result)
        # stdout format: "dir | m | <TOK> tok\n"
        line = result.stdout.rstrip("\n")
        parts = line.split(" | ")
        self.assertEqual(len(parts), 3, f"unexpected stdout: {result.stdout!r}")
        suffix = " tok"
        self.assertTrue(
            parts[2].endswith(suffix), f"unexpected token segment: {parts[2]!r}"
        )
        return parts[2][: -len(suffix)]

    def test_zero(self):
        self.assertEqual(self._token_part(0), "0")

    def test_one(self):
        self.assertEqual(self._token_part(1), "1")

    def test_just_under_1k(self):
        self.assertEqual(self._token_part(999), "999")

    def test_exactly_1k(self):
        self.assertEqual(self._token_part(1000), "1.0k")

    def test_1500_is_1_5k(self):
        self.assertEqual(self._token_part(1500), "1.5k")

    def test_42600_is_42_6k(self):
        self.assertEqual(self._token_part(42600), "42.6k")

    def test_999900_is_999_9k(self):
        # 999900 / 1000 = 999.9
        self.assertEqual(self._token_part(999900), "999.9k")

    def test_999949_is_999_9k_boundary_below(self):
        # 999949 < 999950, formats with k. 999949/1000 = 999.949 -> "999.9k"
        self.assertEqual(self._token_part(999949), "999.9k")

    def test_999950_promotes_to_M(self):
        # The promotion threshold: 999950 → "1.0M", never "1000.0k"
        self.assertEqual(self._token_part(999950), "1.0M")

    def test_just_above_threshold_is_M(self):
        # 1000000 / 1_000_000 = 1.0
        self.assertEqual(self._token_part(1_000_000), "1.0M")

    def test_1_2M(self):
        self.assertEqual(self._token_part(1_200_000), "1.2M")

    def test_half_up_rounds_at_boundary(self):
        # 1450 → 1.5k under half-up. Banker's rounding would give "1.4k" (4
        # is even). This test pins the spec choice.
        self.assertEqual(self._token_part(1450), "1.5k")
        # 1449 — under .5, rounds down regardless of mode.
        self.assertEqual(self._token_part(1449), "1.4k")

    def test_never_outputs_1000_0k(self):
        """Sweep around the boundary; must never emit '1000.0k'."""
        for n in [999_949, 999_950, 999_951, 1_000_000, 1_000_500, 1_234_567]:
            with self.subTest(n=n):
                tok = self._token_part(n)
                self.assertNotEqual(tok, "1000.0k", f"got bad output for n={n}")


# ---------------------------------------------------------------------------
# Token total: usage-row selection and summation
# ---------------------------------------------------------------------------


class TokenSummationTests(StatuslineTestBase):
    def test_sums_all_four_fields(self):
        path = self.make_transcript(
            [
                usage_row(
                    input_tokens=10,
                    cache_read_input_tokens=20,
                    cache_creation_input_tokens=30,
                    output_tokens=40,
                ),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 100 tok")

    def test_missing_fields_treated_as_zero(self):
        path = self.make_transcript(
            [
                usage_row(input_tokens=10),  # only input_tokens present
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 10 tok")

    def test_input_tokens_zero_is_valid_usage_row(self):
        # input_tokens: 0 IS a valid usage row (fully cached turn)
        path = self.make_transcript(
            [
                usage_row(
                    input_tokens=0,
                    cache_read_input_tokens=500,
                    output_tokens=100,
                ),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 600 tok")

    def test_last_usage_row_wins(self):
        path = self.make_transcript(
            [
                usage_row(input_tokens=100, output_tokens=50),
                usage_row(input_tokens=10, output_tokens=5),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        # last row's total = 15
        self.assert_line(result, "widget | opus | 15 tok")

    def test_message_usage_preferred_location(self):
        path = self.make_transcript(
            [
                usage_row(input_tokens=7, output_tokens=3, location="message"),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 10 tok")

    def test_top_level_usage_recognized(self):
        path = self.make_transcript(
            [
                usage_row(input_tokens=7, output_tokens=3, location="top"),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 10 tok")

    def test_message_usage_wins_when_both_have_input_tokens(self):
        # Both locations have a valid usage block — message-level is preferred.
        row = json.dumps(
            {
                "message": {"usage": {"input_tokens": 100, "output_tokens": 50}},
                "usage": {"input_tokens": 999, "output_tokens": 999},
            }
        )
        path = self.make_transcript([row])
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 150 tok")

    def test_falls_back_to_top_level_when_message_usage_empty(self):
        # message.usage is {} (no input_tokens) → must fall back to top-level.
        row = json.dumps(
            {
                "message": {"usage": {}},
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )
        path = self.make_transcript([row])
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 150 tok")

    def test_falls_back_to_top_level_when_message_usage_lacks_input_tokens(self):
        # message.usage has cache fields but no input_tokens → not a valid block.
        # Top-level has input_tokens → use top-level.
        row = json.dumps(
            {
                "message": {"usage": {"cache_read_input_tokens": 9999}},
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )
        path = self.make_transcript([row])
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 150 tok")

    def test_row_without_input_tokens_skipped(self):
        # only cache_read_input_tokens — no input_tokens → not a usage row
        path = self.make_transcript(
            [
                usage_row(input_tokens=100, output_tokens=50),  # valid
                usage_row(cache_read_input_tokens=999),  # not a usage row, skipped
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        # last *valid* usage row total = 150
        self.assert_line(result, "widget | opus | 150 tok")

    def test_row_with_no_usage_block_skipped(self):
        path = self.make_transcript(
            [
                usage_row(input_tokens=5, output_tokens=5),
                json.dumps({"message": {"role": "user", "content": "hi"}}),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 10 tok")

    def test_no_usage_rows_at_all_yields_zero(self):
        path = self.make_transcript(
            [
                json.dumps({"message": {"role": "user", "content": "hi"}}),
                json.dumps({"foo": "bar"}),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 0 tok")


# ---------------------------------------------------------------------------
# Defensive parsing: non-objects, malformed lines, blanks
# ---------------------------------------------------------------------------


class TranscriptDefensiveTests(StatuslineTestBase):
    def test_empty_transcript_yields_zero(self):
        path = self.empty_transcript()
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 0 tok")

    def test_only_malformed_lines_yields_zero(self):
        path = self.make_transcript(
            [
                "{not json",
                "also not json}}}",
                "{{{",
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 0 tok")

    def test_non_object_json_lines_skipped(self):
        path = self.make_transcript(
            [
                "null",
                "[]",
                "42",
                '"a string"',
                "true",
                usage_row(input_tokens=7, output_tokens=3),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 10 tok")

    def test_non_dict_json_lines_after_last_usage_row_skipped(self):
        # Valid-JSON-but-non-dict lines positioned AFTER the last usage row.
        # The reverse scan reaches these first and feeds them to _row_usage,
        # which must reject a non-dict row (the isinstance guard) and keep
        # scanning backwards to the real usage row. In the existing
        # test_non_object_json_lines_skipped the non-dict lines precede the
        # usage row, so the scan returns before ever passing them to
        # _row_usage — this orders them so that path is actually exercised.
        path = self.make_transcript(
            [
                usage_row(input_tokens=7, output_tokens=3),
                "[1, 2, 3]",
                "42",
                '"a string"',
                "true",
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 10 tok")

    def test_trailing_blank_lines_ignored(self):
        path = self.make_transcript(
            [
                usage_row(input_tokens=4, output_tokens=6),
                "",
                "   ",
                "\t",
                "",
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 10 tok")

    def test_mixed_valid_and_malformed(self):
        path = self.make_transcript(
            [
                "garbage",
                usage_row(input_tokens=1, output_tokens=1),
                "{still bad",
                usage_row(input_tokens=10, output_tokens=10),
                "}}}",
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        # last valid usage row wins: 10+10=20
        self.assert_line(result, "widget | opus | 20 tok")

    def test_negative_values_treated_as_zero(self):
        path = self.make_transcript(
            [
                usage_row(
                    input_tokens=-50,
                    cache_read_input_tokens=-100,
                    cache_creation_input_tokens=20,
                    output_tokens=30,
                ),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        # only positives count → 20 + 30 = 50
        self.assert_line(result, "widget | opus | 50 tok")

    def test_non_numeric_string_values_treated_as_zero(self):
        path = self.make_transcript(
            [
                usage_row(
                    input_tokens=10,
                    cache_read_input_tokens="banana",
                    cache_creation_input_tokens=None,
                    output_tokens=5,
                ),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        # 10 + 0 + 0 + 5 = 15
        self.assert_line(result, "widget | opus | 15 tok")

    def test_float_values_accepted(self):
        # spec says "if non-numeric, treat as 0"; floats are numeric
        path = self.make_transcript(
            [
                usage_row(input_tokens=10.0, output_tokens=5.0),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        # 10 + 5 = 15
        self.assert_line(result, "widget | opus | 15 tok")

    def test_fractional_floats_use_half_up_rounding(self):
        # Consistent with the display formatter (half-up): 10.5 → 11, 10.4 → 10.
        # Floats won't appear in real Anthropic responses, but if they did the
        # rounding rule should match the displayed-number convention.
        path = self.make_transcript(
            [
                usage_row(input_tokens=10.5, output_tokens=10.4),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        # 11 + 10 = 21
        self.assert_line(result, "widget | opus | 21 tok")

    def test_total_never_negative(self):
        # All-negative usage block must not produce a negative total
        path = self.make_transcript(
            [
                usage_row(
                    input_tokens=-1,
                    cache_read_input_tokens=-1,
                    cache_creation_input_tokens=-1,
                    output_tokens=-1,
                ),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 0 tok")

    def test_boolean_values_treated_as_zero(self):
        # bool is a subclass of int in Python; True/False should not contribute
        # to the token total. A usage block of booleans → 0.
        path = self.make_transcript(
            [
                usage_row(
                    input_tokens=True,
                    cache_read_input_tokens=False,
                    cache_creation_input_tokens=True,
                    output_tokens=False,
                ),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 0 tok")

    def test_boolean_input_tokens_does_not_replace_valid_row(self):
        # A row whose input_tokens is True/False should NOT count as a valid
        # usage row — the previous real row should still win.
        path = self.make_transcript(
            [
                usage_row(input_tokens=100, output_tokens=50),
                usage_row(input_tokens=True, output_tokens=999),
            ]
        )
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 150 tok")


# ---------------------------------------------------------------------------
# Transcript file failures
# ---------------------------------------------------------------------------


class TranscriptFileFailureTests(StatuslineTestBase):
    def test_nonexistent_transcript_file(self):
        path = "/tmp/definitely_does_not_exist_statusline_test_xyzzy.jsonl"
        # ensure it really doesn't exist
        if os.path.exists(path):
            os.unlink(path)
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 0 tok")

    def test_transcript_path_is_a_directory(self):
        d = tempfile.mkdtemp(prefix="statusline_dir_")
        self.addCleanup(os.rmdir, d)
        result = run_script(make_stdin(transcript_path=d))
        self.assert_line(result, "widget | opus | 0 tok")

    def test_transcript_unreadable_chmod_000(self):
        path = self.make_transcript(
            [
                usage_row(input_tokens=1, output_tokens=1),
            ]
        )
        os.chmod(path, 0)
        # Running as root could bypass; if so, this still must not crash.
        result = run_script(make_stdin(transcript_path=path))
        self.assert_clean_success(result)
        # Acceptable outcomes: token=0 (unreadable) OR token=2 (if running as root).
        line = result.stdout.rstrip("\n")
        parts = line.split(" | ")
        self.assertEqual(parts[0], "widget")
        self.assertEqual(parts[1], "opus")
        self.assertIn(parts[2], {"0 tok", "2 tok"})

    def test_transcript_path_missing(self):
        stdin = make_stdin(include_transcript=False)
        result = run_script(stdin)
        self.assert_line(result, "widget | opus | 0 tok")

    def test_transcript_path_null(self):
        payload = {
            "transcript_path": None,
            "model": {"display_name": "opus"},
            "cwd": "/Users/me/widget",
        }
        result = run_script(json.dumps(payload))
        self.assert_line(result, "widget | opus | 0 tok")

    def test_transcript_path_wrong_type(self):
        payload = {
            "transcript_path": 12345,
            "model": {"display_name": "opus"},
            "cwd": "/Users/me/widget",
        }
        result = run_script(json.dumps(payload))
        self.assert_line(result, "widget | opus | 0 tok")

    def test_relative_transcript_path_rejected(self):
        # Even if the relative path exists (e.g. created in CWD), it must be
        # treated as missing. Claude Code always sends absolute paths.
        payload = {
            "transcript_path": "session.jsonl",
            "model": {"display_name": "opus"},
            "cwd": "/Users/me/widget",
        }
        result = run_script(json.dumps(payload))
        self.assert_line(result, "widget | opus | 0 tok")


# ---------------------------------------------------------------------------
# Bad stdin handling
# ---------------------------------------------------------------------------


class BadStdinTests(StatuslineTestBase):
    def test_malformed_json_prints_bad_input(self):
        result = run_script("{this is not valid json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, BAD_INPUT_LINE + "\n")

    def test_empty_stdin_does_not_crash(self):
        result = run_script("")
        self.assertEqual(result.returncode, 0)
        # Either fallback or sensible defaults are acceptable
        self.assertTrue(
            result.stdout.endswith("\n"),
            f"stdout must end with newline: {result.stdout!r}",
        )
        line = result.stdout.rstrip("\n")
        self.assertTrue(
            line == BAD_INPUT_LINE or " | " in line,
            f"unexpected stdout on empty stdin: {line!r}",
        )

    def test_whitespace_only_stdin_does_not_crash(self):
        result = run_script("   \n\t  \n")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.endswith("\n"))
        line = result.stdout.rstrip("\n")
        self.assertTrue(
            line == BAD_INPUT_LINE or " | " in line,
            f"unexpected stdout on whitespace stdin: {line!r}",
        )

    def test_stdin_json_null_does_not_crash(self):
        result = run_script("null")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.endswith("\n"))
        line = result.stdout.rstrip("\n")
        # Spec: either fallback or standard line with defaults is acceptable
        self.assertTrue(
            line == BAD_INPUT_LINE or " | " in line,
            f"unexpected stdout: {line!r}",
        )

    def test_stdin_json_array_does_not_crash(self):
        result = run_script("[]")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.endswith("\n"))
        line = result.stdout.rstrip("\n")
        self.assertTrue(
            line == BAD_INPUT_LINE or " | " in line,
            f"unexpected stdout: {line!r}",
        )

    def test_stdin_json_number_does_not_crash(self):
        result = run_script("42")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.endswith("\n"))
        line = result.stdout.rstrip("\n")
        self.assertTrue(
            line == BAD_INPUT_LINE or " | " in line,
            f"unexpected stdout: {line!r}",
        )

    def test_stdin_json_string_does_not_crash(self):
        result = run_script('"hello"')
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.endswith("\n"))
        line = result.stdout.rstrip("\n")
        self.assertTrue(
            line == BAD_INPUT_LINE or " | " in line,
            f"unexpected stdout: {line!r}",
        )

    def test_bad_input_exits_zero_and_no_traceback(self):
        result = run_script("{bad")
        self.assertEqual(result.returncode, 0)
        # On the bad-input path stderr is not strictly required to be empty by spec
        # (only success paths require empty stderr), but no Python traceback should leak.
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Traceback", result.stdout)

    def test_empty_object_uses_all_defaults(self):
        result = run_script("{}")
        self.assert_clean_success(result)
        # With nothing, expect: empty cwd basename, "claude" model, 0 tok
        self.assertEqual(result.stdout, " | claude | 0 tok\n")


# ---------------------------------------------------------------------------
# Integration / sanity
# ---------------------------------------------------------------------------


class IntegrationTests(StatuslineTestBase):
    def test_all_fields_present_with_realistic_transcript(self):
        path = self.make_transcript(
            [
                json.dumps({"message": {"role": "user", "content": "hi"}}),
                usage_row(
                    input_tokens=1200, cache_read_input_tokens=300, output_tokens=250
                ),
                json.dumps({"message": {"role": "assistant", "content": "ok"}}),
                usage_row(
                    input_tokens=2000,
                    cache_read_input_tokens=500,
                    cache_creation_input_tokens=100,
                    output_tokens=400,
                ),
                "",
            ]
        )
        stdin = make_stdin(
            transcript_path=path,
            model_display_name="opus-4.7",
            cwd="/Users/me/projects/my-app/",
        )
        result = run_script(stdin)
        # last usage = 2000+500+100+400 = 3000 → "3.0k"
        self.assert_line(result, "my-app | opus-4.7 | 3.0k tok")

    def test_script_runs_from_any_cwd(self):
        path = self.empty_transcript()
        # Run from /tmp explicitly; SCRIPT path is absolute so this should work
        stdin = make_stdin(transcript_path=path)
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=stdin,
            capture_output=True,
            text=True,
            cwd="/tmp",
            timeout=10.0,
        )
        self.assert_line(result, "widget | opus | 0 tok")


# ---------------------------------------------------------------------------
# Reverse-scan chunk-boundary behavior
#
# statusline.py reads the transcript in 16KB chunks from EOF, carrying any
# partial first line into the next iteration. These tests exercise that path
# directly: the basic-suite transcripts all fit in a single chunk.
# ---------------------------------------------------------------------------


CHUNK_SIZE = 16384  # mirrors scripts/statusline.py


class ReverseScanChunkBoundaryTests(StatuslineTestBase):
    def test_last_usage_row_far_from_eof_across_many_chunks(self):
        # Valid usage row near the start, then many KB of non-usage rows.
        # Reverse scan must traverse multiple chunks to find it.
        valid = usage_row(input_tokens=11, output_tokens=22)
        padding_row = json.dumps(
            {"message": {"role": "assistant", "content": "x" * 60}}
        )
        lines = [valid] + [padding_row] * 2000  # ~160KB of padding
        path = self.make_transcript(lines)
        self.assertGreater(os.path.getsize(path), CHUNK_SIZE * 4)
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 33 tok")

    def test_last_usage_row_straddles_chunk_boundary(self):
        # The last usage line is itself longer than a chunk — its start lies
        # in an earlier chunk than its end. The carry-over branch must
        # reassemble it.
        prefix_filler = json.dumps(
            {"message": {"role": "assistant", "content": "y" * 60}}
        )
        long_content = "z" * 20_000  # > chunk_size, guaranteed straddle
        big_usage = json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": long_content,
                    "usage": {"input_tokens": 77, "output_tokens": 23},
                }
            }
        )
        front_pad = [prefix_filler] * 50
        path = self.make_transcript(front_pad + [big_usage])
        self.assertGreater(os.path.getsize(path), CHUNK_SIZE * 1.5)
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 100 tok")

    def test_many_usage_rows_across_chunks_last_one_wins(self):
        # Many rows, file spans multiple chunks; final row must still win.
        rows = [usage_row(input_tokens=i * 10, output_tokens=i) for i in range(500)]
        rows.append(usage_row(input_tokens=100, output_tokens=7))
        path = self.make_transcript(rows)
        self.assertGreater(os.path.getsize(path), CHUNK_SIZE)
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 107 tok")

    def test_file_without_trailing_newline(self):
        # Bypass write_transcript()'s implicit trailing-newline behavior.
        fd, path = tempfile.mkstemp(prefix="transcript_nonl_", suffix=".jsonl")
        os.close(fd)
        self.addCleanup(self._safe_unlink, path)
        line = usage_row(input_tokens=42, output_tokens=8)
        with open(path, "wb") as f:
            f.write(line.encode("utf-8"))  # no trailing \n
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 50 tok")

    def test_single_giant_line_spanning_multiple_chunks(self):
        # File is ONE line, no newlines anywhere, bigger than several chunks.
        # The "no newline in block" carry-over path must still work.
        long_content = "q" * (CHUNK_SIZE * 3 + 137)
        big_usage = json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": long_content,
                    "usage": {"input_tokens": 5, "output_tokens": 6},
                }
            }
        )
        fd, path = tempfile.mkstemp(prefix="transcript_oneline_", suffix=".jsonl")
        os.close(fd)
        self.addCleanup(self._safe_unlink, path)
        with open(path, "wb") as f:
            f.write(big_usage.encode("utf-8"))
        self.assertGreater(os.path.getsize(path), CHUNK_SIZE * 3)
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 11 tok")

    def test_crlf_line_endings(self):
        # JSON tolerates leading/trailing whitespace including \r, so a CRLF
        # transcript should still parse cleanly.
        fd, path = tempfile.mkstemp(prefix="transcript_crlf_", suffix=".jsonl")
        os.close(fd)
        self.addCleanup(self._safe_unlink, path)
        line = usage_row(input_tokens=3, output_tokens=4)
        with open(path, "wb") as f:
            f.write(line.encode("utf-8") + b"\r\n")
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 7 tok")

    def test_embedded_nul_bytes_do_not_crash(self):
        fd, path = tempfile.mkstemp(prefix="transcript_nul_", suffix=".jsonl")
        os.close(fd)
        self.addCleanup(self._safe_unlink, path)
        valid = usage_row(input_tokens=12, output_tokens=8).encode("utf-8")
        with open(path, "wb") as f:
            f.write(b"\x00\x00garbage\x00bytes\n")
            f.write(valid + b"\n")
            f.write(b"\x00trailing\x00noise\n")
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 20 tok")

    def test_invalid_utf8_bytes_do_not_crash(self):
        # Script decodes with errors="replace"; lines that don't parse skip.
        fd, path = tempfile.mkstemp(prefix="transcript_badutf_", suffix=".jsonl")
        os.close(fd)
        self.addCleanup(self._safe_unlink, path)
        valid = usage_row(input_tokens=1, output_tokens=2).encode("utf-8")
        with open(path, "wb") as f:
            f.write(b"\xff\xfe\xfd not valid utf-8 garbage\n")
            f.write(valid + b"\n")
            f.write(b"\xc3\x28 also invalid\n")
        result = run_script(make_stdin(transcript_path=path))
        self.assert_line(result, "widget | opus | 3 tok")


# ---------------------------------------------------------------------------
# Statusline script wiring sanity — invoked directly by Claude Code via the
# installer-managed ~/.claude/settings.json (see scripts/install-statusline.py
# and tests/test_install_statusline.py for the wiring contract).
# ---------------------------------------------------------------------------


class StatuslineScriptWiringTests(unittest.TestCase):
    def test_statusline_script_is_executable(self):
        # The installer registers this script as a `command`-type statusLine,
        # invoked directly (no `python3` prefix), so the shebang + executable
        # bit must both be present.
        self.assertTrue(os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable")
        with open(SCRIPT) as f:
            first_line = f.readline()
        self.assertTrue(
            first_line.startswith("#!"),
            f"expected shebang, got: {first_line!r}",
        )


if __name__ == "__main__":
    unittest.main()
