# Adaptive core refactor

Date: 2026-09-18  
Status: implementation started; component extraction is active in the existing runtime.
Implementation branch: `refactor/adaptive-core-cutover`  
Integration baseline: `dev@4d04637d31b4b7808b64839afe198ca016984e2a`.  
Implementation baseline: `feat/work-package-goal-contract@5da9d3fad327370193847b3f08086c28b791ccbf` (four commits ahead, zero behind dev when inspected).

## Active scope and backlog

The active milestone contains the Adaptive core and its Storage boundary: an
explicit Adaptive Controller, Collector, Thinker, Decider, bounded Runtime/Tools,
verification, durable state and evidence. Current tables may be migrated or
reused when they satisfy the target invariant, but Controller and Runtime stop
depending on the legacy database module directly.

The following work is moved to the backlog and is not an Adaptive acceptance
dependency:

- Pipeline migration to shared Runtime/Storage.
- Memory retrieval, knowledge lifecycle and terminal consolidation.
- HoH evidence analysis, candidate isolation and evaluation.

Existing Pipeline and Memory behavior must not regress. Backlog items receive
separate plans only after the Adaptive golden path is accepted. The old Adaptive
path is not a supported second architecture: it is removed after each replacement
slice passes its acceptance tests.

## Implementation checkpoint

The combined strategy is **simplify the existing application while extracting
usable components**. Move each implementation, update its callers, delete the
old definition, and preserve behavior before introducing a new provider. Do
not create a parallel framework or a second orchestration object.

Implemented in the first increment:

- `agent/collector.py` prepares bounded model context and records its input
  identity, including tool schemas. Both normal and salvage preparation use it.
- `agent/thinker.py` owns effort calls and aggregate token usage.
- `agent/decider.py` owns response interpretation and existing recovery choices.
  The compatibility adapter consumes the current LLM response; it is not Jev
  and does not introduce another model call.
- `agent/runtime_tools.py` owns MCP text transport and file-argument validation.
- `agent/run_state.py` owns existing run statistics and orchestration state.
- `agent/adaptive_control.py` owns deterministic Root contract controls and
  completion blockers. A Decider proposal cannot grant verification.
- The old implementations were removed from `run.py`; compatibility aliases
  remain for existing clients. Production consumers import moved helpers from
  their owner. `run.py` shrank from 4,948 to 4,141 lines.
- Regression tests cover context identity/bounds, effort accounting, response
  interpretation and component import boundaries.

This is preparatory progress across WP1/WP3/WP4/WP5, not acceptance of those
complete work packages. Existing Storage and Memory are preserved. No live Jev
provider, new evidence schema, terminal Memory consolidation, or HoH candidate
engine has been implemented. `run.py` still owns the execution lifecycle and
needs further reduction. Existing state types are moved, not a new universal
controller. Architectural decision: ADR 0002.

Validation of this increment: **1,809 passed, 1 skipped, 4 deselected**
(`pytest musubi/tests/`, excluding three mypy-dependent executor tests and
`test_run_tests_passing_suite`). The unrestricted run encountered missing mypy;
installation failed after network timeouts. Import/name lint and lifecycle-tag
checks passed. This is not a green full CI claim.

### Typed decision contract checkpoint

Implemented after the initial extraction:

- `agent/decision_contracts.py` provides versioned DecisionRequest/Decision,
  immutable Candidate payloads, DecisionState and a DecisionProvider port.
- Request identity binds run/unit/revision, contract, context, candidate IDs and
  arguments, allowed action kinds and remaining tokens. Resolver rejects stale
  state, changed request identity and unknown selections before handing back an
  existing candidate. It neither dispatches a tool nor grants completion.
- JSON records reject extra/missing top-level fields and unsupported request
  schema versions. Round trips retain identity; candidate payload reads return
  copies rather than mutable references into the option set.
- `ResponseSelectionProvider` reads an already-accounted LMRouter response. It
  accepts only a candidate ID or abstention, rejects new parameters/tool calls,
  and shares the same port with fake providers. It makes no additional call.
- Timeout/abstention are typed outcomes, but enforcing deadlines and charging
  calls remain caller responsibilities; no fallback is implicit.

Validation: 172 cases passed across decision contracts, component tests, agent
loop and Work Package tests. Import/name lint and lifecycle-tag checks passed. This is a contract-layer checkpoint within WP1, not WP1
or WP4 acceptance. The live loop still uses its legacy response adapter. Actual
provider invocation, state revision ownership, durable replay/idempotency,
existing persistence integration, and dispatch integration remain required
before cutover.

