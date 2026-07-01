# GUI On-Demand Task Launcher Implementation Plan

## Context

The Musubi GUI is currently a Tauri desktop shell that reads the real
Musubi `audit.db` and renders the orchestration state. It does not own the
agent runtime. The backend remains the portable Python Musubi package in the
project folder, and the standalone `agent` CLI is the primary driver surface.

The desired behavior is an on-demand launcher:

- Opening the GUI must not start an agent.
- The GUI starts work only when the user submits a task and presses Run.
- The spawned process must be the governed standalone `agent "<task>"` path.
- The GUI must stream stdout/stderr, support cancellation, and keep rendering
  the audit DB written by the backend.
- No browser, localhost simulation, embedded web driver, or substrate-side LLM
  calls should be introduced.

This keeps the GUI as a native control surface while Musubi's Python package
remains the reusable backend that can be moved across projects.

## Goal

Implement a native Tauri task launcher for the GUI:

1. Let the user type a task in the GUI and choose the active LM profile.
2. Spawn one `agent` CLI process only when Run is pressed.
3. Run the child process in the detected project root with the correct Musubi
   environment.
4. Stream bounded stdout/stderr into GUI state.
5. Cancel the running process when Stop is pressed.
6. Keep the audit DB as the source of truth for orchestration state.

Non-goals:

- Do not embed Python agent logic in Rust or React.
- Do not add a local HTTP server.
- Do not add direct LLM SDK calls to the GUI.
- Do not auto-run tasks at GUI startup.
- Do not implement full multi-task queueing in the first slice.

## Tech Stack

- Rust / Tauri 2 in `gui/src-tauri/src/lib.rs`
- Rust data model in `gui/src-tauri/musubi-data/src/lib.rs`
- React presentation layer in `gui/src/views/Pipeline.jsx`
- View-model mapping in `gui/src/model/viewModel.js`
- Native Tauri bridge in `gui/src/data/TauriSource.js`
- Python backend invoked through the installed or locally discoverable `agent`
  CLI
- Existing audit database discovery via `MUSUBI_DB`, `MUSUBI_ROOT`, and
  workspace/package fallback

## Architecture

Add a small process manager to the Tauri side. The process manager owns the
single active child process and exposes a serializable launcher snapshot to the
React UI.

```text
React Task Runner
  -> Tauri action("run_task", [task, profile])
    -> Rust process manager
      -> spawn: agent "<task>" --profile <profile>
        -> Python Musubi backend
          -> LMRouter, policy gates, audit DB, compression, skills
  <- state://update includes task launcher status + audit DB snapshot
```

The GUI will continue polling the audit DB as it does today. Task output is a
GUI runtime overlay, not the orchestration source of truth.

## Implementation Steps

### Step 1 - Add launcher state to the Rust data model

- Add a serializable `TaskLauncherStatus` to
  `gui/src-tauri/musubi-data/src/lib.rs`.
- Add `task_launcher: TaskLauncherStatus` to `State`.
- Keep defaults empty and idle so existing snapshots remain valid.

Suggested shape:

```rust
#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct TaskLauncherStatus {
    pub running: bool,
    pub task: String,
    pub profile: String,
    pub started_at: Option<i64>,
    pub finished_at: Option<i64>,
    pub exit_code: Option<i32>,
    pub stdout_tail: String,
    pub stderr_tail: String,
    pub error: String,
}
```

### Step 2 - Implement a Tauri process manager

- Extend `AppState` in `gui/src-tauri/src/lib.rs` with a
  `Mutex<TaskProcessState>`.
- Store:
  - child process handle
  - running flag
  - current task/profile
  - bounded stdout/stderr tails
  - last exit code or launch error
- Use `std::process::Command` with piped stdout/stderr.
- Spawn background reader threads for stdout and stderr.
- Bound output memory, for example keep the last 64 KiB per stream.
- Update launcher status on process exit.

Important behavior:

- Reject empty tasks.
- Reject a second `run_task` while one task is already running.
- Do not block the Tauri event loop while the child process runs.
- On `cancel_task`, terminate the child process and mark the launcher stopped.

### Step 3 - Build launch spec deterministically

Create a testable helper, for example:

```rust
struct AgentLaunchSpec {
    program: PathBuf,
    args: Vec<String>,
    cwd: PathBuf,
    env: Vec<(String, String)>,
}
```

The helper should:

