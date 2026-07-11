# GUI Pipeline Studio Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Pipeline Studio run its selected registered pipeline directly in an isolated current session, display real pipeline runs correctly, and replace overlapping chat controls with the approved New session pill on both surfaces.

**Architecture:** The Tauri backend will expose exact current chat IDs and a registered Studio pipeline catalog, and launch the existing `agent --pipeline` path for Studio submissions. The Rust data reader will construct pipeline run rows from `pipeline_runs` plus the append-only pipeline-envelope/stage ancestry, while the frontend will render those rows and filter both surfaces by exact chat ID. The Python pipeline runner will finalize every pipeline envelope on terminal paths.

**Tech Stack:** React 18, Vite, Node test runner, Rust/Tauri, rusqlite, Python 3.11+, pytest, Musubi MCP tools.

## Global Constraints

- The substrate makes zero LLM calls; only the standalone driver reaches a model.
- Pipeline selection is explicit and deterministic.
- Keep evaluator firewall, fail-closed policy, append-only audit, and visible spawn/completion events intact.
- Preserve one shared child-process slot across Orchestrator and Pipeline Studio.
- Do not make edited client-only compositions executable.
- Do not add a YAML dependency; read only the supported registered pipelines needed by Studio.
- Use exact current chat IDs for current-session views; prefix matching is fallback-only for legacy rows.

---

### Task 1: Registered Studio Pipeline Catalog and Launch Spec

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`

**Interfaces:**
- Produces: `PipelineCatalogEntry { name, description, stages, runnable, blocked_reason }`.
- Produces: `read_studio_pipeline_catalog(project_root: &Path) -> Vec<PipelineCatalogEntry>`.
- Changes: `build_agent_launch_spec(..., chat_id: Option<&str>, pipeline_name: Option<&str>) -> Result<AgentLaunchSpec, String>`.

- [ ] **Step 1: Write failing catalog and launch-spec tests**

Add Rust tests that create temporary `feature-dev` and `dev-lite` YAML files, assert their ordered stages, and assert unsupported `code-review` is excluded. Extend the launch-spec test with:

```rust
let spec = build_agent_launch_spec(
    "ship it", "", "", None, Path::new("/proj"),
    &HashMap::new(), Some("gui-pipeline-abc"), Some("feature-dev"),
).unwrap();
assert_eq!(spec.args, vec![
    "ship it", "--chat-id", "gui-pipeline-abc",
    "--pipeline", "feature-dev", "--tool-surface", "agent",
]);
```

Also assert `../bad`, unknown names, empty briefs, and a single-stage recipe return `Err` before a launch spec is built.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml studio_pipeline
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml launch_spec_adds_pipeline
```

Expected: FAIL because the catalog API and pipeline argument do not exist.

- [ ] **Step 3: Implement the minimal catalog and optional pipeline argument**

Use a fixed supported-name list (`feature-dev`, `dev-lite`) and a small line-oriented reader for their known schema shapes. Validate names with ASCII lowercase/digit/hyphen only, require at least two resolved stages, and append `--pipeline NAME` after `--chat-id` and before `--tool-surface`.

- [ ] **Step 4: Run Rust data tests and verify GREEN**

Run `cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml`.

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add gui/src-tauri/musubi-data/src/lib.rs
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(gui): expose runnable studio pipelines"
```

### Task 2: Exact Session IDs and Pipeline Run Ancestry

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Modify: `gui/src-tauri/src/lib.rs`
- Modify: `gui/src-tauri/SCHEMA.md`

**Interfaces:**
- Produces in `State`: `orchestratorChatId`, `pipelineChatId`, `pipelineCatalog`, `pipelineRuns`.
- Produces: `PipelineRun { session_id, chat_id, pipeline_name, brief, started_at, ended_at, status, stages }`.
- Consumes: pipeline envelope rows where `role = pipeline:<name>` and stage rows whose `parent_session_id` is the envelope handle/session ID.

- [ ] **Step 1: Write failing ancestry tests**

Insert an `agent_turns` outer session, a `pipeline_runs` row, a pipeline envelope spawn, and two child stage lifecycle pairs. Assert one `PipelineRun` owns the exact `gui-pipeline-...-nonce` ID and contains only the two stages. Add a second old pipeline chat ID and assert it remains distinguishable.

- [ ] **Step 2: Run the focused test and verify RED**

Run `cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml pipeline_run_ancestry`.

Expected: FAIL because `State.pipeline_runs` does not exist.

- [ ] **Step 3: Implement pipeline envelope folding**

Retain pipeline marker rows while folding audit data, build `outer_parent -> chat_id`, then map `pipeline_session -> chat_id` through the marker. Load `pipeline_runs`, attach non-marker agents by `parent_session == pipeline_session`, and exclude those descendants from ordinary surface grouping by representing them only inside `PipelineRun`.

In `snapshot`, set exact current IDs from the two mutex slots and populate the catalog from `state.project_root`.

- [ ] **Step 4: Document the backend state contract**

Update `SCHEMA.md` so Pipeline Studio maps from `pipeline_runs` and ancestry, and document exact current chat IDs.

- [ ] **Step 5: Run Rust suites and verify GREEN**

Run both Rust crates. Expected: all tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add gui/src-tauri/musubi-data/src/lib.rs gui/src-tauri/src/lib.rs gui/src-tauri/SCHEMA.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(gui): scope pipeline runs to exact sessions"
```

