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
the append-only audit tables. The app writes only GUI-side `chat_log` and `meta`
state directly. Explicit driver actions launch the standalone CLI, which
performs governed mutations through the MCP substrate; the GUI never writes
append-only audit rows itself.

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
| `args_json`, `result_hash` | TEXT | raw values are never surfaced; only a sanitized `skill_id` from successful `musubi_get_skill` calls may be projected as provenance |
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
| `surface` | TEXT | `orchestrator` \| `pipeline` |
| `chat_id` | TEXT | owning GUI session; new sessions preserve prior rows |

The console groups non-empty Orchestrator `chat_id` values into the serialized
`orchestratorSessions[]` index. A freshly minted ID does not appear until its
first `chat_log` row exists. Each summary carries the first and latest user
request, first/latest row timestamps, and root/worker counts; selecting a
summary while the driver is idle promotes the requested exact ID without
deleting any rows. While the driver is busy, selection changes only
`viewedOrchestratorChatId`: `orchestratorChatId`, `driverStatus.chatId`, and
nonce ownership remain unchanged, and the viewed session is read-only until
the driver is idle.

Legacy `surface = 'pipeline'` rows remain readable for compatibility. Pipeline
Studio no longer writes chat rows or launches a process; new pipeline runs use
an Orchestrator chat ID.

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
| Orchestrator runtime graph | selected root turn, `subagent_audit`, and Orchestrator-scoped pipeline envelopes/stages |
| Orchestrator runtime logs | `agent_cycles`, safe `toolEvidence`, policy rows, and lifecycle evidence filtered by selected node |
| Orchestrator skill provenance | successful `musubi_get_skill` rows only, with sanitized identifiers and exact worker correlation only when `(session, role)` is unambiguous |
| Policy stream + allow/deny tallies | `policy_audit` if it has rows, else `tool_audit` |
| Audit ledger | `subagent_audit` (newest first, capped 120) |
| Models active profile | `meta.active_profile` → `.musubi/llm.json` `default` |
| Settings first-run status | runtime discovery of Python, `musubi`, `agent`, `.musubi/llm.json`, and audit DB |
| Driver chat | `chat_log` |
| Pipeline Studio builder | registered deterministic recipes under `.github/pipelines/`, resolved preset/agent contracts, and local unsaved draft state |
| Orchestrator pipeline runs | finalized/live `pipeline_runs` from the read-only sibling `musubi.db`, joined through the `pipeline:<name>` audit envelope. The outer driver's durable `pipeline_runs.chat_id` scopes an active or halted run before `agent_turns` exists; completed turns use `agent_turns.chat_id` as a compatible fallback. Child stages come from `subagent_audit.parent_session_id = pipeline_runs.session_id`. Only an envelope handle is a displayed run; the outer driver session row is excluded. |

## State shape (Rust → JSON → `buildViewModel`)

`load_state()` serialises to camelCase JSON matching the frontend domain state:
`subagents[]`, `policy[]`, `audit[]`, `chat[]`, `agentCycles[]`,
`toolEvidence[]`, `pipelineRuns[]`, `pipelineBuilderCatalog`, `totalSpawned`,
`totalDone`, `allowCount`, `denyCount`, and `activeProfile`. The frontend
derives runtime graph and log presentation from these domain fields. Raw tool
arguments and results never cross this boundary; ambiguous tool evidence is
left unassigned rather than guessed. See `musubi-data/src/lib.rs` and its tests.

The snapshot also exposes `orchestratorChatId`,
`viewedOrchestratorChatId`, legacy `pipelineChatId`,
`orchestratorSessions[]`, and
`driverStatus.chatId`. `orchestratorChatId` is the active Orchestrator session
and owner of future writes; `viewedOrchestratorChatId` is the optional
navigation target; and `driverStatus.chatId` is the exact owner of the live or
retained process. Each `agentTurns[]` item includes a `request` joined from
`sessions.request` through `parent_session_id`; this is presentation metadata
for the root worker and does not alter its lifecycle. Current surface run lists
compare these complete IDs; the `gui-orchestrator-` and `gui-pipeline-`
prefixes are classification fallbacks for legacy rows only.
When the sibling state database is absent, the audit snapshot remains usable
and `pipelineRuns` is empty rather than synthesizing historical pipeline cards.

## Project and session boundary

The canonical project root owns the shared workspace, dependencies, databases,
and one mutating writer slot. Exact chat IDs own conversation replay, process
status, cancellation, task metadata, and retained process logs. Every session
for a project launches with the same project root as its working directory; a
session never owns a filesystem root, worktree, clone, virtualenv, or container.

Settings may replace the canonical project root through the persisted Console
workspace preference. Applying a new existing directory restarts the process
before any state is reopened, creates/opens that project's
`.musubi/data/audit.db`, and exports the same root to the driver as
`MUSUBI_WORKSPACE`. A running driver blocks the switch.

`driverStatus.chatId` is the exact owner of the live or retained runtime state.
The frontend renders that state only when it matches the current surface's full
chat ID. Surface names remain useful labels but are not ownership boundaries.

For an Orchestrator send, the optional requested session ID is validated and
atomically promoted before both the `chat_log` insertion and driver launch.
If another process wins the runtime between viewing and sending, the busy race
is fail-closed: no message is inserted and no driver is launched for a
different session.
