# Stage Goals and the Deterministic Stage Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every pipeline stage advance only after a model-authored, frozen acceptance contract passes deterministic harness checks, with explicit model-selected skills, append-only attempts, crash-safe resume, and attributable Console evidence.

**Architecture:** The standalone driver performs a bounded model preflight, while substrate modules validate recipes, skills, contracts, policy, persistence, and deterministic predicates without making model calls. `musubi.db` stores write-once attempt checkpoints and an append-only transition ledger; `audit.db` remains the durable worker/tool evidence record. Pipeline Studio authors only recipe ceilings, and Orchestrator projects request-, stage-, attempt-, gate-, and worker-scoped evidence from those stores.

**Tech Stack:** Python 3.11+, SQLite/WAL, PyYAML, BeautifulSoup4, MCP/FastMCP, React 18, Node test runner, Rust/Tauri, serde/serde_yaml.

## Global Constraints

- Work only on `fix/console-run-evidence-scope`; do not create another branch or worktree.
- Preserve `vietnam-weather.html` as untracked user-owned content; never stage, modify, or delete it.
- The model selects skills; the harness never ranks, defaults, substitutes, silently drops, or removes a model selection.
- The substrate makes zero LLM calls. Preflight calls run only in the standalone driver through `LMRouter`.
- The evaluator sees only the artifact and fixed rubric; it never receives the request, plan, design, goal, predicates, or failure provenance.
- `max_iterations` defaults to `1` and is limited to `1..3`.
- Acceptance contracts are conjunctions, canonical JSON, SHA-256 hashed, and immutable after the first valid preflight.
- Named commands use exact operator-authored argv with no shell interpolation and pass through policy, audit, root, timeout, and artifact tracking.
- Stage output, contract, manifest, and gate result are individually write-once; every attempt and transition remains append-only.
- A worker may not start until its spawn audit obligation is durable; audit failure is fail-closed.
- Automatic retry is ephemeral and must retain the lifecycle trigger defined in the design spec.
- Use Conventional Commits with `Eurus <t.hoang7895@gmail.com>` for both author and committer.

---

### Task 1: Canonical recipe ceilings and strict validation

**Files:**
- Modify: `musubi/composer.py`
- Modify: `.github/pipelines/feature-dev/pipeline.yaml`
- Modify: `.github/pipelines/code-review/pipeline.yaml`
- Test: `musubi/tests/test_pipeline_yaml.py`
- Test: `musubi/tests/test_pipeline_yaml_spawns.py`
- Test: `musubi/tests/test_composer.py`

**Interfaces:**
- Produces: `NamedCommandSpec`, `StageRecipe`, and `PipelineRecipeContract` frozen dataclasses.
- Produces: `composer.load_pipeline_contract(pipeline_name: str) -> PipelineRecipeContract`.
- Produces: `composer.stage_recipe(pipeline_name: str, stage: str) -> StageRecipe | None`.
- Preserves: `active_stages`, `agent_for_stage`, `evaluator_input_stage`, and spawn-resolution behavior for existing callers.

- [ ] **Step 1: Write failing strict-recipe tests**

Add tests that load a temporary recipe with top-level `checks`, stage-level `allowed_checks`, `allowed_commands`, and `max_iterations`, and assert this exact projection:

```python
contract = composer.load_pipeline_contract("weather-flow")
assert contract.commands["project-tests"].argv == ("npm", "test")
assert contract.commands["project-tests"].timeout_seconds == 120
assert contract.stages[1].allowed_checks == (
    "file_created_or_modified", "dom_count", "named_command",
)
assert contract.stages[1].allowed_commands == ("project-tests",)
assert contract.stages[1].max_iterations == 3
```

Also parametrize rejection of duplicate stage names, unknown stage fields, unknown checker types, missing command IDs, duplicate command IDs, invalid roles, `max_iterations` values `0` and `4`, and `max_iterations=2` with no allowed checks.

- [ ] **Step 2: Run the recipe tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_pipeline_yaml.py musubi/tests/test_pipeline_yaml_spawns.py musubi/tests/test_composer.py -q`

Expected: FAIL because `load_pipeline_contract` and the new dataclasses do not exist.

- [ ] **Step 3: Implement strict recipe types and parsing**

Add these immutable shapes to `composer.py`:

```python
@dataclass(frozen=True)
class NamedCommandSpec:
    command_id: str
    argv: tuple[str, ...]
    timeout_seconds: int
    root: str = "musubi"
    cwd: str = "."

