# GUI/CLI Orchestrator Token Economics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist provider token usage and tool activity per orchestrator cycle, show it in both Console surfaces, and remove Musubi credit accounting from the live product.

**Architecture:** The shared Python worker loop produces one normalized `agent_cycles` row for every logical cycle across root, child, and pipeline workers. The Rust data core reads the rows compatibly from `musubi.db`; the JavaScript view model derives selected-session totals for a shared React component. Existing SQLite databases may retain ignored legacy columns, but new schemas and live contracts omit them.

**Tech Stack:** Python 3.11+, SQLite, pytest, Rust/rusqlite/serde, React 18, Node test runner, Vite.

## Global Constraints

- Economics uses input, cached-input subset, output, LM milliseconds, and tool names only.
- No money, credit, replay, or seed economics labels remain in live CLI/API/schema/Console contracts.
- Clamp cached input to `0..tokens_in`; never add it to input totals.
- Aggregate all effort-escalation vendor attempts into their logical cycle.
- Record the forced no-tools final LM call as its own cycle.
- Label usage `provider` only when every attempt supplied provider usage; otherwise `estimated`.
- Audit writes remain best-effort and cannot abort a run.
- Do not destructively rewrite existing databases or historical rows.
- Do not alter token budgets, output ceilings, cycle limits, worker ceilings, continuation policy, or hard invariants.
- Preserve `carl-jung-dashboard.html` and `vietnam-dashboard.html` as unrelated untracked user artifacts.

---

## File Map

- Python runtime: `musubi/agent/budget.py`, `musubi/agent/run.py`, `musubi/agent/subagent.py`, `musubi/agent/pipeline_runner.py`.
- Storage/API: `musubi/storage/db.py`, `musubi/storage/schema.sql`, `musubi/server.py`, `musubi/session/state.py`, `musubi/agent/boundary.py`, `musubi/tool_surface.py`.
- Rust projection: `gui/src-tauri/musubi-data/src/lib.rs`.
- Console: `gui/src/data/createSource.js`, `gui/src/data/TauriSource.js`, `gui/src/model/viewModel.js`, `gui/src/components/TokenEconomics.jsx`, `gui/src/views/Orchestrator.jsx`, `gui/src/views/Pipeline.jsx`.
- Tests: focused Python tests under `musubi/tests/`, Rust tests colocated in `lib.rs`, and `gui/src/model/viewModel.test.mjs`.
- Current docs: `README.md`, `docs/guide.md`, `docs/compression.md`, `CLAUDE.md`, `docs/roadmap.md`.

---

### Task 1: Remove live credit and replay-attribution contracts

**Files:**
- Modify: `musubi/agent/budget.py`
- Modify: `musubi/agent/run.py`
- Modify: `musubi/agent/vendors/base.py`
- Modify: `musubi/storage/db.py`
- Modify: `musubi/storage/schema.sql`
- Modify: `musubi/server.py`
- Modify: `musubi/session/state.py`
- Modify: `musubi/agent/boundary.py`
- Modify: `musubi/tool_surface.py`
- Modify: `musubi/tests/test_agent_budget.py`
- Modify: `musubi/tests/test_agent_loop.py`
- Modify: `musubi/tests/test_g3_observability.py`
- Modify: `musubi/tests/test_agent_turns.py`
- Modify: `musubi/tests/test_tool_surface.py`

**Interfaces:**
- Consumes: current token estimator and `TokenBudgetEnforcer`.
- Produces: `_build_token_budget(max_tokens, log)`, token-only `AgentRunStats.record_cycle`, credit-free storage/MCP signatures, and current schemas without replay attribution.

- [ ] **Step 1: Write failing absence tests**

Replace credit-specific tests with this contract and retain existing token-budget tests:

```python
def test_budget_module_exports_token_accounting_only() -> None:
    from agent import budget

    assert not hasattr(budget, "RATES")
    assert not hasattr(budget, "estimate_call_credits")
    assert not hasattr(budget, "BudgetEnforcer")
    assert not hasattr(budget, "BudgetExhaustedError")
```

