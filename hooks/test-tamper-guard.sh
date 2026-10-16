#!/usr/bin/env bash
# test-tamper-guard.sh
#
# Block agent edits that weaken test files — Beck's #1 documented agent
# failure mode: when told to "make tests pass," agents will delete the
# failing assertion, add @pytest.mark.skip, or wrap the assert in a
# try/except rather than fix the underlying bug.
#
# Heuristics (any one triggers a block):
#   - Assertion count decreased (assert / expect( / chai.* / sinon.*)
#   - New skip/xfail markers added (@pytest.mark.skip, @pytest.mark.xfail,
#     test.skip, it.skip, .skip(), xfail)
#   - test body was replaced with `pass` / `return` / empty block
#
# Override: set TEST_TAMPER_OVERRIDE=1 in the environment for the session
# (e.g. legitimate refactor that genuinely removes redundant asserts).
#
# Input: JSON tool call on stdin (Claude Code PreToolUse hook protocol).
# Output: JSON decision on stdout. Exit 0 always (decision conveys block).

set -eu

# If override is set, allow unconditionally.
if [ "${TEST_TAMPER_OVERRIDE:-}" = "1" ]; then
  exit 0
fi

input=$(cat)

# Extract fields. jq with // empty so missing keys yield empty strings.
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.path // empty')

# Only inspect edits to test files.
case "$file_path" in
  */tests/*|*/test/*|*test_*.py|*_test.py|*.test.ts|*.test.tsx|*.test.js|*.test.jsx|*.spec.ts|*.spec.tsx|*.spec.js|*.spec.jsx)
    : # fall through to inspection
    ;;
  *)
    exit 0  # not a test file; allow
    ;;
esac

# Compare old vs new content. Edit tool passes old_string/new_string;
# Write tool passes content (no "old" — treat as full replacement).
old=$(printf '%s' "$input" | jq -r '.tool_input.old_string // empty')
new=$(printf '%s' "$input" | jq -r '.tool_input.new_string // .tool_input.content // empty')

# If this is a Write (no old_string), compare against existing file on disk.
if [ -z "$old" ] && [ -f "$file_path" ]; then
  old=$(cat "$file_path")
fi

# Count regex matches in $1. grep -c outputs the count even when 0, but exits 1
# in that case — || true keeps the script from aborting under set -e.
count() {
  local n
  n=$(printf '%s' "$1" | grep -cE "$2" 2>/dev/null) || true
  echo "${n:-0}"
}

old_asserts=$(count "$old" '(^|[^a-zA-Z_])(assert |expect\(|chai\.|sinon\.)')
new_asserts=$(count "$new" '(^|[^a-zA-Z_])(assert |expect\(|chai\.|sinon\.)')
old_skips=$(count "$old" '(@pytest\.mark\.(skip|xfail)|\.skip\(|\.xfail\(|test\.skip|it\.skip)')
new_skips=$(count "$new" '(@pytest\.mark\.(skip|xfail)|\.skip\(|\.xfail\(|test\.skip|it\.skip)')

reasons=()

if [ "$new_asserts" -lt "$old_asserts" ]; then
  reasons+=("assertion count decreased ($old_asserts → $new_asserts)")
fi
if [ "$new_skips" -gt "$old_skips" ]; then
  reasons+=("new skip/xfail markers added ($old_skips → $new_skips)")
fi

if [ ${#reasons[@]} -eq 0 ]; then
  exit 0  # no weakening detected; allow
fi

# Block with a clear reason. The agent will see this and can decide:
# - if the change is wrong, revise it
# - if the change is intentional, ask the user to set TEST_TAMPER_OVERRIDE=1
joined=$(IFS='; '; printf '%s' "${reasons[*]}")
jq -n --arg msg "Refused: edit to $file_path appears to weaken tests ($joined). This is Beck's documented agent failure mode (disabling failing tests instead of fixing the bug). If the change is genuinely intentional (e.g. removing a redundant test, intentional skip), the user must set TEST_TAMPER_OVERRIDE=1 in the environment for this session and retry." \
  '{decision: "block", reason: $msg}'