@dataclass(frozen=True)
class StageRecipe:
    stage: str
    agent: str
    preset: str | None
    spawns: tuple[str, ...]
    allowed_checks: tuple[str, ...]
    allowed_commands: tuple[str, ...]
    max_iterations: int

@dataclass(frozen=True)
class PipelineRecipeContract:
    name: str
    stages: tuple[StageRecipe, ...]
    commands: Mapping[str, NamedCommandSpec]
```

Make `load_pipeline_contract` raise `PipelineRecipeError` for every malformed or unknown governed declaration. Keep `_load_pipeline_yaml` for compatibility reads, but make runnable pipeline discovery call the strict loader and exclude invalid recipes.

- [ ] **Step 4: Migrate shipped recipes without static skill selection**

Remove runtime `skill:` choices from shipped generator/evaluator entries. Convert them to flat `stages:` entries, declare `max_iterations: 1` for text/evaluator stages, and opt only the feature-dev `code` stage into the initial deterministic vocabulary and `max_iterations: 3`. Keep legacy reads in the loader for one compatibility release.

- [ ] **Step 5: Run focused and full composer tests**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_pipeline_yaml.py musubi/tests/test_pipeline_yaml_spawns.py musubi/tests/test_composer.py musubi/tests/test_user_pipeline_presets.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the recipe boundary**

```powershell
git add musubi/composer.py musubi/tests/test_pipeline_yaml.py musubi/tests/test_pipeline_yaml_spawns.py musubi/tests/test_composer.py musubi/tests/test_user_pipeline_presets.py .github/pipelines/feature-dev/pipeline.yaml .github/pipelines/code-review/pipeline.yaml
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(pipeline): validate stage acceptance ceilings"
```

### Task 2: Lossless Pipeline Studio recipe model

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Modify: `gui/src/model/pipelineBuilder.js`
- Modify: `gui/src/model/pipelineBuilder.test.mjs`
- Modify: `gui/src/views/Pipeline.jsx`
- Modify: `gui/src/views/Pipeline.test.mjs`
- Modify: `gui/src/index.css`

**Interfaces:**
- Consumes: `PipelineRecipeContract` field names from Task 1.
- Produces: Rust `NamedPipelineCommand` and expanded `PipelineStageRecipe` serde models.
- Produces: JavaScript draft fields `checks`, `maxIterations`, `allowedChecks`, and `allowedCommands`.

- [ ] **Step 1: Add failing Rust round-trip and fail-closed save tests**

Add a fixture containing a named command and all stage ceilings. Assert `read_pipeline_recipe` returns them and `save_pipeline_recipe` emits semantically identical YAML. Add a legacy fixture with `skill:` and assert save is refused until the draft can preserve the legacy field.

- [ ] **Step 2: Run the Rust data tests and verify failure**

Run: `cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml pipeline_recipe -- --nocapture`

Expected: FAIL because the Rust document structs reject or discard the new fields.

- [ ] **Step 3: Expand the Rust document and validation types**

Use these public field shapes:

```rust
pub struct NamedPipelineCommand {
    pub command_id: String,
    pub argv: Vec<String>,
    pub timeout_seconds: u32,
    pub root: String,
    pub cwd: String,
}

pub struct PipelineStageRecipe {
    pub preset: String,
    pub agent: String,
    pub stage: String,
    pub spawns: Vec<String>,
    pub max_iterations: u8,
    pub allowed_checks: Vec<String>,
    pub allowed_commands: Vec<String>,
}
```

Add `checks: Vec<NamedPipelineCommand>` to `PipelineRecipe` and `PipelineOutputDocument`. Validate the same checker vocabulary, command references, cap range, duplicate stage names, exact argv, root, cwd, and timeouts as Python.

- [ ] **Step 4: Add failing JavaScript draft tests**

Assert `createPipelineDraft`, `updateStage`, `isDirty`, and save payloads preserve the new fields without coercing `1` to a falsey default or losing command arrays.

- [ ] **Step 5: Implement the Studio draft and controls**

Normalize with explicit numeric handling:

```javascript
maxIterations: Number.isInteger(Number(stage.maxIterations))
  ? Number(stage.maxIterations) : 1,
