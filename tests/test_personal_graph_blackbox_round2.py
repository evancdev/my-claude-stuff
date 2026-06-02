"""Round-2 black-box audit for the personal-graph feature.

These tests target contract dimensions that the existing suites either
miss or only partially cover. Per the audit brief, the forbidden source
files are NOT read — assertions are written purely from the contract.

Specific gaps targeted:

- Dimension #13 (edge-type cross-file consistency): graph-seed.md must
  not tell Claude to write edges that the schema's edge table forbids
  (e.g., `(:Project)-[:ABOUT]->(:Topic)` violates ABOUT's allowed source
  labels). Existing tests check only labels and MERGE keys, not edge
  source/target consistency across files.

- Dimension #12 (MERGE-key prose AND code, both directions): existing
  tests only catch the `id`-as-key anti-pattern. We also positively
  assert that the seed command's prose lists the *same* MERGE keys as
  the schema's table for every label.

- README cold-start UX claim: the brief says the README must document
  first-run cold-start UX honestly. We assert it explicitly mentions
  the image pull / multi-minute / first-run delay.

- Container name consistency between README and graph-seed.md prose:
  the README documents container name `my-claude-stuff-graph`; the
  graph-seed.md preconditions reference it by name when telling the
  user to debug an unreachable graph. If they disagree, the error
  message graph-seed.md prints will name the wrong container.

- `.mcp.json` package coordinates aren't a literal path (uvx fetches
  from registry / git).

- Hook stdout is exactly one JSON object — when invoked with garbage
  in env (e.g., NEO4J_PASSWORD containing JSON-special chars).

- Idempotency of compose definition (`docker compose config -q` twice
  in a row both succeed — bare-minimum reproducibility check).

Black-box only. Files like `hooks/session-start-graph.sh`,
`scripts/neo4j-up.sh`, `.mcp.json`, `docker/neo4j-compose.yml`,
`hooks/hooks.json`, `conventions/graph-schema.md`, `commands/graph-seed.md`
are observed via subprocess output, JSON parsing of public-facing
fields, and regex over their text — never by reading the
implementation/internal logic of the scripts.
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

COMPOSE_FILE = REPO_ROOT / "docker" / "neo4j-compose.yml"
NEO4J_UP_SH = REPO_ROOT / "scripts" / "neo4j-up.sh"
MCP_JSON = REPO_ROOT / ".mcp.json"
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"
SESSION_START_SH = REPO_ROOT / "hooks" / "session-start-graph.sh"
GRAPH_SCHEMA_MD = REPO_ROOT / "conventions" / "graph-schema.md"
GRAPH_SEED_MD = REPO_ROOT / "commands" / "graph-seed.md"
README_MD = REPO_ROOT / "README.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def docker_available() -> bool:
    return shutil.which("docker") is not None


def _write_exec(path: str, body: str) -> str:
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)
    return path


def _make_daemon_down_shim() -> str:
    """docker info exits 1 (daemon unreachable). Anything else exits 0
    so the hook can probe and bail without errors."""
    d = tempfile.mkdtemp(prefix="daemon_down_")
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
    return d


# ---------------------------------------------------------------------------
# Dimension #13: edge-type cross-file consistency
# ---------------------------------------------------------------------------


class EdgeTypeCrossFileConsistencyTests(unittest.TestCase):
    """Schema declares which (source-label, edge-type, target-label)
    triples are legal. graph-seed.md instructs Claude what to write.
    Any edge described in graph-seed.md — in either prose or Cypher —
    must be a legal triple per the schema's edge-type table.
    """

    def setUp(self):
        if not GRAPH_SCHEMA_MD.exists():
            self.fail(f"missing {GRAPH_SCHEMA_MD}")
        if not GRAPH_SEED_MD.exists():
            self.fail(f"missing {GRAPH_SEED_MD}")
        self.schema_text = GRAPH_SCHEMA_MD.read_text()
        self.seed_text = GRAPH_SEED_MD.read_text()

    # --- parse schema edge table -----------------------------------------

    def _parse_schema_edges(self) -> dict[str, dict]:
        """Parse the `## Edge types` table.

        Returns {edge_type: {"from": set(labels), "to": set(labels)}}.

        Cell format example (markdown):
        ``| `KNOWS` | `Person` | `Person` | ... |``
        ``| `INVOLVES` | `Event` | `Project` \\| `Topic` | ... |``

        The schema uses ``\\|`` (escaped pipe inside a table cell) to
        separate alternate labels.
        """
        # Find the Edge types section content. Stop at next "##" header.
        m = re.search(
            r"##\s*Edge types\s*\n(?P<body>.+?)(?=\n##\s|\Z)",
            self.schema_text,
            re.DOTALL | re.IGNORECASE,
        )
        if not m:
            return {}
        body = m.group("body")

        edges: dict[str, dict] = {}
        # Markdown rows: `| `TYPE` | `From` | `To` | notes |`
        # Use a permissive regex: capture three backticked cells in order.
        row_re = re.compile(
            r"\|\s*`(?P<type>[A-Z][A-Z_]+)`"
            r"\s*\|\s*(?P<from>[^|]+?)"
            r"\s*\|\s*(?P<to>[^|]+?)"
            r"\s*\|",
        )
        for rm in row_re.finditer(body):
            etype = rm.group("type")
            from_cell = rm.group("from")
            to_cell = rm.group("to")
            from_labels = self._labels_from_cell(from_cell)
            to_labels = self._labels_from_cell(to_cell)
            if from_labels and to_labels:
                edges[etype] = {"from": from_labels, "to": to_labels}
        return edges

    @staticmethod
    def _labels_from_cell(cell: str) -> set[str]:
        """Extract backticked CamelCase labels from a table cell.
        Tolerates escaped pipes (`\\|`) between alternates."""
        return set(re.findall(r"`([A-Z][A-Za-z]+)`", cell))

    # --- parse seed edges -------------------------------------------------

    def _seed_cypher_blocks(self) -> list[str]:
        blocks = []
        for m in re.finditer(
            r"```cypher\b(.+?)```", self.seed_text, re.DOTALL | re.IGNORECASE
        ):
            blocks.append(m.group(1))
        return blocks

    def _seed_edges_from_cypher(self) -> list[tuple[str, str, str, str]]:
        """Find every Cypher edge expression of the shape
        `(var:Label)-[:TYPE]->(var2:Label2)` in fenced cypher blocks.

        Returns list of (edge_type, src_label, dst_label, snippet).
        """
        # Allow either side to be a `(:Label)` bare-label form or
        # `(var:Label {props})`. The label is the token after `:`.
        edge_re = re.compile(
            r"\(\s*\w*\s*:\s*(?P<src>[A-Z][A-Za-z]+)[^)]*\)"
            r"\s*-\s*\[\s*:\s*(?P<type>[A-Z][A-Z_]+)[^\]]*\]"
            r"\s*->\s*\(\s*\w*\s*:\s*(?P<dst>[A-Z][A-Za-z]+)[^)]*\)",
            re.DOTALL,
        )
        found: list[tuple[str, str, str, str]] = []
        for blk in self._seed_cypher_blocks():
            for m in edge_re.finditer(blk):
                snippet = m.group(0)
                found.append((m.group("type"), m.group("src"), m.group("dst"), snippet))
        return found

    def _seed_edges_from_prose(self) -> list[tuple[str, str]]:
        """Heuristic: in prose, look for phrases like
        "`PREFERS` edge from Evan" → infer (PREFERS, Person). We
        don't try to extract the target label from prose; we only
        verify the *source* matches what the schema allows.

        Returns list of (edge_type, src_label) hints. Best-effort.
        """
        # Strip code blocks
        prose = re.sub(r"```.+?```", "", self.seed_text, flags=re.DOTALL)
        # Phrases like "`ABOUT` originates only from `Decision`/`Preference`"
        # are themselves declaring the rule — those are echo-cite and OK.
        # We're looking for narrative writes: "link a `Project` to a
        # `Topic`", "store as a `Preference` node + `PREFERS` edge from
        # Evan".
        results: list[tuple[str, str]] = []

        # Pattern: "`TYPE` edge from <Label>" or "`TYPE` edge from Evan".
        # "from Evan" → Person.
        for m in re.finditer(
            r"`(?P<type>[A-Z][A-Z_]+)`\s+edge\s+from\s+(?P<src>[`\'\"]?\w+[`\'\"]?)",
            prose,
        ):
            etype = m.group("type")
            src_raw = m.group("src").strip("`'\"")
            # Map "Evan" to Person; otherwise treat token as a label.
            if src_raw == "Evan":
                results.append((etype, "Person"))
            elif src_raw[:1].isupper():
                results.append((etype, src_raw))
        return results

    # --- tests ------------------------------------------------------------

    def test_schema_edge_table_parses(self):
        """Precondition: we can parse the edge table at all. Without it,
        the other tests are vacuous."""
        edges = self._parse_schema_edges()
        # At minimum, the schema should declare the named-in-text edge
        # types: KNOWS, ATTENDS, PREFERS, INVOLVES, ABOUT.
        for needed in ("KNOWS", "ATTENDS", "PREFERS", "INVOLVES", "ABOUT"):
            self.assertIn(
                needed,
                edges,
                f"schema edge table must declare {needed!r}; parsed types: "
                f"{sorted(edges.keys())!r}",
            )

    def test_seed_cypher_edges_are_legal_per_schema(self):
        """Every `(a:X)-[:T]->(b:Y)` in a fenced cypher block of
        graph-seed.md must have (X, T, Y) appearing in the schema's
        edge table. If schema says `ABOUT` is `Decision|Preference ->
        Topic|Project|Person`, a `(:Project)-[:ABOUT]->(:Topic)` in
        graph-seed.md is a bug — it'd teach Claude to write illegal
        edges."""
        edges = self._parse_schema_edges()
        if not edges:
            self.skipTest("schema edge table could not be parsed")

        seed_edges = self._seed_edges_from_cypher()
        violations = []
        for etype, src, dst, snippet in seed_edges:
            spec = edges.get(etype)
            if spec is None:
                violations.append(
                    f"edge type {etype!r} used in graph-seed.md is not in "
                    f"schema's edge table (snippet: {snippet!r})"
                )
                continue
            if src not in spec["from"]:
                violations.append(
                    f"edge {etype!r} source {src!r} not allowed; "
                    f"schema allows {sorted(spec['from'])!r} "
                    f"(snippet: {snippet!r})"
                )
            if dst not in spec["to"]:
                violations.append(
                    f"edge {etype!r} target {dst!r} not allowed; "
                    f"schema allows {sorted(spec['to'])!r} "
                    f"(snippet: {snippet!r})"
                )

        self.assertFalse(
            violations,
            "graph-seed.md instructs Claude to write Cypher edges that "
            "are illegal per the schema's edge table:\n  " + "\n  ".join(violations),
        )

    def test_seed_prose_edge_hints_are_legal_per_schema(self):
        """For each '`TYPE` edge from `Label`' / 'from Evan' phrase in
        graph-seed.md prose, the source label must be allowed for that
        edge type per the schema."""
        edges = self._parse_schema_edges()
        if not edges:
            self.skipTest("schema edge table could not be parsed")

        prose_hints = self._seed_edges_from_prose()
        if not prose_hints:
            self.skipTest("no prose edge hints found in graph-seed.md")

        violations = []
        for etype, src in prose_hints:
            spec = edges.get(etype)
            if spec is None:
                violations.append(
                    f"prose mentions {etype!r} edge but schema does not "
                    f"declare that type"
                )
                continue
            if src not in spec["from"]:
                violations.append(
                    f"prose says '`{etype}` edge from {src}' but schema "
                    f"allows {sorted(spec['from'])!r}"
                )
        self.assertFalse(
            violations,
            "graph-seed.md prose contradicts schema edge table:\n  "
            + "\n  ".join(violations),
        )

    def test_about_edge_not_used_with_project_or_event_source_in_seed(self):
        """Most-specific concrete check: schema says ABOUT originates
        only from `Decision`/`Preference`. The seed doc must not — in
        any code block — write `(:Event)-[:ABOUT]->...` or
        `(:Project)-[:ABOUT]->...`. (The schema even calls this out
        explicitly: 'Does not originate from `Event` — use `INVOLVES`
        there.')"""
        seed_edges = self._seed_edges_from_cypher()
        offenders = [
            (src, snippet)
            for etype, src, _dst, snippet in seed_edges
            if etype == "ABOUT" and src in ("Event", "Project")
        ]
        self.assertFalse(
            offenders,
            "graph-seed.md has Cypher writing `ABOUT` edges from "
            "`Event`/`Project` — schema forbids this (use INVOLVES "
            f"instead). Offenders: {offenders!r}",
        )


# ---------------------------------------------------------------------------
# Dimension #12 extension: positive MERGE-key prose consistency
# ---------------------------------------------------------------------------


class MergeKeyProseConsistencyTests(unittest.TestCase):
    """Beyond 'don't name `id` as the MERGE key', the prose in
    graph-seed.md must positively echo the SAME MERGE-key property for
    every label that the schema declares. Schema is the canonical
    source; the seed command is teaching Claude what to do.
    """

    def setUp(self):
        if not GRAPH_SCHEMA_MD.exists():
            self.fail(f"missing {GRAPH_SCHEMA_MD}")
        if not GRAPH_SEED_MD.exists():
            self.fail(f"missing {GRAPH_SEED_MD}")
        self.schema_text = GRAPH_SCHEMA_MD.read_text()
        self.seed_text = GRAPH_SEED_MD.read_text()

    def _parse_schema_merge_keys(self) -> dict[str, set[str]]:
        keys: dict[str, set[str]] = {}
        row_re = re.compile(
            r"\|\s*`(?P<label>[A-Z][A-Za-z]+)`\s*\|\s*`(?P<key>[^`]+)`\s*\|"
        )
        for m in row_re.finditer(self.schema_text):
            label = m.group("label")
            key_spec = m.group("key").strip()
            inner = key_spec.strip("()")
            props = {p.strip() for p in inner.split(",") if p.strip()}
            keys[label] = props
        return keys

    def test_seed_prose_lists_merge_keys_for_all_labels_in_schema(self):
        """For each label the schema declares, the seed doc — somewhere
        in its prose — should name the SAME MERGE-key property/properties.
        The seed doc has a 'Write discipline' section that walks
        through label-to-key mappings; that section's listed key must
        match the schema's table.

        Adversarial signal: if schema says Preference is keyed on
        `summary` but graph-seed.md says `description`, Claude will
        produce a different MERGE statement than the schema mandates,
        violating idempotency.

        Heuristic: every expected MERGE-key property must appear (as
        a backticked token) somewhere in graph-seed.md prose, so long
        as the corresponding label is also mentioned in the prose.
        """
        schema_keys = self._parse_schema_merge_keys()
        # Strip fenced code blocks: prose-only consistency check.
        prose = re.sub(r"```.+?```", "", self.seed_text, flags=re.DOTALL)
        problems = []
        for label, expected_props in schema_keys.items():
            # Only enforce for labels mentioned in the prose.
            if not re.search(rf"`{label}`", prose):
                continue
            for prop in expected_props:
                # Accept the prop named anywhere in the prose either as
                # a bare word boundary or inside a backticked span
                # (including composite forms like `(title, start_at)`).
                if re.search(rf"\b{re.escape(prop)}\b", prose):
                    continue
                problems.append(
                    f"label {label!r}: schema MERGE key includes "
                    f"{prop!r}, but that property is not mentioned "
                    f"anywhere in graph-seed.md prose"
                )
        self.assertFalse(
            problems,
            "graph-seed.md prose omits MERGE-key properties documented "
            "in the schema:\n  " + "\n  ".join(problems),
        )

    def test_seed_does_not_invent_label_not_in_schema(self):
        """If graph-seed.md uses `:SomeLabel` in any Cypher block, that
        label must exist in the schema's labels table. Adversarial:
        catches "we added :Goal to graph-seed but forgot to add it to
        the schema doc"."""
        schema_keys = self._parse_schema_merge_keys()
        if not schema_keys:
            self.skipTest("schema keys could not be parsed")
        schema_labels = set(schema_keys.keys())

        cypher_blocks = re.findall(
            r"```cypher\b(.+?)```", self.seed_text, re.DOTALL | re.IGNORECASE
        )
        invented = set()
        for blk in cypher_blocks:
            for m in re.finditer(r":\s*([A-Z][A-Za-z]+)", blk):
                lab = m.group(1)
                if lab not in schema_labels:
                    invented.add(lab)
        self.assertFalse(
            invented,
            f"graph-seed.md Cypher uses labels not in schema: {sorted(invented)!r}; "
            f"schema labels are {sorted(schema_labels)!r}",
        )


# ---------------------------------------------------------------------------
# README cold-start UX honesty
# ---------------------------------------------------------------------------


class ReadmeColdStartHonestyTests(unittest.TestCase):
    """Brief says: README 'Documents first-run cold-start UX honestly.'

    Concretely, the README should set expectations about:
    - The first-ever launch has to pull the Neo4j image (~500MB).
    - That can take minutes; the graph is unavailable until then.
    - Subsequent starts are fast.

    A README that doesn't say any of this leaves users believing the
    very first SessionStart will have a working graph (it won't).
    """

    def setUp(self):
        if not README_MD.exists():
            self.fail(f"missing {README_MD}")
        self.text = README_MD.read_text()

    def test_first_run_pull_delay_mentioned(self):
        lower = self.text.lower()
        # Accept any phrasing that signals "first run takes time / pulls
        # the image / not running yet".
        signals = [
            "first run",
            "first-run",
            "first ever",
            "first launch",
            "first time",
            "image is pulled",
            "image pull",
            "pull",
            "cold-start",
            "cold start",
        ]
        present = [s for s in signals if s in lower]
        self.assertTrue(
            present,
            "README must document the first-run cold-start UX (image "
            "pull, first launch delay). None of the expected phrasings "
            f"{signals!r} appear.",
        )

    def test_first_run_dormant_state_mentioned(self):
        """The README should also tell the user that the graph reports
        as not-running until the pull/start completes — otherwise the
        SessionStart message looks like a bug."""
        lower = self.text.lower()
        candidates = (
            "not running",
            "configured but not running",
            "dormant",
            "until the next session",
            "subsequent",
        )
        present = [c for c in candidates if c in lower]
        self.assertTrue(
            present,
            "README must explain that the graph is not available on the "
            "very first session (pull happening in background). None of "
            f"{candidates!r} appears.",
        )

    def test_warns_about_neo4j_password_hashing(self):
        """Neo4j hashes NEO4J_PASSWORD into the data volume on first
        startup — changing it later doesn't actually rotate the password.
        Anyone running this in production needs to know that. A README
        that silently lets the user change NEO4J_PASSWORD after the
        first compose-up will produce mysterious auth failures.

        Honesty check: does the README mention this gotcha?
        """
        lower = self.text.lower()
        # Either say "hashed" / "set before first compose up" / "won't
        # change unless you down -v" / similar.
        signals = (
            "before the first",
            "first `compose up`",
            "first compose up",
            "hashed",
            "down -v",
            "changing it later",
            "no effect",
        )
        present = [s for s in signals if s in lower]
        self.assertTrue(
            present,
            "README should document the NEO4J_PASSWORD-baked-into-volume "
            "gotcha (must be set BEFORE first compose up; later changes "
            f"have no effect without `down -v`). None of {signals!r} present.",
        )


# ---------------------------------------------------------------------------
# Container-name consistency across files
# ---------------------------------------------------------------------------


class ContainerNameCrossFileTests(unittest.TestCase):
    """README documents container name `my-claude-stuff-graph`. The
    graph-seed.md preconditions reference it by name when telling the
    user to check if the graph is up. If they disagree, the user-facing
    error from `/graph-seed` will point at a non-existent container.

    The compose file itself may or may not pin a `container_name:` —
    that's its choice — but if it DOES, that must agree with the
    README and graph-seed.md.
    """

    def setUp(self):
        if not README_MD.exists():
            self.fail(f"missing {README_MD}")
        if not GRAPH_SEED_MD.exists():
            self.fail(f"missing {GRAPH_SEED_MD}")
        self.readme = README_MD.read_text()
        self.seed = GRAPH_SEED_MD.read_text()

    def _extract_container_name_from_readme(self) -> str | None:
        # README format observed: `- Docker container: ` `my-claude-stuff-graph` ``
        m = re.search(
            r"Docker container[^\n`]*[`']([A-Za-z0-9._-]+)[`']",
            self.readme,
        )
        return m.group(1) if m else None

    def test_seed_references_same_container_name_as_readme(self):
        readme_name = self._extract_container_name_from_readme()
        if not readme_name:
            self.skipTest("README does not declare a container name")
        # Seed doc should reference the same name in its
        # graph-not-reachable error template.
        self.assertIn(
            readme_name,
            self.seed,
            f"graph-seed.md should reference the documented container "
            f"name {readme_name!r} when telling the user how to debug "
            f"a not-reachable graph.",
        )


# ---------------------------------------------------------------------------
# Launcher: package coordinates are sensible
# ---------------------------------------------------------------------------


class LauncherPackageCoordinatesTests(unittest.TestCase):
    """The MCP server is run through scripts/graph-mcp.py, which execs `uvx`
    with the package coordinate (the wrapper exists so the server can source
    NEO4J_* from ~/.claude/secrets.env). That coordinate must be something
    `uvx` can resolve on any machine — a PyPI name (`mcp-neo4j-cypher`) or a
    git URL (`git+https://...`), NOT a host-specific local path that only
    works on Evan's box.
    """

    def setUp(self):
        self.launcher = REPO_ROOT / "scripts" / "graph-mcp.py"
        if not self.launcher.exists():
            self.fail(f"missing {self.launcher}")
        self.text = self.launcher.read_text()

    def test_launcher_does_not_reference_absolute_user_path(self):
        """No /Users/... or /home/... absolute paths in the uvx invocation —
        uvx should fetch the package by name or git URL, never a local path."""
        self.assertNotRegex(
            self.text,
            r"/Users/|/home/",
            "launcher references a host-specific absolute path; "
            "this won't work for any other user / machine.",
        )

    def test_launcher_has_a_package_or_url_token(self):
        """The launcher must name a uvx package coordinate (a PyPI name, a git
        URL, or a `--from <pkg>` form) so `uvx` has something to resolve."""
        self.assertRegex(
            self.text,
            r"mcp-neo4j-cypher|git\+https?://|[a-z]+://",
            "launcher must include a uvx package coordinate (e.g. mcp-neo4j-cypher)",
        )


# ---------------------------------------------------------------------------
# Hook stdout robustness against weird env values
# ---------------------------------------------------------------------------


class HookEnvRobustnessTests(unittest.TestCase):
    """Adversarial environment scenarios: what if NEO4J_PASSWORD
    contains JSON-special characters (quote, backslash, control char)?
    The hook's stdout must still be exactly one well-formed JSON
    object — anything less, and Claude's parser explodes."""

    HOOK_TIMEOUT = 10.0

    def setUp(self):
        if not SESSION_START_SH.exists():
            self.fail(f"missing {SESSION_START_SH}")

    def _run_hook(self, env: dict, timeout: float | None = None):
        return subprocess.run(
            ["bash", str(SESSION_START_SH)],
            capture_output=True,
            text=True,
            timeout=timeout or self.HOOK_TIMEOUT,
            env=env,
        )

    def test_hook_handles_password_with_double_quote(self):
        d = _make_daemon_down_shim()
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
                "NEO4J_PASSWORD": 'has"quote"in',
            }
            r = self._run_hook(env)
            self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
            stripped = r.stdout.strip()
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as e:
                self.fail(
                    f"hook stdout not valid JSON when NEO4J_PASSWORD "
                    f"contains a double-quote: {e}; stdout={r.stdout!r}"
                )
            self.assertIn("hookSpecificOutput", obj)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_hook_handles_password_with_backslash_and_newline(self):
        d = _make_daemon_down_shim()
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
                "NEO4J_PASSWORD": "evil\\n\nvalue",
            }
            r = self._run_hook(env)
            self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
            stripped = r.stdout.strip()
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as e:
                self.fail(
                    f"hook stdout not valid JSON with backslash/newline "
                    f"in NEO4J_PASSWORD: {e}; stdout={r.stdout!r}"
                )
            self.assertIn("hookSpecificOutput", obj)
            # And — crucially — the password value must never leak into
            # additionalContext. (We use a unique sentinel.)
            ctx = obj.get("hookSpecificOutput", {}).get("additionalContext", "")
            self.assertNotIn(
                "evil",
                ctx,
                f"NEO4J_PASSWORD value leaked into additionalContext: ctx={ctx!r}",
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_hook_does_not_leak_password_to_stdout_when_daemon_up(self):
        """Tighter than existing tests: even if the hook decides to
        narrate 'graph is reachable', it should not echo the password
        anywhere on stdout."""
        # Daemon-up shim where everything reports success.
        d = tempfile.mkdtemp(prefix="daemon_up_ok_")
        sentinel = "sup3r-s3cret-do-not-leak-XYZZY"
        try:
            _write_exec(
                os.path.join(d, "docker"),
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    case "$1" in
                        info) exit 0 ;;
                        inspect)
                            # Container exists and is running.
                            echo "true"
                            exit 0
                            ;;
                        compose)
                            shift
                            case "$1" in
                                ps)
                                    # Pretend a container id is here.
                                    echo "abcdef123456"
                                    exit 0
                                    ;;
                                *) exit 0 ;;
                            esac
                            ;;
                        ps)
                            echo "abcdef123456 neo4j:5 Up healthy"
                            exit 0
                            ;;
                        *) exit 0 ;;
                    esac
                    """
                ),
            )
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
                "NEO4J_PASSWORD": sentinel,
            }
            r = self._run_hook(env)
            self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
            self.assertNotIn(
                sentinel,
                r.stdout,
                f"NEO4J_PASSWORD sentinel leaked into hook stdout: {r.stdout!r}",
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Compose file: integration sanity using docker CLI if present
# ---------------------------------------------------------------------------


class ComposeIntegrationTests(unittest.TestCase):
    """Cross-checks against `docker compose` itself. Skipped if docker
    CLI isn't on PATH."""

    @unittest.skipUnless(docker_available(), "docker CLI not on PATH")
    def test_docker_compose_config_idempotent(self):
        """Two successive `config -q` runs should both succeed —
        catches any 'first run side-effects' in the compose file (e.g.,
        local .env writes)."""
        for i in range(2):
            r = subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "-q"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(
                r.returncode,
                0,
                f"`docker compose config -q` run #{i} failed: stderr={r.stderr!r}",
            )

    @unittest.skipUnless(docker_available(), "docker CLI not on PATH")
    def test_compose_config_emits_neo4j_5x_image(self):
        """Rendered config (not raw yaml) confirms the image is
        neo4j:5.x. Resilient to ${...} indirection in the source."""
        r = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            self.skipTest(f"compose config --format json failed: {r.stderr!r}")
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            self.fail(f"compose config did not emit valid JSON: {e}")
        services = data.get("services") or {}
        self.assertEqual(
            len(services), 1, f"expected 1 service; got {list(services)!r}"
        )
        svc = next(iter(services.values()))
        image = svc.get("image", "")
        self.assertRegex(
            image,
            r"^neo4j:5\.",
            f"rendered image must match neo4j:5.x; got {image!r}",
        )