### Adaptive cutover checkpoint

The refactor now targets one final architecture rather than two supported modes.
Replacement slices retain the old code only as rollback until their acceptance
tests pass; the corresponding old path is then deleted on this branch.

- `storage/adaptive_runs.py` is the public Adaptive persistence boundary. The
  first slice records provider decisions append-only, rejects stale revisions
  and rewritten request identity, and reconstructs a run without Controller or
  Runtime imports.
- `WorkPackageController` now depends on that repository for Goal/Work Package
  versions, attempts, criterion state, verification and budget records; it no
  longer imports the table-level `storage.db` module. The repository currently
  preserves proven table behavior while subsequent slices consolidate atomic
  attempt completion and public run identity.
- `adaptive_decision_events` is additive to the current database. Existing
  Goal/Work Package/attempt/evidence tables remain reused until their behavior
  is moved behind this repository; a parallel set of truth tables is not added.
- `agent/adaptive_controller.py` validates a provider selection against current
  state, persists it, and only then exposes the existing candidate. It neither
  executes the action nor treats finish as verified completion.

This checkpoint does not yet cut over the live CLI. The next slices move
contract/attempt/evidence persistence behind the repository, introduce the
bounded Runtime attempt and replace the root lifecycle in `run.py` before its
legacy Adaptive branches are removed.

Validation at this checkpoint: 177 decision/component/Work Package/agent-loop
tests passed, plus 59 Storage schema/migration/state tests in a separate
overlapping run. Changed-file lint, lifecycle tags and whitespace checks passed.

This plan takes priority over `2026-09-18-test-suite-simplification.md`; the latter
is deferred. Add tests only for concrete new boundary behavior during this work.

Next implementation order:

1. Freeze one Adaptive acceptance scenario and an independent verifier oracle.
2. Refactor Adaptive Storage behind a public repository: frozen contracts,
   revisions, decisions, attempts, evidence, budgets and reconstructable state.
3. Define one bounded Runtime attempt using policy, budget and the new Storage
   boundary.
4. Move lifecycle ownership into an explicit Adaptive Controller. It builds
   legal actions and applies deterministic state transitions.
5. Connect Jev-first routing where more than one legitimate action remains.
   Call the LLM Thinker only when generation or deeper reasoning is required.
6. Run a complete collect -> decide -> think/execute -> verify -> retry/finish
   golden path; remove the corresponding legacy Adaptive branches.
7. Compare rule, LLM and Jev decisions on the same recorded Adaptive states.
   Memory, Pipeline and HoH remain backlog work.

## 1. Context and source of decisions

The active design contains an Adaptive Controller plus Collector, Thinker,
Decider and Runtime/Tools. The Controller owns the lifecycle; the Decider owns
selection among legal actions. Runtime must not grow into a god object that
also authors plans, chooses recovery strategies, retrieves knowledge, and
judges semantic completion.

Prior decisions were checked against the September 4-5 Musubi HoH discussion,
`musubi-hoh-spec.md` v0.1 and `musubi-architecture-spec.md` v0.2. These were
design specifications, not proof that the repository already implements them.
This plan carries forward the relevant decisions so implementation does not
depend on access to conversation history:

- Adaptive Goal/Work Package execution and its Storage boundary are the active
  golden path. Pipeline, Memory and HoH remain documented backlog capabilities.
- Contracts are immutable once execution begins; retries retain the contract
  hash. Changing scope, expected delta, or verifier requires a new version.
- HoH is a separate optimization engine, manually activated initially.
  Recording task evidence or consolidating Memory never implicitly triggers it.
- HoH uses original Storage evidence and optional Memory knowledge. It can
  operate when Memory is empty or unavailable.
- Candidate workspaces and evaluations are isolated; evaluate the actual
  candidate version. Candidate runs cannot recursively start HoH.
- Fixed evaluation tasks/scoring remain outside candidate write authority.
  Selection produces a report and patch, not automatic merge/deployment.
- Runtime/Tools preserve policy, budgets, evaluator and skill firewalls,
  append-only audit, and truthful evidence-backed completion.

The earlier specifications assigned workflow decisions to engine controllers.
This plan refines that ownership: adaptive semantic choices move to a driver
Decider; a small deterministic engine transition module validates and applies
them. Pipeline retains configured transitions; HoH retains its own lifecycle.
There is no additional universal autonomous controller above these engines.