allowedChecks: normalizeIds(stage.allowedChecks),
allowedCommands: normalizeIds(stage.allowedCommands),
```

Add stage controls for iteration cap, allowed checks, and allowed named commands, plus a recipe-level exact-argv command editor. Show validation findings next to their owning stage or command.

- [ ] **Step 6: Run Studio data, UI, and build verification**

Run: `npm run test:data`

Run: `npm test -- --test-name-pattern="pipeline"`

Run: `npm run build`

Expected: all PASS and the Vite production build completes.

- [ ] **Step 7: Commit lossless Studio authoring**

```powershell
git add gui/src-tauri/musubi-data/src/lib.rs gui/src/model/pipelineBuilder.js gui/src/model/pipelineBuilder.test.mjs gui/src/views/Pipeline.jsx gui/src/views/Pipeline.test.mjs gui/src/index.css
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(gui): author stage acceptance ceilings"
```

### Task 3: Explicit model-selected skill contracts

**Files:**
- Modify: `musubi/skills/skill_loader.py`
- Modify: `musubi/validation/subagent_context.py`
- Modify: `musubi/server.py`
- Modify: `.github/skills/web-ui/SKILL.md`
- Modify: `.github/skills/testing/SKILL.md`
- Modify: `.github/skills/debugging/SKILL.md`
- Modify: `.github/skills/code-review/SKILL.md`
- Test: `musubi/tests/test_skill_access.py`
- Test: `musubi/tests/test_subagent_context.py`
- Test: `musubi/tests/test_spawn_pipeline.py`
- Test: `musubi/tests/test_root_skill_injection.py`

**Interfaces:**
- Produces: `CompletionContract(required_output_fields, required_check_types)`.
- Produces: `SkillMeta.version`, `SkillMeta.content_hash`, and `SkillMeta.completion_contract`.
- Changes: `build_subagent_context` requires an explicit validated `pushed_skill_id` for pipeline and helper workers.
- Changes: invalid or missing selected skills return an error; they are never converted to `None`.

- [ ] **Step 1: Write failing metadata and no-default tests**

Assert catalog entries calculate `sha256:<hex>` from exact UTF-8 skill bytes and parse:

```yaml
completion-contract:
  required-output-fields: [summary, files_modified]
  required-check-types: [file_created_or_modified]
```

Replace tests that pin `SUBAGENT_ROLE_SKILLS` defaults with assertions that no runtime role default is consulted. Assert an omitted, unknown, disallowed, or missing-on-disk `pushed_skill_id` prevents pipeline/helper spawn.

- [ ] **Step 2: Run the skill tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_skill_access.py musubi/tests/test_subagent_context.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_root_skill_injection.py -q`

Expected: FAIL on missing completion metadata and current default/drop behavior.

- [ ] **Step 3: Extend skill catalog metadata**

Add:

```python
@dataclass(frozen=True)
class CompletionContract:
    required_output_fields: tuple[str, ...] = ()
    required_check_types: tuple[str, ...] = ()
```

Parse frontmatter strictly for pipeline-selectable skills. Store a catalog version from frontmatter and the exact content hash. Reject malformed completion declarations instead of treating them as empty.

- [ ] **Step 4: Remove harness-selected runtime skills**

Delete `_prepared_stage_skill` from `pipeline_runner.py` in Task 7 and remove `SUBAGENT_ROLE_SKILLS` resolution from `build_subagent_context`. Keep only explicit `pushed_skill_id`; load exact content after the server validates allowlist, catalog existence, version, and hash. Make `musubi_spawn_pipeline_stage` and `musubi_spawn_subagent` fail closed for missing or invalid model selections when the role has selectable skills.

- [ ] **Step 5: Add observable completion metadata to shipped selectable skills**

Use output-field requirements for text-only review/planning skills and `file_created_or_modified` for mutation skills. Ensure every requirement is permitted by at least one shipped recipe role; impossible combinations fail recipe/preflight validation.