Update `test_agent_loop.py` to call `_build_token_budget(4321, log)` and add an argparse test asserting `--max-credits 10` exits 2 as an unrecognized argument. Update storage tests to assert fresh `stage_metrics`/`agent_cycles` omit `credits`, fresh `agent_turns` omit `replay_messages`/`replay_tokens`, and a DB with manually added legacy columns still initializes and reads. Update tool-surface tests to assert the two credit aggregate tools are absent.

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_budget.py musubi/tests/test_agent_loop.py musubi/tests/test_g3_observability.py musubi/tests/test_agent_turns.py musubi/tests/test_tool_surface.py -q
```

Expected: FAIL because credit symbols, CLI/API fields, schema columns, tools, and replay fields still exist.

- [ ] **Step 3: Remove credit runtime code**

Keep only `BudgetStatus`, `estimate_tokens_from_chars`, `TokenBudgetEnforcer`, and `TokenBudgetExhaustedError` in `budget.py`. In `run.py`, remove the credit import, `max_credits`, CLI option, calculations, stats field, and log fields. Use:

```python
@dataclass
class AgentRunStats:
    cycles: int = 0
    lm_ms: int = 0
    tokens_in_estimate: int = 0
    tokens_out_estimate: int = 0

    def record_cycle(self, *, lm_ms: int, tokens_in: int, tokens_out: int) -> None:
        self.cycles += 1
        self.lm_ms += lm_ms
        self.tokens_in_estimate += tokens_in
        self.tokens_out_estimate += tokens_out
```

Change `_build_token_budget` to accept only `(max_tokens, log)`; preserve its environment fallback and zero-disables behavior unchanged. Remove `replay_messages` / `replay_tokens` accumulation, logging, and `_record_agent_turn` arguments from `run_agent`; provider input usage already includes that traffic without exposing an internal attribution category.
Update `musubi/agent/vendors/base.py` so the `LMResponse.usage` comment describes per-cycle token audit rather than a credit log.

- [ ] **Step 4: Remove live storage/API fields without destructive migration**

Remove credit columns/parameters/aggregators from both schemas, DB CRUD, `musubi_record_stage_metric`, and `musubi_record_agent_cycle`. Remove `musubi_session_credits` and `musubi_credits_since` from server and tool catalogs. Remove `total_credits` from session status. Remove replay columns/parameters from the current `agent_turns` schema and CRUD. Do not call `DROP COLUMN`.

Keep the stage metric migration as:

```python
_STAGE_METRICS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("model_family", "TEXT"),
)
```

- [ ] **Step 5: Run tests to verify GREEN**

Run the Step 2 command again.

Expected: PASS; legacy extra columns are tolerated but no live code reads or writes them.

- [ ] **Step 6: Commit**

```powershell
git add musubi/agent/budget.py musubi/agent/run.py musubi/agent/vendors/base.py musubi/storage/db.py musubi/storage/schema.sql musubi/server.py musubi/session/state.py musubi/agent/boundary.py musubi/tool_surface.py musubi/tests/test_agent_budget.py musubi/tests/test_agent_loop.py musubi/tests/test_g3_observability.py musubi/tests/test_agent_turns.py musubi/tests/test_tool_surface.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "refactor(agent): remove credit accounting"
```

---

### Task 2: Extend the per-cycle storage contract

**Files:**
- Modify: `musubi/storage/db.py`
- Modify: `musubi/storage/schema.sql`
- Modify: `musubi/server.py`
- Modify: `musubi/tests/test_g3_observability.py`

**Interfaces:**
- Consumes: append-only `agent_cycles` and `db.init_db`.
- Produces: `insert_agent_cycle(..., worker_id="root", tokens_in=0, cached_input_tokens=0, tokens_out=0, token_source="estimated", tool_calls_json=None)` and matching query/MCP rows.

- [ ] **Step 1: Write failing round-trip and migration tests**

Add a round-trip row with `worker_id="worker-7"`, `tokens_in=1200`, `cached_input_tokens=800`, `tokens_out=90`, `token_source="provider"`, and `tool_calls_json=json.dumps(["musubi_read_file", "musubi_grep"])`. Assert exact values. Add tests that cached 150/input 100 stores cached 100, `token_source="invoice"` raises `ValueError`, and `init_db` additively upgrades a minimal legacy `agent_cycles` table.

- [ ] **Step 2: Run test to verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_g3_observability.py -q
```

Expected: FAIL because the fields and migration do not exist.

- [ ] **Step 3: Add schema and migration columns**

Add to both schema definitions and `_AGENT_CYCLE_COLUMNS`:

