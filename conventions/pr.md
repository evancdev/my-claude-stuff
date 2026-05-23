# Pull Request Conventions

Rules for PR titles, bodies, and how to push + open the PR.

## Title

`<type>(<optional scope>): <subject>` — lowercase, imperative, ≤72 chars, no trailing period.

Append `!` after the type/scope for breaking changes.

## Body

```markdown
## Summary
- <one bullet per major decision or trade-off — explain the *why*, not the diff>
- Closes #<issue> <!-- omit if none -->

## Test plan
- [ ] <manual action — observable result>
- [ ] <regression check — what should still work>

## Breaking change
- <what broke, who's affected, migration path>
```

Each test-plan item pairs an **action** with an **observable outcome**. List only what a human needs to verify — manual UX checks, real-service behavior, regressions CI can't catch.

`Closes #N` / `Fixes #N` auto-closes the issue on merge — use the exact keyword.

### Anti-examples (do NOT put these in the test plan)

The default GitHub-PR pattern is to checklist *everything*, including CI jobs. That's wrong here. CI runs automatically — checklisting it adds noise without adding any human-verifiable action.

- ❌ `[ ] CI lint job (ruff) passes`
- ❌ `[ ] CI tests pass on Python 3.11–3.14`
- ❌ `[ ] Type checker is happy`
- ❌ `[ ] All N tests pass`
- ❌ `[ ] No regressions`

If CI catches it, it doesn't belong in the test plan. If a human has to *do* something — open the app, hit a URL, watch a metric, run a script with specific args — that belongs.

## Confirm scope before staging

When the user says "open a PR for this," **"this" is ambiguous**. The worktree may contain multiple unrelated changes that piled up during one session — a feature, a tangentially-related skill that emerged while building it, personal design notes, editor configs. Bundling everything by default is wrong:

- it ties unrelated features to a single merge decision
- it balloons the diff and slows review
- it obscures intent ("what is this PR actually for?")

**Process:**

1. Run `git status --short` and `git ls-files --others --exclude-standard` to list every uncommitted change.
2. **Group** the changes into distinct concerns (e.g. "annotate tool", "test-panel skill", "personal design notes", "editor configs").
3. **Surface the list** to the user and ask which subset belongs in this PR. Don't assume.
4. Default proposal: the narrowest scope the user explicitly named. One atomic concern is the stretch goal.

Being in the same worktree does not mean being the same feature. A worktree named `ec-annotate` is not a license to ship every adjacent change just because it accumulated nearby.

## Size

Aim for under ~400 lines of **review-relevant** changes. Above that, defect detection drops off sharply and reviews stall — split into a stack of smaller PRs instead.

The limit measures reviewer cognitive load, not raw diff size. Don't count tests, snapshots, lockfiles, generated code, or repetitive JSX/markup. A 1500-line PR that's mostly tests + one small handler change is fine; a 500-line PR of dense logic is not.

## Push & open

**IMPORTANT:** Not done until a PR URL is printed.

1. Check if the branch has an upstream: `git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null`.
2. Push:
   - **No upstream** → `git push -u origin HEAD`
   - **Has upstream** → `git push`
3. Check if a PR already exists for this branch: `gh pr view --json number,url 2>/dev/null`.
4. **If a PR exists** → print its URL. Done.
5. **If no PR exists** → create one: `gh pr create --base main --title "..." --body "$(cat <<'EOF' ... EOF)"`, then print the returned URL. Override `--base` only if the user named a different target branch.

If the work isn't ready for review, open as a Draft: `gh pr create --draft …`.

## Hard stops

- **Pushing to `main`**
- **Force-pushing** — never, unless the user explicitly said "force push" in this turn. Do not pass `--force` / `-f`.