- [ ] **Step 6: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_skill_access.py musubi/tests/test_subagent_context.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_root_skill_injection.py -q`

Expected: PASS.

- [ ] **Step 7: Commit explicit skill enforcement**

```powershell
git add musubi/skills/skill_loader.py musubi/validation/subagent_context.py musubi/server.py musubi/tests/test_skill_access.py musubi/tests/test_subagent_context.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_root_skill_injection.py .github/skills/web-ui/SKILL.md .github/skills/testing/SKILL.md .github/skills/debugging/SKILL.md .github/skills/code-review/SKILL.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(skills): require explicit model selections"
```

### Task 4: Durable spawn audit obligations for Hard Invariant #8

**Files:**
- Modify: `musubi/storage/schema.sql`
- Modify: `musubi/storage/db.py`
- Modify: `musubi/storage/subagent_audit.py`
- Modify: `musubi/server.py`
- Test: `musubi/tests/test_subagent_audit.py`
- Test: `musubi/tests/test_spawn_pipeline.py`
- Test: `musubi/tests/test_g16_dispatcher_audit.py`

**Interfaces:**
- Produces: `db.record_audit_obligation(...) -> int` and `db.mark_audit_obligation_delivered(obligation_id: int) -> None`.
- Produces: `subagent_audit.deliver_spawn_obligation(obligation: Mapping[str, Any]) -> None`.
- Guarantees: server spawn methods return `spawned` only after both the worker row and durable spawn evidence exist.

- [ ] **Step 1: Write failing fail-closed spawn tests**

Monkeypatch `subagent_audit.record_spawn` to raise `sqlite3.OperationalError`. Assert `musubi_spawn_subagent`, `musubi_spawn_pipeline`, and `musubi_spawn_pipeline_stage` return `status=error`, create no runnable handle, and leave a queryable `pending` obligation in `musubi.db` when the primary worker row was already reserved.

- [ ] **Step 2: Run audit tests and verify current silent behavior fails**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_subagent_audit.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_g16_dispatcher_audit.py -q`

Expected: FAIL because the three spawn paths currently swallow audit exceptions.

- [ ] **Step 3: Add the local durable obligation table**

Add `audit_obligations(id, created_at, kind, handle_id, payload_json, status, delivered_at, error)` with a unique `(kind, handle_id)` index. Insert the worker reservation and obligation in one `BEGIN IMMEDIATE` transaction. Delivery to `audit.db` is idempotent by the existing handle/event key.

- [ ] **Step 4: Make spawn delivery synchronous and fail closed**

Replace each `except Exception: pass` in spawn paths. Reserve row + obligation, deliver the obligation, mark it delivered, and only then return a runnable handle. If delivery fails, mark the worker reservation `abandoned`, retain the obligation with its error, and return `error_kind=audit_unavailable`.

- [ ] **Step 5: Add relay recovery for pending non-runnable obligations**

On server initialization and explicit audit query, retry pending obligations idempotently. Recovery may make evidence durable, but it must not resurrect an abandoned worker; the operator launches a new attempt.

- [ ] **Step 6: Run audit tests and inspect the exception scan**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_subagent_audit.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_g16_dispatcher_audit.py -q`

Run: `rg -n "record_spawn|except Exception" musubi/server.py`

Expected: tests PASS; no spawn audit call is followed by a swallowing handler.

- [ ] **Step 7: Commit the strengthened invariant**

```powershell
git add musubi/storage/schema.sql musubi/storage/db.py musubi/storage/subagent_audit.py musubi/server.py musubi/tests/test_subagent_audit.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_g16_dispatcher_audit.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(audit): fail closed before worker start"
```

### Task 5: Generic append-only stage attempts and transition ledger

**Files:**
- Modify: `musubi/storage/schema.sql`
- Modify: `musubi/storage/db.py`
- Modify: `musubi/session/state.py`
- Modify: `musubi/server.py`
- Test: `musubi/tests/test_state.py`
- Create: `musubi/tests/test_stage_attempts.py`

**Interfaces:**
- Produces: `StagePhase` string enum values from `pending` through `escalated`.
- Produces: `db.transition_stage_attempt(identity, expected_phase, next_phase, event, detail) -> dict[str, Any]`.
- Produces: `db.create_next_stage_attempt(identity, expected_attempt, detail) -> int` using `BEGIN IMMEDIATE`.
- Produces: `state.create_session(..., stages: Sequence[str])` with composer-derived stage seeding.

- [ ] **Step 1: Write failing migration, uniqueness, write-once, and CAS tests**

Cover arbitrary stage names, the two partial unique indexes for NULL/non-NULL `chunk_id`, two concurrent writers racing for attempt 2, immutable contract/output/manifest/gate fields, forward-only phase transitions, and event append in the same transaction.

- [ ] **Step 2: Run stage-store tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_state.py musubi/tests/test_stage_attempts.py -q`