## 2. Evidence from the inspected code

Paths and symbols below refer to the implementation baseline, not future paths.

| Current location | Observed responsibility | Refactor direction |
| --- | --- | --- |
| `musubi/agent/run.py` (4,948 lines) | `Orchestration`, `_run_loop`, `run_unit`, recovery, model calls, dispatch, contract controls, CLI | Extract responsibilities incrementally; retain entry compatibility during migration |
| `agent/run.py::Orchestration` (line 311), `_run_loop` (1137), `run_unit` (1943) | Run state and execution lifecycle mixed in one module | Typed state, bounded operations, engine-specific transition reducer |
| `agent/run.py::_dispatch_one` (3764), `_handle_root_control_tool` (4268) | Tool execution and Root control operations | Separate tool executor and contract/transition handlers |
| `musubi/agent/work_package_controller.py` | Contract freeze/restore, attempt lifecycle, criterion projection, gap report, retry admissibility | Reuse contracts/ledger; extract narrow services instead of replacing with another manager |
| `musubi/agent/context.py` | Prompts, input fitting, compression and recovery hints | Reuse deterministic fitting in Collector; isolate reasoning/decision prompts |
| `musubi/agent/pipeline_runner.py` | Pipeline sequencing, stage attempts, checkpoints and gates | Preserve deterministic selection and migrate shared execution/evidence interfaces |
| `musubi/agent/vendors/base.py` | `LMRouter.call()` returns text/tool-use-shaped `LMResponse` | Keep provider access in driver; add typed decision capability without fabricating chat output |
| `musubi/memory/memory_loader.py` | Tiered file reads and keyword-based session retrieval | Adapt behind scoped Memory retrieval; do not call it an implemented vector RAG system |
| `musubi/memory/session_distiller.py` | Distillation tied to review-stage output | Replace coupling with terminal run/evidence consolidation |
| `musubi/storage/db.py`, `storage/schema.sql` | Existing audit and Work Package persistence | Add versioned run/event/artifact interfaces with compatibility adapters |

One concrete documentation/prompt mismatch to resolve during extraction:
`agent/context.py::_ACCEPTANCE_NOTE` permits acceptance from summaries and
mechanical hints, while ADR 0001 requires evidence-backed goal completion.
Prompt wording must agree with the enforced contract; summaries cannot replace
required evidence. A verifier error or skipped check is not a pass for a
required criterion, and is also not automatically a product defect.

The implementation baseline passed 175 targeted tests before edits. The first
component extraction passed 256 regression tests; the subsequent contract-control
extraction and new component tests passed 161 targeted tests. These counts are
separate overlapping runs, not additive. See the implementation checkpoint below.

## 3. Target responsibilities and authority

This remains a modular Python application, not seven services or seven agents.

| Component | Owns | Must not own |
| --- | --- | --- |
| Adaptive Controller | Lifecycle, legal actions, validated transitions and operation invocation | Semantic generation, tool execution, granting authority, treating proposals as verified completion |
| Collector | Minimal context assembly, requested observations, scoped retrieval, provenance and freshness | Whether context is sufficient, choosing the next business action, hidden model calls |
| Thinker | LLM analysis, candidate solutions, query/content/code generation when requested | Running mutations, changing frozen criteria or granting authority |
| Decider | Adaptive selection: collect, think, execute, request verification, finish proposal or escalation | Executing tools, marking a goal verified, changing its own permissions |
| Runtime/Tools | Dispatch, timeout/cancellation, operation lifecycle; guarded tool execution through code/MCP | Semantic routing, solution generation, automatic business recovery choices |
| Memory | Derived reusable knowledge with provenance, scope, conflicts and invalidation | Source-of-truth task state, completion verdicts, execution control |
| Storage | Contracts, versioned state, append-only events, immutable artifacts and experiment records | Model calls or selection of next actions |
| HoH | Optimization hypotheses, isolated candidates, comparable evals and selection reports | Live task routing, rewriting eval criteria, automatic promotion |

Runtime/Tools is a conceptual block, internally separated into an operation
dispatcher, attempt lifecycle, tool executor, verification runner and injected
guards. The Adaptive transition reducer stays engine-specific: it applies
typed results to state and computes legal transitions. It contains no SDK
calls, free-text intent classifier, semantic ranking or recovery strategy.
These are modules with explicit functions/ports, not another controller object
that imports every subsystem.