### Task 3: Deterministic Studio Launch Action

**Files:**
- Modify: `gui/src-tauri/src/lib.rs`
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`

**Interfaces:**
- Produces Tauri action: `send_pipeline_task([brief, pipelineName])`.
- Changes: `start_chat_agent(..., surface: &str, pipeline_name: Option<&str>)`.
- Adds to `DriverStatus`: `pipelineName`.

- [ ] **Step 1: Write failing backend action/validation tests**

Extract and test `prepare_pipeline_launch(brief, pipeline_name, catalog) -> Result<String, String>` for valid, empty, unsafe, missing, and non-runnable inputs. Assert invalid input does not insert the user message or spawn.

- [ ] **Step 2: Run and verify RED**

Run `cargo test --manifest-path gui/src-tauri/Cargo.toml pipeline_launch`.

Expected: FAIL because the helper/action does not exist.

- [ ] **Step 3: Implement direct action and shared launcher**

Replace `send_pipe_chat` with `send_pipeline_task`, pass the selected pipeline into `build_agent_launch_spec`, and store it in runtime status. Keep Orchestrator calls at `pipeline_name = None`. Insert validation failures as pipeline deny messages without inserting the user brief or starting a process.

- [ ] **Step 4: Run Rust tests and verify GREEN**

Run `cargo test --manifest-path gui/src-tauri/Cargo.toml`. Expected: all tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add gui/src-tauri/src/lib.rs gui/src-tauri/musubi-data/src/lib.rs
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(gui): launch selected studio pipeline"
```

### Task 4: Finalize Pipeline Envelopes on Every Exit

**Files:**
- Modify: `musubi/agent/pipeline_runner.py`
- Modify: `musubi/server.py`
- Modify: `musubi/tests/test_spawn_pipeline.py`
- Modify: `musubi/tests/test_g3_observability.py`

**Interfaces:**
- Consumes: `musubi_finalize_pipeline_run(session_id, final_status, escalated)`.
- Produces: exactly one terminal `subagent_audit` completion row for the `pipeline:<name>` envelope.

- [ ] **Step 1: Write failing success and failure finalization tests**

Extend the fake MCP dispatcher to capture `musubi_finalize_pipeline_run` calls. Assert success finalizes as `success`, strict stage rejection as `aborted`, and budget/cycle escalation as `escalated`. Add a server test asserting finalization appends the pipeline-envelope completion row once.

- [ ] **Step 2: Run and verify RED**

Run `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_spawn_pipeline.py musubi/tests/test_g3_observability.py -q`.

Expected: FAIL because the runner never finalizes its pipeline session/envelope.

- [ ] **Step 3: Implement terminal finalization**

Wrap the stage loop after `psid` creation, select `success`, `escalated`, or `aborted`, and call the finalizer once in `finally`. Extend the server finalizer to locate the matching pipeline envelope spawn and append `record_complete` only if no completion row exists.

- [ ] **Step 4: Run Python tests and verify GREEN**

Run the two focused files. Expected: all tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add musubi/agent/pipeline_runner.py musubi/server.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_g3_observability.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): finalize deterministic pipeline runs"
```

### Task 5: Frontend Exact-Session State and Studio Submission

**Files:**
- Modify: `gui/src/data/TauriSource.js`
- Modify: `gui/src/data/createSource.js`
- Modify: `gui/src/data/TauriSource.test.mjs`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/model/viewModel.test.mjs`
- Modify: `gui/src/model/data.js`

**Interfaces:**
- Consumes backend: `orchestratorChatId`, `pipelineChatId`, `pipelineCatalog`, `pipelineRuns`, `driverStatus.pipelineName`.
- Produces action: `sendPipelineTask()` -> `_action('send_pipeline_task', [brief, pipeName])`.
- Produces VM: registered/modified state, exact current runs, pipeline card fields, input placeholder, and disabled reason.

- [ ] **Step 1: Write failing action tests**