```python
_AGENT_CYCLE_COLUMNS = (
    ("worker_id", "TEXT NOT NULL DEFAULT 'root'"),
    ("tokens_in", "INTEGER NOT NULL DEFAULT 0"),
    ("cached_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_out", "INTEGER NOT NULL DEFAULT 0"),
    ("token_source", "TEXT NOT NULL DEFAULT 'estimated'"),
    ("tool_calls_json", "TEXT"),
    ("lm_ms", "INTEGER NOT NULL DEFAULT 0"),
    ("text_chars", "INTEGER NOT NULL DEFAULT 0"),
    ("cycle_status", "TEXT NOT NULL DEFAULT 'ok'"),
    ("schema_version", "TEXT NOT NULL DEFAULT 'v1'"),
)
```

Call `_migrate_columns(conn, "agent_cycles", _AGENT_CYCLE_COLUMNS)` from `init_db`.

- [ ] **Step 4: Normalize DB and MCP inputs**

Validate and clamp before inserting:

```python
if token_source not in {"provider", "estimated"}:
    raise ValueError("token_source must be 'provider' or 'estimated'")
tokens_in = max(0, int(tokens_in))
tokens_out = max(0, int(tokens_out))
cached_input_tokens = max(0, min(tokens_in, int(cached_input_tokens)))
```

Expose and forward the same arguments through `musubi_record_agent_cycle`; keep its JSON error response behavior.

- [ ] **Step 5: Run test to verify GREEN**

Run the Step 2 command again. Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add musubi/storage/db.py musubi/storage/schema.sql musubi/server.py musubi/tests/test_g3_observability.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(audit): record per-cycle token usage"
```

---

### Task 3: Persist cycles from the shared worker loop

**Files:**
- Modify: `musubi/agent/run.py`
- Modify: `musubi/agent/subagent.py`
- Modify: `musubi/agent/pipeline_runner.py`
- Modify: `musubi/tests/test_agent_loop.py`
- Modify: `musubi/tests/test_subagent_orchestrator.py`
- Modify: `musubi/tests/test_spawn_pipeline.py`

**Interfaces:**
- Consumes: Task 2 `insert_agent_cycle`.
- Produces: `CycleTokenUsage`; `run_unit(..., audit_session_id=None, audit_worker_id="root", audit_stage=None)`; one row per logical cycle.

- [ ] **Step 1: Write failing usage aggregation tests**

Use two `LMResponse` attempts with provider usage. Assert `_cycle_token_usage` sums 100+120 input, 20+30 output, and clamps cached 40 + min(200,120) to 160. Add a mixed test where one attempt has no usage and assert source `estimated`.

- [ ] **Step 2: Write failing persistence tests**

Run `_run_loop` against a temporary initialized DB/session and fake router. Assert root identity and tool-name JSON. Monkeypatch `db.insert_agent_cycle` to raise and assert the model text still returns with `cycle audit write failed` logged. Add a forced-final test asserting indices `[0, 1]`, statuses `ok/final`, and an empty final tool list.

- [ ] **Step 3: Run tests to verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py -q
```

Expected: FAIL because typed usage and persistence do not exist.

- [ ] **Step 4: Implement typed usage and token-only logs**

Add:

```python
@dataclass(frozen=True)
class CycleTokenUsage:
    tokens_in: int
    cached_input_tokens: int
    tokens_out: int
    source: str
```

Replace `_cycle_token_counts` with `_cycle_token_usage`. Aggregate every effort attempt, clamp each cached count to its attempt input, and mark the aggregate estimated if any attempt uses fallback. Update budget charging/stats/logs from the typed fields.

- [ ] **Step 5: Add a best-effort cycle recorder**

Implement `_safe_record_agent_cycle` to initialize/write `compression_db_path`, return immediately when path/session is absent, JSON-encode only tool names, and catch every exception with the exact log prefix `cycle audit write failed`. Pass explicit `audit_session_id`, `audit_worker_id`, and `audit_stage` through `run_unit` and `_run_loop`. Record after response usage is known; record forced final at index `max_cycles` with status `final`.

The DB call must use:

