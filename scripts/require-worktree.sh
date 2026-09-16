#!/usr/bin/env bash
# PreToolUse(Edit|Write|MultiEdit): if enabled in settings, block writes in
# the user's main checkout. Forces Claude to set up a worktree before touching
# the working tree, so the user's main checkout stays untouched.
#
# Opt-in via settings.json: { "my-claude-stuff": { "requireWorktree": true } }
# Default off. When on, blocks Edit/Write/MultiEdit unless the cwd is a
# linked worktree (git-dir != git-common-dir).
set -euo pipefail

# Read settings.json for opt-in. Project settings override user settings.
read_setting() {
  local file="$1"
  [ -f "$file" ] || return 1
  /usr/bin/python3 -c '
import json, sys
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
    v = d.get("my-claude-stuff", {}).get("requireWorktree")
    print("true" if v is True else "false" if v is False else "")
except Exception:
    print("")
' "$file" 2>/dev/null
}

enabled=""
for candidate in ".claude/settings.local.json" ".claude/settings.json" "${HOME}/.claude/settings.json"; do
  v="$(read_setting "$candidate" || true)"
  if [ -n "$v" ]; then
    enabled="$v"
    break
  fi
done

[ "$enabled" != "true" ] && exit 0

# Are we even in a git repo? If not, this hook has nothing to do.
git_dir="$(git rev-parse --git-dir 2>/dev/null || true)"
[ -z "$git_dir" ] && exit 0

git_common="$(git rev-parse --git-common-dir 2>/dev/null || true)"
[ -z "$git_common" ] && exit 0

# Resolve to absolute paths so the comparison is reliable.
git_dir_abs="$(cd "$git_dir" 2>/dev/null && pwd -P || echo "$git_dir")"
git_common_abs="$(cd "$git_common" 2>/dev/null && pwd -P || echo "$git_common")"

# In a linked worktree, git-dir != git-common-dir. In the main checkout, they're equal.
if [ "$git_dir_abs" != "$git_common_abs" ]; then
  # Already in a worktree — allow.
  exit 0
fi

# Submodule guard: in a submodule, git-dir != git-common-dir is also true,
# but `show-superproject-working-tree` returns a path. We've already accepted
# the worktree case above, so reaching here means we're in a normal main checkout
# (not a worktree, not a submodule of a worktree). Block.

# Block via exit 2 with stderr feedback so Claude sees the message.
{
  echo "[my-claude-stuff] Refusing write: not in a git worktree."
  echo ""
  echo "my-claude-stuff.requireWorktree is enabled. Set up a worktree before editing files:"
  echo ""
  echo "  grep -qx '.worktrees/' .gitignore || { echo '.worktrees/' >> .gitignore && git add .gitignore && git commit -m 'chore: ignore .worktrees/'; }"
  echo "  git worktree add .worktrees/<flattened-branch> -b ec/<summary>"
  echo "  cd .worktrees/<flattened-branch>"
  echo ""
  echo "See conventions/worktree.md for the full convention."
  echo "To disable this guard, set my-claude-stuff.requireWorktree to false in settings.json."
} >&2

exit 2