Expected: FAIL on the legacy `STAGES` guard and absent columns/table/indexes.

- [ ] **Step 3: Extend schema and idempotent migrations**

Add the checkpoint columns and `stage_attempt_events` exactly as specified in the design. Add the two partial unique indexes. Mirror every schema change in embedded `_SCHEMA_SQL` and migration column lists in `storage/db.py`.

- [ ] **Step 4: Implement atomic attempt operations**

Use an immutable identity object:

```python
@dataclass(frozen=True)
class StageAttemptIdentity:
    session_id: str
    stage: str
    attempt: int
    chunk_id: str | None = None
```

Every transition executes compare-and-swap update plus event insert in one transaction. Every write-once setter requires its target column to be NULL.

- [ ] **Step 5: Generalize session state**

Remove runtime rejection based on hardcoded `STAGES`. Seed the exact validated composer plan. Retain the legacy stage-to-agent migration map only for historical schema migration reads.

- [ ] **Step 6: Run storage tests**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_state.py musubi/tests/test_stage_attempts.py musubi/tests/test_pause_resume.py musubi/tests/test_g2_schema_migration.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the append-only state machine store**

```powershell
git add musubi/storage/schema.sql musubi/storage/db.py musubi/session/state.py musubi/server.py musubi/tests/test_state.py musubi/tests/test_stage_attempts.py musubi/tests/test_pause_resume.py musubi/tests/test_g2_schema_migration.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(storage): persist stage attempt checkpoints"
```

### Task 6: Deterministic acceptance predicates

**Files:**
- Create: `musubi/validation/stage_contract.py`
- Create: `musubi/validation/stage_gate.py`
- Create: `musubi/tests/test_stage_contract.py`
- Create: `musubi/tests/test_stage_gate.py`
- Modify: `musubi/musubi.spec`

**Interfaces:**
- Produces: `validate_and_freeze_contract(raw, recipe, skill_meta, roots) -> FrozenStageContract`.
- Produces: `evaluate_stage_gate(contract, snapshot, manifest, command_runner) -> GateResult`.
- Produces: `CheckResult(type, status, message, evidence)` where status is `pass`, `fail`, or `error`.

- [ ] **Step 1: Write failing contract canonicalization tests**

Assert key-order-independent JSON yields the same SHA-256 hash, retry hash mismatch is rejected, required skill check types merge before hashing, unknown/disallowed predicates fail closed, and all paths remain inside the named immutable root.

- [ ] **Step 2: Write failing checker table tests**

Build temporary static HTML and cover passing/failing `file_exists`, `file_created_or_modified`, `dom_count`, `dom_distinct_text`, `dom_text_set`, and `lint_clean`. Assert all deterministic failures are returned, while parser/I/O failures produce `GateResult.status == "gate_error"`.

- [ ] **Step 3: Run the tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_stage_contract.py musubi/tests/test_stage_gate.py -q`

Expected: FAIL because both modules are new.

- [ ] **Step 4: Implement immutable contract validation**

Use frozen dataclasses and canonical serialization `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. Prefix hashes with `sha256:`. Reject selectors containing unsupported dynamic/browser syntax; static DOM parsing must never execute JavaScript.

- [ ] **Step 5: Implement all non-command predicates**

Normalize DOM text by collapsing Unicode whitespace and trimming. Fingerprints use size plus SHA-256 content, not mtime alone. `paths: changed` resolves against the cumulative surviving manifest. `lint_clean` treats `skipped` as failure unless `allow_skipped` is true.

- [ ] **Step 6: Bundle and run tests**

