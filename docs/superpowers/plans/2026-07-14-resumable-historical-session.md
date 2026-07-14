# Resumable Historical Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator continue the historical Orchestrator session currently on screen as soon as the shared driver is idle, without sending the follow-up to another session.

**Architecture:** Keep historical browsing read-only only while a driver process owns the runtime. The frontend sends the selected historical chat ID as part of the existing `send_chat` command; the Rust boundary validates and promotes that ID before using it for both message persistence and agent launch. Worker hover styling becomes visually distinct from selection without changing selection events.

**Tech Stack:** React 18, plain JavaScript view models, Node.js test runner, Tauri 2, Rust, rusqlite, Cargo tests.

**Status:** Implemented and verified on `fix/gui-resume-historical-session`.

## Global Constraints

- Preserve the single shared driver process and its runtime ownership checks.
- Preserve project/surface validation for durable chat IDs.
- A historical session remains read-only while any driver process is running.
- Never implement resume as separate frontend `select_session` and `send_chat` calls.
- Pipeline session behavior is unchanged.
- Do not touch the three untracked dashboard artifacts in the workspace root.

---

### Task 1: Enable idle historical chat in the view model

**Files:**
- Modify: `gui/src/model/viewModel.js:302-309, 842-844`
- Test: `gui/src/model/viewModel.test.mjs:742-798`

**Interfaces:**
- Consumes: `selectedSession`, `orchestratorChatId`, and `driverStatus.running` from GUI state.
- Produces: `viewingHistoricalSession: boolean`, `historicalSessionBlocked: boolean` (local calculation), and resumable `sendDisabled` / `inputDisabled` values.

- [x] **Step 1: Change the idle-history regression to expect enabled chat**

Replace the existing idle test with:

```js
test('historical session becomes resumable after the other run finishes', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'live-session',
    selectedSession: 'old-session',
    orchestratorSessions: [{
      chatId: 'old-session', title: 'old', lastRequest: 'old request',
      createdAt: '100', updatedAt: '101', rootTurns: 1, workers: 0,
    }],
    driverStatus: {
      running: false,
      surface: 'orchestrator',
      chatId: 'live-session',
      task: 'finished',
      startedAt: 1,
      stdoutTail: '',
      stderrTail: '',
    },
  }), actions())

  assert.equal(vm.viewingHistoricalSession, true)
  assert.equal(vm.sendDisabled, false)
  assert.equal(vm.inputDisabled, false)
  assert.equal(vm.disabledText, '')
})
```

- [x] **Step 2: Run the test and verify RED**

Run:

```powershell
node --test gui/src/model/viewModel.test.mjs
```

Expected: FAIL because idle historical sessions currently set both disabled flags to `true`.

- [x] **Step 3: Gate historical blocking on runtime ownership**

Keep `viewingHistoricalSession` as the identity fact and add:

```js
const historicalSessionBlocked = viewingHistoricalSession && driverRunning
const historicalDisabledText = historicalSessionBlocked
  ? 'Viewing historical session (read-only) while another run is active.'
  : ''
```

Update Orchestrator chat values:

```js
sendDisabled: orchestratorBlockedByPipeline || historicalSessionBlocked,
inputDisabled: orchestratorBlockedByPipeline || historicalSessionBlocked,
disabledText: historicalDisabledText || (orchestratorBlockedByPipeline
  ? `${activeSurfaceLabel} run is active...`
  : ''),
```

- [x] **Step 4: Run view-model tests and verify GREEN**

Run:

```powershell
node --test gui/src/model/viewModel.test.mjs
```

Expected: all tests pass, including the existing running-history read-only test.

- [x] **Step 5: Commit Task 1**

```powershell
git add gui/src/model/viewModel.js gui/src/model/viewModel.test.mjs
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "fix(gui): unlock idle historical chat"
```

---

### Task 2: Forward the viewed session and distinguish hover

**Files:**
- Modify: `gui/src/data/TauriSource.js:190-204`
- Test: `gui/src/data/TauriSource.test.mjs`
- Modify: `gui/src/views/Orchestrator.jsx:56, 141`
- Test: `gui/src/components/NewSessionButton.test.mjs`

**Interfaces:**
- Consumes: `TauriSource.state.selectedSession`.
- Produces: backend action `send_chat` arguments `[text: string, requestedChatId: string]`.

- [x] **Step 1: Add a failing selected-session send test**

