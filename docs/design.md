# Design — CopilotHarness

> Architecture and schemas of the **substrate** layer.
> Direction and discipline → [`docs/harness-direction.md`](./harness-direction.md).
> Forward-looking plan and active work → [`docs/roadmap.md`](./roadmap.md).
> Repo-wide rules → [`/CLAUDE.md`](../CLAUDE.md).

This document is the canonical reference for the **durable substrate** —
the parts of CopilotHarness expected to outlive any specific model
release. Ephemeral structures (the 4-stage pipeline shape, cycle-loop
guards, sub-agent-for-exploration split, preamble blocks) are NOT
authoritatively described here; they live in code and are
[harness-tier-tagged](./harness-direction.md) so their expiration is
visible.

---

## One Sentence

CopilotHarness is a **governance layer** for agentic
software-engineering work in VS Code. It provides firewall, audit,
validator, budget, and skill-injection primitives that survive model
releases. It is not a wrapper around the model's intelligence.

**Copilot Chat reasons. CopilotHarness controls the environment.**
Zero LLM calls inside the harness.

---

## Substrate vs ephemeral, in one table

| Layer | Component | harness-tier |
|---|---|---|
| Storage | `storage/audit.db` (SQLite) — sessions, stage_outputs, stage_metrics, pipeline_runs, subagent_audit, conversation_messages, orchestrator_turns | **substrate** |
| Storage | `.harness/sessions/<sid>/*.md` — append-only stage artefacts | **substrate** |
| Storage | `.github/skills/<name>/SKILL.md` — fat-skills catalog | **substrate** |
| Storage | `.github/memory/{MEMORY,architecture,failure-patterns}.md` — 3-tier markdown memory | **substrate** |
| Verification | Firewall: `_STAGE_PERMISSIONS` (`validation/context_builder.py` + `pipeline.ts`) | **substrate** |
| Verification | Policy engine: `scripts/policy_engine.py` `PIPELINE_POLICIES` (fail-closed) | **substrate** |
| Verification | Validator: `validation/verifier.py` schema + injection scan | **substrate** |
| Cost control | `BudgetEnforcer` + per-call charges (`pipelineBudgetCore.ts`) | **substrate** |
| Cost control | `pipeline.yaml::max_credits` + `warn_at` | **substrate** |
| Interface | MCP tool catalog (`harness_*`) | **substrate** |
| Interface | Hooks (SessionStart / PreToolUse / PostToolUse) | **substrate** |
| Routing | Zero-cost routing (`/<pipeline>` → pipeline; anything else → butler) | **substrate** |
| Orchestration | 4-stage pipeline (planner / designer / coder / reviewer) | **ephemeral** |
| Orchestration | Sub-agent-for-exploration split (explorer / investigator / reviewer-aux on haiku) | **ephemeral** |
| Orchestration | Correction loop + `validation_feedback` retry | **ephemeral** |
| Orchestration | Cycle-loop guards (`CONSECUTIVE_EMPTY_CYCLE_LIMIT`, salvage) | **ephemeral** |
| Orchestration | `runAgentLM` tool preamble blocks (path-rules / empty-project / workspace-root) | **ephemeral** |
| Orchestration | `materializeCoderFiles` + JSON manifest contract | **ephemeral** |
| Orchestration | `preSpawnAndSplice` fanout | **ephemeral** |
| Orchestration | `runStageReviewGate` 4-button UX | **ephemeral** |

See [`docs/harness-direction.md`](./harness-direction.md) for each
ephemeral component's `expires-when:` and `cost-lever:` annotations.

---

## Why MCP, not CLI bridge

The harness must be the environment Copilot **operates within** — not
a helper tool the developer bridges manually. With MCP stdio:

- Copilot agents call `harness_read_stage` → harness enforces firewall, injects skills
- Copilot agents call `harness_write_stage` → harness validates before storing
- Agents cannot skip the harness — it is the only path to read inputs and write outputs

