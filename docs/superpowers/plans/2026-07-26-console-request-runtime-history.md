# Console Request Runtime History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve every request and its agent topology inside a Console session, with durable whole-request and agent-scoped raw logs plus graph-to-detail navigation.

**Architecture:** The Console creates a stable `request_id` before process launch and passes it to the Python driver. Tauri persists line-framed host/root/worker records in an append-only SQLite ledger, while `agent_turns.request_id` joins raw logs to the structured audit graph. The React presentation layer projects all turns in a chat into request subgraphs and uses one mutually exclusive graph/request/agent workspace.

**Tech Stack:** Python 3.11+, SQLite, Rust/Tauri 2, React 18, plain CSS, Node test runner, pytest.

## Global Constraints

- The substrate and Console make zero LLM calls; the existing `LMRouter` remains the only model inject point.
- Runtime log rows are append-only and are never reassigned to another request or worker.
- `request_id` is generated before launch; no timestamp-nearest or free-text ownership inference.
- Existing `parent_session_id` remains the worker-governance relationship key.
- Old DBs remain readable; pre-feature requests show an explicit transcript-unavailable state.
- One center workspace exists at a time: graph, request detail, or agent detail.
- Request continuation edges are dashed orange; summon edges are solid blue.
- Use `IBM Plex Mono`, `#0d1117`, `#0c131c`, `#ff9b3d`, and the approved 11–20 px type scale.
- Preserve the unrelated untracked `vietnam-weather.html`.

---

## File Structure

### New files

- `musubi/agent/runtime_log.py` — line-framed structured stderr writer and worker-scope context.
- `musubi/tests/test_runtime_log.py` — protocol, buffering, and attribution tests.
- `gui/src/model/runtimeHistory.js` — pure request graph and scoped-log projection.
- `gui/src/model/runtimeHistory.test.mjs` — request/agent projection tests.

### Modified files

- `musubi/storage/schema.sql` — nullable `agent_turns.request_id`.
- `musubi/storage/db.py` — additive migration and CRUD support for `request_id`.
- `musubi/agent/run.py` — read launch request identity, wrap stderr, and record request identity.
- `musubi/agent/subagent.py` — set the exact worker role/handle log scope.
- `musubi/tests/test_agent_turns.py` — request identity schema/CRUD coverage.
- `musubi/tests/test_agent_cli_output.py` — CLI protocol activation coverage.
- `gui/src-tauri/musubi-data/src/lib.rs` — runtime log schema/types/loaders and request-aware launch environment.
- `gui/src-tauri/src/lib.rs` — request ID generation, host/stream capture, and ledger inserts.
- `gui/src/model/viewModel.js` — use full runtime-history projection.
- `gui/src/model/viewModel.test.mjs` — multi-request graph integration tests.
- `gui/src/data/TauriSource.js` — reset detail navigation when sessions or requests change.
- `gui/src/data/TauriSource.test.mjs` — navigation reset tests.
- `gui/src/views/Orchestrator.jsx` — graph, request detail, agent detail, hidden Sessions rail, simplified Conversation.
- `gui/src/views/Orchestrator.test.mjs` — source-level UI contract.
- `gui/src/index.css` — topology/detail/log styling and approved visual tokens.
- `gui/src-tauri/SCHEMA.md` — ledger and identity contract.
- `docs/roadmap.md` — mark the Console runtime-history work and link this plan.

---

### Task 1: Persist Console request identity in `agent_turns`

**Files:**
- Modify: `musubi/storage/schema.sql`
- Modify: `musubi/storage/db.py`
- Modify: `musubi/agent/run.py`
- Test: `musubi/tests/test_agent_turns.py`
- Test: `musubi/tests/test_agent_cli_output.py`

**Interfaces:**
- Consumes: `MUSUBI_REQUEST_ID` from the Tauri launch environment.
- Produces: `insert_agent_turn(..., request_id: str | None)` and nullable `agent_turns.request_id`.

- [ ] **Step 1: Write failing schema and CRUD tests**

Add:

```python
def test_agent_turn_request_id_migrates_and_round_trips(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        conn.execute("CREATE TABLE legacy_turns(id INTEGER)")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_turns)")}
    assert "request_id" in cols

    db.insert_agent_turn(
        chat_id="chat-1", request_id="req-1", parent_session_id="session-1",
        started_at=1.0, ended_at=2.0, model_family="deepseek",
        cycles=1, tokens_in_estimate=10, tokens_out_estimate=5,
        lm_ms=20, total_ms=30, db_path=fresh_db,
    )
    assert db.query_agent_turns("chat-1", db_path=fresh_db)[0]["request_id"] == "req-1"
```

