# Session Inbox, Resume, and Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Needs you` an unread-session inbox, add safe selected/all session cleanup, and make paused pipeline decisions resume the exact governed run.

**Architecture:** Console-owned mutable session state stores viewed cursors and deletion tombstones beside the append-only audit tables. Pipeline pause metadata is projected read-only from `musubi.db`; decision commands reopen that state database for one validated transaction and relaunch the standalone agent with an exact continuation checkpoint. The runner consumes the pending decision once and resumes from durable stage outputs.

**Tech Stack:** Rust/rusqlite/Tauri, React/JavaScript/node:test, Python/pytest/asyncio, SQLite.

## Global Constraints

- `Needs you` means unread durable activity only; worker status never selects that bucket.
- Selecting a session marks its current activity cursor viewed immediately.
- Viewing never approves a pause.
- Audit, runtime evidence, and request folder snapshots remain append-only.
- Delete and clean-all reject while the affected runtime is active.
- Resume reuses the original immutable request folder snapshot.
- Every decision and deletion is validated transactionally and fails closed.
- The unrelated `vietnam-weather.html` is never staged or modified.

---

### Task 1: Durable unread and deletion state

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Test: `gui/src-tauri/musubi-data/src/lib.rs`

**Interfaces:**
- Produces: `OrchestratorSession { latest_activity: f64, viewed_through: f64, unread: bool }`
- Produces: `mark_orchestrator_session_viewed(conn, chat_id, viewed_through, now)`
- Produces: `delete_orchestrator_session(conn, chat_id, deleted_at)`
- Produces: `clean_orchestrator_sessions(conn, deleted_at)`

- [ ] **Step 1: Write failing Rust tests**

Add tests that create two chats, mark only one viewed, append later activity,
and assert:

```rust
assert!(!sessions_by_id["chat-a"].unread);
assert!(sessions_by_id["chat-b"].unread);
mark_orchestrator_session_viewed(&conn, "chat-b", latest, "200").unwrap();
assert!(!load_orchestrator_sessions(&conn).unwrap()[0].unread);
```

Add deletion tests asserting chat and editable grants are removed, the session
is absent from `load_orchestrator_sessions`, and
`request_folder_grant_snapshots`, `agent_turns`, `runtime_log_events`,
`subagent_audit`, and `tool_audit` counts are unchanged.

- [ ] **Step 2: Verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml orchestrator_session
```

Expected: compile failure because the new fields/functions do not exist.

- [ ] **Step 3: Add schema and data functions**

Add:

```sql
CREATE TABLE IF NOT EXISTS orchestrator_session_state (
  chat_id TEXT PRIMARY KEY,
  viewed_through REAL NOT NULL DEFAULT 0,
  deleted_at REAL,
  updated_at REAL NOT NULL
);
```

Compute `latest_activity` as the maximum session timestamp visible in
`chat_log`, `agent_turns`, `subagent_audit` through parent-session ancestry,
and `runtime_log_events`. Filter rows whose lifecycle row has non-null
`deleted_at`. `unread` is `latest_activity > viewed_through`.

Use an upsert for mark-viewed that advances but never decreases the cursor:

```sql
INSERT INTO orchestrator_session_state(chat_id, viewed_through, updated_at)
VALUES(?1, ?2, ?3)
ON CONFLICT(chat_id) DO UPDATE SET
  viewed_through = MAX(viewed_through, excluded.viewed_through),
  deleted_at = NULL,
  updated_at = excluded.updated_at
```

Deletion runs in one transaction, tombstones the chat, deletes its `chat_log`
and `session_folder_grants`, and does not touch evidence/snapshot tables.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml
```

Expected: all musubi-data tests pass.

- [ ] **Step 5: Commit**

```powershell
git add gui/src-tauri/musubi-data/src/lib.rs
git commit -m "feat(console): persist session inbox state"
```

### Task 2: Console session selection and cleanup commands

**Files:**
- Modify: `gui/src-tauri/src/lib.rs`
- Test: `gui/src-tauri/src/lib.rs`

**Interfaces:**
- Consumes: Task 1 session state functions.
- Produces Tauri actions: `mark_session_viewed`, `delete_session`, `clean_sessions`.

- [ ] **Step 1: Write failing command-boundary tests**

Add tests proving:

```rust
// selecting marks exactly the requested existing session viewed
// deleting the displayed idle session mints a fresh chat id
// deleting a running owner returns an error before mutation
// clean-all returns an error when any agent is running
// clean-all leaves one fresh active chat id
```

Assert the runtime lock is acquired before the DB lock on every mutation.

- [ ] **Step 2: Verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml --lib session_view
cargo test --manifest-path gui/src-tauri/Cargo.toml --lib delete_session
```

Expected: failure because the action helpers do not exist.

- [ ] **Step 3: Implement minimal helpers and action routes**

`mark_session_viewed` validates the session exists, advances its latest cursor,
and then selects it. `delete_session` only accepts the displayed session,
rejects the running owner, invokes Task 1 deletion, and mints a replacement
when deleting the active ID. `clean_sessions` rejects any running runtime,
tombstones all indexed chats transactionally, and mints one replacement.

Do not reuse `clear_driver_chat`: cleanup has lifecycle semantics and must not
silently retain the old session index row.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml --lib
```

