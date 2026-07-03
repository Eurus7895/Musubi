# Backend contract — Musubi `audit.db`

The desktop app's Rust core (`musubi-data`) reads a SQLite database into the
state the UI renders. DB selection order:

1. `MUSUBI_DB`
2. `MUSUBI_ROOT/data/audit.db`
3. nearest workspace `musubi/storage/audit.db`
4. empty in-memory first-run state

```bash
MUSUBI_DB=/path/to/storage/audit.db npm run tauri:dev
```

When no real DB can be inferred, the app initializes the schema in memory and
shows empty surfaces. `seed_demo` remains available for unit tests and static
artifacts, but runtime first-run state is not simulated.

The reader maps the **real** tables the Musubi substrate writes
(`musubi/storage/subagent_audit.py`, `scripts/post_tool_use.py`) — column
names below match those writers. It is **read-mostly** and tolerant: a fresh DB
with empty tables yields empty surfaces; missing optional columns fall back to
defaults; `ts` may be a REAL epoch or a pre-formatted string. It never writes to
the append-only audit tables. The only writes the app performs are to the
GUI-side `chat_log` and `meta` tables (driver chat, active profile); governed
mutations (spawning agents, running pipelines) must go through the MCP server,
not direct DB writes — those action handlers are stubbed with a `todo`.

## Tables

`init_schema()` / `SCHEMA_SQL` create these on a fresh DB. On a real `audit.db`
the substrate's own tables already exist, so the `CREATE TABLE IF NOT EXISTS`
statements are no-ops there; only the GUI-side `chat_log` / `meta` (and an empty
forward-compat `policy_audit`) are added.

### `subagent_audit` — append-only sub-agent lifecycle (HI #8: no silent sub-agents)

Written by `musubi/storage/subagent_audit.py`. One row per lifecycle event; a
handle is **running** until its `completed` row lands. The cohort and the audit
ledger are both folded from this table.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | monotonic; ledger ordering |
| `ts` | REAL | epoch seconds; rendered `HH:MM:SS` (a TEXT value is shown verbatim) |
| `event` | TEXT | `spawned` \| `completed` |
| `handle_id` | TEXT | sub-agent id → `handle` |
| `role` | TEXT | `explorer` \| `investigator` \| `reviewer-aux` \| … |
| `parent_session_id` | TEXT | parent session (UUID; shortened in the card label) |
| `parent_agent_name` | TEXT | parent agent → composed into `parent` (`agent · sid`) |
| `brief` | TEXT | firewalled brief |
| `allowed_tools` | TEXT | JSON array (or comma list) of tool names |
| `max_turns` | INTEGER | turn cap |
| `wall_clock_timeout_s` | INTEGER | wall-clock budget seconds → `wall` |
| `final_status` | TEXT | `done`\|`failed`\|`escalated`\|`abandoned` (on `completed`) → `status` |
| `turns` | INTEGER | turns used (on `completed`) |
| `tools_used` | TEXT | JSON array; the reader reports its **count** |
| `escalated`, `summary_truncated`, `verification_errors` | INTEGER/TEXT | not surfaced |

> The real schema records **no per-handle `model`/`profile`**, so those card
> fields render blank against a real DB.

### `tool_audit` — governed tool calls (the real allow ledger)

Written by `scripts/post_tool_use.py`. Every executed (i.e. allowed) tool call.
The substrate's `pre_tool_use` hook returns allow/deny but **does not persist
the verdict**, so denied calls never reach this table — the Policy view folds
each row as an `ALLOW`, and `denyCount` is `0` against a real DB.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | epoch seconds |
| `session_id` | TEXT | |
| `pipeline` | TEXT | |
| `agent` | TEXT | requesting agent → `role` |
| `tool` | TEXT | the executed tool |
| `args_json`, `result_hash` | TEXT | not surfaced |
| `status` | TEXT | shown as the decision `reason` |

### `policy_audit` — optional verdict ledger (console / forward-compat)

Not written by the current substrate. When present **with rows** it wins over
`tool_audit` (so the demo can show a real `DENY` for the evaluator firewall,
HI #3). Empty (the real-DB case) → the Policy view folds from `tool_audit`.

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

### `meta` — key/value (GUI-side)

| key | meaning |
|---|---|
| `active_profile` | explicit console override for the active LMRouter profile |

## Active profile resolution

The **Models** view / trust strip show the active profile resolved as: an
explicit console choice (`meta.active_profile`, written when you pick one) →
else the `default` in `.musubi/llm.json` (the LMRouter source of truth, located
via `MUSUBI_LLM_CONFIG` or by walking up from `$MUSUBI_DB`) → else
`anthropic.default`.

## Mapping to the UI

| UI surface | source |
|---|---|
| Orchestrator cohort | `subagent_audit` folded per `handle_id` |
| Orchestrator counts (running/completed) | derived from `final_status` |
| Policy stream + allow/deny tallies | `policy_audit` if it has rows, else `tool_audit` |
| Audit ledger | `subagent_audit` (newest first, capped 120) |
| Models active profile | `meta.active_profile` → `.musubi/llm.json` `default` |
| Settings first-run status | runtime discovery of Python, `musubi`, `agent`, `.musubi/llm.json`, and audit DB |
| Driver chat | `chat_log` |
| Pipeline studio | authoring surface — default `feature-dev`, not from the DB |
| Run task (launcher) | GUI-side process overlay (`taskLauncher`), not from the DB — spawns one governed `agent "<task>"` child; orchestration state still arrives via `audit.db` |

## State shape (Rust → JSON → `buildViewModel`)

`load_state()` serialises to camelCase JSON matching the frontend domain state:
`subagents[]`, `policy[]`, `audit[]`, `chat[]`, `totalSpawned`, `totalDone`,
`allowCount`, `denyCount`, `activeProfile`, `pipeSteps[]`, … The frontend derives
all presentation (colours, chips) from `role`/`status`, so the backend only
supplies domain fields. See `musubi-data/src/lib.rs` and its tests.

The snapshot also carries `taskLauncher` — the on-demand launcher overlay
(`running`, `task`, `profile`, `startedAt`, `finishedAt`, `exitCode`,
`stdoutTail`, `stderrTail`, `error`), filled in by the Tauri process manager
(`src/lib.rs`), defaulting to idle. Stdout/stderr tails are bounded to the
newest 64 KiB per stream on UTF-8 boundaries. The launch recipe itself is the
pure, unit-tested `build_agent_launch_spec()` in `musubi-data`: the task as the
positional argument, `--profile` only when non-default, and
`--tool-surface agent`.