The MCP stdio server runs **entirely locally** as a subprocess. No
network calls. No `api.githubcopilot.com`. The corporate firewall is
irrelevant.

VS Code reads `.vscode/mcp.json` → spawns the Python MCP server →
Copilot Chat agents call `harness_*` tools.

---

## How it works (substrate flow)

The substrate provides a single contract for any agent-shaped consumer:

```
1. agent calls harness_get_active_session  → resume or create
2. agent calls harness_new_session         → new pipeline_runs row, locks agent versions
3. agent calls harness_read_stage          → harness applies firewall, injects skill + memory
4. <agent reasons> (Copilot Chat side — NOT in harness)
5. agent calls harness_write_stage         → harness validates, runs injection scan, writes append-only
6. agent calls harness_get_status          → next pending stage
7. <repeat from step 3 for next stage>
```

Each `harness_*` call writes audit rows; each LM call writes a
`stage_metrics` row. The substrate is the contract + audit + firewall;
the orchestration shape (whether there are 4 stages or 1) is ephemeral
on top.

---

## Audit DB schema (`storage/audit.db`)

The audit DB is the durable record of every session, stage, LM call,
sub-agent spawn, and conversation message. It survives model releases
and pipeline-shape changes.

```sql
-- One row per pipeline session
CREATE TABLE sessions (
  session_id   TEXT PRIMARY KEY,
  request      TEXT NOT NULL,
  status       TEXT NOT NULL,                -- 'active' | 'paused' | 'success' | 'aborted' | 'escalated'
  started_at   REAL NOT NULL,
  ended_at     REAL,
  agent_versions_json TEXT NOT NULL
);

-- One row per stage attempt (per chunk_id when chunked)
CREATE TABLE stage_outputs (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id     TEXT NOT NULL,
  stage          TEXT NOT NULL,
  chunk_id       TEXT,
  attempt        INTEGER NOT NULL,
  status         TEXT NOT NULL,              -- 'pending' | 'in_progress' | 'complete' | 'failed'
  output         TEXT,                       -- JSON
  user_hint      TEXT,
  schema_version TEXT NOT NULL DEFAULT 'v1',
  FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

-- One row per pipeline run for cross-session analytics
CREATE TABLE pipeline_runs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id      TEXT NOT NULL,
  pipeline_name   TEXT NOT NULL,
  started_at      REAL NOT NULL,
  ended_at        REAL,
  final_status    TEXT,                      -- 'success' | 'aborted' | 'escalated'
  escalated       INTEGER NOT NULL DEFAULT 0,
  chunked         INTEGER NOT NULL DEFAULT 0,
  chunk_count     INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

-- One row per runAgentLM call (per cycle once L2 ships)
CREATE TABLE stage_metrics (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id          TEXT NOT NULL,
  stage               TEXT NOT NULL,
  chunk_id            TEXT,
  attempt             INTEGER NOT NULL,
  started_at          REAL NOT NULL,
  ended_at            REAL,
  tokens_in_estimate  INTEGER NOT NULL DEFAULT 0,
  tokens_out_estimate INTEGER NOT NULL DEFAULT 0,
  lm_ms               INTEGER NOT NULL DEFAULT 0,
  tool_count          INTEGER NOT NULL DEFAULT 0,
  tool_failures       INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

-- One row per sub-agent spawn + completion
CREATE TABLE subagent_audit (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_session  TEXT NOT NULL,
  parent_agent    TEXT NOT NULL,
  handle_id       TEXT NOT NULL,
  role            TEXT NOT NULL,
  event           TEXT NOT NULL,             -- 'spawn' | 'complete'
  brief           TEXT,
  final_status    TEXT,
  escalated       INTEGER,
  turns           INTEGER,
  tools_used      TEXT,                      -- JSON array
  summary_truncated INTEGER,
  verification_errors TEXT,                  -- JSON array
  ts              REAL NOT NULL
);

-- One row per butler turn (table name kept as `orchestrator_turns`
-- for backwards compatibility; rename is a future PR with migration)
CREATE TABLE orchestrator_turns (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id              TEXT NOT NULL,
  parent_session_id    TEXT NOT NULL,
  started_at           REAL NOT NULL,
  ended_at             REAL,
  model_family         TEXT NOT NULL,
  cycles               INTEGER NOT NULL DEFAULT 0,
  tokens_in_estimate   INTEGER NOT NULL DEFAULT 0,
  tokens_out_estimate  INTEGER NOT NULL DEFAULT 0,
  lm_ms                INTEGER NOT NULL DEFAULT 0,
  total_ms             INTEGER NOT NULL DEFAULT 0
);

-- One row per chat message (butler turn + sub-agent results)
CREATE TABLE conversation_messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id     TEXT NOT NULL,
  role        TEXT NOT NULL,                 -- 'user' | 'assistant' | 'tool' | 'system'
  content     TEXT NOT NULL,
  created_at  REAL NOT NULL
);
```