### Provider and governance boundary

- Thinker and Decider model calls live only in the driver. Jev is also a model
  call: its different output format is not an exemption from HI #1.
- Extend the driver gateway rooted at `agent/vendors/base.py` with an optional
  typed decision capability; preserve existing `call()` adapters. Inject the
  gateway into Thinker/Decider. No model SDK in `server.py`, tools, validators,
  policy, Storage, Memory or deterministic Collector code.
- A model-assisted Collector ranking request goes through the driver Decider;
  retrieval itself remains an evidence-producing operation.
- Preserve HI #2 skill selection/injection, HI #3 evaluator firewall, HI #5
  fail-closed permissions, HI #7 append-only attempts, HI #8 spawn auditing,
  and HI #9 lifecycle tags. No new sub-agent topology is implied.
- The artifact evaluator receives only the permitted artifact view. Goal
  acceptance is a separate deterministic mapping of contract criteria to
  verifier evidence or explicitly designated review records.

### Proposed package boundaries

Use existing packages where possible; these new paths are targets, not facts:

- `musubi/agent/collector/`: requests, context assembly, retrieval adapters.
- `musubi/agent/thinker/`: solution generation through the model gateway.
- `musubi/agent/decider/`: decision port, Jev adapter, LLM baseline, fake.
- `musubi/agent/engines/adaptive/`: pure transitions and run loop wiring.
- `musubi/agent/runtime/`: bounded lifecycle and operation dispatch.
- `musubi/agent/hoh/`: driver-side optimization lifecycle and analysis.
- Existing `validation/`, `tools/`, `memory/`, `storage/` retain deterministic
  roles; extract smaller modules as responsibilities move.

Contracts/ports have no concrete provider imports. Components do not import
`agent/run.py`; it becomes a composition/CLI entry point. HoH reads public
Storage interfaces rather than Adaptive internal state classes. Do not rename
the entire repository before the first end-to-end slice works.

## 4. Typed handoffs

New names below are proposed DTOs, not extra fields to inject into existing
closed GoalContract schemas. Schema changes require explicit versions.

| Record | Required logical content |
| --- | --- |
| `CollectionRequest` | Goal/unit refs, state revision, source/tool selector, bounded arguments, information need, read scope and budget |
| `ContextBundle` | Request/state refs, observations with source/hash/time, retrieved evidence refs, missing/stale/truncated flags, effective input hash |
| `SolutionSet` | Version, state/context refs, candidate IDs, intended effects, preconditions, action/query/artifact references, verification expectations |
| `DecisionRequest` | Goal/gap refs, state/context version, immutable option-set ID/hash, allowed candidate IDs and remaining budget |
| `Decision` | Decision kind, selected option ID, request/state/option-set refs, available probability/confidence fields, evidence refs, provider/config identity |
| `AttemptRequest` | Existing goal/unit/contract hash, selected action and argument refs, authority scope, idempotency key, workspace, verifier and budget |
| `AttemptResult` | Execution status, output/evidence refs, verification status, usage, typed errors, event refs and stop reason |
| `RunRecord` | Run/parent/engine IDs, contract and manifest refs, state/status, budgets/usage, trajectory ref, outputs and terminal report refs |
| `RunEvent` | ID, per-run sequence, producer/type/time, run/unit/attempt IDs, causal links, payload refs, completeness/redaction flags |

Jev selects an existing option. Free-text queries, code and tool arguments come
from a validated template, existing state, or a Thinker-produced candidate.
Never treat a choice label as a shell command or synthesize free-text parameters
inside Runtime. Include abstain/need-more-information options. Confidence is
not evidence, a permission grant, or proof that a multi-step plan will succeed.

Bind decisions to state revision and the exact option set. If the state changes,
reject the stale decision and collect/re-decide; do not reinterpret its numeric
index against a different list. Recheck scope and authorization at execution.

## 5. Execution and memory workflows

1. Validate request and enter Adaptive. Other engine migration is out of scope.
2. Adaptive initializes state and a bounded bootstrap context. Thinker proposes
   a Goal Contract when needed; deterministic validation freezes it before any
   worker mutation. Existing bounded contract correction remains enforced.
3. Collector prepares minimal context: goal/constraints, known state, legal
   capabilities, gaps and relevant evidence. It does not scan everything by
   default. Repository/tool policy instructions needed for execution are kept.