Expected: all Console Rust tests pass.

- [ ] **Step 5: Commit**

```powershell
git add gui/src-tauri/src/lib.rs
git commit -m "feat(console): add session cleanup commands"
```

### Task 3: Unread rail and cleanup UI

**Files:**
- Modify: `gui/src/data/TauriSource.js`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/views/Orchestrator.jsx`
- Modify: `gui/src/index.css`
- Test: `gui/src/data/TauriSource.test.mjs`
- Test: `gui/src/model/viewModel.test.mjs`
- Test: `gui/src/views/Orchestrator.test.mjs`

**Interfaces:**
- Consumes: `session.unread`, `session.latestActivity`, Task 2 actions.
- Produces: `run.bucket`, `run.onDelete`, `cleanSessions`, confirmation state.

- [ ] **Step 1: Write failing JavaScript tests**

Replace the worker-status rail test with:

```js
assert.equal(failedUnread.bucket, 'needsYou')
assert.equal(escalatedViewed.bucket, 'earlier')
assert.equal(doneUnread.bucket, 'needsYou')
assert.equal(runningViewed.bucket, 'active')
```

Assert `selectSession(id)` invokes `mark_session_viewed` before/with selection,
only the selected card/main session strip exposes `Delete session`, and
`Clean all sessions` requires confirmation before calling `clean_sessions`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
npm test -- --run
```

from `gui/`.

Expected: rail and action tests fail against status-based grouping.

- [ ] **Step 3: Implement unread presentation**

Change:

```js
function railBucketFor(run) {
  if (run.status === 'running') return 'active'
  return run.session?.unread ? 'needsYou' : 'earlier'
}
```

Keep `Incomplete`, `Failed`, `Budget halted`, and `Waiting for decision` as
status text only. Add selected-session delete in the session strip and a
confirmed clean-all control in the rail header. Surface backend errors and
disable controls while mutations run.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
npm test -- --run
npm run build
```

Expected: all UI tests and production build pass.

- [ ] **Step 5: Commit**

```powershell
git add gui/src/data/TauriSource.js gui/src/model/viewModel.js gui/src/views/Orchestrator.jsx gui/src/index.css gui/src/data/TauriSource.test.mjs gui/src/model/viewModel.test.mjs gui/src/views/Orchestrator.test.mjs
git commit -m "feat(console): make needs-you an unread inbox"
```

### Task 4: Project pipeline pause state and validate decisions

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Modify: `gui/src-tauri/src/lib.rs`
- Test: both Rust modules above

**Interfaces:**
- Produces `PipelineRun` fields: `paused_at_stage`, `paused_at_chunk`, `pause_reason`, `pending_action`, `request_id`, `profile`.
- Produces: `resume_pipeline_session(session_id, action, user_hint, extra_budget)`.

- [ ] **Step 1: Write failing projection and transaction tests**

Create state DB fixtures with paused `sessions` rows and assert exact camelCase
projection. Add table-driven decision tests for:

```rust
stage_review => ["approve", "retry", "abort", "auto_approve_rest"]
budget_exhausted => ["grant", "force", "abort"]
```

Assert unknown pairs, stale second clicks, and non-paused sessions fail without
writing `pending_action`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml pause
cargo test --manifest-path gui/src-tauri/Cargo.toml --lib resume_pipeline
```

- [ ] **Step 3: Implement pause projection and short-lived writes**

Store the resolved state DB path in `AppState`; keep the polling connection
read-only. For decisions, open a new writable connection, use
`TransactionBehavior::Immediate`, re-read pause state, validate the matrix,
and atomically clear pause fields/set pending fields. `abort` finalizes without
launch; other actions produce a continuation launch request.

Add non-secret checkpoint columns to `pipeline_runs`:

```sql
request_id TEXT,
profile TEXT,
task TEXT
```

Populate them when Console launches a pipeline.

- [ ] **Step 4: Verify GREEN**