# ---------------------------------------------------------------------------
# scripts/neo4j-up.sh: detachment under daemon-up but compose-up sleeps
# (complements existing detachment test — this checks the case where
# the script's "is it already running" probe happens to return false
# but the existing-but-stopped path also needs to detach).
# ---------------------------------------------------------------------------


class Neo4jUpDetachmentExistingButStoppedTests(unittest.TestCase):
    """Adversarial: container exists but is stopped. Some
    implementations might use `docker start <name>` (synchronous) here
    instead of `docker compose up -d`. If `docker start` is the slow
    call, the script still has to detach it."""

    BUDGET = 12.0

    def setUp(self):
        if not NEO4J_UP_SH.exists():
            self.fail(f"missing {NEO4J_UP_SH}")

    def _shim(self) -> str:
        d = tempfile.mkdtemp(prefix="stopped_container_")
        _write_exec(
            os.path.join(d, "docker"),
            textwrap.dedent(
                """\
                #!/bin/sh
                cmd="$1"; shift
                case "$cmd" in
                    info) exit 0 ;;
                    inspect)
                        # Container exists.
                        # Heuristic: if format requests Running, say false.
                        for arg in "$@"; do
                            case "$arg" in
                                *Running*|*State.Running*)
                                    echo "false"
                                    exit 0
                                    ;;
                            esac
                        done
                        echo "false"
                        exit 0
                        ;;
                    ps) exit 0 ;;
                    start)
                        # Slow start — must be detached.
                        sleep 60
                        exit 0
                        ;;
                    compose)
                        sub="$1"; shift || true
                        case "$sub" in
                            ps) exit 0 ;;
                            up)
                                sleep 60
                                exit 0
                                ;;
                            start)
                                sleep 60
                                exit 0
                                ;;
                            *) exit 0 ;;
                        esac
                        ;;
                    *) exit 0 ;;
                esac
                """
            ),
        )
        return d

    def test_existing_but_stopped_container_does_not_block(self):
        d = self._shim()
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
                    timeout=20.0,
                    env=env,
                )
            except subprocess.TimeoutExpired as e:
                elapsed = time.monotonic() - t0
                self.fail(
                    f"neo4j-up.sh blocked for {elapsed:.1f}s when restarting "
                    f"a stopped container — `docker start` (or compose up) "
                    f"must be detached. TimeoutExpired={e!r}"
                )
            elapsed = time.monotonic() - t0
            self.assertLess(
                elapsed,
                self.BUDGET,
                f"neo4j-up.sh took {elapsed:.2f}s on stopped-container "
                f"branch; should return <10s. stderr={r.stderr!r}",
            )
            self.assertEqual(
                r.returncode, 0, f"stderr={r.stderr!r} stdout={r.stdout!r}"
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Hook stdout: single JSON object even with extreme stress
# ---------------------------------------------------------------------------


class HookSingleJsonObjectStressTests(unittest.TestCase):
    """Spec: stdout is exactly ONE JSON object. Stress-test with
    multiple concurrent invocations all writing to separate pipes;
    each must emit exactly one JSON object and exit 0."""

    HOOK_TIMEOUT = 12.0

    def setUp(self):
        if not SESSION_START_SH.exists():
            self.fail(f"missing {SESSION_START_SH}")

    def test_three_concurrent_invocations_each_emit_one_object(self):
        d = _make_daemon_down_shim()
        try:
            env = {
                "PATH": f"{d}:/usr/bin:/bin",
                "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            procs = []
            for _ in range(3):
                procs.append(
                    subprocess.Popen(
                        ["bash", str(SESSION_START_SH)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=env,
                    )
                )
            results = []
            for p in procs:
                try:
                    out, err = p.communicate(timeout=self.HOOK_TIMEOUT)
                except subprocess.TimeoutExpired:
                    p.kill()
                    out, err = p.communicate()
                    self.fail(f"hook hung; stdout={out!r} stderr={err!r}")
                results.append((p.returncode, out, err))

            for rc, out, err in results:
                self.assertEqual(rc, 0, f"rc={rc} stderr={err!r}")
                stripped = out.strip()
                self.assertTrue(stripped, "stdout empty")
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError as e:
                    self.fail(
                        f"concurrent invocation produced invalid JSON: "
                        f"{e}; stdout={out!r}"
                    )
                self.assertIsInstance(obj, dict)
                self.assertIn("hookSpecificOutput", obj)
                # No stray JSON tail.
                decoder = json.JSONDecoder()
                _, end = decoder.raw_decode(stripped)
                self.assertEqual(
                    stripped[end:].strip(),
                    "",
                    f"trailing content after JSON: {stripped[end:]!r}",
                )
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# launcher env vs README env-var documentation
# ---------------------------------------------------------------------------


class McpEnvDocumentedInReadmeTests(unittest.TestCase):
    """README declares four MCP env vars (NEO4J_URI, NEO4J_USERNAME,
    NEO4J_PASSWORD, NEO4J_DATABASE) with defaults. Any NEO4J_* var the
    graph-mcp.py launcher actually resolves should be documented in the
    README. Catches drift where the launcher adds a new connection var the
    README doesn't mention."""

    def setUp(self):
        launcher = REPO_ROOT / "scripts" / "graph-mcp.py"
        if not launcher.exists():
            self.fail(f"missing {launcher}")
        if not README_MD.exists():
            self.fail(f"missing {README_MD}")
        self.launcher_raw = launcher.read_text()
        self.readme = README_MD.read_text()

    def test_every_neo4j_env_var_in_launcher_is_documented_in_readme(self):
        # Find all NEO4J_* names the launcher references (e.g. in its DEFAULTS).
        vars_in_launcher = set(re.findall(r"NEO4J_[A-Z_]+", self.launcher_raw))
        undocumented = [v for v in vars_in_launcher if v not in self.readme]
        self.assertFalse(
            undocumented,
            f"graph-mcp.py references NEO4J_* env vars that the README does "
            f"not document: {undocumented!r}",
        )


if __name__ == "__main__":
    unittest.main()