- Use the detected `agent` CLI path from setup detection when available.
- Fall back to `"agent"` if the command is expected on `PATH`.
- Use the detected project root as current directory.
- Add the task as the positional `agent` argument.
- Add `--profile <profile>` only when a profile is selected.
- Forward `MUSUBI_ROOT` when already set or when the GUI has a resolved project
  root that should anchor the backend.
- Forward `MUSUBI_DB` only for GUI/audit compatibility; do not depend on it as
  the agent's only storage input.
- Preserve existing environment variables so provider credentials still work.

Example command:

```powershell
agent "add a health endpoint and tests" --profile azure.work
```

### Step 4 - Wire Tauri actions

Extend `action(kind, args, state)` in `gui/src-tauri/src/lib.rs`:

- `run_task`
  - args: `[task, profile]`
  - validates the task
  - builds the launch spec
  - starts the process manager
  - returns a clear error if setup is incomplete
- `cancel_task`
  - kills the active child process if present
  - leaves already-written audit rows intact
- `clear_task_output`
  - clears local stdout/stderr/error tails only

Keep existing pipeline placeholder actions unchanged until pipeline execution
is implemented separately.

### Step 5 - Expose launcher state to React

Update `gui/src/data/TauriSource.js`:

- Add `taskLauncher` to `DOMAIN_KEYS`.
- Add default local state:
  - `taskDraft`
  - `taskProfile`
- Add actions:
  - `onTaskDraft`
  - `setTaskProfile`
  - `runTask`
  - `cancelTask`
  - `clearTaskOutput`

Update `gui/src/model/viewModel.js`:

- Map launcher status to stable UI fields:
  - `taskRunning`
  - `taskRunLabel`
  - `taskRunDisabled`
  - `taskStatusText`
  - `taskStdout`
  - `taskStderr`
  - `taskError`
  - `taskExitCode`
- Keep active profile display aligned with the existing model settings view.

### Step 6 - Add the task runner UI

Update `gui/src/views/Pipeline.jsx` or split a small
`TaskLauncher.jsx` component if the file becomes too large.

First slice UI:

- A task textarea at the top of the Pipeline view.
- A profile field/select using the active profile as default.
- Run / Stop button.
- Status line showing idle/running/exited/error.
- Bounded terminal-style stdout/stderr panel.
- Clear output button.

The UI should make the task runner the primary action while keeping the
existing pipeline composer visible as a future workflow builder.

### Step 7 - Document the runtime path

Update:

- `gui/README.md`
- `gui/src-tauri/SCHEMA.md`
- `docs/guide.md`
- `docs/roadmap.md`

Document:

- GUI launch does not start an agent.
- Run starts exactly one standalone `agent` process.
- The Python Musubi package remains the backend.
- The audit DB remains the orchestration source of truth.
- Local development still requires the native Tauri toolchain, while normal
  users should prefer the prebuilt Windows installer.

## Tests

Add Rust unit tests around deterministic launch behavior. Prefer testing helper
functions rather than spawning a real LLM-backed process.

Required tests:

- Build command includes the task as the positional argument.
- Build command includes `--profile` only when a profile is present.
- Empty task is rejected.
- Already-running launcher rejects a second task.
- Output tail truncation keeps the newest content and preserves UTF-8
  boundaries.
- Cancel path marks the launcher as stopped.
- Snapshot defaults serialize as idle launcher state.

Recommended commands:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml
npm --workspace gui run build
.\.venv\Scripts\python.exe -m pytest musubi\tests -q -p no:cacheprovider
git diff --check
```

Manual verification:

```powershell
npm --workspace gui run tauri:dev
```

Then:

1. Open the GUI.
2. Confirm no agent starts automatically.
3. Enter a small task.
4. Press Run.
5. Confirm stdout/stderr stream into the task panel.
6. Confirm audit rows appear in the existing Orchestrator/Audit views.
7. Press Stop during a long task and confirm the process is terminated.

## Rollout

Phase 1 should ship a single-task launcher. Queueing, saved task templates,
pipeline execution from GUI, and profile editing can follow later.

This gives users the needed behavior first: install the GUI, open it from any
Musubi project, type a task, and let the existing governed backend do the work.

## Open Questions

- Should the task runner live in the current Pipeline view, or should the nav
  label become `Run` / `Tasks` once the launcher is implemented?
- Should profile selection initially be a free-form text field or loaded from
  `.musubi/llm.json` into a dropdown?
- Should the GUI pass an explicit `--tool-surface agent` once the standalone
  CLI exposes that as a stable public flag for launcher use?