4. Decider selects a typed option: collect, think, execute, verify, finish or
   escalate. Available options reflect current lifecycle and permissions.
5. The engine validates applicability. Runtime dispatches one bounded operation.
   Collection may read tools or Memory; Thinker may create candidate content;
   mutation requires a frozen Work Package, skill and execution authority.
6. Persist outputs/evidence before returning terminal references. Reduce the
   result into new state. All operations, including model/collection calls,
   are charged and visible in the trajectory.
7. Re-enter Decider with updated context. Identical failed operations and repeated
   no-new-evidence collection consume explicit limits and eventually stop.
8. A finish proposal only succeeds when mandatory criteria and regression checks
   pass from evidence. Otherwise return the gap, or terminate honestly on limits.
9. Finalize the Adaptive result through the new Storage boundary. Enqueuing Memory
   consolidation and its retry/reconciliation lifecycle is backlog work.

Task changes stay in task workspaces. HoH candidate changes stay in isolated
harness workspaces. Do not mix task artifact patches with harness improvements.

## 6. Implementation work packages

Each WP below is a planning unit. Before execution, freeze its scope,
expected_delta, verifier, budget and rollback_point using the existing contract
schema. Budget allocation is an explicit per-WP value bounded by the parent
run, never an invented unlimited default. All WPs start pending.

### WP0 — Baseline and boundary agreement

- Scope: existing tests, ADR 0001, this plan, `AGENTS.md`, `CLAUDE.md`, diagrams.
- Expected delta: an executable baseline and an ADR recording Adaptive ownership,
  driver-only model calls and the backlog boundaries.
- Work: run relevant contract/loop/budget/firewall tests; then existing CI suite.
  Record failures before changes. Confirm three-consecutive-invalid-contract
  behavior from the earlier discussion exists; implement missing protection in
  its own bounded fix rather than assume an unpushed historical hotfix exists.
- Verifier: published baseline report; no mutation before frozen contract, no
  invalid-planning escape to text completion, required missing artifacts fail.
- Rollback: no behavior migration yet; revert only added fixtures/docs.

### WP1 — Contracts, state and component ports

- Depends on WP0. Scope: new typed records/ports and existing contract adapters.
- Expected delta: provider-independent handoffs, decision/state binding and
  pure transition rules; no new model call path enabled.
- Verifier: round-trip versioned records; reject unknown/stale candidate IDs,
  invalid state transitions, changed contract hashes and illegal actions;
  swapping fake and LLM decision providers does not change Runtime code.
- Rollback: new interfaces remain unused by the live loop until WP5.

### WP2 — Storage evidence and Harness Version Manifest

- Depends on WP1. Scope: `storage/`, schema migration, evidence/runtime-log
  adapters, read compatibility used by Console and audit reports.
- Expected delta: shared RunRecord, append-only events, immutable payload refs
  and manifest usable by Adaptive, Pipeline and HoH.
- Manifest includes revision/dirty patch, prompt/config hashes, model and tool
  settings, skills, policies and environment identity. It describes which
  harness ran; the trajectory describes what happened. Never persist secrets
  or require private chain-of-thought; explicitly mark unavailable payloads.
- Verifier: reconstruction of successful, failed and interrupted runs; stable
  causal IDs; missing artifact cannot be referenced by successful completion;
  migration preserves old audit/Console reads and retry history.
- Rollback: additive migration and old read adapters; back up before migration,
  avoid destructive down-migration over newly recorded evidence.

### WP3 — Collector and Memory lifecycle

- Collector context preparation remains active. The Memory lifecycle portion is
  **backlog** and is not an active dependency. Scope when resumed:
  `agent/context.py`, `agent/collector/`, `memory/`.
- Expected delta: minimal bounded collection, provenance/freshness and scoped
  retrieval; terminal-run consolidation independent of Pipeline review stages.
- Reuse input fitting/compression; no vector database is required for MVP.
  Retrieval during planning/context assembly/HoH remains separate from
  consolidation after durable run finalization. A no-new-knowledge outcome is valid.
- Verifier: only requested permitted reads run; stale/missing evidence is marked;
  repeated collection is bounded; retrieval is project/revision scoped;
  duplicate consolidation is harmless; Memory outage preserves task outcome;
  evaluator contexts cannot receive Memory, request or plan.
