# Orchestrator Session List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace parent-run navigation with durable, selectable project sessions and render each selected session's latest agent flow beginning at root.

**Architecture:** Use `chat_log.chat_id` as the durable Orchestrator session index, enrich completed `agent_turns` with the root request from the audit `sessions` table, and keep the exact active `chat_id` in the Rust shell as the selection source of truth. The React view model groups history by chat session, then renders the latest root turn and only its summoned workers.

**Tech Stack:** Rust, rusqlite, serde, Tauri 2, React 18, JavaScript ES modules, Node test runner.

## Global Constraints

- Keep one project root and one project writer lease; do not create per-session directories or worktrees.
- Preserve append-only `agent_turns`, `subagent_audit`, and governance audit rows.
- New session creates no rail item until its first `chat_log` message exists.
- Never push directly to `dev`; implementation stays on `fix/gui-pipeline-studio-sessions`.
- Preserve the four untracked dashboard artifacts and never stage them.

---

### Task 1: Expose durable Orchestrator sessions and root requests

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Modify: `gui/src-tauri/SCHEMA.md`
- Test: `gui/src-tauri/musubi-data/src/lib.rs`

**Interfaces:**
- Produces: `State.orchestrator_sessions: Vec<OrchestratorSession>` serialized as `orchestratorSessions`.
- Produces: `AgentTurn.request: String` serialized as `request`.
- Consumes: existing `chat_log`, audit `sessions`, `agent_turns`, and resolved subagent `chat_id` data.

- [x] **Step 1: Write failing Rust data tests**

Add tests that insert two Orchestrator `chat_id` values plus one empty minted ID,
then assert only IDs with chat rows appear newest first. Add a `sessions` request
and matching `agent_turns.parent_session_id`, then assert:

```rust
assert_eq!(st.orchestrator_sessions.len(), 2);
assert_eq!(st.orchestrator_sessions[0].chat_id, "gui-orchestrator-project-new");
assert_eq!(st.orchestrator_sessions[0].last_request, "new request");
assert_eq!(st.agent_turns[0].request, "build the dashboard");
```

- [x] **Step 2: Run tests and verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml orchestrator_sessions
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml agent_turn_root_request
```

Expected: compilation or assertion failure because the fields and loader do not exist.

- [x] **Step 3: Implement minimal data contracts**

Add:

```rust
#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct OrchestratorSession {
    pub chat_id: String,
    pub created_at: String,
    pub updated_at: String,
    pub title: String,
    pub last_request: String,
    pub root_turns: i64,
    pub workers: i64,
}
```

Load non-empty `surface='orchestrator'` chat IDs, derive first/latest user text,
then count `agent_turns` and resolved subagents by `chat_id`. Join audit
`sessions.request` into `AgentTurn.request` by `parent_session_id`.

- [x] **Step 4: Run Rust data tests and verify GREEN**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml
```

Expected: all `musubi-data` tests pass.

- [x] **Step 5: Document the serialized fields**

Add `orchestratorSessions` and `AgentTurn.request` to `gui/src-tauri/SCHEMA.md`,
including the rule that an ID is listed only after its first chat row.

### Task 2: Switch active sessions without deleting history

**Files:**
- Modify: `gui/src-tauri/src/lib.rs`
- Modify: `gui/src/data/TauriSource.js`
- Test: `gui/src-tauri/src/lib.rs`
- Test: `gui/src/data/TauriSource.test.mjs`

**Interfaces:**
- Produces: Rust helper `select_driver_session(...) -> Result<(), String>`.
- Produces: Tauri action `select_session` with `[chat_id]` arguments.
- Produces: `TauriSource.actions.selectSession(chatId)` that updates selection and invokes the backend switch.
- Consumes: `State.orchestrator_sessions` from Task 1.

- [x] **Step 1: Write failing selection and history tests**

Rust tests must assert an existing same-project Orchestrator ID replaces the
active slot and persists its nonce, while unknown/cross-scope IDs and busy
runtime state fail without changing the slot. JavaScript must assert:

```javascript
source.actions.selectSession('gui-orchestrator-project-old')
assert.deepEqual(calls, [{
  kind: 'select_session',
  args: ['gui-orchestrator-project-old'],
}])
```

and `newSession()` leaves `orchestratorSessions` unchanged.