```js
test('sendChat forwards the viewed session before clearing local selection', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    draft: '  continue this session  ',
    selectedSession: 'gui-orchestrator-project-old',
    driverStatus: { running: false },
  })

  source.actions.sendChat()

  assert.deepEqual(calls, [{
    kind: 'send_chat',
    args: ['continue this session', 'gui-orchestrator-project-old'],
  }])
  assert.equal(source.state.selectedSession, null)
  assert.equal(source.state.draft, '')
})
```

- [x] **Step 2: Add a failing hover-style contract**

Extend `NewSessionButton.test.mjs` after loading `orchestrator`:

```js
assert.match(orchestrator, /hover="border-color:rgba\(255,255,255,0\.2\)"/)
assert.doesNotMatch(orchestrator, /hover="border-color:rgba\(255,155,61,0\.55\)"/)
```

- [x] **Step 3: Run both tests and verify RED**

Run:

```powershell
node --test gui/src/data/TauriSource.test.mjs gui/src/components/NewSessionButton.test.mjs
```

Expected: FAIL because `send_chat` receives only the text and worker hover is orange.

- [x] **Step 4: Capture and forward the requested session**

Change `sendChat` to capture selection before clearing it:

```js
sendChat: () => {
  const d = (this.state.draft || '').trim()
  if (!d) return
  const requestedChatId = this.state.selectedSession || ''
  this._setLocal({ draft: '', selectedSession: null, selected: null })
  const command = classifyChatCommand(d)
  if (command.kind === 'openPipelinePicker') {
    this._setLocal({ view: 'pipeline' })
    this._action('pipeline_hint', [d])
    return
  }
  this._action('send_chat', [d, requestedChatId])
},
```

- [x] **Step 5: Make worker hover neutral**

In `StepCard`, replace the orange hover border with:

```jsx
hover="border-color:rgba(255,255,255,0.2)"
```

Do not change `onClick={step.onSelect}` or add mouse-enter selection handlers.

- [x] **Step 6: Run frontend tests and verify GREEN**

Run:

```powershell
node --test gui/src/data/TauriSource.test.mjs gui/src/components/NewSessionButton.test.mjs
```

Expected: all tests pass.

- [x] **Step 7: Commit Task 2**

```powershell
git add gui/src/data/TauriSource.js gui/src/data/TauriSource.test.mjs gui/src/views/Orchestrator.jsx gui/src/components/NewSessionButton.test.mjs
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "fix(gui): target resumed chat sessions"
```

---

### Task 3: Promote the requested session at the Rust boundary

**Files:**
- Modify: `gui/src-tauri/src/lib.rs:366-412, 1134-1150`
- Test: `gui/src-tauri/src/lib.rs` test module near `select_driver_session_*`

**Interfaces:**
- Consumes: optional second `send_chat` argument `requested_chat_id: &str`.
- Produces: `resolve_orchestrator_send_session(...) -> Result<String, String>` returning the exact chat ID used for persistence and launch.

- [x] **Step 1: Add failing idle-promotion and busy-refusal tests**

```rust
#[test]
fn resolve_send_session_promotes_idle_viewed_history() {
    let conn = Connection::open_in_memory().unwrap();
    musubi_data::init_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO chat_log(ts,role,text,surface,chat_id) VALUES
         ('old','you','old request','orchestrator','gui-orchestrator-project-old')",
        [],
    )
    .unwrap();
    let slot = Mutex::new("gui-orchestrator-project-current".to_string());
    let viewed = Mutex::new(Some("gui-orchestrator-project-old".to_string()));
    let mut rt = ChatAgentRuntime::default();

    let resolved = resolve_orchestrator_send_session(
        &conn,
        &mut rt,
        &slot,
        &viewed,
        "gui-orchestrator-project-old",
    )
    .unwrap();

    assert_eq!(resolved, "gui-orchestrator-project-old");
    assert_eq!(slot.lock().unwrap().as_str(), resolved);
    assert!(viewed.lock().unwrap().is_none());
}

#[test]
fn resolve_send_session_refuses_busy_historical_promotion() {
    let conn = Connection::open_in_memory().unwrap();
    musubi_data::init_schema(&conn).unwrap();
    let slot = Mutex::new("gui-orchestrator-project-current".to_string());
    let viewed = Mutex::new(Some("gui-orchestrator-project-old".to_string()));
    let mut rt = ChatAgentRuntime {
        running: true,
        chat_id: "gui-orchestrator-project-current".to_string(),
        ..ChatAgentRuntime::default()
    };

    let error = resolve_orchestrator_send_session(
        &conn,
        &mut rt,
        &slot,
        &viewed,
        "gui-orchestrator-project-old",
    )
    .unwrap_err();

    assert!(error.contains("running"));
    assert_eq!(slot.lock().unwrap().as_str(), "gui-orchestrator-project-current");
    assert_eq!(
        viewed.lock().unwrap().as_deref(),
        Some("gui-orchestrator-project-old")
    );
}
```

