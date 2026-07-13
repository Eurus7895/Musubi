# Read-only Session Browsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator view a prior orchestrator session's full chat and worker flow while another session's agent continues running under its original chat ID.

**Architecture:** Keep the existing active `chat_id` as the sole owner of the driver and future messages. Add a separate backend viewed-session slot used only when building the Console snapshot; the frontend keeps `selectedSession` as navigation state and treats a selected ID that differs from `orchestratorChatId` as read-only.

**Tech Stack:** Tauri 2, Rust, rusqlite, React 18, vanilla JavaScript view-models, Node test runner, Cargo tests.

## Global Constraints

- Never mutate the running `ChatAgentRuntime.chat_id`, active chat-ID slot, or persisted session nonce when browsing history.
- Historical sessions are read-only whenever their ID differs from the active orchestrator chat ID.
- Invalid, missing, cross-project, and cross-surface session IDs remain fail-closed.
- Pipeline Studio session behavior is out of scope.
- Preserve all uncommitted agent-elision changes and the untracked dashboard file; stage only files named by each task.

---

### Task 1: Separate active and viewed sessions in the Rust backend

**Files:**
- Modify: `gui/src-tauri/src/lib.rs`
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Test: `gui/src-tauri/src/lib.rs`

**Interfaces:**
- Produces: serialized state field `viewedOrchestratorChatId: String`; empty string means the active session is being viewed.
- Produces: `select_driver_session(conn, rt, active_slot, viewed_slot, surface, requested_chat_id) -> Result<(), String>`.
- Behavior: busy selection updates only `viewed_slot`; idle selection updates `active_slot` and the persisted nonce, then clears `viewed_slot`.

- [ ] **Step 1: Write failing backend tests**

Replace the existing busy-rejection assertion with tests that create `let viewed = Mutex::new(None)` and prove:

```rust
rt.running = true;
rt.chat_id = "gui-orchestrator-project-current".into();
select_driver_session(
    &conn,
    &mut rt,
    &slot,
    &viewed,
    "orchestrator",
    "gui-orchestrator-project-old",
)
.unwrap();
assert_eq!(slot.lock().unwrap().as_str(), "gui-orchestrator-project-current");
assert_eq!(rt.chat_id, "gui-orchestrator-project-current");
assert_eq!(viewed.lock().unwrap().as_deref(), Some("gui-orchestrator-project-old"));
assert_eq!(load_or_mint_session_nonce(&conn, "orchestrator"), "current");
```

Extend the idle test to assert that selection updates the active slot and sets the viewed slot to `None`. Retain the current unknown and cross-scope assertions and confirm neither slot changes on error.

- [ ] **Step 2: Run the Rust test to verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml select_driver_session -- --nocapture
```

Expected: compile failure because `select_driver_session` has no viewed-slot parameter and busy selection still returns an error.

- [ ] **Step 3: Add the viewed-session state and selection behavior**

In `musubi_data::State`, add:

```rust
pub viewed_orchestrator_chat_id: String,
```

In `AppState`, add `viewed_orchestrator_chat_id: Mutex<Option<String>>` and initialize it to `None`. Update `select_driver_session` so it validates scope and existence first, then applies:

```rust
if rt.running {
    *viewed_chat_id_slot.lock().map_err(|e| e.to_string())? =
        (requested_chat_id != current_id).then(|| requested_chat_id.to_string());
    return Ok(());
}

*chat_id_slot.lock().map_err(|e| e.to_string())? = requested_chat_id.to_string();
*viewed_chat_id_slot.lock().map_err(|e| e.to_string())? = None;
store_session_nonce(conn, surface, requested_nonce);
```

Update `snapshot` to load orchestrator chat from the viewed ID when present, while continuing to serialize `orchestrator_chat_id` from the active slot:

```rust
let viewed_id = state
    .viewed_orchestrator_chat_id
    .lock()
    .map_err(|e| e.to_string())?
    .clone();
let displayed_id = viewed_id.as_deref().unwrap_or(&orchestrator_chat_id);
st.chat = musubi_data::load_chat_for_session(&conn, "orchestrator", displayed_id)
    .map_err(|e| e.to_string())?;
st.viewed_orchestrator_chat_id = viewed_id.unwrap_or_default();
```

Pass the viewed slot through the `select_session` action. Clear it in `new_driver_session`/`new_session` so a newly minted active session is displayed immediately. Do not change `append_driver_chat`: it must continue using `ChatAgentRuntime.chat_id`.

- [ ] **Step 4: Run backend tests to verify GREEN**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml select_driver_session -- --nocapture
cargo test --manifest-path gui/src-tauri/Cargo.toml snapshot -- --nocapture
```

Expected: all selected tests pass; the busy test proves active ownership and nonce are unchanged.

- [ ] **Step 5: Commit the backend boundary**

```powershell
git add gui/src-tauri/src/lib.rs gui/src-tauri/musubi-data/src/lib.rs
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(gui): separate active and viewed sessions"
```

---

### Task 2: Allow frontend selection during active runs

**Files:**
- Modify: `gui/src/data/TauriSource.js`
- Test: `gui/src/data/TauriSource.test.mjs`

**Interfaces:**
- Consumes: backend state keys `orchestratorChatId`, `viewedOrchestratorChatId`, and `chat`.
- Produces: `actions.selectSession(id)` always updates local navigation and invokes `select_session`, including while `driverStatus.running`.

- [ ] **Step 1: Write failing data-source tests**

Add a test with a running orchestrator driver:

