#!/usr/bin/env bash
# Idempotent: start the personal-graph Neo4j container if not running.
#
# Every docker call is wrapped in a small internal timeout so the script
# can't hang on a sick daemon. The (potentially slow) `docker compose up -d`
# is fully detached via Popen+start_new_session=True — first-run image pulls
# (~500MB) don't block the caller. The container becomes available on a
# subsequent session.
#
# Exits 0 on success or when Docker isn't available — never blocks the calling
# hook.
set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
COMPOSE_FILE="${PLUGIN_ROOT}/docker/neo4j-compose.yml"
CONTAINER="my-claude-stuff-graph"

run_timed() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
    return $?
  fi
  if command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$secs" "$@"
    return $?
  fi
  python3 - "$secs" "$@" <<'PY'
import os, signal, subprocess, sys
secs = float(sys.argv[1])
cmd = sys.argv[2:]
try:
    p = subprocess.Popen(cmd, start_new_session=True)
except FileNotFoundError:
    sys.exit(127)
try:
    rc = p.wait(timeout=secs)
except subprocess.TimeoutExpired:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    p.wait()
    sys.exit(124)
sys.exit(rc)
PY
}

# Spawn a command fully detached — survives parent process-group kill so the
# slow compose-up can continue even if the caller times out. Fallback chain:
# setsid > python3 (start_new_session) > nohup. At least one is universally
# present on macOS and any reasonable Linux.
detach() {
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" </dev/null >/dev/null 2>&1 &
    disown 2>/dev/null || true
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$@" <<'PY' || true
import subprocess, sys
subprocess.Popen(sys.argv[1:],
                 start_new_session=True,
                 stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL)
PY
    return 0
  fi
  # Last resort — nohup detaches from controlling terminal but not from pgroup.
  # Better than nothing on a minimal box without setsid or python3.
  nohup "$@" </dev/null >/dev/null 2>&1 &
  disown 2>/dev/null || true
  return 0
}

if ! command -v docker >/dev/null 2>&1; then
  echo "[graph] docker not installed — skipping" >&2
  exit 0
fi

if ! run_timed 2 docker info >/dev/null 2>&1; then
  echo "[graph] docker daemon not reachable — skipping" >&2
  exit 0
fi

running="$(run_timed 1 docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)"
if [ "$running" = "true" ]; then
  exit 0
fi

if ! run_timed 1 docker compose version >/dev/null 2>&1; then
  echo "[graph] docker compose v2 not available — skipping" >&2
  exit 0
fi

# Detach so first-run image pulls (~500MB) and slow startup don't block the
# caller. The container becomes available on a later session.
detach docker compose -f "$COMPOSE_FILE" up -d
exit 0
