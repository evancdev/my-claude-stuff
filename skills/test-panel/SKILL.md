---
name: test-panel
description: Use when the user wants thorough adversarial test coverage of a module (CLI, library, API). Dispatches a black-box tester (spec only) and a white-box tester (code access) in parallel; classifies findings; recommends an audit instead of iteration when bugs cluster by pattern.
---

# Test panel

**IMPORTANT:** One pass is the default. Don't loop. If bugs cluster by pattern, do a targeted code audit — don't spawn more agents.

## What this is for

You want tests that try to *break* a module, not just exercise it. Two perspectives in parallel:
- **Black-box** — sees only the spec; tests user-visible behavior. Catches spec violations and UX bugs.
- **White-box** — sees the code; tests internals adversarially. Catches missing `isinstance` checks, boundary math, error paths.

Their findings overlap deliberately — same bug found two ways is high-signal.

## Required inputs (ask the user if missing)

1. **Target** — file/module path to test (e.g. `scripts/foo.py`).
2. **Spec** — 1-paragraph behavioral spec for the black-box agent. If only the code is "the spec," say so and skip black-box.
3. **Test framework** — pytest, jest, vitest, etc. Glance at any existing tests to match style.
4. **Test output location** — each agent writes to a temporary, methodology-named file (e.g. `tests/_blackbox_scratch.py`, `tests/_whitebox_scratch.py`). These are intermediate artifacts; you'll merge them into a single subject-named file (see "Finalize before reporting done"). Don't ship methodology-named files.

## Default flow

Dispatch both subagents in parallel via the `Agent` tool:
- `subagent_type: test-blackbox` — receives the spec, target path, framework, output path.
- `subagent_type: test-whitebox` — receives the target path, framework, output path.

The agent definitions (`agents/test-blackbox.md`, `agents/test-whitebox.md`) carry the role-specific instructions. Your prompt is just the inputs — don't summarize what you think the code does or paste it in. Each agent starts cold by design.

When both return, classify findings:

- **No failing tests, no flagged behaviors** → done. Report green.
- **Failing tests cluster in one pattern** (e.g. 3+ bugs are all "raw traceback on malformed input") → STOP. Do a targeted audit instead: grep the suspect pattern across the file (`.get(...)\[`, `for x in .*\.get(...)`, `open(path)` without isfile check, etc.) and fix all instances in one pass. This is faster and cheaper than spawning a third iteration.
- **Failing tests are scattered (no pattern)** → fix individually, then optionally run one more pass.

## What NOT to do

- **Don't loop by default.** Iteration was the mistake that prompted this skill — fresh agents re-derive the same context and re-test the same boundaries. One thorough pass + audit catches ~80% of value at ~20% of cost.
- **Don't let white-box "lock in" buggy behavior as passing tests.** The instructions file forbids this — verify the agent followed it. If white-box reports "questionable behaviors locked in as passing tests," treat each as a candidate bug to triage with the user.
- **Don't paste the code into the black-box prompt.** Spec only. Leaking implementation details defeats the second perspective.
- **Don't iterate to chase "no bugs found."** Convergence is "no new *categories* of bugs," not zero findings.

## Iteration (opt-in, rare)

Only run a second pass if:
1. The first pass surfaced bugs you fixed, AND
2. The fixes were substantial enough to plausibly introduce new bugs (not just defensive validation), AND
3. The user explicitly asks for it.

If you do iterate: tell each agent its previous test file is gone (delete or rename it). Don't ask them to extend prior coverage — that biases them into the same blind spots.

## Finalize before reporting done

Subagent output is a draft, not the canonical artifact. Before reporting
the work complete:

1. **Merge methodology-named files into a single subject-named file.**
   `tests/_blackbox_scratch.py` + `tests/_whitebox_scratch.py` → `tests/test_<subject>.py`. The file is named for *what's tested* (the
   subject under test), not for *how the tests were generated*. Methodology
   leaks process into the canonical artifact and confuses future readers.
2. **Dedupe overlapping coverage.** Black-box and white-box agents
   intentionally overlap (same bug found two ways = high signal during
   discovery). After fixes land, that overlap is just maintenance burden.
   Keep the higher-level (CLI / integration) test when a unit test asserts
   the same observable behavior; keep the unit test when it asserts something
   the CLI test can't see (exact monkeypatched values, internal state).
3. **Organize by subject inside the file.** Top section: unit tests for
   internal helpers that have no public surface. Then one section per
   public operation (subcommand, endpoint, exported function) mixing unit
   and integration tests for that operation. A future reader looking up
   "how does `foo` behave?" should find everything in one place.
4. **Delete the scratch files.** The merged file is the ship target. Leaving
   the scratch files committed ratifies the antipattern this skill warns
   against — and a future iteration will treat them as the test suite.

**Rule of thumb:** subagents produce drafts; the dispatcher ships the
canonical version. If you'd be embarrassed for a future contributor to
open the test file and see "blackbox" / "whitebox" in the filename, you
haven't finalized.

## Reporting

After the finalize step, give the user:
- Final test file path (the merged, subject-named one)
- Total tests passing/failing in the final file
- Each failing test: one-line bug description
- Pattern classification: are the bugs related?
- Recommendation: audit (if pattern), individual fixes (if scattered), or done (if green)
