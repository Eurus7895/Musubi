# Project-Scoped Session Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind process ownership, retained logs, and cancellation to an exact session while every session in a project continues to share one workspace and one writer slot.

**Architecture:** The canonical project root remains the only filesystem boundary. `ChatAgentRuntime` records the exact owning `chat_id`; Rust snapshots and the React view model expose process state only to that current session. The existing single child-process slot becomes the explicit per-project writer lease.

**Tech Stack:** Rust, Tauri, rusqlite, React view model, Node test runner.

## Global Constraints

- Never create a per-session directory, worktree, clone, virtualenv, or container.
- All sessions for one project use the same canonical project root and databases.
- Keep one active mutating child process per project.
- Session state is keyed by exact `chat_id`; surface prefix matching is legacy-only.
- Preserve append-only audit and the existing shared workspace behavior.

---

### Task 1: Exact Runtime Owner

**Files:**
- Modify: `gui/src-tauri/src/lib.rs`
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`

**Interfaces:**
- Produces: `ChatAgentRuntime.chat_id: String`.
- Produces: serialized `DriverStatus.chat_id: String` as `driverStatus.chatId`.
- Consumes: the exact ID already passed to `start_chat_agent(..., chat_id, ...)`.

- [ ] **Step 1: Write the failing Rust tests**

Add a data serialization assertion:

```rust
let status = DriverStatus {
    chat_id: "gui-pipeline-project-session".into(),
    ..DriverStatus::default()
};
let value = serde_json::to_value(status).unwrap();
assert_eq!(value["chatId"], "gui-pipeline-project-session");
```

Add a Tauri runtime test that launches through the extracted runtime setup
helper and asserts the owner is the complete ID, not `pipeline` alone:

```rust
let mut runtime = ChatAgentRuntime::default();
set_runtime_owner(
    &mut runtime,
    "gui-pipeline-project-session",
    "pipeline",
    "feature-dev",
    "ship it",
    100,
);
assert_eq!(runtime.chat_id, "gui-pipeline-project-session");
assert_eq!(runtime.surface, "pipeline");
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml driver_status
cargo test --manifest-path gui/src-tauri/Cargo.toml runtime_owner
```

Expected: FAIL because neither runtime/status type carries `chat_id` and the
owner helper does not exist.

- [ ] **Step 3: Implement the minimal owner field**

Add the field to both structs and centralize launch-time assignment:

```rust
fn set_runtime_owner(
    runtime: &mut ChatAgentRuntime,
    chat_id: &str,
    surface: &str,
    pipeline_name: &str,
    task: &str,
    started_at: i64,
) {
    runtime.chat_id = chat_id.to_string();
    runtime.surface = surface_arg(surface).to_string();
    runtime.pipeline_name = pipeline_name.to_string();
    runtime.task = task.to_string();
    runtime.started_at = Some(started_at);
}
```

Call it from `start_chat_agent` before spawning and copy the field into
`snapshot().driver_status`. Clear it only when New session clears the retained
runtime or a new launch replaces the owner.

- [ ] **Step 4: Run Rust suites and verify GREEN**

Run both Rust crate suites. Expected: all tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add gui/src-tauri/src/lib.rs gui/src-tauri/musubi-data/src/lib.rs
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(gui): bind runtime state to exact session"
```

### Task 2: Session-Scoped Process State in the Frontend

