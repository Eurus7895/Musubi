# Console Workspace Separation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Implement locally unless the operator explicitly requests delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Orchestrator the Console's only execution surface and turn Pipeline Studio into a fail-closed drag-and-drop pipeline recipe builder.

**Architecture:** Extend the existing Python composer and policy parser with one canonical flat-stage `spawns` projection, then expose a Rust recipe document/validation/save boundary to the React client. Orchestrator launches both direct and pipeline runs under the same durable chat ID and derives topology, logs, narrative, and skill provenance from existing audit rows; Pipeline Studio edits only recipe-owned fields and never launches a model process.

**Tech Stack:** Python 3.11, pytest, Rust/Tauri 2, rusqlite, serde/serde_yaml, React 18, Vite, Node's built-in test runner.

## Global Constraints

- The GUI shell and substrate make zero LLM calls; only the launched standalone `agent` driver reaches `LMRouter`.
- Orchestrator exposes exactly Direct and Pipeline modes; Direct is the default for a new session.
- Primary pipeline stages remain sequential; `spawns` is only an allowlist and same-turn sibling summons retain existing parallel dispatch semantics.
- The evaluator remains artifact-only, policy and spawn membership remain fail-closed, every spawn remains audited, and stage attempts remain append-only.
- Pipeline Studio may edit recipe-owned stage overrides and `spawns`; agent prompts, tools, turn caps, skills, and output budgets remain catalog-owned read-only data.
- Recipe writes stay inside `<project>/.github/pipelines/<safe-name>/pipeline.yaml`, reject traversal/symlink escape, validate before write, and replace atomically.
- Historical Pipeline Studio chat rows remain readable for compatibility but the new UI and command boundary never mint them.
- No new production dependency is added unless the existing lockfiles can resolve it deterministically.

---

### Task 1: Canonical flat-stage spawn projection

**Files:**
- Modify: `musubi/composer.py`
- Modify: `scripts/policy_engine.py`
- Test: `musubi/tests/test_composer.py`
- Test: `musubi/tests/test_g2_policy_validation.py`

**Interfaces:**
- Produces: `composer.pipeline_stage_entries(pipeline_name: str) -> list[dict[str, object]]` with normalized `agent`, `stage`, `preset`, and `spawns` keys.
- Produces: `_load_pipeline_spawns()` support for flat `stages[].spawns` keyed by resolved agent role.

- [ ] **Step 1: Write failing composer tests**

```python
def test_flat_stage_entries_preserve_spawn_allowlists(tmp_path, monkeypatch):
    _write_pipeline_yaml(tmp_path, "nested", {
        "name": "nested",
        "stages": [
            {"agent": "planner", "stage": "plan", "spawns": ["explorer"]},
            {"agent": "reviewer", "stage": "review"},
        ],
    })
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()
    assert composer.pipeline_stage_entries("nested")[0]["spawns"] == ["explorer"]
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_composer.py -q`

Expected: FAIL because `pipeline_stage_entries` does not exist.

- [ ] **Step 3: Implement one normalized stage parser and reuse it in the chain**

```python
def pipeline_stage_entries(pipeline_name: str) -> list[dict[str, object]]:
    data = _load_pipeline_yaml(pipeline_name)
    presets = _load_presets()
    entries: list[dict[str, object]] = []
    for raw in data.get("stages") or []:
        if not isinstance(raw, dict):
            continue
        resolved = _resolve_stage_entry(raw, presets)
        if not resolved:
            continue
        agent, stage = resolved
        spawns = raw.get("spawns")
        entries.append({
            "agent": agent,
            "stage": stage,
            "preset": str(raw.get("preset") or ""),
            "spawns": [r.lower() for r in spawns if isinstance(r, str)]
            if isinstance(spawns, list) else [],
        })
    return entries
```

- [ ] **Step 4: Add fail-closed spawn validation and policy tests**

Cover duplicate roles, unknown roles, non-string entries, and a role outside `MAIN_SUBAGENT_ALLOWLIST[agent]`; assert `_effective_spawn_roles()` returns the declared/firewall intersection and omitted `spawns` returns `[]`.

