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
#   hunk-review.cmux.sh                 # working tree, including untracked files
#   hunk-review.cmux.sh --staged        # staged changes only
#   hunk-review.cmux.sh main...HEAD     # a ref range
#   hunk-review.cmux.sh -- src/ tests/  # only these paths; bare paths are read as refs
#   hunk-review.cmux.sh --close         # close the review tab for this repo
#   Every other argument is passed through to `hunk diff`.
#
# Prints the session id on success. Exits non-zero with a reason the agent can
# repeat to the user, which usually means "open hunk yourself".
set -euo pipefail

command -v hunk >/dev/null 2>&1 ||
  { echo "error: hunk is not installed. brew install hunk" >&2; exit 1; }

root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$root" ] ||
  { echo "error: not inside a git repository, so there is nothing to review." >&2; exit 1; }

title="review: $(basename "$root")"

# The agent's own pane. cmux defaults `new-surface` and `list-pane-surfaces` to
# the focused pane, wherever the user is looking, and that is never where the
# review belongs. No pane means this session is not in a cmux tab, so anything
# that would open or scan one stops here instead.
here="$(cd "$(dirname "$0")" && pwd -P)"
place="$("${here}/agent-pane.py" 2>/dev/null || true)"
require_place() {
  [ -n "$place" ] ||
    { echo "error: could not resolve this agent's cmux pane, so there is nowhere to put the review tab. Is this session running inside cmux?" >&2; exit 1; }
}

# UUID of the surface running the live session, matched by tty. A UUID resolves
# in any workspace; a `surface:N` ref only resolves in the caller's. Empty when
# no session is live.
review_surface_uuid() {
  tty="$(hunk session get --repo "$root" --json 2>/dev/null | /usr/bin/python3 -c '
import json, sys
try:
    locs = json.load(sys.stdin)["session"]["terminal"]["locations"]
    print(next((x["tty"] for x in locs if x.get("tty")), ""))
except Exception:
    print("")
' 2>/dev/null || true)"
  [ -n "$tty" ] || return 0
  "${here}/agent-pane.py" --tty "$tty" 2>/dev/null || true
}

# Fallback when no session is live: surfaces in the agent's pane carrying our
# title. cmux cannot say which surface owns a hunk session, so the title is the
# only handle. `|| true` because pipefail aborts on an empty listing.
# shellcheck disable=SC2086  # $place is two validated refs.
review_surfaces() {
  cmux list-pane-surfaces $place 2>/dev/null |
    awk -v t="$title" 'index($0, t) { for (i = 1; i <= NF; i++) if ($i ~ /^surface:[0-9]+$/) { print $i; break } }' || true
}

# Closing the tab kills the session and every comment on it, so it only happens
# when the caller asks.
if [ "${1:-}" = "--close" ]; then
  command -v cmux >/dev/null 2>&1 ||
    { echo "error: cmux is not available, so ask the user to close the review tab." >&2; exit 1; }
  found=""
  uuid="$(review_surface_uuid)"
  if [ -n "$uuid" ]; then
    cmux close-surface --surface "$uuid" >/dev/null 2>&1 || true
    found="${uuid} "
  else
    require_place
    for s in $(review_surfaces); do
      cmux close-surface --surface "$s" >/dev/null 2>&1 || true
      found="${found}${s} "
    done
  fi
  if [ -n "$found" ]; then
    echo "closed ${found% }"
  else
    echo "no review tab open for ${root}"
  fi
  exit 0
fi

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
require_place
for dead in $(review_surfaces); do
  cmux close-surface --surface "$dead" >/dev/null 2>&1 || true
done

# `new-surface` cannot carry a command, so the shell in the new tab is fed one.
# --focus false on purpose: pulling the user into the tab interrupts them.
# --id-format both returns the UUID beside the ref, and everything below
# addresses the tab by UUID. `rename-tab` and `send` do not error on a ref that
# does not resolve; they fall through to the focused tab.
# shellcheck disable=SC2086
surface_out="$(cmux --id-format both new-surface --type terminal $place --working-directory "$root" --focus false 2>&1)" ||
  { echo "error: cmux new-surface failed: ${surface_out}" >&2; exit 1; }
surface="$(printf '%s\n' "$surface_out" | awk '/^OK/ {print $2; exit}')"
target="$(printf '%s\n' "$surface_out" | awk '/^OK/ {gsub(/[()]/, "", $3); print $3; exit}')"
[ -n "$surface" ] ||
  { echo "error: could not read a surface id out of: ${surface_out}" >&2; exit 1; }
[ -n "$target" ] || target="$surface"

# An auto-titled tab is one the corpse scan can never find, so a failed rename
# is reported, not swallowed.
renamed=1
cmux rename-tab --surface "$target" "$title" >/dev/null 2>&1 || renamed=""
cmux send --surface "$target" "hunk diff $* \n" >/dev/null

# The shell has to start, hunk has to boot, and the daemon has to register it.
for _ in $(seq 1 30); do
  id="$(session_id)"
  if [ -n "$id" ]; then
    warn=""
    [ -n "$renamed" ] ||
      warn="${warn} (the tab kept its auto-title; --close still finds it by tty while the session lives)"
    echo "opened ${id} in ${surface}${warn}"
    exit 0
  fi
  sleep 0.5
done

# Left open rather than closed, because whatever hunk printed in there is the
# only record of why it failed. The next run reclaims it.
echo "error: hunk was launched in ${surface} but never registered with the daemon. Read that tab for the reason; the next run will close it." >&2
exit 1