Run both full Rust suites:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml
cargo test --manifest-path gui/src-tauri/Cargo.toml --lib
```

- [ ] **Step 5: Commit**

```powershell
git add gui/src-tauri/musubi-data/src/lib.rs gui/src-tauri/src/lib.rs
git commit -m "feat(console): expose pipeline resume decisions"
```

### Task 5: Resume the exact deterministic pipeline

**Files:**
- Modify: `musubi/agent/run.py`
- Modify: `musubi/agent/pipeline_runner.py`
- Modify: `musubi/storage/db.py`
- Modify: `musubi/storage/schema.sql`
- Test: `musubi/tests/test_agent_loop.py`
- Test: `musubi/tests/test_pipeline_runner.py`
- Test: `musubi/tests/test_pause_resume.py`

**Interfaces:**
- Consumes: `MUSUBI_FOLDER_GRANTS_JSON`, checkpoint fields, pending action.
- Produces CLI argument: `--resume-pipeline-session SESSION_ID`.
- Produces: `pipeline_runner.resume_pipeline(...)`.

- [ ] **Step 1: Write failing Python tests**

Cover all decision paths with a fake MCP session and durable stage rows:

```python
assert completed_roles == ["planner"]       # already durable, not rerun
assert resumed_roles == ["coder"]           # approve continues next
assert retry_attempt == previous_attempt + 1
assert pending_action_consumed_once is True
assert abort_model_calls == 0
```

Assert continuation rejects a missing checkpoint, mismatched pipeline/chat,
and a folder manifest different from the original request snapshot.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_pipeline_runner.py musubi/tests/test_pause_resume.py musubi/tests/test_agent_loop.py -q -p no:cacheprovider
```

- [ ] **Step 3: Implement continuation**

Parse the internal resume argument, load the checkpoint from `pipeline_runs`,
rebuild `RootRegistry` from the original request snapshot, and call
`resume_pipeline`. The runner loads completed stage summaries, atomically
consumes the action through `musubi_consume_pending_action`, selects the
stage/chunk, and runs only the required remaining work. Preserve the pipeline
session ID; generate a new host request ID.

- [ ] **Step 4: Verify GREEN**

Run the focused Python command from Step 2, then:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_workspace_grants.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_agent_loop.py -q -p no:cacheprovider
```

- [ ] **Step 5: Commit**

```powershell
git add musubi/agent/run.py musubi/agent/pipeline_runner.py musubi/storage/db.py musubi/storage/schema.sql musubi/tests/test_agent_loop.py musubi/tests/test_pipeline_runner.py musubi/tests/test_pause_resume.py
git commit -m "feat(agent): resume paused pipeline sessions"
```

### Task 6: Pause action panel and automatic continuation

**Files:**
- Modify: `gui/src/data/TauriSource.js`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/views/Orchestrator.jsx`
- Modify: `gui/src/index.css`
- Test: corresponding JavaScript test files

**Interfaces:**
- Consumes: Task 4 pause fields and resume action.
- Produces: selected-session `pausePanel` and in-flight/error state.

- [ ] **Step 1: Write failing UI tests**

Assert stage-review and budget action sets exactly match the backend matrix,
viewed paused sessions remain in `Earlier`, and a decision disables every
button until the backend refresh completes.

- [ ] **Step 2: Verify RED**

Run `npm test -- --run` from `gui/`.

- [ ] **Step 3: Implement minimal panel**

Render `Waiting for decision` only for the selected paused pipeline. Retry
shows a small hint input. Grant sends `extraBudget=3`. On success, rely on the
backend snapshot and automatic relaunch; do not clear pause optimistically.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
npm test -- --run
npm run build
```

- [ ] **Step 5: Commit**

```powershell
git add gui/src/data/TauriSource.js gui/src/model/viewModel.js gui/src/views/Orchestrator.jsx gui/src/index.css gui/src/data/TauriSource.test.mjs gui/src/model/viewModel.test.mjs gui/src/views/Orchestrator.test.mjs
git commit -m "feat(console): add paused pipeline actions"
```

### Task 7: Fix Windows shell cwd from attached roots

**Files:**
- Modify: `musubi/tools/fs.py`
- Test: `musubi/tests/test_fs_tools.py`

**Interfaces:**
- Produces: `_subprocess_cwd(path: Path) -> str`.

- [ ] **Step 1: Write failing Windows-path test**

Mock `subprocess.run` and assert a resolved
`Path(r"\\?\C:\Workspace\Projects\AgentShield")` is passed as
`cwd=r"C:\Workspace\Projects\AgentShield"`. Preserve UNC conversion:
`\\?\UNC\server\share` becomes `\\server\share`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_fs_tools.py -q -p no:cacheprovider
```

- [ ] **Step 3: Implement lexical Windows conversion**

Convert only the process-boundary string. Keep canonical `Path` values for
authorization and audit metadata.

- [ ] **Step 4: Verify GREEN**

Run the focused test command and `git diff --check`.

- [ ] **Step 5: Commit**

```powershell
git add musubi/tools/fs.py musubi/tests/test_fs_tools.py
git commit -m "fix(tools): normalize Windows command cwd"
```

### Task 8: End-to-end verification

**Files:**
- Modify only files required by failures attributable to this plan.

- [ ] Run `cargo fmt --check --manifest-path gui/src-tauri/Cargo.toml`.
- [ ] Run both complete Rust test suites.
- [ ] Run `npm test -- --run` and `npm run build` from `gui/`.
- [ ] Run the focused Python pause/pipeline/workspace/filesystem suites.
- [ ] Run `git diff --check`.
- [ ] Confirm `vietnam-weather.html` remains untracked and untouched.
- [ ] Commit any test-only corrections with a Conventional Commit message.