- [ ] **Step 5: Run composer and policy tests**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_composer.py musubi/tests/test_g2_policy_validation.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add musubi/composer.py scripts/policy_engine.py musubi/tests/test_composer.py musubi/tests/test_g2_policy_validation.py
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "feat(pipeline): support flat stage spawn allowlists"
```

### Task 2: Fail-closed recipe document and atomic save boundary

**Files:**
- Modify: `gui/src-tauri/musubi-data/Cargo.toml`
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Modify: `gui/src-tauri/src/lib.rs`
- Test: inline Rust tests in both modified `lib.rs` files

**Interfaces:**
- Produces: serializable `PipelineRecipe`, `PipelineStageRecipe`, `ResolvedStageContract`, and `PipelineFinding`.
- Produces: `read_pipeline_recipe(project_root, name)`, `validate_pipeline_recipe(project_root, recipe)`, and `save_pipeline_recipe(project_root, recipe)`.
- Produces Tauri actions: `load_pipeline_recipe`, `validate_pipeline_recipe`, and `save_pipeline_recipe`, with the saved catalog returned on the next state snapshot.

- [ ] **Step 1: Add failing Rust tests for round-trip, validation, and path safety**

```rust
#[test]
fn flat_recipe_round_trips_spawns_without_owning_agent_contract() {
    let recipe = parse_pipeline_recipe(RAW).unwrap();
    assert_eq!(recipe.stages[0].spawns, vec!["explorer"]);
    assert!(!render_pipeline_recipe(&recipe).contains("maxOutputTokens"));
}

#[test]
fn invalid_recipe_does_not_replace_existing_file() {
    let before = fs::read_to_string(&path).unwrap();
    assert!(save_pipeline_recipe(&root, invalid).is_err());
    assert_eq!(fs::read_to_string(path).unwrap(), before);
}
```

- [ ] **Step 2: Run Rust data tests and verify failure**

Run: `cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml pipeline_recipe`

Expected: compile failure for missing recipe interfaces.

- [ ] **Step 3: Implement YAML parse/render and resolved catalog projection**

Use `serde_yaml` structs with `#[serde(deny_unknown_fields)]` on recipe-owned structures. Resolve prompt path, role skill, tool allowlist, max turns, and output budget from the existing agent/policy catalogs into `ResolvedStageContract`; never serialize those resolved fields back into pipeline YAML.

- [ ] **Step 4: Implement safe atomic save**

Canonicalize the pipeline root and existing target parent, reject unsafe names and symlink escapes, write a same-directory `.pipeline.yaml.tmp`, `sync_all`, then rename. Validate the complete catalog after the rename; return a distinct `saved_but_refresh_failed` result if refresh fails.

- [ ] **Step 5: Wire Tauri actions and remove the Studio launch action**

`send_chat` accepts `args = [text, requested_chat_id, mode, pipeline_name]`; it validates Pipeline mode against the current catalog and calls `start_chat_agent(..., Some(pipeline_name))` using the Orchestrator chat ID. Delete the `send_pipeline_task` mutation path while leaving legacy DB reads intact.

- [ ] **Step 6: Run Rust suites**

Run: `cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml`

Run: `cargo test --manifest-path gui/src-tauri/Cargo.toml`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add gui/src-tauri/musubi-data/Cargo.toml gui/src-tauri/musubi-data/Cargo.lock gui/src-tauri/musubi-data/src/lib.rs gui/src-tauri/Cargo.lock gui/src-tauri/src/lib.rs
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "feat(gui): add governed pipeline recipe storage"
```

### Task 3: Pipeline Studio builder state

**Files:**
- Create: `gui/src/model/pipelineBuilder.js`
- Create: `gui/src/model/pipelineBuilder.test.mjs`
- Modify: `gui/src/data/TauriSource.js`
- Modify: `gui/src/data/TauriSource.test.mjs`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/model/viewModel.test.mjs`

**Interfaces:**
- Produces: `createPipelineDraft`, `moveStage`, `setStageSpawns`, `isDirty`, and `validateDraft` pure functions.
- Produces view-model fields `pipelineBuilder.{step,draft,selectedStage,findings,canSave}` and actions for new/load/save/import/export/add/reorder/remove/spawns/unsaved confirmation.

- [ ] **Step 1: Write failing pure-state tests**

```javascript
test('moving a stage preserves its spawn allowlist', () => {
  const draft = createPipelineDraft({ stages: [stage('plan', ['explorer']), stage('review')] })
  assert.deepEqual(moveStage(draft, 0, 1).stages[1].spawns, ['explorer'])
})

test('new, close, and switch require confirmation when dirty', () => {
  assert.equal(nextPipelineSelection(dirtyDraft, 'other').needsConfirmation, true)
})
```

- [ ] **Step 2: Run Node tests and verify failure**

Run: `node --test gui/src/model/pipelineBuilder.test.mjs gui/src/data/TauriSource.test.mjs gui/src/model/viewModel.test.mjs`

Expected: FAIL for missing builder functions.

- [ ] **Step 3: Implement immutable builder transitions**

Keep draft state local so polling cannot overwrite edits. Backend responses update only load/validation/save result fields. Save stays disabled for dirty unresolved catalog data, fewer than two stages, or any error finding.

- [ ] **Step 4: Replace Studio runtime actions with recipe actions**

