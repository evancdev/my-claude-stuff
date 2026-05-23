---
name: ship
description: Use only when the user explicitly asks to ship or open a PR. Handles any starting state (uncommitted, committed, unpushed) end-to-end, with a PR-level reviewer panel.
---

# Ship

**IMPORTANT:** Never push without the PR-level panel clearing. The commit skill handles the commit-level panel first.

## Flow

1. **Determine base branch.** Try in order, stopping at the first that resolves:
   - User's explicit instruction
   - `git symbolic-ref refs/remotes/origin/HEAD` (the remote's default branch)
   - `main` if it exists, else `master`
   - Ask the user

2. **Check current branch** (`git branch --show-current`). If on the base branch, create a new branch named `<type>/<kebab-summary>` (Conventional Commits type, ≤50 chars) and switch. Derive the name from conversation context and the `git status` file list; ask the user if neither is clear. Otherwise stay on the current branch.

3. **Commit any uncommitted changes.** If `git status` shows uncommitted changes, confirm they're shippable as one PR (ask which to ship if mixed/unrelated), then invoke the `commit` skill. Skip if the working tree is clean.

4. **PR-level review panel.** Dispatch two fresh subagents in parallel via Claude's `Task` tool, scoped to the branch-level diff (`git diff <base>...HEAD`). Each subagent receives only its reviewer prompt — no pre-fetched data, no file contents, no framing from this session.

   Reviewer prompts:
   - `${CLAUDE_PLUGIN_ROOT}/skills/ship/reviewers/impact.md` — impact reviewer (call sites, breaking changes, migrations)
   - `${CLAUDE_PLUGIN_ROOT}/skills/ship/reviewers/craft.md` — craft reviewer (reuse, quality, performance)

   Additionally invoke the `/security-review` skill.

   Aggregate:
   - Any **BLOCK** verdict or **high-severity** security finding → halt; surface findings grouped by reviewer; user decides (fix, push anyway, or abort).
   - Any **WARN** verdict or non-blocking security finding → surface findings; user decides (fix first, push anyway, or abort).
   - All clear → push.

   If `git diff <base>...HEAD` errors because the branch has no remote yet (first push), reviewers may return empty or skip — note that to the user and proceed.

5. **Push.** `git push -u origin HEAD` if the branch isn't tracked, otherwise `git push`.

6. **Open or surface the PR.** Check if a PR already exists for the branch (`gh pr view --json number,url 2>/dev/null`). If one exists, surface its URL — the push already updated it. If not, **re-read `${CLAUDE_PLUGIN_ROOT}/conventions/pr.md` right now** (do not draft from memory; the conventions deviate from GitHub defaults in load-bearing ways), then create the PR per those conventions, summarizing *all* commits since `<base>` (`git log <base>..HEAD --oneline`) — not just the latest. Before posting, scan your drafted body against pr.md's "Anti-examples" section.

7. **Print the PR URL.**

## Why fresh subagents

Each reviewer must read the branch diff cold. Do not pre-summarize; do not paste the diff into their prompt body. Each subagent runs `git diff <base>...HEAD` itself and forms its own judgment.