- Rollback: retain legacy read adapter; derived entries retain original evidence.

### WP4 — Thinker and Decider providers

- Depends on WP1 and the active Collector preparation slice. It does not depend
  on Memory lifecycle work. Scope: driver components, gateway and configuration.
- Expected delta: Thinker proposes solutions/content; Decider selects typed
  options. Jev is a configured target provider, not a hardcoded dependency.
- Implement fake provider first, LLM decision baseline next, Jev adapter after
  verifying actual API access, schema, option limits and usage reporting.
  Unsupported features fail explicitly; no fake text/tool-call compatibility.
- Verifier: arbitrary generated parameters never originate in Jev selection;
  provider timeout/abstention is typed and bounded; any configured fallback is
  explicit, separately charged and logged. No SDK imports in substrate.
- Rollback: operator selects baseline adapter; missing Jev credentials does not
  cause a silent provider switch. Live Jev validation is a separate gate.

### WP5 — Thin Runtime and Adaptive golden path

- Depends on WP1-WP2, the active Collector slice and WP4. Scope: `run.py`,
  Work Package controller, runtime/tool
  boundaries, validation services, existing CLI entry points.
- Expected delta: decision ownership leaves the monolithic loop. Runtime
  dispatches operations and guards execution without inventing next steps.
- Split current controller functions into contract/attempt/evidence helpers;
  preserve skill injection, worker firewalls, budget reservations, rollback
  limits and audited spawns. Route free-form code generation through Thinker
  in a bounded role context; only guarded execution can apply it.
- Verifier: small goal -> collect -> think -> decide -> frozen Work Package ->
  execute -> verifier failure -> bounded repair -> verified output. Also verify
  policy denial, budget/cancellation, invalid planning, missing output and
  false finish. Record duration/model/collection/tool costs per operation.
- Rollback: retain old CLI adapter during a bounded migration period; do not
  create a permanent ungated legacy mode or salvage bypass.

### WP6 — Pipeline compatibility and restart integrity

- **Backlog.** Depends on accepted Adaptive cleanup and a separate implementation
  plan. Scope: `pipeline_runner.py`, checkpoint/resume, Console reads.
- Expected delta: Pipeline consumes shared attempt/evidence ports but retains
  user-invoked deterministic stage/gate transitions. Root cannot invoke it as
  an unapproved hidden control path.
- Verifier: stage order, retries, evaluator firewall and checkpoints remain
  compatible. Resume restores contracts, budget and pending operations; uncertain
  external side effects require reconciliation, never blind duplicate execution.
- Rollback: revert adapter migration while retaining recorded event/schema data.

### WP7 — HoH evidence reader and Optimization Contract

- **Backlog.** Depends on an accepted Adaptive path and later Storage evidence
  work. Scope: `agent/hoh/`, evidence queries, manual CLI.
- Expected delta: independent manual HoH run can diagnose an actual task run
  without importing Adaptive state or requiring Memory.
- Freeze target metrics, baseline identity, modification scope, eval suite,
  regression limits, aggregate budget, repeat policy and stop conditions.
  Analysis yields an evidence-linked falsifiable hypothesis or insufficient-evidence
  result. Automatic triggers are explicitly deferred.
- Verifier: ordinary runs/finalization cannot trigger HoH; missing/redacted
  evidence is visible; task criteria cannot be rewritten by optimization.
- Rollback: disable HoH entry point without affecting Adaptive/Pipeline.

### WP8 — HoH isolated candidate evaluation

- **Backlog.** Depends on WP7; initial evaluation may use child Adaptive runs
  without waiting for Pipeline migration. Scope: candidate workspace,
  evaluation launcher, comparator,
  experiment archive and selection report.
- Expected delta: propose -> isolated change -> evaluate -> record -> compare ->
  continue/stop; output patch/report or no verified improvement.
- Verify candidate execution using revision, import-root and manifest checks;
  isolate workspace, audit DB, Memory writes and external effects. Parent HoH
  stays on its baseline. Protect eval tasks/scoring and the running HoH source.
- Charge baseline/candidate child runs to the experiment budget. Pin comparable
  inputs, providers, budgets and environment; use declared repeats and held-out
  cases. Preserve failures and rejected candidates, not only winners.
- Verifier: recursive HoH rejected; candidate cannot edit evaluator; baseline
  and candidate results are correctly attributed; regression rejected; limits
  stop search; cancellation/restart does not re-run an uncertain side effect.