```js
source._setLocal({ pipeDraft: ' ship it ', pipeName: 'feature-dev', pipeModified: false })
source.actions.sendPipelineTask()
assert.deepEqual(calls, [{
  kind: 'send_pipeline_task',
  args: ['ship it', 'feature-dev'],
}])
```

Add a test that modified/unregistered composition makes no backend call.

- [ ] **Step 2: Write failing view-model tests**

Create current and old exact pipeline chat IDs plus backend `pipelineRuns`. Assert only current runs render, card title is `feature-dev`, subtitle is `4 stages`, and no Studio string contains `driver-only turn`. Assert Orchestrator excludes pipeline descendants.

- [ ] **Step 3: Run and verify RED**

Run `node --test gui/src/data/TauriSource.test.mjs gui/src/model/viewModel.test.mjs`.

Expected: FAIL because the new state/action/run model does not exist.

- [ ] **Step 4: Implement state and VM changes**

Add backend-owned keys, initialize safe defaults, replace static runnable presets with backend catalog entries, mark composition modified on add/remove/move/clear, and make load registered pipeline restore runnable state. Build Studio cards from `pipelineRuns` plus the pipeline-owned live overlay. Filter Orchestrator agent turns by exact `orchestratorChatId`.

- [ ] **Step 5: Run Node tests and verify GREEN**

