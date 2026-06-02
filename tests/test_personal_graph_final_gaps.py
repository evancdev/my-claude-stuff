"""Final gaps audit: black-box tests targeting adversarial dimensions
not exhaustively covered by the existing suites.

Focuses on three specific contract clauses where prior tests are
either missing or only partial:

- Dimension #11 (`neo4j-up.sh` detachment): the contract says the
  script must return promptly (<10s) "even when the image needs
  pulling". That implies the slow `docker compose up` step must be
  detached / backgrounded. Existing tests use a globally-slow shim
  but never specifically test the case where `compose up` is the
  only slow call. Without this targeted shim, a sync `compose up`
  invocation would pass the existing tests as long as no `docker
  inspect` was slow.

- Dimension #12 (cross-file MERGE-key consistency): the schema doc
  and `commands/graph-seed.md` must agree about MERGE keys for
  `Event`, `Decision`, `Preference`. If the schema says
  `(title, start_at)` but graph-seed.md instructs MERGE on `id`,
  Claude will produce duplicate nodes under load. Prior tests only
  check that `MERGE` is *mentioned*, never that the keys match
  cross-file.

- Dimension #10 (python3 trap fallback — no-concatenation): the
  existing contract-audit test loads stdout with `json.loads`, which
  would also fail if the trap emitted valid JSON *after* the partial
  garbage. But the failure mode there is ambiguous — a developer
  reading the assertion can't tell if it's "concat happened" or
  "trap didn't fire at all". This file adds a targeted check that
  fingerprint-rejects concatenated output: stdout must be exactly
  one JSON object with NO extra bytes / NO partial fragment before
  or after.

Black-box only — none of the forbidden files are read here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

NEO4J_UP_SH = REPO_ROOT / "scripts" / "neo4j-up.sh"
SESSION_START_SH = REPO_ROOT / "hooks" / "session-start-graph.sh"
GRAPH_SCHEMA_MD = REPO_ROOT / "conventions" / "graph-schema.md"
GRAPH_SEED_MD = REPO_ROOT / "commands" / "graph-seed.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_exec(path: str, body: str) -> str:
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)
    return path


def _make_slow_compose_up_shim() -> str:
    """Docker shim where:
      - `docker info` returns instantly (daemon up).
      - `docker inspect` returns instantly with "false" (container exists,
        not running) OR "no such object" to trigger the up-path.
      - `docker compose ps -q` returns instantly with no id (container
        not running, needs to be brought up).
      - **`docker compose up` sleeps 60s** — simulating a long image
        pull. THIS is the call that must NOT block neo4j-up.sh.

    Contract dimension #11: neo4j-up.sh must return promptly even when
    `compose up` would take 60s, i.e. it must detach the up call.
    """
    d = tempfile.mkdtemp(prefix="slow_compose_up_")
    body = textwrap.dedent(
        """\
        #!/bin/sh
        cmd="$1"; shift
        case "$cmd" in
            info)
                # Daemon up; respond immediately.
                exit 0
                ;;
            inspect)
                # Container is missing — script will try to start it.
                echo "Error: No such object" 1>&2
                exit 1
                ;;
            ps)
                # No running container with that name.
                exit 0
                ;;
            compose)
                sub="$1"; shift || true
                case "$sub" in
                    ps)
                        # `compose ps -q` → empty (no running container).
                        exit 0
                        ;;
                    up)
                        # This is the slow call — simulates image pull.
                        sleep 60
                        exit 0
                        ;;
                    start)
                        # Same hazard if the script uses compose start
                        # when there's nothing to start.
                        sleep 60
                        exit 0
                        ;;
                    *)
                        exit 0
                        ;;
                esac
                ;;
            start|run)
                # Top-level `docker start <name>` against a missing
                # container would fail fast in reality, but here we
                # simulate slow so the test catches a script that uses
                # `docker start` instead of `compose up`.
                sleep 60
                exit 0
                ;;
            *)
                exit 0
                ;;
        esac
        """
    )
    _write_exec(os.path.join(d, "docker"), body)
    return d


# ---------------------------------------------------------------------------
# Dimension #11: neo4j-up.sh detachment from slow `compose up`
# ---------------------------------------------------------------------------


class Neo4jUpDetachmentTests(unittest.TestCase):
    """The contract is explicit:

        > Doesn't block on `docker compose up` even when image needs
        > pulling — the script should detach the slow operation. Test:
        > `docker` shim that sleeps 60s on `compose up` should not
        > delay neo4j-up.sh's return.

    A non-detached implementation will sit on `compose up` for the full
    60s and hit the unittest timeout. A correct implementation backgrounds
    that step (`&`, `nohup ... &`, `disown`, etc.) and returns promptly.
    """

    # Generous slop on top of the 10s contract budget for slow CI.
    FAST_RETURN_BUDGET = 12.0

    def setUp(self):
        if not NEO4J_UP_SH.exists():
            self.fail(f"missing {NEO4J_UP_SH}")

    def test_returns_fast_when_compose_up_is_slow(self):
        """The headline detachment test: `compose up` sleeps 60s,
        neo4j-up.sh must still return within 10s (12s with CI slop)."""
        d = _make_slow_compose_up_shim()
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            t0 = time.monotonic()
            try:
                r = subprocess.run(
                    ["bash", str(NEO4J_UP_SH)],
                    capture_output=True,
                    text=True,
                    # Hard cap WELL below the shim's 60s sleep — if the
                    # script blocks on compose up, subprocess will time
                    # out here and we'll know detachment is broken.
                    timeout=20.0,
                    env=env,
                )
            except subprocess.TimeoutExpired as e:
                elapsed = time.monotonic() - t0
                self.fail(
                    f"neo4j-up.sh did NOT detach `docker compose up` — "
                    f"blocked for {elapsed:.1f}s on a 60s shim "
                    f"(should detach and return <10s). "
                    f"This means a real cold-start image pull "
                    f"would freeze the SessionStart hook chain for minutes. "
                    f"TimeoutExpired={e!r}"
                )
            elapsed = time.monotonic() - t0
            self.assertLess(
                elapsed,
                self.FAST_RETURN_BUDGET,
                f"neo4j-up.sh took {elapsed:.2f}s with slow `compose up` "
                f"shim; contract requires <10s return even when image "
                f"needs pulling. stdout={r.stdout!r} stderr={r.stderr!r}",
            )
            self.assertEqual(
                r.returncode,
                0,
                f"neo4j-up.sh must exit 0 after detaching compose up; "
                f"got rc={r.returncode}, stderr={r.stderr!r}",
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_returns_fast_when_compose_up_is_slow_under_session_hook(self):
        """End-to-end: if `session-start-graph.sh` invokes neo4j-up.sh
        (directly or via `docker compose up`) and that step is slow, the
        hook itself must still hit the <10s contract.

        We use the same slow-compose-up shim and run the hook (not the
        up script) to make sure the hook layer also detaches / times
        out the slow call."""
        if not SESSION_START_SH.exists():
            self.fail(f"missing {SESSION_START_SH}")

        d = _make_slow_compose_up_shim()
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            t0 = time.monotonic()
            try:
                r = subprocess.run(
                    ["bash", str(SESSION_START_SH)],
                    capture_output=True,
                    text=True,
                    timeout=15.0,
                    env=env,
                )
            except subprocess.TimeoutExpired as e:
                elapsed = time.monotonic() - t0
                self.fail(
                    f"session-start-graph.sh blocked for {elapsed:.1f}s "
                    f"on a slow `compose up` shim — the hook must time "
                    f"out / detach so SessionStart never delays Claude "
                    f"more than 10s. TimeoutExpired={e!r}"
                )
            elapsed = time.monotonic() - t0
            self.assertLess(
                elapsed,
                12.0,
                f"hook wallclock {elapsed:.2f}s exceeds 10s contract on "
                f"slow-compose-up shim; stdout={r.stdout!r}",
            )
            self.assertEqual(
                r.returncode,
                0,
                f"hook must exit 0 even on slow compose up; stderr={r.stderr!r}",
            )
            # And — per the stdout contract — it must emit exactly one
            # valid JSON object even on this branch.
            stripped = r.stdout.strip()
            self.assertTrue(stripped, "hook stdout was empty")
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as e:
                self.fail(
                    f"hook stdout not valid JSON on slow-compose-up "
                    f"branch: {e}; stdout={r.stdout!r}"
                )
            self.assertIsInstance(obj, dict)
            self.assertIn("hookSpecificOutput", obj)
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Dimension #12: cross-file MERGE-key consistency
# ---------------------------------------------------------------------------


class MergeKeyConsistencyTests(unittest.TestCase):
    """Per contract:

        > MERGE keys for Event/Decision/Preference are documented
        > consistently — the schema doc's key column and any examples
        > in `commands/graph-seed.md` must agree.

    The schema file is the canonical source. We parse the "Node labels"
    table (a markdown table) to extract its declared MERGE keys, then
    scan graph-seed.md for any contradictory MERGE statements or
    instructions.
    """

    # Whitelist of acceptable MERGE-key tokens per label, derived from
    # the schema's declared keys at the time of writing. The point of
    # the test is to detect when graph-seed.md drifts away from these.
    # We re-derive this set from the schema doc itself below — if the
    # schema changes, this test recalibrates automatically. So the
    # block here is the FALLBACK only if parsing fails.
    FALLBACK_KEYS = {
        "Event": {"title", "start_at"},
        "Decision": {"summary", "decided_at"},
        "Preference": {"summary"},
    }

    def setUp(self):
        if not GRAPH_SCHEMA_MD.exists():
            self.fail(f"missing {GRAPH_SCHEMA_MD}")
        if not GRAPH_SEED_MD.exists():
            self.fail(f"missing {GRAPH_SEED_MD}")
        self.schema_text = GRAPH_SCHEMA_MD.read_text()
        self.seed_text = GRAPH_SEED_MD.read_text()

    def _parse_schema_merge_keys(self) -> dict:
        """Extract the declared MERGE key for each label from the
        Node-labels table. Returns {label: set-of-key-property-names}.
        On parse failure, returns the fallback constants."""
        keys: dict[str, set[str]] = {}
        # The contract phrases the table as "| `Event` | `(title, start_at)` |"
        # — backticked label, then backticked key spec in the next cell.
        row_re = re.compile(
            r"\|\s*`(?P<label>[A-Z][A-Za-z]*)`\s*\|\s*`(?P<key>[^`]+)`\s*\|"
        )
        for m in row_re.finditer(self.schema_text):
            label = m.group("label")
            key_spec = m.group("key").strip()
            # Strip the surrounding parens for composite keys
            inner = key_spec.strip("()")
            props = {p.strip() for p in inner.split(",") if p.strip()}
            keys[label] = props
        if not keys:
            return dict(self.FALLBACK_KEYS)
        return keys

    def _seed_cypher_blocks(self) -> list[str]:
        """All fenced cypher code blocks (and bare backtick spans) in
        graph-seed.md."""
        blocks = []
        # Fenced ```cypher ... ``` blocks
        for m in re.finditer(
            r"```cypher\b(.+?)```", self.seed_text, re.DOTALL | re.IGNORECASE
        ):
            blocks.append(m.group(1))
        # Also collect any other fenced ``` ... ``` blocks as fallback
        for m in re.finditer(
            r"```(?!cypher)([^\n]*)\n(.+?)```", self.seed_text, re.DOTALL
        ):
            blocks.append(m.group(2))
        return blocks

    def test_schema_declares_merge_keys_for_three_labels(self):
        """Precondition: the schema actually declares MERGE keys we can
        compare against. If this fails, the rest of the consistency
        tests are vacuous."""
        keys = self._parse_schema_merge_keys()
        for label in ("Event", "Decision", "Preference"):
            self.assertIn(
                label,
                keys,
                f"schema doc must declare a MERGE key for {label} (none parsed); "
                f"the cross-file consistency check cannot proceed.",
            )
            self.assertTrue(
                keys[label],
                f"schema doc has no key properties parsed for {label}",
            )

    def test_seed_examples_do_not_contradict_schema_keys(self):
        """For each Cypher `MERGE (n:Label {...})` in graph-seed.md,
        the property keys used inside `{...}` must be a subset of the
        schema's declared MERGE keys for that label. If schema says
        `(title, start_at)` and seed says `{id: ...}`, that's a bug.
        """
        schema_keys = self._parse_schema_merge_keys()
        blocks = self._seed_cypher_blocks()
        if not blocks:
            self.skipTest("no cypher blocks found in graph-seed.md")

        # Find every `MERGE (varname:Label {key1: $..., key2: $...})`
        merge_re = re.compile(
            r"MERGE\s*\(\s*\w+\s*:\s*(?P<label>[A-Z][A-Za-z]*)\s*\{(?P<props>[^}]*)\}",
            re.DOTALL,
        )
        prop_key_re = re.compile(r"(\w+)\s*:")

        mismatches: list[tuple[str, set[str], set[str]]] = []
        for blk in blocks:
            for m in merge_re.finditer(blk):
                label = m.group("label")
                if label not in ("Event", "Decision", "Preference"):
                    continue
                props_text = m.group("props")
                # First-token-before-colon for each comma-separated pair.
                used = set()
                for pair in props_text.split(","):
                    km = prop_key_re.match(pair.strip())
                    if km:
                        used.add(km.group(1))
                expected = schema_keys.get(label, set())
                if not used.issubset(expected):
                    mismatches.append((label, used, expected))
        self.assertFalse(
            mismatches,
            "graph-seed.md MERGE example(s) disagree with schema MERGE keys. "
            "Schema says one thing; the seed command MERGEs on different "
            "properties — running the seed will violate the idempotency "
            "guarantee. Mismatches (label, used, expected): "
            f"{mismatches!r}",
        )

    def test_seed_instructions_do_not_name_wrong_merge_key(self):
        """Adversarial: even outside code blocks, plain-prose
        instructions like "id for Event/Decision/Preference" or
        "MERGE keyed on id" contradict the schema's
        `(title, start_at)` etc.

        Schema explicitly says `do not MERGE on id` for Event. The
        seed-command body must not paraphrase the rule differently.
        We strip fenced code blocks first so only prose is examined.
        """
        schema_keys = self._parse_schema_merge_keys()

        # Remove fenced code blocks — code is checked by the other
        # tests; here we want prose contradictions.
        prose = re.sub(r"```.+?```", "", self.seed_text, flags=re.DOTALL)

        # Strip inline backtick spans (e.g. `MERGE`) so we don't confuse
        # them with bare prose tokens — but FIRST extract backticked
        # property names so we can include those too.
        # ...actually simpler: just scan the raw prose for the
        # contradiction patterns, allowing optional backticks around
        # `id`.

        offenders = []
        labels = ("Event", "Decision", "Preference")
        seen_snippets: set[str] = set()

        # Pattern A: "id for Event" / "id for Decision" / "id for
        # Preference" — the literal phrasing of the foot-gun found in
        # real seed-doc drift. Allow optional backticks around `id`.
        # Match each label independently then dedupe by snippet so we
        # don't double-report the same phrase under multiple labels.
        for label in labels:
            pat = re.compile(
                rf"\b`?id`?\s+for\s+(?:[A-Z][A-Za-z]+\s*[,/]\s*)*{label}\b",
                re.IGNORECASE,
            )
            for m in pat.finditer(prose):
                snippet = prose[max(0, m.start() - 40) : m.end() + 40].strip()
                if snippet in seen_snippets:
                    continue
                seen_snippets.add(snippet)
                offenders.append(("id-for-LABEL", snippet))

        # Pattern B: "MERGE ... keyed on id ..." in a sentence that
        # also names Event/Decision/Preference.
        sentences = re.split(r"(?<=[.\n])", prose)
        for s in sentences:
            if not any(lbl in s for lbl in labels):
                continue
            lower = s.lower()
            # Require some MERGE-key-discussion verbiage
            if not (("merge" in lower) and ("key" in lower or "keyed" in lower)):
                continue
            # Look for bare-word "id" in this sentence.
            if re.search(r"\b`?id`?\b", s):
                # ...but not if the only `id` here is "id (UUID)" being
                # mentioned as a downstream handle (the schema's own
                # phrasing). Excluding "id (UUID)" / "stable id" would
                # be over-eager; instead, require that the sentence
                # explicitly says `id` is the MERGE key.
                if re.search(
                    r"(MERGE\s+(?:on|keyed?\s+on)\s+`?id`?|"
                    r"`?id`?\s+(?:is\s+the\s+)?MERGE\s+key|"
                    r"key(?:ed)?\s+on\s+`?id`?)",
                    s,
                    re.IGNORECASE,
                ):
                    offenders.append(("merge-keyed-on-id", s.strip()))

        self.assertFalse(
            offenders,
            "graph-seed.md prose claims MERGE-key properties that "
            "contradict the schema doc. The schema declares "
            f"{ {k: sorted(v) for k, v in schema_keys.items()} } as MERGE keys "
            "for Event/Decision/Preference; the schema explicitly says "
            "'do not MERGE on id'. graph-seed.md says otherwise:\n  "
            + "\n  ".join(f"[{kind}] {snip}" for kind, snip in offenders),
        )

    def test_seed_example_block_uses_schema_keys_for_event(self):
        """Most-specific concrete check: the schema picks
        `(title, start_at)` as the MERGE key for Event and explicitly
        says 'do not MERGE on id'. graph-seed.md must — if it shows
        any MERGE on `Event` — MERGE on those two properties (and not
        on `id`)."""
        schema_keys = self._parse_schema_merge_keys()
        event_keys = schema_keys.get("Event")
        if not event_keys or event_keys == {"id"}:
            self.skipTest("schema does not declare composite-key Event")

        blocks = self._seed_cypher_blocks()
        event_merges = []
        merge_re = re.compile(
            r"MERGE\s*\(\s*\w+\s*:\s*Event\s*\{(?P<props>[^}]*)\}",
            re.DOTALL,
        )
        prop_key_re = re.compile(r"(\w+)\s*:")
        for blk in blocks:
            for m in merge_re.finditer(blk):
                used = set()
                for pair in m.group("props").split(","):
                    km = prop_key_re.match(pair.strip())
                    if km:
                        used.add(km.group(1))
                event_merges.append(used)
        if not event_merges:
            self.skipTest("no Event MERGE example in graph-seed.md")

        for used in event_merges:
            self.assertNotIn(
                "id",
                used,
                f"graph-seed.md MERGEs Event on `id` — the schema explicitly "
                f"says 'do not MERGE on id' (key is {sorted(event_keys)!r}). "
                f"Found: MERGE (...: Event {{{', '.join(sorted(used))}: ...}})",
            )
            self.assertTrue(
                used.issubset(event_keys),
                f"graph-seed.md MERGEs Event on {sorted(used)!r}; "
                f"schema key is {sorted(event_keys)!r}",
            )


# ---------------------------------------------------------------------------
# Dimension #10 (stronger): trap fallback must not concatenate onto
# python3's partial garbage. Be loud about the *concatenation* mode
# rather than just "stdout isn't valid JSON".
# ---------------------------------------------------------------------------


class HookNoConcatenationOnPython3CrashTests(unittest.TestCase):
    """Existing tests check `json.loads(stdout.strip())` succeeds when
    python3 dies mid-emit. That fails for both 'concat happened' AND
    'no fallback fired'. This test discriminates: we explicitly assert
    there's no leftover fragment from python3 still on stdout."""

    HOOK_TIMEOUT = 12.0
    PARTIAL_GARBAGE = '{"partial": "broken-from-test'

    def setUp(self):
        if not SESSION_START_SH.exists():
            self.fail(f"missing {SESSION_START_SH}")

    def _shim_dir(self) -> str:
        d = tempfile.mkdtemp(prefix="py3_partial_concat_")
        # docker shim: daemon down so hook takes the simple branch
        _write_exec(
            os.path.join(d, "docker"),
            textwrap.dedent(
                """\
                #!/bin/sh
                if [ "$1" = "info" ]; then
                    echo "Cannot connect to the Docker daemon" 1>&2
                    exit 1
                fi
                exit 0
                """
            ),
        )
        # python3 shim: emit a UNIQUE partial fragment then die.
        _write_exec(
            os.path.join(d, "python3"),
            textwrap.dedent(
                f"""\
                #!/bin/sh
                # Emit unique partial garbage. Test will check this
                # specific string does NOT appear in final stdout.
                printf '{self.PARTIAL_GARBAGE}'
                exit 1
                """
            ),
        )
        return d

    def test_partial_garbage_does_not_leak_to_final_stdout(self):
        """If python3 wrote 'X' to stdout and crashed, the trap MUST
        ensure 'X' is not part of stdout when the hook exits. Otherwise
        Claude reads `Xtrap_envelope` as stdout, which isn't valid JSON,
        the SessionStart additionalContext is dropped, and the user
        silently loses context injection on every cold start.

        Implementation options that satisfy this contract:
        - python3 writes to a tempfile; shell only `cat`s it on success.
        - python3 writes to a sub-pipe and the parent only forwards on
          exit-code 0.
        - shell collects python3's stdout into a variable and prints it
          only when python3 succeeded.
        """
        d = self._shim_dir()
        try:
            env = {
                "PATH": f"{d}:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            r = subprocess.run(
                ["bash", str(SESSION_START_SH)],
                capture_output=True,
                text=True,
                timeout=self.HOOK_TIMEOUT,
                env=env,
            )
            self.assertEqual(
                r.returncode,
                0,
                f"hook must exit 0 even when python3 crashes mid-emit; "
                f"stderr={r.stderr!r} stdout={r.stdout!r}",
            )
            # The discriminating assertion: the partial garbage must
            # be absent.
            self.assertNotIn(
                self.PARTIAL_GARBAGE,
                r.stdout,
                f"hook leaked python3's partial garbage "
                f"{self.PARTIAL_GARBAGE!r} into final stdout. "
                f"The trap fallback wrote a valid envelope, but the "
                f"partial pre-crash bytes from python3 were not "
                f"suppressed — Claude will read concatenated garbage. "
                f"stdout={r.stdout!r}",
            )
            # And — for completeness — final stdout must still be
            # exactly one valid JSON object (not two concatenated).
            stripped = r.stdout.strip()
            self.assertTrue(stripped, "hook stdout was empty")
            # If stdout starts with `{` but contains another `}{`
            # boundary, it's two concatenated objects.
            self.assertNotRegex(
                stripped,
                r"\}\s*\{",
                f"hook stdout contains '}}{{' boundary — looks like two "
                f"concatenated JSON objects. stdout={r.stdout!r}",
            )
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as e:
                self.fail(
                    f"hook final stdout is not a single valid JSON "
                    f"object: {e}; stdout={r.stdout!r}"
                )
            self.assertIsInstance(obj, dict)
            self.assertIn("hookSpecificOutput", obj)
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Bonus: dimension #11 + #10 interaction — slow `compose up` shim
# should not cause partial JSON either. (Already implied above but
# explicit fingerprint check here.)
# ---------------------------------------------------------------------------