Remove `pipeChat`, `pipeDraft`, `pipeRunning`, `newPipeSession`, `sendPipelineTask`, and runtime selection from the active Studio view model. Retain no-op compatibility projection only where old snapshots still contain those keys.

- [ ] **Step 5: Run all GUI data/model tests**

Run: `node --test gui/src/**/*.test.mjs`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add gui/src/model/pipelineBuilder.js gui/src/model/pipelineBuilder.test.mjs gui/src/data/TauriSource.js gui/src/data/TauriSource.test.mjs gui/src/model/viewModel.js gui/src/model/viewModel.test.mjs
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "feat(gui): model pipeline builder state"
```

### Task 4: Builder-only Pipeline Studio UI

**Files:**
- Modify: `gui/src/views/Pipeline.jsx`
- Modify: `gui/src/index.css`
- Test: `gui/src/model/viewModel.test.mjs`

**Interfaces:**
- Consumes: `vals.pipelineBuilder` and builder actions from Task 3.
- Produces: Basics, Stages, Handoffs, and Validate workspace with no execution/chat/history controls.

- [ ] **Step 1: Add render-contract assertions**

Render the static component in a Node test or assert the view model exposes all labels/actions needed for: `New Pipeline`, `Save Pipeline`, four guided steps, ordered stages, selected-stage resolved contract, `May spawn`, validation findings, YAML target/diff, and unsaved confirmation.

- [ ] **Step 2: Implement the guided workspace**

Stages is the only ordered lane. Handoffs shows the read-only sequential backbone plus per-parent `May spawn` clusters and the exact copy “Runs in parallel only when summoned in the same worker turn.” Validate owns the sole full topology/YAML preview.

- [ ] **Step 3: Remove Studio execution UI**

Delete Chat · pipeline, New pipeline session, Run, Studio runs, Pipeline run history, token telemetry, and process-log controls from `Pipeline.jsx`.

- [ ] **Step 4: Build and test**

Run: `node --test gui/src/**/*.test.mjs`

Run: `npm run build --prefix gui`

Expected: PASS and Vite build succeeds.

- [ ] **Step 5: Commit**

```powershell
git add gui/src/views/Pipeline.jsx gui/src/index.css gui/src/model/viewModel.test.mjs
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "feat(gui): make Pipeline Studio builder-only"
```

### Task 5: Unified Orchestrator direct/pipeline launch contract

**Files:**
- Modify: `gui/src/data/TauriSource.js`
- Modify: `gui/src/data/TauriSource.test.mjs`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/model/viewModel.test.mjs`
- Modify: `gui/src/views/Orchestrator.jsx`
- Modify: `gui/src/components/ChatBody.jsx`

**Interfaces:**
- Produces local durable composer state `runMode: 'direct' | 'pipeline'` and `selectedPipeline`.
- Consumes Task 2 `send_chat` args and registered `pipelineCatalog`.

- [ ] **Step 1: Write failing launch-contract tests**

```javascript
test('new sessions default to direct mode', () => assert.equal(vm.runMode, 'direct'))
test('pipeline send uses the viewed orchestrator session', async () => {
  source.actions.setRunMode('pipeline')
  source.actions.selectPipeline('dev-lite')
  source.actions.sendChat()
  assert.deepEqual(invoked.args, ['ship it', 'chat-old', 'pipeline', 'dev-lite'])
})
```

- [ ] **Step 2: Implement Direct/Pipeline composer**

Pipeline mode requires a runnable registered recipe. Run/Cancel reflects exact shared-driver ownership; browsing a non-owning historical session remains read-only and the first idle follow-up retains the existing atomic resume path.

- [ ] **Step 3: Remove Auto and pipeline navigation command behavior**

`/pipeline` may select Pipeline mode in the current Orchestrator composer, but it must not navigate to Studio or create a separate chat ID.

- [ ] **Step 4: Run GUI tests and build**

Run: `node --test gui/src/**/*.test.mjs`

Run: `npm run build --prefix gui`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add gui/src/data/TauriSource.js gui/src/data/TauriSource.test.mjs gui/src/model/viewModel.js gui/src/model/viewModel.test.mjs gui/src/views/Orchestrator.jsx gui/src/components/ChatBody.jsx
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "feat(gui): launch pipelines from Orchestrator"
```

### Task 6: Evidence-derived graph, worker logs, narrative, and skill provenance

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/model/viewModel.test.mjs`
- Modify: `gui/src/views/Orchestrator.jsx`
- Modify: `gui/src/components/ChatBody.jsx`
- Modify: `gui/src/index.css`

**Interfaces:**
- Produces audit projection fields needed to correlate cycle/tool/policy/skill rows to stable worker IDs without reconstructing hidden data.
- Produces `runtimeGraph`, `workerLogs`, `conversationMode`, and `skillsByWorker` view-model projections.

