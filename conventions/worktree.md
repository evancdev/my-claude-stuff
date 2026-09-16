# Worktree & Branch Conventions

Claude works in **git worktrees**, not in the main checkout. Each task gets its own worktree (with its own branch), so the main checkout remains undisturbed.

## When to use a worktree (and when to skip)

Worktrees are heavyweight setup. Use them only for **substantial work** — a feature, a multi-file refactor, anything that warrants planning first (e.g. via `/cook`).

**Skip the worktree for:**
- Single-line or single-file fixes
- Typo fixes, doc tweaks
- Quick experiments
- Anything done in one or two writes

Rule of thumb: if it's worth planning, it's worth a worktree. If it's a one-off edit, just edit.

## Why worktrees instead of plain branches

Plain `git checkout -b` switches the working tree to the new branch — disrupting open files, terminal CWD, IDE state. Worktrees give Claude a separate directory with its own checkout, sharing the same `.git/`. Both worktrees see the same commits.

## Setup order

1. **Check if already in a worktree.** Compare `git rev-parse --git-dir` to `git rev-parse --git-common-dir`. If they differ, the current location is a linked worktree — work there, don't create a new one.
2. **Prefer the harness's worktree tool.** If a tool like `EnterWorktree` or `Agent` with `isolation: "worktree"` is available, use it — the harness manages placement and cleanup automatically.
3. **Fall back to manual `git worktree add`** only when no harness tool is available.

## Path convention (manual fallback)

In-repo, under `.worktrees/`, with the branch name flattened (slashes replaced by dashes):

```
<repo>/.worktrees/<flattened-branch>/
```

Example: branch `ec/login` → worktree at `<repo>/.worktrees/ec-login/`.

**Required:** `.worktrees/` must be in `.gitignore` before creating any worktree there. If it isn't, add it and commit the change first — otherwise the worktree contents pollute git status.

## Branch naming

Format: `ec/<kebab-summary>`

- Kebab-case, ≤50 chars total, no special characters.

## Manual fallback

Verify `.worktrees/` is gitignored, then create:

```bash
grep -qx '.worktrees/' .gitignore || { echo '.worktrees/' >> .gitignore && git add .gitignore && git commit -m 'chore: ignore .worktrees/'; }

git worktree add .worktrees/<flattened-branch> -b ec/<summary>
```

Then `cd` into the worktree path for all subsequent work in this task.

If the create fails (sandbox restriction, permission error), report to the user and work in the current directory instead.

Clean up after the PR is merged:

```bash
git worktree remove .worktrees/<flattened-branch>
git branch -d ec/<summary>
```