```python
db.insert_agent_cycle(
    session_id,
    stage,
    attempt=1,
    cycle_idx=cycle_idx,
    started_at=started_at,
    ended_at=ended_at,
    db_path=db_path,
    worker_id=worker_id,
    lm_ms=lm_ms,
    tokens_in=usage.tokens_in,
    cached_input_tokens=usage.cached_input_tokens,
    tokens_out=usage.tokens_out,
    token_source=usage.source,
    tool_calls_json=json.dumps(tool_names),
    text_chars=text_chars,
    cycle_status=cycle_status,
)
```

- [ ] **Step 6: Thread explicit identities through callers**

Use root `(parent_session_id, "root", "agent")`; subagent `(spawn_args.parent_session_id, handle_id, role)`; pipeline stage `(psid, handle_id, stage)`. Add capture tests for the subagent and pipeline keyword arguments. Never parse `_worker_log_label` for identity.

- [ ] **Step 7: Run tests to verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_g3_observability.py -q
```

Expected: PASS, including effort retry, forced-final, identity, and failure-isolation cases.

- [ ] **Step 8: Commit**

```powershell
git add musubi/agent/run.py musubi/agent/subagent.py musubi/agent/pipeline_runner.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_spawn_pipeline.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(agent): persist logical cycle economics"
```

---

### Task 4: Load compatible cycles in Rust

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`

**Interfaces:**
- Consumes: Task 2 cycle rows from `musubi.db`.
- Produces: `State.agent_cycles: Vec<AgentCycle>` serialized with camelCase fields and parsed `toolNames`.

- [ ] **Step 1: Write failing Rust tests**

Insert a current row and assert session, worker, input 1200, cached 800, output 90, provider source, and two tool names. Add a minimal legacy-table test with defaults. Add malformed `not-json` and legacy `[{"name":"old_tool","ok":true}]` cases; expect empty and `old_tool` respectively.

- [ ] **Step 2: Run test to verify RED**

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml agent_cycles -- --nocapture
```

Expected: compile/test failure because `AgentCycle` is absent.

- [ ] **Step 3: Add type and compatible reader**

Add:

```rust
#[derive(Serialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AgentCycle {
    pub session_id: String,
    pub stage: String,
    pub worker_id: String,
    pub cycle_idx: i64,
    pub lm_ms: i64,
    pub tokens_in: i64,
    pub cached_input_tokens: i64,
    pub tokens_out: i64,
    pub token_source: String,
    pub tool_names: Vec<String>,
    pub cycle_status: String,
}
```

Add the vector to `State`. Read from `pipeline_state_conn.unwrap_or(conn)`. Build SELECT defaults with `column_exists`; missing numbers are 0, worker is root, source is estimated, and tool JSON is null. Parse both the current string array and legacy object array defensively. Remove replay fields from `AgentTurn` and its SELECT.

- [ ] **Step 4: Update embedded current schema and verify**

Mirror Task 2's cycle table and Task 1's credit/replay-free schemas in Rust fixtures. Run:

```powershell
cargo fmt --manifest-path gui/src-tauri/musubi-data/Cargo.toml -- --check
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml
```

Expected: format check and all Rust tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add gui/src-tauri/musubi-data/src/lib.rs
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(gui): load orchestrator cycle economics"
```

---

### Task 5: Present economics in Orchestrator and Pipeline Studio