**Planned (L2):** `agent_cycles` table — one row per `sendRequest` cycle.
Tracks per-cycle credit + tool-call patterns so dissolution decisions
can be data-driven. See [`docs/roadmap.md`](./roadmap.md) § Track A.2.

---

## MCP tools

Names + one-line purpose. The full Python signatures live in
`copilot-harness/server.py`.

### Session lifecycle

| Tool | Purpose |
|---|---|
| `harness_get_active_session` | Crash recovery — returns interrupted session or null |
| `harness_clear_active_session` | Clear the active-session pointer (preserves stage outputs + audit) |
| `harness_new_session` | Start pipeline, lock agent versions |
| `harness_get_status` | Pipeline stage summary |
| `harness_increment_attempt` | Bump attempt counter for retry |
| `harness_pause_session` / `harness_resume_session` | Review-gate pause / resume |
| `harness_finalize_pipeline_run` | Record final_status into `pipeline_runs` |

### Stage I/O (firewall is enforced here)

| Tool | Purpose |
|---|---|
| `harness_read_stage` | Read prior stages with firewall + skill + memory injection. The firewall is the verification primitive — never bypass. |
| `harness_write_stage` | Validate output schema + run injection scan + append to `stage_outputs` |

### Skills + memory

| Tool | Purpose |
|---|---|
| `harness_get_skill` | Load `SKILL.md` on demand (butler path) |
| `harness_list_skills` | Per-caller filtered skill catalog |
| `harness_get_reference` | Load reference document under a skill |
| `harness_get_memory_context` | Return tier-1 index + tier-2 available list |
| `harness_get_memory_entry` | Load a specific tier-2 entry |
| `harness_append_failure_pattern` | Record a pattern (used by distillation triggers) |
| `harness_compact_memory` | Prune `failure-patterns.md` when > 5 KB |
| `harness_distill_session` | Mine a failed session into memory tier-2 |
| `harness_query_sessions` | Cross-session substring search |

### Sub-agents

| Tool | Purpose |
|---|---|
| `harness_list_subagents` | Return spawn allow-list for the calling main agent |
| `harness_spawn_subagent` | Validate spawn (policy ∩ caller tools) + insert sub-session row, return handle |
| `harness_get_subagent_context` | Return firewalled `{brief, role, role_skill, allowed_tools}` for a handle |
| `harness_complete_subagent` | Record terminal result; verify summary cap + secrets / injection / schema check |
| `harness_await_subagent` | Poll until terminal or wall-clock kill; return summary + structured + tools_used + turns + escalated |
| `harness_query_subagent_events` | Read durable audit log of sub-agent spawns + completions |
| `harness_delete_subsessions_for_parent` | Housekeeping pruner — delete terminal sub_sessions rows (audit table preserved) |

### Telemetry

