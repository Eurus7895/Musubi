# Backend contract — Musubi `audit.db`

The desktop app's Rust core (`musubi-data`) reads a SQLite database into the
state the UI renders. Point it at Musubi's real database with the `MUSUBI_DB`
env var:

```bash
MUSUBI_DB=/path/to/storage/audit.db npm run tauri:dev
```

When `MUSUBI_DB` is unset, an in-memory **demo** DB is seeded (`seed_demo`) so
the app runs standalone with representative data.

The reader is **read-mostly** and tolerant: a fresh DB with empty tables yields
empty surfaces; missing optional columns fall back to defaults. It never writes
to the append-only audit tables. The only writes the app performs are to the
GUI-side `chat_log` and `meta` tables (driver chat, active profile); governed
mutations (spawning agents, running pipelines) must go through the MCP server,
not direct DB writes — those action handlers are stubbed with a `todo`.

## Tables

`init_schema()` / `SCHEMA_SQL` create these; a real Musubi `audit.db` should
expose the same shape (column names matter; extra columns are ignored).

### `subagent_audit` — append-only sub-agent lifecycle (HI #8: no silent sub-agents)

One row per lifecycle event. A handle is **running** until its `completed` row
lands; the cohort and the audit ledger are both folded from this table.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | monotonic; ledger ordering |
| `ts` | TEXT | timestamp string, shown verbatim (e.g. `14:46:01`) |
| `event` | TEXT | `spawned` \| `completed` |
| `handle` | TEXT | 8-hex sub-agent id |
| `role` | TEXT | `explorer` \| `investigator` \| `reviewer-aux` \| … |
| `parent` | TEXT | e.g. `driver · agent-loop` |
| `model` | TEXT | resolved per-agent model |
| `profile` | TEXT | `llm.toml` profile |
| `brief` | TEXT | firewalled brief |
| `allowed_tools` | TEXT | JSON array (or comma list) of tool names |
| `max_turns` | INTEGER | turn cap |
| `turns` | INTEGER | turns used (on `completed`) |
| `tools_used` | INTEGER | |
| `status` | TEXT | `running`\|`done`\|`failed`\|`escalated`\|`abandoned` (on `completed`) |
| `wall_remaining` | INTEGER | seconds of wall-clock budget left |
| `verification_errors` | INTEGER | reserved |

### `policy_audit` — fail-closed PreToolUse decisions

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | TEXT | |
| `verdict` | TEXT | `ALLOW` \| `DENY` |
| `tool` | TEXT | the requested tool |
| `role` | TEXT | requesting agent role |
| `handle` | TEXT | requesting agent handle |
| `reason` | TEXT | e.g. `outside firewall surface — code-only (HI #3)` |

### `chat_log` — driver conversation (GUI-side)

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | TEXT | optional |
| `role` | TEXT | `you` \| `driver` \| `system` |
| `tone` | TEXT | optional: `spawn` \| `deny` (styles system notes) |
| `text` | TEXT | |

### `meta` — key/value

| key | meaning |
|---|---|
| `active_profile` | LMRouter default profile shown selected in **Models** |

## Mapping to the UI

| UI surface | source |
|---|---|
| Orchestrator cohort | `subagent_audit` folded per handle |
| Orchestrator counts (running/completed) | derived from statuses |
| Policy stream + allow/deny tallies | `policy_audit` |
| Audit ledger | `subagent_audit` (newest first, capped 120) |
| Models active profile | `meta.active_profile` (+ static `profileDefs`) |
| Driver chat | `chat_log` |
| Pipeline studio | authoring surface — default `feature-dev`, not from the DB |

## State shape (Rust → JSON → `buildViewModel`)

`load_state()` serialises to camelCase JSON matching the frontend domain state:
`subagents[]`, `policy[]`, `audit[]`, `chat[]`, `totalSpawned`, `totalDone`,
`allowCount`, `denyCount`, `activeProfile`, `pipeSteps[]`, … The frontend derives
all presentation (colours, chips) from `role`/`status`, so the backend only
supplies domain fields. See `musubi-data/src/lib.rs` and its tests.