- [x] **Step 2: Run the Rust tests and verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml resolve_send_session -- --nocapture
```

Expected: compile failure because `resolve_orchestrator_send_session` does not exist.

- [x] **Step 3: Implement session resolution**

Add after `select_driver_session`:

```rust
fn resolve_orchestrator_send_session(
    conn: &Connection,
    rt: &mut ChatAgentRuntime,
    chat_id_slot: &Mutex<String>,
    viewed_chat_id_slot: &Mutex<Option<String>>,
    requested_chat_id: &str,
) -> Result<String, String> {
    let current_id = chat_id_slot.lock().map_err(|e| e.to_string())?.clone();
    if requested_chat_id.trim().is_empty() || requested_chat_id == current_id {
        return Ok(current_id);
    }
    if rt.running {
        return Err(
            "Cannot resume a historical session while another agent is running.".into(),
        );
    }
    select_driver_session(
        conn,
        rt,
        chat_id_slot,
        viewed_chat_id_slot,
        "orchestrator",
        requested_chat_id,
    )?;
    chat_id_slot.lock().map_err(|e| e.to_string()).map(|id| id.clone())
}
```

- [x] **Step 4: Resolve before persistence and launch**

At the beginning of the `send_chat` action:

```rust
let text = str_arg(0);
if text.trim().is_empty() {
    return Ok(());
}
let requested_chat_id = str_arg(1);
let chat_id = {
    let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
    let conn = state.db.lock().map_err(|e| e.to_string())?;
    resolve_orchestrator_send_session(
        &conn,
        &mut rt,
        &state.chat_id,
        &state.viewed_orchestrator_chat_id,
        &requested_chat_id,
    )?
};
```

Keep the existing `insert_chat` and `start_chat_agent` calls, but use only the
resolved `chat_id` returned above. Do not fall back to the previous slot value
after a resolution error.

- [x] **Step 5: Run focused and full Rust tests**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml resolve_send_session -- --nocapture
cargo test --manifest-path gui/src-tauri/Cargo.toml
```

Expected: focused tests pass; full crate tests pass.

- [x] **Step 6: Commit Task 3**

```powershell
git add gui/src-tauri/src/lib.rs
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "fix(gui): resume selected session atomically"
```

---

### Task 4: Update roadmap and verify the complete GUI flow

**Files:**
- Modify: `docs/roadmap.md` project-scoped GUI sessions entry

**Interfaces:**
- Consumes: completed frontend and Rust behavior from Tasks 1-3.
- Produces: current roadmap status and release-ready verification evidence.

- [x] **Step 1: Update the roadmap summary**

Add to the project-scoped GUI sessions entry:

```markdown
Historical Orchestrator sessions remain read-only only while another process
owns the driver; once idle, a follow-up atomically promotes and resumes the
viewed session.
```

Link this plan beside the existing session plans.

- [x] **Step 2: Run all frontend unit tests**

```powershell
node --test gui/src/model/viewModel.test.mjs gui/src/data/TauriSource.test.mjs gui/src/components/NewSessionButton.test.mjs gui/src/components/chatLinks.test.mjs gui/src/data/chatCommands.test.mjs
```

Expected: all tests pass.

- [x] **Step 3: Build the GUI**

```powershell
npm run build --prefix gui
```

Expected: Vite exits 0 and produces the GUI bundle.

- [x] **Step 4: Run Rust verification**

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml
```

Expected: all Tauri shell tests pass.

- [x] **Step 5: Check the final diff**

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only planned files plus the three pre-existing
untracked dashboard artifacts appear.

- [x] **Step 6: Commit Task 4**

```powershell
git add docs/roadmap.md docs/superpowers/plans/2026-07-14-resumable-historical-session.md
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "docs(gui): document historical session resume"
```