Run the focused files. Expected: all tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add gui/src/data/TauriSource.js gui/src/data/createSource.js gui/src/data/TauriSource.test.mjs gui/src/model/viewModel.js gui/src/model/viewModel.test.mjs gui/src/model/data.js
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(gui): render current studio pipeline runs"
```

### Task 6: Approved Session Controls and Studio Copy

**Files:**
- Create: `gui/src/components/NewSessionButton.jsx`
- Create: `gui/src/components/NewSessionButton.test.mjs`
- Modify: `gui/src/views/Pipeline.jsx`
- Modify: `gui/src/views/Orchestrator.jsx`
- Modify: `gui/src/components/ChatBody.jsx`

**Interfaces:**
- Produces reusable `NewSessionButton({ disabled, onClick })`.
- Consumes VM pipeline placeholder, runnable state, submit/cancel semantics.

- [ ] **Step 1: Write failing component-source test**

Use a focused Node source test to assert the component renders a visible plus and `New session`, and that Pipeline/Orchestrator no longer reference `onClearDriverChat`, `closePipeChat`, or the old lowercase control.

- [ ] **Step 2: Run and verify RED**

Run `node --test gui/src/components/NewSessionButton.test.mjs`.

Expected: FAIL because the component does not exist and old controls remain.

- [ ] **Step 3: Implement the approved accent pill**

Create the 32 px, 9 px radius amber translucent button with plus icon and label. Use it in both headers; remove Clear chat and Pipeline close controls. Update Studio header subtitle, placeholder, and submit title to name the selected pipeline.

- [ ] **Step 4: Run tests and build**

Run the component/data/VM tests and `npm run build --workspace musubi-gui`.

Expected: tests PASS and Vite build succeeds.

- [ ] **Step 5: Commit**

```powershell
git add gui/src/components/NewSessionButton.jsx gui/src/components/NewSessionButton.test.mjs gui/src/views/Pipeline.jsx gui/src/views/Orchestrator.jsx gui/src/components/ChatBody.jsx
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "style(gui): unify new session controls"
```

### Task 7: Roadmap and Full Verification

**Files:**
- Modify: `docs/roadmap.md`
- Modify: `docs/superpowers/plans/2026-07-10-gui-pipeline-studio-sessions.md`

**Interfaces:**
- Documents the direct Studio pipeline launch and exact-session run scoping.

- [ ] **Step 1: Update roadmap**

Replace the completed-track statement that Studio is only a composer/inspector with the new deterministic launch behavior and link this plan.

- [ ] **Step 2: Run full verification**

Run:

```powershell
node --test gui/src/data/*.test.mjs gui/src/model/*.test.mjs gui/src/components/*.test.mjs
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml
cargo test --manifest-path gui/src-tauri/Cargo.toml
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_spawn_pipeline.py musubi/tests/test_g3_observability.py -q
npm run build --workspace musubi-gui
git diff --check
```

Expected: all tests PASS, production build succeeds, and diff check is empty.

- [ ] **Step 3: Visually verify the running GUI**

Verify both headers use the accent pill; Studio has no clear/close controls; New session empties only the owning current session; submitting a registered pipeline creates a Studio live card and invokes the direct runner; Orchestrator does not show pipeline stages; failure copy appears in Studio chat.

- [ ] **Step 4: Commit documentation**

```powershell
git add docs/roadmap.md docs/superpowers/plans/2026-07-10-gui-pipeline-studio-sessions.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "docs(roadmap): record runnable pipeline studio"
```

---

## Post-implementation corrective work (2026-07-11)

Visual review exposed contradictory status, incomplete pipeline progress, and
ambiguous Studio controls. Complete Tasks 8–11 before merge.

### Task 8: Reconcile Driver and Pipeline Terminal State

**Files:** `gui/src-tauri/src/lib.rs`, `gui/src-tauri/musubi-data/src/lib.rs`,
`gui/src/data/TauriSource.js`, `gui/src/model/viewModel.js`, and focused tests.

**Problem:** The chat can say `Budget halted before the next model call.` while
the process card says `agent running` and the Studio card says `running`.

**Contract:** Normalize every run to one state: `running | success | aborted |
escalated | budget_halted | failed`. Prefer finalized `pipeline_runs` data;
until it arrives use exited process data. An exited child must set
`driverStatus.running = false`.

- [ ] Add a failing test for a budget-halted child exit followed by a finalized
  pipeline row; assert chat, process card, Studio card, and detail all report
  `budget_halted`, with no active-worker copy. Cover success and non-budget
  failure separately.
- [ ] Implement one named terminal-state mapper and route all status badges,
  messages, and summaries through it. Do not infer liveness from a stale flag.
- [ ] Verify: `node --test gui/src/data/TauriSource.test.mjs gui/src/model/viewModel.test.mjs`,
  plus both Rust crate test suites; then commit `fix(gui): reconcile pipeline terminal status`.

### Task 9: Make Pipeline Progress and Empty Stages Legible

**Files:** `gui/src/model/viewModel.js`, `gui/src/model/viewModel.test.mjs`,
`gui/src/views/Pipeline.jsx`, and `gui/src/model/data.js` only if stage
metadata must change.

**Problem:** `feature-dev` advertises four stages while only three are readily
discoverable; `designer` shows `0 tools / max 0 turns`; and the detail heading
truncates as `Session driver-runni`.

**Contract:** Every configured stage is discoverable with an explicit overflow
affordance. Render `handoff-only` only for explicit metadata; otherwise show
an actionable configuration error. Use `<pipeline name> · run <short id>` for
the detail title.

- [ ] Test four visible/discoverable stage labels, a flow overflow indication,
  no misleading zero-tool worker card, and a semantic detail title.
- [ ] Model handoff-only intent explicitly rather than silently accepting a
  malformed worker; render the complete flow and stable title.
- [ ] Verify `node --test gui/src/model/viewModel.test.mjs gui/src/components/*.test.mjs`
  and `npm run build --workspace musubi-gui`; commit `fix(gui): clarify pipeline stage progress`.

### Task 10: Remove Ambiguous Studio Controls and Duplicate Task Events

**Files:** `gui/src/views/Pipeline.jsx`, `gui/src/components/ChatBody.jsx`,
`gui/src/data/TauriSource.js`, `gui/src/data/TauriSource.test.mjs`,
`gui/src/components/NewSessionButton.jsx`, and its test.

**Problem:** Header `clear` actually clears editable composition, not chat
history, while sitting beside New session. The same task appears in a user
bubble and again as an isolated driver-process card.

**Contract:** `New pipeline session` is the only Studio header reset action;
it creates a fresh Studio chat/session without deleting audit history or
changing completed runs. Remove header `clear`. If reset is retained, make it
an editor-only, explicitly named, confirmation-protected action. Attach
process progress to the originating run/message rather than add a duplicate
user-like event.

- [ ] Test absence of a header `onClearPipe` button, the accessible `New pipeline
  session` label, a single task brief with attached progress, and disabled New
  session only while the shared child process is active.
- [ ] Remove obsolete clear actions without UI callers; preserve the draft
  through New session; make any retained editor reset explicit and confirmed.
- [ ] Verify `node --test gui/src/data/TauriSource.test.mjs gui/src/components/NewSessionButton.test.mjs gui/src/components/chatLinks.test.mjs`
  and production build; commit `fix(gui): simplify studio session controls`.

### Task 11: Corrective Regression Suite and Manual Acceptance

**Files:** `docs/roadmap.md` and this plan.

- [ ] Run Node, both Rust suites, focused Python pipeline tests, production
  build, and `git diff --check`.
- [ ] Manually run one completed and one budget-halted pipeline. For both,
  verify all four status surfaces agree, stage count matches the flow, every
  stage is discoverable, and no header clear action or duplicate brief remains.
- [ ] Update the roadmap only after acceptance passes; commit
  `docs(roadmap): record studio corrective pass`.