| Tool | Purpose |
|---|---|
| `harness_record_stage_metric` | Per LM-call row (lm_ms, tokens, tool counts) |
| `harness_query_stage_metrics` | All stage_metrics rows for a session |
| `harness_record_orchestrator_turn` | Per butler turn row (tool name kept as `orchestrator_turn` for back-compat) |
| `harness_query_orchestrator_turns` | All butler turns for a chat_id (tool name kept as `orchestrator_turns` for back-compat) |

### Conversation (butler)

| Tool | Purpose |
|---|---|
| `harness_append_message` | Append a row to `conversation_messages` |
| `harness_get_conversation` | Token-budgeted, chronological history (newest-first truncation) |

### Verification (deterministic, NOT LLM)

| Tool | Purpose |
|---|---|
| `harness_run_lint` | Run `ruff` |
| `harness_run_typecheck` | Run `mypy` |
| `harness_run_tests` | Run `pytest` |
| `harness_run_hook` | Execute `hooks.json` lifecycle hook |

---

## Hooks

| Hook | Script | Behavior |
|---|---|---|
| `SessionStart` | `scripts/session_start.py` | Run `baseline_checks` from `pipeline.yaml` |
| `PreToolUse` | `scripts/pre_tool_use.py` | Policy gate — exit 0 allow, 1 deny |
| `PostToolUse` | `scripts/post_tool_use.py` | SQLite audit log to `storage/audit.db` |

`on-eval-fail` and `on-escalate` are reserved — not wired yet.

**Rule:** "Never send an LLM to do a linter's job." Deterministic checks
belong in hooks.

---

## Context firewall

Defined in `copilot-harness/validation/context_builder.py`:

```python
_STAGE_PERMISSIONS = {
    "planner":    {"request"},
    "designer":   {"request", "plan"},
    "coder":      {"request", "plan", "design", "review"},
    "reviewer":   {"code"},                # Hard Invariant #3 — evaluator firewall
    # /code-review pipeline
    "scoper":     {"request"},
    "finder":     {"scope"},
    "synthesizer":{"findings"},
}
```

When an agent calls `harness_read_stage`, the harness returns only the
intersection of the agent's allowed set and the requested set. Anything
outside is silently filtered. **This is the substrate's primary
verification primitive** — it survives any pipeline shape change.

The reviewer's `{"code"}` permission is the article-aligned "evaluator
under the firewall" pattern. Tools the reviewer calls on its own (`copilot_readFile` etc.)
are pull-on-demand and don't violate the firewall (the harness only
controls what it pushes, not what the model chooses to read).

---

## Memory architecture (3-tier markdown)

Plain text in the working directory, the way the article praises.

```
.github/memory/
├── MEMORY.md                  # tier-1: index + load order
├── architecture.md            # tier-2: always-loaded core
└── failure-patterns.md        # tier-3: recurring failures, distilled
```

The harness injects MEMORY.md content into pipeline-mode agents at
`harness_read_stage` time. Tier-2 entries are listed in the index and
loaded by `harness_get_memory_entry` on demand.

Compaction (`harness_compact_memory`) trims `failure-patterns.md` when
it exceeds 5 KB. Distillation (`harness_distill_session`) appends new
patterns from failed sessions.

No vector DB. No embeddings. No reranker. The model reads markdown
natively.

---

## Pipeline YAML format

`.github/pipelines/<name>/pipeline.yaml`:

```yaml
name: feature-dev
level: 1                       # 0 / 1 / 2 — per CLAUDE.md decision rules

agents:
  - role: planner
    agent: agents/planner.agent.md
    write_stage: plan
    read_stages: [request]
  - role: designer
    agent: agents/designer.agent.md
    write_stage: design
    read_stages: [request, plan]
  # ... etc

correction_rules:
  escalate_on_critical: true
  escalate_on_count:
    critical: 1
    high: 2

max_credits: 50                # Phase J.3 budget enforcement
warn_at: 0.8                   # Warn at 80% of max_credits

context_cap: 50000             # Optional override for the default 50k context cap
```

The pipeline.yaml is the only place the 4-stage shape lives. When the
pipeline collapses to fewer stages in a future model release, this is
where the change lands.