- [x] **Step 2: Run tests and verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml select_driver_session
node --test gui/src/data/TauriSource.test.mjs
```

Expected: Rust helper/action missing and JavaScript call list missing.

- [x] **Step 3: Implement guarded selection**

Validate all of the following before swapping the slot:

```text
runtime.running == false
requested ID is non-empty
requested ID shares the current project's gui-orchestrator-<hash>- prefix
chat_log contains at least one orchestrator row for the requested ID
```

After validation, set the active slot and persist the selected ID's nonce so a
restart reopens the same session. Do not delete chat or audit rows.

- [x] **Step 4: Wire the frontend action**

Add `orchestratorSessions` to `DOMAIN_KEYS`. Make `selectSession` retain the
chosen chat ID locally and invoke `select_session`. Keep `newSession` limited to
clearing the current chat/editor/process view; it must not clear the session
index.

- [x] **Step 5: Run shell and source tests and verify GREEN**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml
node --test gui/src/data/TauriSource.test.mjs
```

Expected: all selected suites pass.

### Task 3: Render Sessions and root-first agent flow

**Files:**
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/views/Orchestrator.jsx`
- Modify: `gui/src/components/NewSessionButton.test.mjs`
- Test: `gui/src/model/viewModel.test.mjs`

**Interfaces:**
- Consumes: `state.orchestratorSessions`, `agentTurns[].request`, `subagents[].chatId`, and exact `driverStatus.chatId`.
- Produces: `vals.runs` as chat-session cards and `vals.activeRunSteps` beginning with a root presentation node.
- Produces: approved rail and empty-state copy.

- [x] **Step 1: Write failing view-model tests**

Cover two chat IDs after New session and assert both remain listed, newest first;
select the old ID and assert the active flow is:

```javascript
assert.deepEqual(vm.activeRunSteps.map((step) => step.role), ['root', 'coder'])
assert.equal(vm.activeRunSteps[0].brief, 'build the dashboard')
assert.equal(vm.activeRunSteps[1].brief, 'implement the page')
```

Add a driver-only turn assertion that `activeRunSteps` contains exactly one
root node. Add source assertions for `Sessions`, `agent flow`, and absence of
`Parent runs` / `Session unavailable`.

- [x] **Step 2: Run tests and verify RED**

Run:

```powershell
node --test gui/src/model/viewModel.test.mjs gui/src/components/NewSessionButton.test.mjs
```

Expected: old sessions are filtered out and no root step exists.

- [x] **Step 3: Implement session grouping and root node**

Build rail cards from `orchestratorSessions`, aggregate their root/worker counts
from exact `chatId`, and use the selected/current session's latest `AgentTurn` as
the focused run. Synthesize a root step with handle `root:<parentSession>`, then
append only workers matching that parent session. When the exact runtime owner
is active, synthesize the root from `driverStatus.task` until the aggregate turn
is written.

- [x] **Step 4: Update Orchestrator copy**

Render:

```text
Sessions
newest first · project conversations
No sessions yet. Send a message to start one.
agent flow
```

Keep the existing card and horizontal flow visual language; root uses a distinct
`root` role chip and the same status semantics as workers.

- [x] **Step 5: Run JavaScript tests and build**

Run:

```powershell
node --test gui/src/**/*.test.mjs
npm --prefix gui run build
```

Expected: all JavaScript tests pass and Vite exits 0.

### Task 4: Roadmap, full verification, commit, and push

**Files:**
- Modify: `docs/roadmap.md`
- Modify: `docs/superpowers/plans/2026-07-12-orchestrator-session-list.md`

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: roadmap status and reproducible verification evidence.

- [x] **Step 1: Update roadmap**

Record session browsing, history preservation, and root-first flow under the
existing Project-scoped sessions item. Link this plan rather than adding
implementation detail to the roadmap.

- [x] **Step 2: Run full relevant verification**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml
cargo test --manifest-path gui/src-tauri/Cargo.toml
node --test gui/src/**/*.test.mjs
npm --prefix gui run build
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 3: Rebase on current origin/dev**

Run:

```powershell
git fetch origin
git rebase origin/dev
```

Expected: branch is up to date or rebases without unresolved conflicts.

- [ ] **Step 4: Commit implementation**

Stage only the files named by this plan and commit:

```powershell
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(gui): preserve orchestrator session history"
```

- [ ] **Step 5: Push branch**

Run:

```powershell
git push origin fix/gui-pipeline-studio-sessions
```

Expected: the remote branch advances without touching `dev`.
