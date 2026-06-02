---
description: Interview the user to seed the personal graph with baseline facts (the Evan node, current projects, key people, standing events). Idempotent — safe to re-run.
allowed-tools: Read, AskUserQuestion, mcp__personal-graph__read_neo4j_cypher, mcp__personal-graph__write_neo4j_cypher, mcp__personal-graph__get_neo4j_schema
---

Seed the personal-graph Neo4j database with baseline facts about the user. The graph is useless on day one unless we put something in it.

## Preconditions

1. Confirm the `personal-graph` MCP server is reachable: call `get_neo4j_schema`. If it errors, tell the user "graph isn't reachable — check that Docker is running and the container `my-claude-stuff-graph` is up, and that Neo4j is past its cold-start window (~30s after first `docker compose up`). Retry in a moment." Then stop.
2. Read the schema doc at `conventions/graph-schema.md` so you know the allowed labels and edge types. **Do not invent labels or edge types.**
3. Uniqueness constraints for `Person`/`Org`/`Project`/`Topic`/`Preference` are bootstrapped automatically by the SessionStart hook on every Claude session — you don't need to create them here. `Event` and `Decision` MERGE on composite keys (`(title, start_at)` and `(summary, decided_at)`); Neo4j Community doesn't support composite-property uniqueness constraints, so those rely on the single-user MERGE-pattern discipline rather than DB-enforced uniqueness.

## Seed flow

Use `AskUserQuestion` to collect each category. One question at a time. Keep it short — 4-6 questions total, not an interrogation. Skip categories the user passes on.

1. **The Evan node.** MERGE `(:Person {name: 'Evan'})`. No question needed — just confirm with a brief message. Optionally ask for role/title.
2. **Current projects** (1-3 most important). For each: name, optional `cwd`, optional one-line description. Per the schema, `ABOUT` originates only from `Decision`/`Preference`, so do not link a `Project` to a `Topic` here — surface topic associations later via decisions or preferences that reference both.
3. **Key people.** Up to 5 names of people Evan interacts with often. For each: name + which `Org` they're at (if applicable).
4. **Standing meetings.** Recurring events Evan has in the next week (e.g., standups, 1:1s). Capture each as an `Event` node — convert any relative date to absolute ISO 8601 using today's date.
5. **One strong preference.** A non-obvious technical or working preference Evan wants Claude to remember relationally (e.g., "prefers Postgres over MySQL for new projects"). Store as a `Preference` node + `PREFERS` edge from Evan.

## Write discipline

- **MERGE only.** Every node and edge uses `MERGE` keyed on the stable properties documented in `graph-schema.md`:
  - `name` for `Person` / `Org` / `Project` / `Topic`
  - `summary` for `Preference`
  - `(title, start_at)` for `Event` — composite natural key; `id` is a downstream UUID handle set on CREATE, **not** the MERGE key
  - `(summary, decided_at)` for `Decision` — same pattern as `Event`
- For `id` fields, generate with Cypher's built-in `randomUUID()` (Neo4j 5.5+). Example:
  ```cypher
  MERGE (e:Event {title: $title, start_at: $start_at})
    ON CREATE SET e.id = randomUUID(),
                  e.created_at = datetime(),
                  e.updated_at = datetime(),
                  e.source = 'graph-seed'
    ON MATCH  SET e.updated_at = datetime()
  ```
  Note: `MERGE` keys on `(title, start_at)` so the same meeting added twice is one node — the `id` exists only for stable downstream references.
- Set `created_at`, `updated_at`, `source = 'graph-seed'` on every node per the canonical template in `graph-schema.md`.
- After each write batch, run `read_neo4j_cypher` with `MATCH (n) RETURN labels(n) AS label, count(n) AS n` and show the user a compact summary so they can sanity-check.

## Done

Report back with the node counts by label and offer:
- "Want to add more (people, events, projects)?"

Do **not** start writing extracted facts from prior conversation context unless the user asks — `/graph-seed` is for deliberate seeding, not free-form ingestion.