class SlowComposeUpStdoutContractTests(unittest.TestCase):
    """Combined check: on the slow-compose-up branch, hook stdout
    is still exactly one JSON object with a non-empty additionalContext
    that references graph-schema.md (the schema-doc anchor)."""

    HOOK_TIMEOUT = 15.0

    def setUp(self):
        if not SESSION_START_SH.exists():
            self.fail(f"missing {SESSION_START_SH}")

    def test_slow_compose_up_emits_valid_json_with_schema_reference(self):
        d = _make_slow_compose_up_shim()
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            try:
                r = subprocess.run(
                    ["bash", str(SESSION_START_SH)],
                    capture_output=True,
                    text=True,
                    timeout=self.HOOK_TIMEOUT,
                    env=env,
                )
            except subprocess.TimeoutExpired as e:
                self.fail(
                    f"hook timed out on slow-compose-up branch — see "
                    f"dimension-#11 detachment failure. {e!r}"
                )
            self.assertEqual(r.returncode, 0)
            stripped = r.stdout.strip()
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as e:
                self.fail(
                    f"hook stdout not valid JSON on slow-compose-up "
                    f"branch: {e}; stdout={r.stdout!r}"
                )
            ctx = obj.get("hookSpecificOutput", {}).get("additionalContext", "")
            self.assertIsInstance(ctx, str)
            self.assertTrue(ctx, "additionalContext was empty")
            # Per contract: "additionalContext references graph-schema.md."
            self.assertIn(
                "graph-schema.md",
                ctx,
                f"additionalContext should reference graph-schema.md "
                f"even on slow-compose-up branch; got {ctx!r}",
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