---

## Policy engine

`scripts/policy_engine.py::PIPELINE_POLICIES` is a fail-closed allow-list:

```python
PIPELINE_POLICIES = {
    ("feature-dev", "planner"):  {"allowed_subagent_roles": []},
    ("feature-dev", "designer"): {"allowed_subagent_roles": ["explorer"]},
    ("feature-dev", "coder"):    {"allowed_subagent_roles": ["explorer", "investigator"]},
    ("feature-dev", "reviewer"): {"allowed_subagent_roles": ["reviewer-aux"]},
    # ...
}
```

Unknown `(pipeline, agent)` combinations are denied. **Never relax to
fail-open.** New pipelines OR new agents require an explicit entry.

---

## Budget enforcement (cost-control substrate)

`pipelineBudgetCore.ts::BudgetEnforcer` tracks per-session credit
spend. Two thresholds:

1. **warn_at** (default 0.8): one-time warning when usage crosses 80%
2. **max_credits** (default per-pipeline): pipeline halts before next
   stage when projected total would exceed

Per-call accounting is fired from `runAgentLM`:

```typescript
const cost = estimateCallCredits(model.family, inputTokens, outputTokens);
const status = enforcer.charge(cost);
if (status === "halt") throw new BudgetExhaustedError(...);
```

The rate table (`RATES` in `pipelineBudgetCore.ts`) maps `model.family`
to per-million-token rates. Unknown families fall through to
`UNKNOWN_FAMILY_RATE` — a deliberately pessimistic Sonnet-level fallback.

Budget enforcement is **substrate**: it doesn't depend on the pipeline
shape; any pipeline registering an enforcer gets the same protection.

---

## Zero LLM calls in the harness

Hard Invariant #1 is the line that keeps this codebase from accreting
unnecessary structure. The harness orchestrates, validates, audits, and
controls cost. It does NOT think.

What this means in practice:

- No `anthropic`, `openai`, `google-generativeai` etc. imports in
  `copilot-harness/` (Python)
- The TS extension calls `vscode.lm.sendRequest` only from
  `runAgentLM`, `runOrchestrator`, and sub-agent runners — NEVER from
  harness MCP tool handlers
- Routing is regex-based (`/<pipeline-name>` matches a slash command,
  anything else goes to butler) — no LLM call to decide
- Pattern detection is regex / frequency-based, not LLM-judged

**The harness is the substrate. The model is the workload. They don't
mix.**

---

## Boundaries

Things this harness deliberately does NOT do:

- **Tracks model identity / safety.** The model self-identifies via its
  system prompt; the harness does not enforce or rewrite this.
- **Manages chat panel UI beyond the participant contract.** VS Code
  owns the chat panel; we contribute to it.
- **Runs arbitrary user code.** The coder writes file contents via the
  JSON manifest contract (ephemeral); future A2 would let the model
  write directly, with the firewall and audit as the substrate guard.
- **Provides a sandbox for executed code.** That's a runtime concern
  (Lee Hanchung's "Agent Runtime" article); CopilotHarness is the
  harness layer, not the runtime layer. If terminal access lands, the
  runtime substrate (Firecracker-class isolation) becomes a design
  question.

---

## See also

- [`docs/harness-direction.md`](./harness-direction.md) — the lens this
  document is written through. Substrate vs ephemeral discipline,
  three tracks, quarterly review process.
- [`docs/roadmap.md`](./roadmap.md) — what's next, the Dissolution
  Candidates list, active branches.
- [`docs/usecase-diagram.md`](./usecase-diagram.md) — user-facing
  capability map (what `/feature-dev` does from a developer's POV).
- [`docs/class-diagram.md`](./class-diagram.md) — TS class +
  Python dataclass shapes with `harness-tier` annotations.
- [`docs/memory.md`](./memory.md) — memory architecture detail.
- [`/CLAUDE.md`](../CLAUDE.md) — repo-wide rules, conventions, Hard
  Invariants.