Add BeautifulSoup4 to the existing runtime dependency/bundle declarations if not already present.

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_stage_contract.py musubi/tests/test_stage_gate.py -q`

Expected: PASS.

- [ ] **Step 7: Commit deterministic checks**

```powershell
git add musubi/validation/stage_contract.py musubi/validation/stage_gate.py musubi/tests/test_stage_contract.py musubi/tests/test_stage_gate.py musubi/musubi.spec
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(validation): evaluate frozen stage contracts"
```

### Task 7: Governed named-command dispatcher

**Files:**
- Create: `musubi/agent/stage_command.py`
- Modify: `musubi/agent/run.py`
- Modify: `scripts/policy_engine.py`
- Modify: `musubi/validation/stage_gate.py`
- Create: `musubi/tests/test_stage_command.py`
- Modify: `musubi/tests/test_g16_dispatcher_audit.py`

**Interfaces:**
- Produces: `run_named_command(spec, *, role, session_id, stage, attempt, roots, audit_db_path, log) -> NamedCommandResult`.
- Consumes: the same policy and tool-audit primitives used by `_dispatch_one`.
- Guarantees: exact argv execution with `shell=False`, bounded stdout/stderr, stable execution ID, and no duplicate execution after a durable result.

- [ ] **Step 1: Write failing policy/audit/timeout/idempotency tests**

Cover exact argv preservation, cwd/root escape rejection, policy denial, nonzero exit, timeout, output truncation, audit write failure, filesystem manifest updates, and resume returning the durable result without launching a second process.

- [ ] **Step 2: Run dispatcher tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_stage_command.py musubi/tests/test_g16_dispatcher_audit.py -q`

Expected: FAIL because no governed named-command dispatcher exists.

- [ ] **Step 3: Extract shared governed dispatch primitives**

Refactor policy evaluation and audit recording from `run.py` into callable helpers without changing model tool behavior. Do not call MCP directly from the gate and do not add `musubi_run_command` to the pipeline model tool surface.

- [ ] **Step 4: Implement named command execution**

Resolve only operator-authored `NamedCommandSpec`; the preflight supplies only `command_id`. Execute with `asyncio.create_subprocess_exec(*argv, cwd=resolved_cwd)` and a hard timeout. Persist result before returning it to `stage_gate`.

- [ ] **Step 5: Run dispatcher and existing policy tests**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_stage_command.py musubi/tests/test_g16_dispatcher_audit.py musubi/tests/test_agent_tool_loop.py -q`

Expected: PASS.

- [ ] **Step 6: Commit governed command checks**

```powershell
git add musubi/agent/stage_command.py musubi/agent/run.py scripts/policy_engine.py musubi/validation/stage_gate.py musubi/tests/test_stage_command.py musubi/tests/test_g16_dispatcher_audit.py musubi/tests/test_agent_tool_loop.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(pipeline): govern named acceptance commands"
```

### Task 8: Driver preflight, frozen contract, and stage loop

**Files:**
- Create: `musubi/agent/stage_preflight.py`
- Create: `musubi/agent/stage_loop.py`
- Modify: `musubi/agent/pipeline_runner.py`
- Modify: `musubi/agent/run.py`
- Modify: `musubi/server.py`
- Create: `musubi/tests/test_stage_preflight.py`
- Create: `musubi/tests/test_stage_loop.py`
- Modify: `musubi/tests/test_pipeline_runner.py`
- Modify: `musubi/tests/test_spawn_pipeline.py`

**Interfaces:**
- Produces: `run_stage_preflight(vendor, role, brief, catalog, recipe, frozen_contract, failure_evidence, budget, log) -> StagePreflight`.
- Produces: `run_stage_loop(context: StageLoopContext) -> StageLoopResult`.
- Consumes: Task 3 explicit skill metadata, Task 5 transitions, Task 6 gate, and Task 7 command runner.

- [ ] **Step 1: Write failing preflight parser tests**

Cover valid first attempt, retry hash echo, evaluator skill-only response, one correction response after malformed JSON, second invalid response escalation, missing/disallowed/stale skill, impossible completion-contract merge, and absence of mutation tools.

- [ ] **Step 2: Write failing loop behavior tests**

Cover first-attempt pass, failure then pass, exactly three failed attempts then exhaustion, gate error without retry, failed attempts excluded from downstream context, cumulative manifest, credit reservation failure, and budget charges for preflight + worker + helpers.

- [ ] **Step 3: Run new runner tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_stage_preflight.py musubi/tests/test_stage_loop.py musubi/tests/test_pipeline_runner.py musubi/tests/test_spawn_pipeline.py -q`

Expected: FAIL because the runner still resolves recipe/default skills and advances on text.

- [ ] **Step 4: Implement the bounded driver preflight**

Generate a strict JSON-only system prompt from the permitted catalog projection and recipe ceilings. Use `LMRouter` through the same vendor abstraction as `run_unit`, record the call as an agent cycle, charge shared token/credit budgets, and allow exactly one correction call with validation errors.

- [ ] **Step 5: Implement the stage state machine**