- Rollback: discard candidate workspace; selected candidate still requires
  normal human review/CI before any merge or deployment.

### WP9 — Remove Adaptive duplication and qualify providers

- Depends on WP5 only for the active milestone. Scope: obsolete Adaptive
  loop/helper paths, tests, operator docs,
  diagrams, targeted CI boundary checks and benchmark report.
- Expected delta: one authoritative path per responsibility; no replacement
  god object or permanent parallel legacy loop.
- Add import-boundary checks: substrate imports no model SDK; Runtime imports
  ports, not concrete Thinker/Decider providers; active components do not depend
  on CLI entry points. Enforce touched-file lint
  without making unrelated historical lint debt a hidden migration blocker.
- Verifier: existing Python/Rust/Console CI, targeted boundary tests and held-out
  end-to-end tasks. Measure success/false completion, p50/p95 duration, total
  cost per successful task, LLM/Jev call counts, context volume, failed actions
  and escalation rate. Compare at declared quality tolerance, not raw call price.
- Rollback: retain a reviewed pre-cutover revision; do not delete raw evidence.

## 7. Delivery order and diagrams

Active dependency order: WP0 -> WP1 -> WP2 -> WP4 -> WP5 -> WP9. The active
portion of WP3 is bounded Collector preparation inside WP5. Memory in WP3 and
WP6-WP8 are backlog items and do not block Adaptive completion.
Keep reviewable commits and update this checklist with evidence after each WP.

| Milestone | Exit condition |
| --- | --- |
| M1: Adaptive contracts and Storage | WP0-WP2 accepted; state, decisions and evidence are reconstructable |
| M2: Adaptive slice | Active WP3 portion plus WP4-WP5 accepted; one golden path works |
| M3: Adaptive cutover | WP9 accepted; legacy Adaptive path removed and Jev benchmark reported |
| Backlog | Memory work in WP3 and WP6-WP8 each receive a later plan |

Produce PlantUML source plus rendered SVG as implementation deliverables:

- Component boundaries, including the driver/substrate boundary.
- Adaptive collect/think/decide/execute/verify loop.
- Adaptive component boundaries and controller lifecycle.
- Adaptive collect/decide/think/execute/verify loop.

Pipeline, Storage/Memory lifecycle and HoH diagrams remain backlog artifacts;
they are not implementation deliverables or acceptance gates for this milestone.

Keep `.puml` and `.svg` separate. Add reproducible syntax/render checks to CI
and manually review the diagrams; the current CI file does not itself prove
PlantUML rendering is already configured. Do not introduce Mermaid/Draw.io as
an alternative source of truth in repository documentation.

## 8. Decisions and measurements required before enabling production use

- Jev access and actual provider limits are not verified by this plan. Use
  contract tests with fake responses first; never claim provider integration
  from those tests alone.
- Operator-owned retry/collection/timeout/cost limits must be explicit before
  each run. Calibrate confidence thresholds against labeled cases; confidence
  does not grant authority or prove completion.
- Specify eval task set, repeat count and acceptable quality regression in the
  Optimization Contract before examining candidate scores. No post-hoc lowering.
- A fallback to LLM is explicit configuration. Measure its cost and frequency;
  it may eliminate any Jev cost advantage.
- Runtime public behavior and external MCP compatibility should survive module
  moves. Any required schema break gets an explicit migration/version.

## 9. Implementation status

- [x] Prior HoH decisions and current component discussion reconciled.
- [x] Remote branch and source baseline inspected; plan prepared.
- [ ] WP0 baseline and ADR.
- [ ] WP1 contracts and ports — decision/controller boundary in progress.
- [ ] WP2 Adaptive Storage, evidence and manifest — decision ledger in progress.
- [ ] WP3 Collector active slice; Memory lifecycle — backlog.
- [ ] WP4 Thinker and Decider providers.
- [ ] WP5 thin Runtime and Adaptive golden path.
- [ ] WP6 Pipeline and resume compatibility — backlog.
- [ ] WP7 manual HoH evidence analysis — backlog.
- [ ] WP8 isolated HoH evaluation — backlog.
- [ ] WP9 Adaptive cleanup, CI and provider qualification.

Verification is recorded per implementation checkpoint. Component tests and
existing runtime tests do not prove live-provider performance or HoH isolation.
The complete WP acceptance checklist remains open where its wider scope has
not been implemented.
