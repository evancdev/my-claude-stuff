#!/usr/bin/env bash
# PreToolUse(Bash) guard. Reads tool input on stdin (JSON), exits 2 with a
# message on stderr to block, exits 0 to allow.
set -euo pipefail

input="$(cat)"
cmd="$(printf '%s' "$input" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"

block() {
  echo "blocked by pre-bash-guard: $1" >&2
  exit 2
}

# Catastrophic
if printf '%s' "$cmd" | grep -Eq 'rm[[:space:]]+-rf?[[:space:]]+(/|--no-preserve-root|~|\$HOME)'; then
  block "destructive rm"
fi

# Force-push to protected branches
if printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+push.*(--force|-f([[:space:]]|$)).*(main|master|prod|production|release)'; then
  block "force-push to protected branch"
fi

# Skipping verification hooks
if printf '%s' "$cmd" | grep -Eq -- '--no-verify|--no-gpg-sign'; then
  block "bypassing git hooks/signing — fix the underlying issue instead"
fi

# Hard reset on a clean tree is fine; on dirty tree caller should confirm. We just warn.
exit 0
