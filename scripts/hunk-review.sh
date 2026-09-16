#!/usr/bin/env bash
# Open or refresh a hunk review tab for the current repo, so the user can read a
# changeset as a local PR while the agent keeps its own terminal.
#
# A hunk TUI needs a real terminal, which an agent's Bash tool does not have. So
# the agent never runs `hunk diff` itself. It runs this, which puts hunk in a
# cmux tab beside the agent's own, then drives that session over the hunk daemon
# with `hunk session ...`.
#
# Usage:
#   hunk-review.sh                 # working tree, including untracked files
#   hunk-review.sh --staged        # staged changes only
#   hunk-review.sh main...HEAD     # a ref range
#   Every argument is passed through to `hunk diff`.
#
# Environment:
#   HUNK_REVIEW_FOCUS   1 to move the cursor into the new tab (default: 0)
#
# Prints the session id on success. Exits non-zero with a reason the agent can
# repeat to the user, which usually means "open hunk yourself".
set -euo pipefail

focus="false"
[ "${HUNK_REVIEW_FOCUS:-0}" = "1" ] && focus="true"

command -v hunk >/dev/null 2>&1 ||
  { echo "error: hunk is not installed. brew install hunk" >&2; exit 1; }

root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$root" ] ||
  { echo "error: not inside a git repository, so there is nothing to review." >&2; exit 1; }

# The scope arguments reach a live shell on the spawn path, so they are limited
# to the characters a flag, ref, or path actually needs. Anything else would
# have to be quoted, and cmux reads \n and \t back out of the text it sends,
# which makes quoting unsafe to do blind. Checked before either path so the
# accepted input is the same whether or not a session already exists.
for arg in "$@"; do
  case "$arg" in
  *[!A-Za-z0-9._/~^@=+-]*)
    echo "error: refusing to type '${arg}' into a shell. Pass a plain flag, ref, or path, or reload the session by hand." >&2
    exit 1
    ;;
  esac
done

# Agent notes are hidden by default, which makes every note this workflow writes
# invisible until someone presses `a`. Turn them on unless the caller said otherwise.
case " $* " in
*" --agent-notes "* | *" --no-agent-notes "*) ;;
*) set -- --agent-notes "$@" ;;
esac

# `session get --json` nests the id, and `"id"` also appears on every file in the
# review, so this reads the field by name rather than by first match.
session_id() {
  hunk session get --repo "$root" --json 2>/dev/null | /usr/bin/python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("session", {}).get("sessionId", ""))
except Exception:
    print("")
' 2>/dev/null || true
}

# A session already showing this repo is the one to reuse. Reloading keeps the
# user in the review they already have instead of stacking another tab beside it.
existing="$(session_id)"
if [ -n "$existing" ]; then
  hunk session reload --repo "$root" -- diff "$@" >/dev/null
  echo "reloaded ${existing}"
  exit 0
fi

command -v cmux >/dev/null 2>&1 ||
  { echo "error: no hunk session for ${root} and cmux is not available to open one. Ask the user to run 'hunk diff' in a terminal." >&2; exit 1; }

# Reaching here means no live session claims this repo, so any tab still carrying
# our title is a corpse: a previous launch whose hunk died and left the shell
# prompt behind. Without this they accumulate one per failed launch. Two repos
# sharing a basename would collide, and the cost of that is a closed tab, not
# lost work. `|| true` because pipefail would otherwise abort the script here.
title="review: $(basename "$root")"
cmux list-pane-surfaces 2>/dev/null |
  awk -v t="$title" 'index($0, t) { for (i = 1; i <= NF; i++) if ($i ~ /^surface:[0-9]+$/) { print $i; break } }' |
  while read -r dead; do
    cmux close-surface --surface "$dead" >/dev/null 2>&1 || true
  done || true

# `new-surface` cannot carry a command, so the shell in the new tab is fed one.
surface_out="$(cmux new-surface --type terminal --working-directory "$root" --focus "$focus" 2>&1)" ||
  { echo "error: cmux new-surface failed: ${surface_out}" >&2; exit 1; }
surface="$(printf '%s\n' "$surface_out" | awk '/^OK/ {print $2; exit}')"
[ -n "$surface" ] ||
  { echo "error: could not read a surface id out of: ${surface_out}" >&2; exit 1; }

cmux rename-tab --surface "$surface" "$title" >/dev/null 2>&1 || true
cmux send --surface "$surface" "hunk diff $* \n" >/dev/null

# The shell has to start, hunk has to boot, and the daemon has to register it.
for _ in $(seq 1 30); do
  id="$(session_id)"
  if [ -n "$id" ]; then
    echo "opened ${id} in ${surface}"
    exit 0
  fi
  sleep 0.5
done

# Left open rather than closed, because whatever hunk printed in there is the
# only record of why it failed. The next run reclaims it.
echo "error: hunk was launched in ${surface} but never registered with the daemon. Read that tab for the reason; the next run will close it." >&2
exit 1