```javascript
test('selectSession browses history while the active session keeps running', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    orchestratorChatId: 'gui-orchestrator-project-live',
    selectedSession: null,
    chat: [{ role: 'driver', text: 'live output' }],
    driverStatus: {
      running: true,
      surface: 'orchestrator',
      chatId: 'gui-orchestrator-project-live',
    },
  })

  source.actions.selectSession('gui-orchestrator-project-old')

  assert.equal(source.state.selectedSession, 'gui-orchestrator-project-old')
  assert.deepEqual(source.state.chat, [])
  assert.deepEqual(calls, [{
    kind: 'select_session',
    args: ['gui-orchestrator-project-old'],
  }])
})
```

Add `viewedOrchestratorChatId` to a `_mergeDomain` test and assert repeated snapshots preserve `selectedSession` while updating `chat` with the backend's viewed history.

- [ ] **Step 2: Run the frontend test to verify RED**

Run:

```powershell
node --test gui/src/data/TauriSource.test.mjs
```

Expected: the running-selection test fails because `selectSession` returns before changing state or invoking the backend.

- [ ] **Step 3: Implement frontend state merging and selection**

Add `viewedOrchestratorChatId` to `DOMAIN_KEYS` and initialize it to `''`. Remove only this guard from `selectSession`:

```javascript
if (this.state.driverStatus?.running) return
```

Keep the existing local reset of `chat`, `draft`, worker selection, and log panels, then invoke `select_session`. Do not remove the running guards from clear/new-session actions.

- [ ] **Step 4: Run data-source tests to verify GREEN**

Run:

```powershell
node --test gui/src/data/TauriSource.test.mjs
```

Expected: all tests pass, including idle switching and busy browsing.

- [ ] **Step 5: Commit frontend session selection**

```powershell
git add gui/src/data/TauriSource.js gui/src/data/TauriSource.test.mjs
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(gui): allow browsing sessions during active runs"
```

---

### Task 3: Make historical chat explicitly read-only

**Files:**
- Modify: `gui/src/model/viewModel.js`
- Test: `gui/src/model/viewModel.test.mjs`

**Interfaces:**
- Consumes: `selectedSession`, active `orchestratorChatId`, `driverStatus`, and backend-selected `chat`.
- Produces: `viewingHistoricalSession: boolean` and existing ChatBody fields `sendDisabled`, `inputDisabled`, and `disabledText` configured for read-only browsing.

- [ ] **Step 1: Write failing view-model tests**

Add a test whose active driver owns `live-session` while `selectedSession` is `old-session`:

```javascript
test('historical session is read-only while another session owns the driver', () => {
  const state = baseState({
    orchestratorChatId: 'live-session',
    selectedSession: 'old-session',
    chat: [{ role: 'driver', text: 'old answer' }],
    driverStatus: {
      running: true,
      surface: 'orchestrator',
      chatId: 'live-session',
      task: 'working',
      startedAt: 1,
      stdoutTail: '',
      stderrTail: '',
    },
  })
  const vm = buildViewModel(state, actions())

  assert.equal(vm.activeRunId, 'old-session')
  assert.equal(vm.chat[0].text, 'old answer')
  assert.equal(vm.viewingHistoricalSession, true)
  assert.equal(vm.sendDisabled, true)
  assert.equal(vm.inputDisabled, true)
  assert.match(vm.disabledText, /read-only/i)
})
```

Add a second case with `driverStatus.running: false` but the same active/viewed ID mismatch; it must remain read-only until the user selects the session again and the backend promotes it to active. Add a case where selected and active IDs match and cancel remains available during the run.

- [ ] **Step 2: Run view-model tests to verify RED**

Run:

```powershell
node --test gui/src/model/viewModel.test.mjs
```

Expected: failure because historical browsing does not currently disable input or expose `viewingHistoricalSession`.

- [ ] **Step 3: Implement read-only view-model behavior**

Compute:

```javascript
const viewingHistoricalSession = Boolean(
  s.selectedSession
  && s.orchestratorChatId
  && s.selectedSession !== s.orchestratorChatId
)
const historicalDisabledText = viewingHistoricalSession
  ? 'Viewing historical session (read-only). Select it again after the active run finishes to resume.'
  : ''
```

Expose `viewingHistoricalSession` in the returned view model. Set orchestrator `sendDisabled` and `inputDisabled` to `orchestratorBlockedByPipeline || viewingHistoricalSession`; give `historicalDisabledText` precedence in `disabledText`. Preserve cancel behavior when the selected session is the active running session.

- [ ] **Step 4: Run view-model tests and frontend build**

Run:

```powershell
node --test gui/src/model/viewModel.test.mjs
npm --prefix gui run build
```

Expected: all view-model tests pass and Vite exits 0.

- [ ] **Step 5: Run integrated regression verification**

Run:

```powershell
node --test gui/src/data/TauriSource.test.mjs gui/src/model/viewModel.test.mjs
cargo test --manifest-path gui/src-tauri/Cargo.toml
git diff --check
```

Expected: all JavaScript and Rust tests pass; `git diff --check` prints no errors. Manually confirm the running process writes its completion message under `ChatAgentRuntime.chat_id`, not `viewedOrchestratorChatId`.

- [ ] **Step 6: Update roadmap and commit final integration**

Add a summary-only completed entry to `docs/roadmap.md` linking the design and this plan, without duplicating implementation detail. Then commit:

```powershell
git add gui/src/model/viewModel.js gui/src/model/viewModel.test.mjs docs/roadmap.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(gui): keep historical sessions read-only"
```