Add a legacy migration test that creates `agent_turns` without `request_id`,
runs `db.init_db`, and asserts the column is added without deleting its row.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_turns.py -q
```

Expected: FAIL because `request_id` is absent from schema/signatures.

- [ ] **Step 3: Add the schema column and migration**

Add `request_id TEXT` to both embedded and file schemas. Add:

```python
_AGENT_TURN_COLUMNS = {
    "request_id": "TEXT",
}
```

Call `_migrate_columns(conn, "agent_turns", _AGENT_TURN_COLUMNS)` from
`init_db`. Extend `insert_agent_turn` and `query_agent_turns` to write/read
`request_id` while retaining a default of `None` for non-Console callers.

- [ ] **Step 4: Propagate the environment identity through the driver**

Resolve once in `main`:

```python
request_id = os.environ.get("MUSUBI_REQUEST_ID", "").strip() or None
```

Add `request_id: str | None = None` to `run_agent` and `_record_agent_turn`,
pass it through deterministic and model-backed paths, and write it with the
turn row. Direct CLI invocations without the environment remain compatible.

- [ ] **Step 5: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_turns.py musubi/tests/test_agent_cli_output.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add musubi/storage/schema.sql musubi/storage/db.py musubi/agent/run.py musubi/tests/test_agent_turns.py musubi/tests/test_agent_cli_output.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(agent): persist console request identity"
```

---

### Task 2: Emit structured root and worker runtime records

**Files:**
- Create: `musubi/agent/runtime_log.py`
- Create: `musubi/tests/test_runtime_log.py`
- Modify: `musubi/agent/run.py`
- Modify: `musubi/agent/subagent.py`

**Interfaces:**
- Consumes: exact request identity from Task 1.
- Produces:
  - `PROTOCOL_PREFIX = "\x1eMUSUBI_LOG "`
  - `RuntimeLogWriter(stream, request_id)`
  - `runtime_worker_scope(role, handle)`
  - `emit_runtime_log(log, message, category="output")`

- [ ] **Step 1: Write failing writer tests**

Create tests proving one print becomes one JSON envelope and scope is captured:

```python
def test_runtime_writer_emits_exact_worker_scope() -> None:
    raw = io.StringIO()
    writer = RuntimeLogWriter(raw, request_id="req-1")
    with runtime_worker_scope("coder", "worker-abcdef123456"):
        print("[agent] tool musubi_write_file: ok", file=writer)
    line = raw.getvalue().splitlines()[0]
    assert line.startswith(PROTOCOL_PREFIX)
    event = json.loads(line.removeprefix(PROTOCOL_PREFIX))
    assert event == {
        "request_id": "req-1",
        "role": "coder",
        "agent_handle": "worker-abcdef123456",
        "category": "output",
        "message": "[agent] tool musubi_write_file: ok",
    }
```

Also cover split writes, UTF-8, root defaults, nested scope reset, and disabled
protocol fallback.

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_runtime_log.py -q
```

Expected: import failure because `runtime_log.py` does not exist.

- [ ] **Step 3: Implement the line-framed writer**

Use ContextVars for exact worker ownership:

```python
_runtime_role = contextvars.ContextVar("musubi_runtime_role", default="root")
_runtime_handle = contextvars.ContextVar("musubi_runtime_handle", default=None)