**Files:**
- Create: `gui/src/components/TokenEconomics.jsx`
- Modify: `gui/src/data/createSource.js`
- Modify: `gui/src/data/TauriSource.js`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/model/viewModel.test.mjs`
- Modify: `gui/src/views/Orchestrator.jsx`
- Modify: `gui/src/views/Pipeline.jsx`

**Interfaces:**
- Consumes: camelCase `state.agentCycles`.
- Produces: `driverSummary.economics` and `pipeRunSummary.economics` shaped as `{cycles,inputTokens,cachedInputTokens,outputTokens,lmMs,tokenSource,tools}`.

- [ ] **Step 1: Replace replay tests with failing economics tests**

Create two selected-session cycle rows totaling input 1500, cached 700, output 120, LM 150, grep×2, read_file×1; add an unrelated 9999-token row and assert it is excluded. Add pipeline-session, mixed estimated/provider, and empty-state tests.

- [ ] **Step 2: Run test to verify RED**

```powershell
node --test gui/src/model/viewModel.test.mjs
```

Expected: FAIL because `agentCycles` and `economics` do not exist.

- [ ] **Step 3: Add state and pure aggregation**

Add `agentCycles: []` to both sources and `agentCycles` to `STATE_KEYS`. Replace replay helpers with `economicsForSession(agentCycles, sessionId)`: filter exact session, sum non-negative fields, clamp cached per row, mark summary estimated when any row is not provider, and count tool names in insertion order. Root uses selected `parentSession`; pipeline uses selected `sessionId`.

- [ ] **Step 4: Create and render the shared component**

`TokenEconomics.jsx` renders four labeled values—input, cached input, output, LM time—plus audited cycle count, token source, and tool counts. It performs no pricing calculation and never adds cached input to input. Render it in both views and remove replay/seed copy and tooltip from Orchestrator.

- [ ] **Step 5: Verify tests and build**

```powershell
node --test gui/src/model/viewModel.test.mjs
npm run build
```

Run the build from `gui`. Expected: Node tests PASS and Vite exits 0.

- [ ] **Step 6: Commit**

```powershell
git add gui/src/components/TokenEconomics.jsx gui/src/data/createSource.js gui/src/data/TauriSource.js gui/src/model/viewModel.js gui/src/model/viewModel.test.mjs gui/src/views/Orchestrator.jsx gui/src/views/Pipeline.jsx
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(gui): show session token economics"
```

---

### Task 6: Align docs, roadmap, and full verification

**Files:**
- Modify: `README.md`
- Modify: `docs/guide.md`
- Modify: `docs/compression.md`
- Modify: `CLAUDE.md`
- Modify: `docs/roadmap.md`
- Modify: `artifacts/agent/standalone_boundary_status.json`
- Modify: `artifacts/agent/standalone_boundary_report.html`

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: token-only current documentation and a completed roadmap track.

- [ ] **Step 1: Update current docs and roadmap**

Document per-cycle input, cached subset, output, LM time, and tools in README/guide. Remove optional-credit and replay-seed explanations. Make compression docs token-only. In `CLAUDE.md`, quantify `tokens, cycles, milliseconds` and name `TokenBudgetEnforcer + per-call token accounting`. Update both checked-in standalone-boundary artifacts to describe token-only enforcement. Move this roadmap item from Backlog to Completed Tracks, link this plan, and state that legacy DB columns are ignored rather than destructively dropped.

- [ ] **Step 2: Prove retired terms are absent from live contract paths**

```powershell
rg -n -i "estimated_credits|max_credits|session_credits|credits_since|total_credits|replay_tokens|replay_messages|seed cost|seed tok" musubi gui/src gui/src-tauri README.md docs/guide.md docs/compression.md CLAUDE.md docs/roadmap.md --glob '!gui/node_modules/**' --glob '!gui/dist/**'
```

Expected: no matches. Historical spec/plan evidence is outside this current-contract scan.

- [ ] **Step 3: Run focused Python verification**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_budget.py musubi/tests/test_agent_loop.py musubi/tests/test_agent_turns.py musubi/tests/test_g3_observability.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_tool_surface.py musubi/tests/test_mechanical_gate.py musubi/tests/test_schema_sync.py -q
```

Expected: all selected tests PASS; only pre-existing dependency/cache warnings are acceptable.

- [ ] **Step 4: Run Rust, JavaScript, and build verification**

```powershell
cargo fmt --manifest-path gui/src-tauri/musubi-data/Cargo.toml -- --check
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml
node --test gui/src/model/viewModel.test.mjs gui/src/data/TauriSource.test.mjs
npm run build
```

Run the last command from `gui`; run the others from repo root. Expected: every command exits 0.

- [ ] **Step 5: Verify diff and artifacts**

```powershell
git diff --check
git status --short
```

Expected: clean diff check and only the two unrelated dashboard files remain untracked outside scoped changes.

- [ ] **Step 6: Commit docs**

```powershell
git add README.md docs/guide.md docs/compression.md CLAUDE.md docs/roadmap.md artifacts/agent/standalone_boundary_status.json artifacts/agent/standalone_boundary_report.html
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "docs: document token-only orchestrator economics"
```

- [ ] **Step 7: Final branch verification**

```powershell
git log --oneline --decorate origin/dev..HEAD
git diff --stat origin/dev...HEAD
git diff --check origin/dev...HEAD
git status --short --branch
```

Expected: design/plan/implementation commits are present, the scoped diff is clean, and both user dashboard artifacts remain untracked.