Persist each transition before the next side effect. Bound retry evidence to 8 KiB, escape control characters, label it untrusted, and include only failed check IDs/messages/evidence plus the immutable contract hash. On resume, follow the phase table in the spec and never repeat a durable named command.

- [ ] **Step 6: Replace the legacy one-pass runner path**

Delete `_prepared_stage_skill`. For every stage, load its strict recipe, reserve worst-case credits, call `run_stage_loop`, append only the latest passed output to downstream summaries, and stop immediately on `gate_error`, `exhausted`, or evaluator escalation.

- [ ] **Step 7: Enforce final reviewer status**

Strictly parse structured evaluator output. Only `pass` finalizes success. `fail`, `wrong_plan`, `escalate`, malformed, and missing status stop and escalate. Remove any pipeline-path coercion that rewrites reviewer `fail` to `pass`.

- [ ] **Step 8: Run runner, resume, budget, and firewall tests**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_stage_preflight.py musubi/tests/test_stage_loop.py musubi/tests/test_pipeline_runner.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_pause_resume.py musubi/tests/test_subagent_firewall_g1.py musubi/tests/test_verifier.py -q`

Expected: PASS.

- [ ] **Step 9: Commit the executable stage loop**

```powershell
git add musubi/agent/stage_preflight.py musubi/agent/stage_loop.py musubi/agent/pipeline_runner.py musubi/agent/run.py musubi/server.py musubi/tests/test_stage_preflight.py musubi/tests/test_stage_loop.py musubi/tests/test_pipeline_runner.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_pause_resume.py musubi/tests/test_subagent_firewall_g1.py musubi/tests/test_verifier.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(pipeline): enforce deterministic stage loops"
```

### Task 9: Request, stage, gate, and agent evidence in Console

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Modify: `gui/src/model/data.js`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/model/viewModel.test.mjs`
- Modify: `gui/src/views/Orchestrator.jsx`
- Modify: `gui/src/views/Orchestrator.test.mjs`
- Modify: `gui/src/index.css`
- Modify: `gui/src-tauri/SCHEMA.md`

**Interfaces:**
- Produces: request evidence rows for contract freeze, checks, retries, gate errors, exhaustion, and reviewer escalation.
- Produces: stage attempt summaries containing phase, skill provenance, contract hash, handles, and terminal verdict.
- Preserves: Agent Log filters only the selected worker handle; gate events never appear as that worker's own log.

- [ ] **Step 1: Add failing Rust projection tests**

Seed `stage_outputs`, `stage_attempt_events`, `subagent_audit`, and pending `audit_obligations`. Assert one request projection orders them chronologically and attributes request/stage/attempt/worker fields without merging gate rows into an agent handle.

- [ ] **Step 2: Add failing React view-model tests**

Assert Request Log contains all attempt and gate events, selecting an agent filters to its own rows, Overview shows goal/hash/current phase/latest verdict, and pending audit relay state is visible.

- [ ] **Step 3: Run Console tests and verify failure**

Run: `npm run test:data`

Run: `npm test -- --test-name-pattern="Orchestrator|viewModel"`

Expected: FAIL because stage-loop evidence is not projected.

- [ ] **Step 4: Extend Rust data projection**

Read the stage ledger from `musubi.db` and worker/tool rows from `audit.db`, normalize timestamps, and emit stable typed events. Preserve the existing request-group and agent-handle identities from the run-evidence scope work.

- [ ] **Step 5: Extend the single-window evidence navigation**

Keep Graph as the entry. Clicking a request opens Overview/Request Log; clicking a worker opens Overview/Agent Log; Back returns to the graph from the same evidence panel. Add attempt chips, selected-skill receipt/enforcement state, contract hash, check verdicts, retry reason, and terminal state using the existing larger monospace font and dark palette.

- [ ] **Step 6: Run Console test and build suites**

Run: `npm run test:data`

Run: `npm test`

Run: `npm run build`

Expected: all PASS.

- [ ] **Step 7: Commit stage evidence projection**

```powershell
git add gui/src-tauri/musubi-data/src/lib.rs gui/src/model/data.js gui/src/model/viewModel.js gui/src/model/viewModel.test.mjs gui/src/views/Orchestrator.jsx gui/src/views/Orchestrator.test.mjs gui/src/index.css gui/src-tauri/SCHEMA.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(gui): show stage gate evidence"
```

### Task 10: Lifecycle declarations, compatibility cleanup, and end-to-end verification

