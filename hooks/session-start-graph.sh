#!/usr/bin/env bash
# SessionStart: ensure the personal-graph Neo4j is running, surface upcoming
# events + a pointer to the schema doc as additionalContext.
#
# Contract: stdout is exactly one JSON object; exit 0 always; under 10s wallclock
# even when Docker hangs. Stderr is unconstrained and goes to a log file.
#
# Hard-requires python3 (universally present on macOS and any reasonable Linux).
set -uo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
CONTAINER="my-claude-stuff-graph"
NEO4J_USER="${NEO4J_USERNAME:-neo4j}"
NEO4J_PASS="${NEO4J_PASSWORD:-changeme-graph}"
LOG_FILE="${TMPDIR:-/tmp}/personal-graph-hook.log"
EMITTED=0
ENV_FILE=""

emit() {
  local ctx="${1:-}"
  if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Personal graph hook needs python3 in PATH."}}'
    EMITTED=1
    return
  fi
  # Capture python3's output into a buffer FIRST so a mid-emit crash doesn't
  # leave partial garbage on stdout that the trap fallback would then concat
  # another JSON object onto. Only write to stdout on a confirmed clean exit.
  local output
  if output="$(printf '%s' "$ctx" | python3 -c "
import json, sys
print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': sys.stdin.read()}}))
")"; then
    printf '%s\n' "$output"
    EMITTED=1
  fi
}

# Last-resort: always emit valid JSON and clean up the env file.
on_exit() {
  [ -n "$ENV_FILE" ] && rm -f "$ENV_FILE"
  if [ "$EMITTED" -eq 0 ]; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Personal graph hook exited unexpectedly."}}'
  fi
}
trap on_exit EXIT

# Run a command with a wallclock timeout. Returns the command's exit code, or
# 124 on timeout. Prefers GNU/BSD `timeout`/`gtimeout`, falls back to python3
# (which kills the whole process group on timeout so orphaned descendants
# can't keep `$(...)` pipes open).
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
  return $?
}

# Best-effort container start; never blocks emission. neo4j-up.sh's own
# internal timeouts cap at ~4s worst-case; this outer wrapper is defense in
# depth.
run_timed 6 "${PLUGIN_ROOT}/scripts/neo4j-up.sh" >/dev/null 2>>"$LOG_FILE" || true

if ! command -v docker >/dev/null 2>&1; then
  emit "Personal graph (Neo4j) is configured but Docker is not installed. See ${PLUGIN_ROOT}/conventions/graph-schema.md and ${PLUGIN_ROOT}/README.md."
  exit 0
fi

running="$(run_timed 2 docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>>"$LOG_FILE" || true)"
if [ "$running" != "true" ]; then
  emit "Personal graph (Neo4j) is configured but not running. See ${PLUGIN_ROOT}/conventions/graph-schema.md and ${PLUGIN_ROOT}/README.md for setup."
  exit 0
fi

# Pass the password via a 0600 env file so it doesn't appear in the host's
# process table (docker exec argv is visible to all users via ps).
ENV_FILE="$(mktemp "${TMPDIR:-/tmp}/personal-graph-env.XXXXXX")"
chmod 600 "$ENV_FILE"
printf 'NEO4J_PASSWORD=%s\n' "$NEO4J_PASS" > "$ENV_FILE"

# Bootstrap uniqueness constraints idempotently. Separate cypher-shell call
# from the events query so multi-statement output framing can't corrupt the
# events parser below. Output is discarded — `IF NOT EXISTS` makes this a no-op
# after first run.
run_timed 2 docker exec --env-file "$ENV_FILE" "$CONTAINER" \
  cypher-shell -u "$NEO4J_USER" --format plain \
  "CREATE CONSTRAINT person_name  IF NOT EXISTS FOR (n:Person)     REQUIRE n.name IS UNIQUE;
   CREATE CONSTRAINT org_name     IF NOT EXISTS FOR (n:Org)        REQUIRE n.name IS UNIQUE;
   CREATE CONSTRAINT project_name IF NOT EXISTS FOR (n:Project)    REQUIRE n.name IS UNIQUE;
   CREATE CONSTRAINT topic_name   IF NOT EXISTS FOR (n:Topic)      REQUIRE n.name IS UNIQUE;
   CREATE CONSTRAINT pref_summary IF NOT EXISTS FOR (n:Preference) REQUIRE n.summary IS UNIQUE;" \
  >/dev/null 2>>"$LOG_FILE" || true

events_raw="$(run_timed 3 docker exec --env-file "$ENV_FILE" "$CONTAINER" \
  cypher-shell -u "$NEO4J_USER" --format plain \
  "MATCH (e:Event)
   WHERE datetime(e.start_at) > datetime()
     AND datetime(e.start_at) < datetime() + duration({hours: 24})
   RETURN substring(toString(e.start_at), 11, 5) + ' — ' + e.title +
          CASE WHEN coalesce(e.location, '') = '' THEN ''
               ELSE ' (' + e.location + ')' END AS line
   ORDER BY e.start_at LIMIT 5" 2>>"$LOG_FILE")"
events_rc=$?

rm -f "$ENV_FILE"
ENV_FILE=""

events=""
state_note=""
if [ "$events_rc" -ne 0 ]; then
  # Container is running but bolt isn't reachable yet (cold-start window,
  # bad password, APOC still loading, etc). Log path has the detail.
  state_note="(database is starting up or unreachable — see ${LOG_FILE})"
elif [ -n "$events_raw" ]; then
  events="$(printf '%s\n' "$events_raw" | tail -n +2 | sed 's/^"//; s/"$//; s/^/  - /' | sed '/^  - $/d')"
fi

ctx="Personal graph is live (Neo4j @ localhost:7687, MCP server \`personal-graph\`).
Schema: ${PLUGIN_ROOT}/conventions/graph-schema.md — read it before writing.
Write rule: MERGE only, never CREATE."

if [ -n "$state_note" ]; then
  ctx="${ctx}
${state_note}"
fi

if [ -n "$events" ]; then
  ctx="${ctx}

Upcoming events (next 24h):
${events}"
fi

emit "$ctx"
