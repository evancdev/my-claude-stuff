#!/usr/bin/env bash
# Idempotently adds this plugin's scripts/ directory to your PATH via your
# shell rc file. Run once per machine; re-runs replace the existing block
# in place rather than duplicating it.
#
# Usage:
#   install-cli.sh             # add / refresh the PATH block
#   install-cli.sh --uninstall # remove the PATH block
set -euo pipefail

scripts_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${SHELL:-}" in
  */zsh) rc="$HOME/.zshrc" ;;
  */bash)
    if [[ -f "$HOME/.bash_profile" ]]; then rc="$HOME/.bash_profile"
    else rc="$HOME/.bashrc"; fi
    ;;
  *) rc="$HOME/.profile" ;;
esac

marker_start="# >>> my-claude-stuff scripts >>>"
marker_end="# <<< my-claude-stuff scripts <<<"

touch "$rc"
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

if [[ "${1:-}" == "--uninstall" ]]; then
  awk -v start="$marker_start" -v end="$marker_end" '
    $0 == start { in_block = 1; next }
    $0 == end && in_block { in_block = 0; next }
    !in_block { print }
  ' "$rc" > "$tmp"
  mv "$tmp" "$rc"
  echo "install-cli: removed PATH block from $rc"
  exit 0
fi

block_body="export PATH=\"$scripts_dir:\$PATH\""

if grep -qF "$marker_start" "$rc"; then
  awk -v start="$marker_start" -v end="$marker_end" -v body="$block_body" '
    $0 == start { print start; print body; print end; in_block = 1; next }
    $0 == end && in_block { in_block = 0; next }
    !in_block { print }
  ' "$rc" > "$tmp"
  mv "$tmp" "$rc"
  echo "install-cli: updated PATH block in $rc"
else
  printf '\n%s\n%s\n%s\n' "$marker_start" "$block_body" "$marker_end" >> "$rc"
  echo "install-cli: appended PATH block to $rc"
fi

echo "install-cli: scripts dir = $scripts_dir"
echo "install-cli: open a new shell, or run 'source $rc', to pick up the change."