**Files:**
- Modify: `docs/hard-invariants.md`
- Modify: `docs/roadmap.md`
- Modify: `docs/superpowers/specs/2026-08-01-stage-goals-and-loop-design.md`
- Modify: `musubi/tests/test_system_atlas.py`
- Modify: `musubi/tests/test_code_review_pipeline.py`
- Modify: `musubi/tests/test_code_review_standalone.py`
- Create: `musubi/tests/test_stage_loop_e2e.py`
- Modify: `scripts/check_musubi_tier.py`
- Modify: `.github/workflows/ci.yaml`

**Interfaces:**
- Preserves: source lifecycle declarations for `stage-acceptance-gate`, `automatic-stage-retry`, and `pipeline-stage-preflight-adapter`; the central lifecycle registry and expiration evidence query remain the separate track named by the design spec.
- Produces: one end-to-end weather-table fixture proving a model-authored five-city contract can be checked by code.

- [ ] **Step 1: Add failing invariant and lifecycle tests**

Assert HI #2 states model selection plus harness validation/injection/enforcement and HI #8 states durable pre-start spawn evidence. Extend the existing source-tag check so each new ephemeral module has non-empty `musubi-tier`, `expires-when`, and `cost-lever` declarations, while substrate modules declare `expires-when: never`.

- [ ] **Step 2: Add the weather-table end-to-end test**

Use a temporary `index.html` with five `[data-testid='weather-row']` rows and five distinct `[data-testid='city-name']` values. Freeze a contract with `file_created_or_modified`, `dom_count`, `dom_distinct_text`, and a passing named test command; assert the stage passes once, persists every event, and exposes the same evidence in the Console projection.

- [ ] **Step 3: Run invariant and E2E tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests/test_system_atlas.py musubi/tests/test_code_review_pipeline.py musubi/tests/test_code_review_standalone.py musubi/tests/test_stage_loop_e2e.py -q`

Expected: FAIL until documentation, registry, and end-to-end wiring are synchronized.

- [ ] **Step 4: Update invariant and roadmap truth**

Document the model/harness ownership boundary, durable audit obligation, the three lifecycle declarations and their thresholds, compatibility window, and the exact new enforcement points. Keep the central lifecycle registry/evidence query listed as a separate roadmap dependency. Mark the stage-loop roadmap item implemented only after all Python, Rust, React, and build suites pass.

- [ ] **Step 5: Run the complete verification matrix**

Run: `.\.venv\Scripts\python.exe -m pytest musubi/tests -q`

Run: `npm test`

Run: `npm run test:data`

Run: `npm run build`

Run: `git diff --check`

Expected: every test and build passes; `git diff --check` prints no output.

- [ ] **Step 6: Verify invariant searches and worktree scope**

Run: `rg -n "SUBAGENT_ROLE_SKILLS|_prepared_stage_skill|except Exception:\s*$" musubi`

Expected: no runtime skill-default path, no prepared-stage-skill helper, and no swallowing spawn-audit handler.

Run: `git status --short`

Expected: only planned source/docs changes plus the untouched untracked `vietnam-weather.html` are present.

- [ ] **Step 7: Commit final docs and integration coverage**

```powershell
git add docs/hard-invariants.md docs/roadmap.md docs/superpowers/specs/2026-08-01-stage-goals-and-loop-design.md docs/superpowers/plans/2026-08-01-stage-goals-and-loop.md musubi/tests/test_system_atlas.py musubi/tests/test_code_review_pipeline.py musubi/tests/test_code_review_standalone.py musubi/tests/test_stage_loop_e2e.py scripts/check_musubi_tier.py .github/workflows/ci.yaml
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "docs(pipeline): record deterministic stage lifecycle"
```

## Execution Order and Checkpoints

Tasks run sequentially on `fix/console-run-evidence-scope`. Tasks 1-3 establish declarations; Task 4 is the mandatory HI #8 gate; Tasks 5-7 build deterministic substrate; Task 8 enables runtime behavior; Task 9 exposes evidence; Task 10 closes lifecycle and compatibility. Do not enable retries in shipped recipes before Tasks 4-8 pass together.

After each task, run its focused suite, inspect `git diff --check`, and commit only that task's files. Before push, fetch `origin`, rebase on `origin/dev` with the mandated identity flags if the merge-base lags, rerun the full verification matrix, then push the current branch and update its existing PR rather than creating another branch.