**Files:**
- Modify: `gui/src/data/TauriSource.js`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/model/viewModel.test.mjs`

**Interfaces:**
- Consumes: `driverStatus.chatId`, `orchestratorChatId`, `pipelineChatId`.
- Produces: `driverBelongsToSession(status, surface, currentChatId) -> boolean`.

- [ ] **Step 1: Write failing view-model tests**

Add two exact-owner cases:

```js
test('retained pipeline log belongs only to its exact session', () => {
  const vm = buildViewModel(baseState({
    pipelineChatId: 'gui-pipeline-new',
    driverStatus: {
      running: false,
      surface: 'pipeline',
      chatId: 'gui-pipeline-old',
      stderrTail: 'old failure',
      stdoutTail: '',
    },
    logWindowOpen: true,
  }), actions())

  assert.equal(vm.pipeChatBody.hasDriverLog, false)
  assert.equal(vm.pipeChatBody.logWindowOpen, false)
})
```

Repeat with matching IDs and assert both values are true. Add the equivalent
Orchestrator case so prefix-equal historical sessions do not leak state.

- [ ] **Step 2: Run the test and verify RED**

Run `node --test gui/src/model/viewModel.test.mjs`.

Expected: FAIL because ownership currently checks only `surface`.

- [ ] **Step 3: Implement exact ownership**

Use one predicate for liveness, retained logs, task copy, and terminal status:

```js
function driverBelongsToSession(status, surface, currentChatId) {
  return status?.surface === surface
    && !!currentChatId
    && status?.chatId === currentChatId
}
```

Do not fall back to the surface prefix for process state. Keep legacy fallback
only for durable historical rows that predate exact IDs.

- [ ] **Step 4: Run Node tests and build**

Run:

```powershell
node --test gui/src/data/*.test.mjs gui/src/model/*.test.mjs gui/src/components/*.test.mjs
npm run build --workspace musubi-gui
```

Expected: all tests PASS and Vite build succeeds.

- [ ] **Step 5: Commit**

```powershell
git add gui/src/data/TauriSource.js gui/src/model/viewModel.js gui/src/model/viewModel.test.mjs
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(gui): hide runtime state from other sessions"
```

### Task 3: Shared Project Root and Writer Lease Regression Contract

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Modify: `gui/src-tauri/src/lib.rs`
- Modify: `gui/src-tauri/SCHEMA.md`

**Interfaces:**
- Consumes: `build_agent_launch_spec(..., project_root, chat_id, ...)`.
- Preserves: `AgentLaunchSpec.cwd == canonical project root` for every session.
- Preserves: one `ChatAgentRuntime.running` writer lease per application/project.

- [ ] **Step 1: Add the shared-root regression test**

```rust
let first = build_agent_launch_spec(
    "first", "", "", None, root, &env,
    Some("gui-pipeline-project-a"), Some("feature-dev"),
).unwrap();
let second = build_agent_launch_spec(
    "second", "", "", None, root, &env,
    Some("gui-pipeline-project-b"), Some("feature-dev"),
).unwrap();
assert_eq!(first.cwd, root);
assert_eq!(second.cwd, root);
assert_ne!(first.args, second.args);
```

Assert neither launch spec contains either chat ID as a path segment.

- [ ] **Step 2: Add the writer-lease regression test**

Exercise the existing launch guard with one running Pipeline session and a
second Orchestrator request. Assert the second request returns the existing
busy error and does not change `chat_id`, `surface`, task, or child handle.

- [ ] **Step 3: Run tests and verify current behavior**

Run both Rust suites. If either test fails, make only the minimal launch/guard
change required to restore the shared-root and single-writer contracts.

- [ ] **Step 4: Document the boundary**

Update `SCHEMA.md` with:

```text
Project root owns the workspace and one writer slot. Exact chat IDs own
conversation and runtime projections. A session never owns a filesystem root.
```

- [ ] **Step 5: Commit**

```powershell
git add gui/src-tauri/musubi-data/src/lib.rs gui/src-tauri/src/lib.rs gui/src-tauri/SCHEMA.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "test(gui): lock project-scoped session boundaries"
```

### Task 4: Runtime Acceptance

**Files:**
- Modify: `docs/roadmap.md`
- Modify: `docs/superpowers/plans/2026-07-12-project-scoped-session-runtime.md`

- [ ] **Step 1:** Run all Node tests, both Rust suites, production GUI build,
  and `git diff --check`.
- [ ] **Step 2:** Start a pipeline session, let it exit, create a new Pipeline
  session, and verify the new session cannot open the old retained log.
- [ ] **Step 3:** Create Orchestrator and Pipeline sessions in the same project
  and verify both launch specs use the same workspace; while one runs, verify
  the other receives the busy-project message.
- [ ] **Step 4:** Confirm no session directory, worktree, clone, virtualenv, or
  container was created.
- [ ] **Step 5:** Record the acceptance result in the roadmap and commit with
  `docs(roadmap): record project-scoped sessions`.

## Implementation Result

Implemented on `fix/gui-pipeline-studio-sessions` with automated acceptance:

- `DriverStatus.chatId` identifies the exact live or retained runtime owner.
- Orchestrator and Pipeline views render process state only for their current
  full chat ID; surface-prefix matching is not used for process ownership.
- New-session and clear-chat operations leave another session's retained
  runtime state untouched.
- Launch specs for two sessions share the same canonical project root, and the
  project writer lease rejects a second run without replacing the first owner.
- Node tests, both Rust suites, Clippy, the production GUI build, formatter
  checks, and `git diff --check` form the repeatable acceptance contract.
