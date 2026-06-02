# Personal Graph Schema

A time-aware personal-context graph about the user (Evan). Backed by Neo4j; reached via the `personal-graph` MCP server. Claude reads it to ground answers in the user's life context and writes to it when something biographical, relational, or time-sensitive comes up in conversation.

This is **not** a mirror of the markdown auto-memory system. Prose memory captures narrative profile and feedback; this graph captures **atomic, relational, temporal** facts that benefit from multi-hop traversal.

## Write rule: MERGE only, never CREATE

Every write must use `MERGE` keyed on stable properties so re-running the same statement is idempotent. `CREATE` is forbidden — it produces duplicate nodes the moment Claude says the same thing twice across sessions.

Canonical template (sets all three timestamps/source fields the schema promises every node has):

```cypher
MERGE (p:Person {name: 'Sarah Chen'})
  ON CREATE SET p.created_at = datetime(),
                p.updated_at = datetime(),
                p.source     = 'session'
  ON MATCH  SET p.updated_at = datetime()
```

Every node carries `created_at`, `updated_at`, and `source`. `source` is a constant string identifying the writer: `'session'` for nodes Claude writes during a normal session, `'graph-seed'` for the `/graph-seed` slash command, `'manual'` for direct user edits in Neo4j Browser. It's set once on CREATE and not overwritten on MATCH — it records who first put the node in the graph.

Edges follow the same shape — **only** the type and endpoint keys are the MERGE key, never properties:

```cypher
MATCH (a:Person {name: 'Evan'}), (b:Person {name: 'Sarah Chen'})
MERGE (a)-[r:KNOWS]->(b)
  ON CREATE SET r.created_at = datetime(), r.source = 'session'
```

If you key a `MERGE` on edge *properties*, two writes with different property values produce two edges and the idempotency guarantee breaks.

## Node labels

| Label        | MERGE key            | Notes |
|--------------|----------------------|-------|
| `Person`     | `name`               | Anyone Evan knows or interacts with. Optional: `role`, `email`, `notes`. |
| `Org`        | `name`               | Company, team, customer, vendor. |
| `Project`    | `name`               | A piece of work — code repo, side project, initiative. Optional: `cwd` (absolute path), `stack`, `status`. |
| `Event`      | `(title, start_at)`  | Time-bound: meeting, deadline, call, demo. Requires `title`, `start_at` (ISO 8601). Optional: `end_at`, `location`, `agenda`. `id` (UUID via `randomUUID()`) is set on CREATE as a stable downstream handle — **do not MERGE on `id`**. |
| `Decision`   | `(summary, decided_at)` | A choice Evan made with stated reasoning. Optional: `reasoning`, `outcome`. `id` (UUID) set on CREATE. |
| `Preference` | `summary`            | A stated preference or constraint (e.g., "prefers Postgres over MySQL"). Optional: `strength` ∈ `weak|moderate|strong`. `id` (UUID) set on CREATE. |
| `Topic`      | `name`               | A concept or area of interest used to tag other nodes. Lowercase, kebab-case (`graph-databases`, `react`). |

If a fact doesn't fit a label, **do not invent a new one mid-session.** Either map it to the closest existing label or attach a `Topic` and put the detail in a property. New labels get added to this doc deliberately, not improvised.

## Edge types

| Type            | From                              | To                          | Notes |
|-----------------|-----------------------------------|-----------------------------|-------|
| `KNOWS`         | `Person`                          | `Person`                    | Originates from the implicit `Evan` node — these are people *he* knows. |
| `WORKS_AT`      | `Person`                          | `Org`                       | Optional edge props: `role`, `started_at`, `ended_at`. |
| `ATTENDS`       | `Person`                          | `Event`                     | |
| `MADE`          | `Person`                          | `Decision`                  | Usually Evan; explicit so the source of a decision is queryable. |
| `PREFERS`       | `Person`                          | `Preference`                | |
| `SUPERSEDES`    | `Decision`                        | `Decision`                  | New decision replaces old. |
| `INVOLVES`      | `Event`                           | `Project` \| `Topic`        | What an event is for — projects or topics it concerns. Person→Event attendance is `ATTENDS`, not `INVOLVES`. |
| `ABOUT`         | `Decision` \| `Preference`        | `Topic` \| `Project` \| `Person` | What a decision or preference concerns. Does not originate from `Event` — use `INVOLVES` there. |

There is exactly one valid edge per `(source-node, type, target-node)` triple. Because `INVOLVES` and `ABOUT` have disjoint source labels, there is never ambiguity about which type to use for a given pair of nodes.

Edges also carry `created_at` and `source`. Edge properties beyond those are okay (e.g., `WORKS_AT` with `role`/`started_at`).

## The implicit "Evan" node

There's exactly one `Person {name: 'Evan'}` node — the singleton representing the user. Don't duplicate it. All `KNOWS`, `PREFERS`, `MADE` edges that would "originate from the user" originate from this implicit node. `/graph-seed` MERGEs it on first run.

## What goes in the graph vs. markdown memory

| Goes in graph | Goes in markdown memory |
|---------------|------------------------|
| "Evan has a 1:1 with Sarah Friday 3pm" | "Evan prefers terse responses with no trailing summary" |
| "Evan decided to use Neo4j over Kuzu because of MCP support" | "Evan is a deep Go expert, new to React in this repo" |
| "Project `pinnacle-api` uses Postgres" | Project-wide architectural decisions and why |
| "Evan knows person P, who works at company C" | Long-form preferences, feedback, personality |

Rule of thumb: **if it has a date, a relationship, or you'd want to traverse from it**, graph. **If it's prose context that Claude needs to read top-to-bottom**, markdown.

## Querying patterns Claude uses

- **Upcoming events** (the SessionStart hook runs this; same query is fine on demand):
  ```cypher
  MATCH (e:Event)
  WHERE datetime(e.start_at) > datetime()
    AND datetime(e.start_at) < datetime() + duration({hours: 24})
  RETURN substring(toString(e.start_at), 11, 5) + ' — ' + e.title +
         CASE WHEN coalesce(e.location, '') = '' THEN ''
              ELSE ' (' + e.location + ')' END AS line
  ORDER BY e.start_at LIMIT 5
  ```
- **Who is X?** — resolve a name mentioned in conversation:
  ```cypher
  MATCH (p:Person {name: $name})
  OPTIONAL MATCH (p)-[:WORKS_AT]->(o:Org)
  OPTIONAL MATCH (p)-[:ATTENDS]->(e:Event)
    WHERE datetime(e.start_at) > datetime() - duration({days: 30})
  RETURN p, o, [x IN collect(e) WHERE x IS NOT NULL] AS recent_events
  ```
- **Project context** — when CWD matches a Project node:
  ```cypher
  MATCH (p:Project {cwd: $cwd})-[r]-(other) RETURN p, r, other LIMIT 50
  ```

## Hygiene

- Before adding a `Person`, search for similar names — `MERGE` on exact `name` will create "Sarah Chen" and "Sarah" as separate nodes. Prefer canonical full names.
- Don't write speculative facts. If you aren't sure, ask. The graph is high-signal, not a transcript.
- Time-sensitive nodes (`Event`) should have absolute ISO 8601 timestamps. Convert relative ("Friday") to absolute at write time using the current date.
- For new `Event` / `Decision` / `Preference` nodes, generate `id` with `randomUUID()` so the MERGE is keyed on something stable across sessions.
