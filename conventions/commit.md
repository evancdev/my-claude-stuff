# Commit Conventions

Rules for commit messages and how to commit.

## Commit message

```
<type>(<optional scope>): <subject>

<one-line motivation>
- <decision, trade-off, or invariant>
- <decision, trade-off, or invariant>
```

**Types** (pick the most accurate, not the broadest):

| Type | Use for |
|------|---------|
| `feat` | New user-facing capability |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change with no behavior change |
| `test` | Adding or fixing tests only |
| `chore` | Tooling, deps, housekeeping |
| `perf` | Performance improvement |
| `build` | Build system or external deps |
| `ci` | CI configuration |
| `style` | Formatting only (no code change) |
| `revert` | Reverts a previous commit; reference the reverted SHA in the body |

**Subject:**
- ≤50 characters ideal, ≤72 hard limit.
- Lowercase.
- Imperative mood: "add x", not "added x" or "adds x".
- No trailing period.

**Body** (optional):
- Include only when the *why* is non-obvious. Skip for self-explanatory changes.
- Lead with a one-line motivation. Use bullets for distinct points: trade-offs considered, alternatives rejected, invariants preserved, follow-ups deferred.
- Don't bullet *what* changed — the diff shows that.
- Wrap at ~72 chars per line.

## Hard stop: secrets

If the staged diff contains a secret (API key, token, password, private key, `.env` file, connection string with credentials), **do not commit**. Stop and surface the secret to the user.

## How to commit

- Stage only files that belong in this single coherent commit. Never `git add -A` blindly.
- Skip large binaries and generated artifacts.
- Always pass the message via HEREDOC to preserve formatting:
  ```bash
  git commit -m "$(cat <<'EOF'
  <type>(<optional scope>): <subject>

  <one-line motivation>
  - <decision, trade-off, or invariant>
  - <decision, trade-off, or invariant>
  EOF
  )"
  ```
- **Never** use `--no-verify`, `--no-gpg-sign`, or any hook-skipping flag. If a hook fails, fix the cause and create a new commit.
- Prefer new commits over `--amend` once a commit exists.

## Stop-and-ask triggers

Pause and confirm with the user before:
- Committing changes to CI configuration or database migrations. Before asking for confirmation, surface the impact: what's changing, what depends on it, and what could break. A bare "ok to commit?" isn't enough — the user can't decide without knowing the blast radius.
- Shipping unrelated changes bundled together — split first.