- [ ] **Step 1: Write topology and provenance tests**

Use fixtures for sequential siblings, siblings sharing `(parent, spawn_turn)`, nested children, absent evaluator, role-skill injection, successful `musubi_get_skill`, and failed skill calls. Assert only evidence-backed nodes/skills appear.

- [ ] **Step 2: Write log privacy tests**

Assert excerpts come only from stored audit-safe fields, are display-capped, preserve elision markers, and never read environment/process credentials. Assert Conversation Verbose includes tool/skill names and statuses but excludes raw argument/output excerpts.

- [ ] **Step 3: Implement projections**

Graph nodes contain only identity/status/parent/depth/timing/counts. Selecting a node opens Logs filtered by stable worker ID. Logs support All/Tools/Skills/Policy/Model and search; Open in Audit selects the corresponding ledger row/filter.

- [ ] **Step 4: Replace the large summary and expanded cards**

Render the approved three-panel layout: collapsible Sessions, center composer/status/Graph/Logs, collapsible Conversation with Summary/Verbose. Conversation owns narrative and artifact cards; Graph and Logs do not repeat it.

- [ ] **Step 5: Run all GUI and Rust tests**

Run: `node --test gui/src/**/*.test.mjs`

Run: `npm run build --prefix gui`

Run: `cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add gui/src-tauri/musubi-data/src/lib.rs gui/src/model/viewModel.js gui/src/model/viewModel.test.mjs gui/src/views/Orchestrator.jsx gui/src/components/ChatBody.jsx gui/src/index.css
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "feat(gui): separate runtime topology from evidence"
```

### Task 7: Compatibility cleanup and current-state documentation

**Files:**
- Modify: `AGENTS.md`
- Modify: `gui/README.md`
- Modify: `docs/guide.md`
- Modify: `gui/src-tauri/SCHEMA.md`
- Modify: `docs/roadmap.md`
- Modify: `gui/src-tauri/src/lib.rs`
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`

**Interfaces:**
- Removes active Pipeline Studio session creation/execution paths while retaining read compatibility for historical rows.
- Documents the exact post-change owner of recipe, run, graph, narrative, logs, Audit, and policy concerns.

- [ ] **Step 1: Add compatibility regression tests**

Assert old `surface='pipeline'` chat rows remain loadable, new recipe save does not create chat/session rows, a pipeline launch writes under the current Orchestrator chat ID, and historical read-only/atomic-resume/cancel ownership tests still pass.

- [ ] **Step 2: Delete dead runtime mutations and local state**

Remove `pipeline_chat_id` creation, Pipeline Studio clear/new/send actions, and active runtime UI projections only after the compatibility tests protect old reads.

- [ ] **Step 3: Update all required documents**

State that Orchestrator is the only execution surface, Studio is builder-only, flat `stages[].spawns` is an allowlist, primary stages are sequential, same-turn nested siblings can run in parallel, and skill provenance is evidence-backed.

- [ ] **Step 4: Mark the roadmap item complete**

Move Console workspace separation from Active to Completed Tracks and link both the approved spec and this implementation plan.

- [ ] **Step 5: Run full verification**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests -q`

Run: `node --test gui/src/**/*.test.mjs`

Run: `npm run build --prefix gui`

Run: `cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml`

Run: `cargo test --manifest-path gui/src-tauri/Cargo.toml`

Run: `git diff --check`

Expected: all tests/builds pass and no whitespace errors.

- [ ] **Step 6: Commit**

```powershell
git add AGENTS.md gui/README.md docs/guide.md gui/src-tauri/SCHEMA.md docs/roadmap.md gui/src-tauri/src/lib.rs gui/src-tauri/musubi-data/src/lib.rs
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "docs(gui): document Console workspace ownership"
```

### Task 8: Branch verification and publication

**Files:**
- Verify only; no planned source changes.

**Interfaces:**
- Produces a feature branch rebased onto current `origin/dev`, with verified commits ready for review.

- [ ] **Step 1: Fetch and verify ancestry**

Run: `git fetch origin`

Run: `git rev-list --left-right --count origin/dev...HEAD`

Expected: left count is `0`; otherwise rebase onto `origin/dev` and repeat the full verification suite.

- [ ] **Step 2: Verify identity and scope**

Run: `git log --format="%h %an <%ae> | %cn <%ce> | %s" origin/dev..HEAD`

Expected: every author and committer is `Eurus <t.hoang7895@gmail.com>` and every subject follows Conventional Commits.

Run: `git status --short`

Expected: only known user-owned untracked artifacts remain; no implementation file is uncommitted.

- [ ] **Step 3: Push**

Run: `git push -u origin feat/gui-workspace-separation`

Expected: branch is published without pushing directly to `dev`.