class RuntimeLogWriter:
    def write_event(self, message: str, category: str = "output") -> None:
        payload = {
            "request_id": self.request_id,
            "role": _runtime_role.get(),
            "agent_handle": _runtime_handle.get(),
            "category": category,
            "message": message,
        }
        self.stream.write(PROTOCOL_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
```

Buffer partial writes per `(role, handle)` until newline; delegate `flush`,
`encoding`, and `isatty`. Do not mutate messages or truncate them.

- [ ] **Step 4: Activate the protocol only for Console launches**

When both `MUSUBI_RUNTIME_LOG_PROTOCOL=1` and `MUSUBI_REQUEST_ID` exist, create
the writer after `_force_utf8_streams()` and pass it explicitly as
`run_agent(..., log=runtime_log)`. Keep stdout as the final-answer channel.

Replace model/tool/skill/policy lifecycle prints at their existing helper
boundaries with:

```python
emit_runtime_log(log, line, category="model")
emit_runtime_log(log, line, category="tool")
emit_runtime_log(log, line, category="skill")
emit_runtime_log(log, line, category="policy")
```

All other stderr lines remain category `output`.

- [ ] **Step 5: Apply exact worker scope**

In `subagent.py`, wrap `run_unit`:

```python
with runtime_worker_scope(role, handle_id):
    answer, turns = await run_unit(...)
```

Nested workers receive their own ContextVar values and restore the parent on
exit. Spawn/completion lines emitted by the parent remain in the parent scope.

- [ ] **Step 6: Verify GREEN and regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_runtime_log.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py -q
```

Expected: all selected tests PASS and ordinary non-Console log assertions stay
unchanged.

- [ ] **Step 7: Commit**

```powershell
git add musubi/agent/runtime_log.py musubi/agent/run.py musubi/agent/subagent.py musubi/tests/test_runtime_log.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(agent): emit scoped runtime log records"
```

---

### Task 3: Add the append-only Tauri runtime log ledger

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Modify: `gui/src-tauri/src/lib.rs`
- Modify: `gui/src-tauri/SCHEMA.md`

**Interfaces:**
- Consumes: protocol records from Task 2 and `request_id` launch metadata.
- Produces:
  - serialized `State.runtimeLogEvents: RuntimeLogEvent[]`
  - `append_runtime_log_event(conn, RuntimeLogEventInput)`
  - `parse_runtime_log_line(line) -> ParsedRuntimeLine`

- [ ] **Step 1: Write failing data-core tests**

Add Rust tests:

```rust
#[test]
fn runtime_log_events_are_append_only_and_request_scoped() {
    let conn = Connection::open_in_memory().unwrap();
    init_schema(&conn).unwrap();
    append_runtime_log_event(&conn, event("req-1", "chat-1", 1, "root", None, "first")).unwrap();
    append_runtime_log_event(&conn, event("req-2", "chat-1", 1, "root", None, "second")).unwrap();
    let state = load_state(&conn).unwrap();
    assert_eq!(state.runtime_log_events.len(), 2);
    assert_eq!(state.runtime_log_events[0].request_id, "req-1");
    assert_eq!(state.runtime_log_events[1].request_id, "req-2");
}
```

Add migration/index assertions and a loader test for `agent_turns.request_id`
with a legacy fallback expression.

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml runtime_log
```

Expected: compile failure because the schema/types/helpers are absent.

- [ ] **Step 3: Add schema, types, and tolerant readers**

Add `runtime_log_events` exactly as defined in the design spec, including:

```sql
UNIQUE(request_id, seq)
```

Add `RuntimeLogEvent` to `State`. Read events oldest-first for surfaced
Orchestrator chat IDs, capped to a high display-safe page (initially 10,000
rows) without truncating storage. Read `agent_turns.request_id` only when the
column exists and serialize it as `requestId`.

- [ ] **Step 4: Extend the launch contract**

Add `request_id: Option<&str>` to `AgentLaunchScope`. When present,
`build_agent_launch_spec` appends:

```rust
("MUSUBI_REQUEST_ID".into(), request_id.into()),
("MUSUBI_RUNTIME_LOG_PROTOCOL".into(), "1".into()),
```

Update launch-spec tests to prove both values are explicit and absent for
ordinary non-Console launch specs.

- [ ] **Step 5: Write failing Tauri parser/capture tests**

Cover:

```rust
let parsed = parse_runtime_log_line(
    "\u{1e}MUSUBI_LOG {\"request_id\":\"req-1\",\"role\":\"coder\",\
     \"agent_handle\":\"worker-22\",\"category\":\"tool\",\
     \"message\":\"[agent] tool: ok\"}"
).unwrap();
assert_eq!(parsed.agent_handle.as_deref(), Some("worker-22"));
assert_eq!(parsed.display, "[agent] tool: ok");
```

Also test plain stdout fallback to root, invalid envelopes retained at request
scope, UTF-8 chunk boundaries, host event insertion, and monotonic sequence.

- [ ] **Step 6: Run Tauri tests and verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml runtime_log
```

Expected: compile failure because parser/capture helpers are absent.

- [ ] **Step 7: Implement request creation and capture**

Generate `request_id` before `claim_runtime_owner`, store it in
`ChatAgentRuntime`, and pass it in `AgentLaunchScope`. Insert host events for:

- audit DB resolution;
- exact launch command/cwd;
- spawn failure;
- cancellation;
- process exit status.

Replace chunk-only persistence with a line framer per stdout/stderr pump.
Protocol envelopes provide exact `role`, `agent_handle`, and `category`.
Plain stdout/stderr lines are retained as request/root events. Use one shared
atomic sequence per request. Continue maintaining 64 KiB tails only for live
status and chat-summary compatibility.

- [ ] **Step 8: Verify GREEN**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml runtime_log
cargo test --manifest-path gui/src-tauri/Cargo.toml runtime_log
```

Expected: all runtime-log tests PASS.

- [ ] **Step 9: Document and commit**

Document ownership, ordering, compatibility, and the difference between the
durable ledger and tails in `SCHEMA.md`.

```powershell
git add gui/src-tauri/musubi-data/src/lib.rs gui/src-tauri/src/lib.rs gui/src-tauri/SCHEMA.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(console): persist request runtime logs"
```

---

### Task 4: Project full request topology and scoped logs

**Files:**
- Create: `gui/src/model/runtimeHistory.js`
- Create: `gui/src/model/runtimeHistory.test.mjs`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/model/viewModel.test.mjs`
- Modify: `gui/src/data/TauriSource.js`
- Modify: `gui/src/data/TauriSource.test.mjs`

**Interfaces:**
- Consumes: `agentTurns[].requestId`, `runtimeLogEvents[]`, subagents, cycles,
  pipelines, tools, and policy rows.
- Produces:

```js
buildRuntimeHistory({
  chatId, agentTurns, subagents, pipelineRuns, runtimeLogEvents,
  agentCycles, toolEvidence, policy,
}) => {
  requests, nodes, edges, requestById, nodeById,
}
```

Each node has `id`, `kind`, `requestId`, `parentId`, metrics, and scoped logs.
Each edge has relation `summoned` or `next-request`.

- [ ] **Step 1: Write failing pure projection tests**

Create one fixture with two root turns in one chat:

```js
assert.deepEqual(history.requests.map((r) => r.id), ['req-1', 'req-2'])
assert.deepEqual(
  history.edges.filter((e) => e.relation === 'next-request')
    .map((e) => [e.from, e.to]),
  [['request:req-1', 'request:req-2']],
)
assert.deepEqual(history.requestById['req-1'].logs.map((row) => row.message),
  ['host', 'root', 'planner'])
assert.deepEqual(history.nodeById['worker-2'].logs.map((row) => row.message),
  ['coder only'])
```

Cover nested parent handles, a deterministic request with no parent session,
legacy turns without raw logs, old pipeline isolation, and ambiguous rows.

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
node --test gui/src/model/runtimeHistory.test.mjs
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement the pure projector**

Build request roots from request-aware turns plus request-start ledger events.
Join workers by `parentSession`; join pipeline stages only through their owning
request. Sort requests by start time, preserve every request, and derive:

```js
{ from: previousRequestNodeId, to: requestNodeId, relation: 'next-request' }
{ from: auditedParentNodeId, to: worker.handle, relation: 'summoned' }
```

Never mutate input rows and never assign an unknown handle to a worker.

- [ ] **Step 4: Integrate with `buildViewModel`**

Replace latest-turn-only `activeSessionAgents` and runtime graph construction
with `buildRuntimeHistory`. Export:

- `runtimeHistory`
- `runtimeGraph`
- `requestDetails`
- `agentDetails`
- `runtimeLogEvents`

Retain legacy empty states and current driver status/economics behavior.

- [ ] **Step 5: Add navigation state actions**

In `TauriSource`, add:

```js
runtimeScreen: { kind: 'graph' }
openRuntimeRequest: (requestId) => ...
openRuntimeAgent: (requestId, agentHandle) => ...
backToRuntimeGraph: () => ...
```

Selecting another session, sending a request, or creating a session resets the
screen to `{ kind: 'graph' }`.

- [ ] **Step 6: Verify GREEN**

Run:

```powershell
node --test gui/src/model/runtimeHistory.test.mjs gui/src/model/viewModel.test.mjs gui/src/data/TauriSource.test.mjs
```

Expected: all selected tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add gui/src/model/runtimeHistory.js gui/src/model/runtimeHistory.test.mjs gui/src/model/viewModel.js gui/src/model/viewModel.test.mjs gui/src/data/TauriSource.js gui/src/data/TauriSource.test.mjs
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(console): project full request topology"
```

---

### Task 5: Build graph-to-detail Runtime Evidence UI

**Files:**
- Modify: `gui/src/views/Orchestrator.jsx`
- Modify: `gui/src/views/Orchestrator.test.mjs`
- Modify: `gui/src/index.css`

**Interfaces:**
- Consumes: Task 4 runtime history and navigation actions.
- Produces: one graph/request/agent workspace, fully hidden Sessions rail, and
  simplified Conversation.

- [ ] **Step 1: Replace the source-contract tests with failing requirements**

Assert:

```js
for (const label of [
  'Back to graph', 'Overview', 'Request log', 'Agent log',
  'Show sessions', 'Host', 'Root', 'Workers',
]) assert.match(source, new RegExp(label))

for (const removed of ['workspaceTab', '>Graph<', '>Logs<', 'VerboseEvidence',
  '>Summary<', '>Verbose<']) assert.equal(source.includes(removed), false)
```

Also assert request and agent click handlers are distinct and only one detail
screen renders.

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
node --test gui/src/views/Orchestrator.test.mjs
```

Expected: failures for missing graph/detail labels and retained legacy tabs.

- [ ] **Step 3: Implement the mutually exclusive workspace**

Refactor the center section into focused components:

```jsx
function RuntimeWorkspace({ vals }) {
  if (vals.runtimeScreen.kind === 'request') return <RequestDetail vals={vals} />
  if (vals.runtimeScreen.kind === 'agent') return <AgentDetail vals={vals} />
  return <SessionRuntimeGraph vals={vals} />
}
```

Use SVG paths behind request/worker cards for solid blue summon edges and dashed
orange request-continuation edges. Cards remain semantic buttons with accessible
labels.

- [ ] **Step 4: Implement Request and Agent detail**

Both screens render one `Back to graph` control and local Overview/log tabs.
Request log queries the selected request's complete ledger. Agent log uses only
the selected handle's scoped rows. Overview renders structured metrics only,
with an `Open log` action and no embedded transcript rows.

Implement search/filter in React memoized selectors. Render large logs in a
fixed-height scrolling list with incremental 500-row pages.

- [ ] **Step 5: Hide Sessions completely**

When hidden, omit `<SessionsRail>` and change the grid to two columns. Render a
small left-edge `Show sessions` control in the center workspace. Reopening
restores the same selected session.

- [ ] **Step 6: Remove redundant Conversation modes**

Delete `conversationMode`, the `Summary / Verbose` tabs, and
`VerboseEvidence`. Keep skills, token economics, `ChatBody`, and input controls.

- [ ] **Step 7: Apply approved visual tokens and type scale**

Use:

```css
.runtime-detail__title { font: 600 20px/1.25 'IBM Plex Mono', monospace; }
.runtime-log-line { font: 13px/1.65 'IBM Plex Mono', monospace; }
.runtime-detail__tabs button,
.runtime-log-filter button { font: 600 12px 'IBM Plex Mono', monospace; }
.runtime-log-line time,
.runtime-node__metrics { font-size: 11px; }
```

Use the exact palette in Global Constraints, thin borders, compact spacing, and
restrained active glow.

- [ ] **Step 8: Verify GREEN and build**

Run:

```powershell
node --test gui/src/views/Orchestrator.test.mjs gui/src/model/runtimeHistory.test.mjs gui/src/model/viewModel.test.mjs gui/src/data/TauriSource.test.mjs
npm run build
```

Expected: all Node tests PASS and Vite build exits 0.

- [ ] **Step 9: Commit**

```powershell
git add gui/src/views/Orchestrator.jsx gui/src/views/Orchestrator.test.mjs gui/src/index.css
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(console): add request graph and scoped details"
```

---

### Task 6: Full verification, roadmap, and review readiness

**Files:**
- Modify: `docs/roadmap.md`
- Modify if verification reveals a defect: only files already named above.

**Interfaces:**
- Consumes: completed Tasks 1–5.
- Produces: verified feature branch and roadmap traceability.

- [ ] **Step 1: Update roadmap**

Add a summary-only Console runtime-history entry linking:

```markdown
[Implementation plan](./superpowers/plans/2026-07-26-console-request-runtime-history.md)
```

Record that the Console now projects multiple request roots and persists
request/agent-scoped runtime logs; do not duplicate implementation detail.

- [ ] **Step 2: Run the full relevant Python suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_turns.py musubi/tests/test_runtime_log.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_agent_cli_output.py -q
```

Expected: 0 failures.

- [ ] **Step 3: Run full GUI Node tests**

Run:

```powershell
node --test gui/src/**/*.test.mjs
```

Expected: 0 failures.

- [ ] **Step 4: Run Rust data and Tauri tests**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml
cargo test --manifest-path gui/src-tauri/Cargo.toml
```

Expected: 0 failures.

- [ ] **Step 5: Run production build**

Run:

```powershell
npm run build
```

Expected: Vite exits 0 with no unresolved imports.

- [ ] **Step 6: Review the complete diff against the spec**

Run:

```powershell
git diff origin/dev --check
git diff origin/dev --stat
git status --short
```

Confirm all nine Goals and all Compatibility/Error Handling requirements from
the design spec have an implementation and a test. Confirm
`vietnam-weather.html` remains untracked and unstaged.

- [ ] **Step 7: Commit roadmap and verification adjustments**

```powershell
git add docs/roadmap.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "docs(roadmap): track console runtime history"
```
