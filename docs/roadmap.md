# Roadmap - Musubi

> Current direction and live work only. Historical detail lives in git log,
> closed PRs, artifacts, and the audit DB.
> Repo rules -> [`/CLAUDE.md`](../CLAUDE.md).

---

## Discipline

Every PR must move Musubi toward either:

- **Thicker substrate:** queryable audit, sharper invariants, deterministic
  routing, better skill catalog, stronger boundaries.
- **Thinner ephemeral structure:** less pipeline scaffolding, fewer prompt
  preambles, fewer model-limit compensations.

Substrate is anything that still helps when a stronger model lands. Ephemeral
structure exists only to compensate for current model limits and should be
deleted when the limit dissolves.

---

## North Star

Musubi is a governed orchestration substrate: deterministic, zero-LLM
validation at every agent-agent and agent-tool boundary.

The standalone `agent` host is the driver surface; the desktop Console
observes and operates the same substrate through `audit.db`: policy, audit,
skill catalog, compression, memory, and boundary controls. The VS Code
extension was removed — one inject point (`LMRouter`), one prompt catalog.

---

## Current Work

### Active

1. **Skill catalog growth.** Skills remain the cheapest optimization surface.
   Each new skill should carry useful metadata such as `applies-to`, `triggers`,
   and relevant tools.
   First batch landed: `debugging`, `refactoring`, `git-workflow`, and `web-ui`
   (universal procedures) plus `typescript` (router-gated to JS/TS workspaces).
   Coder gains all five; the dispatcher agent gains only the read-safe pair
   (`debugging`, `git-workflow`) so the generator boundary holds. `web-ui` is
   deliberately universal so an HTML/CSS artifact emitted from a non-JS repo
   still matches it — closing the dashboard case where no catalog skill applied.
   Every prior catalog entry was backfilled with `triggers:` so the recommender
   can rank the whole catalog, not just the newest skills.
   Reachability closed: a direct worker carries no skill tool, so grown catalog
   entries were previously unreachable by workers. The root now selects a skill
   per spawned worker and pushes it (option 3, see Completed track below), so
   catalog growth reaches workers without adding a skill tool to their lean
   surface. Extended to pipeline stages: the deterministic runner recommends a
   skill per stage (`musubi_recommend_skills(for_role=…)`, zero-LLM) and pushes
   it through `musubi_spawn_pipeline_stage` so feature-dev stages
   (designer/coder/reviewer) carry role-appropriate procedure instead of showing
   "no skill evidence"; `planner` has an empty skill allowlist and remains
   skill-less by design.

Runtime limits have one owner per dimension: the bounded runtime track owns
pipeline-stage turn caps, model-input characters, and total stage allowances;
per-worker effort owns output tokens for one LM call; root routing owns
worker-count and continuation-spawn policy. Do not introduce a second parser or
enforcement path for the same dimension.

### Backlog

- **Installer runtime reduction.** Prefer a bundled or locally repairable
  Python core payload so first run does not depend on global `pip install` or
  manual `PATH` edits. Keep network install as a fallback for development
  builds.
- **Signing and release hardening.** Sign the Windows installer and document
  the expected Defender / SmartScreen path for non-developer installs.
- **Stage extension by user grant.** When a pipeline stage exhausts its cycle
  cap it currently fails closed (`[stage <x>] exceeded N cycles`). Reuse the
  existing budget-grant gate (`pause_reason='budget_exhausted'`,
  `pending_extra_budget`, `grant` action) — today wired only into the
  server/GUI session path — inside the standalone pipeline runner, so an
  escalated stage (reviewer or any) can be extended by asking the user for
  more cycles instead of aborting the run. Design-gated: the grant is bounded,
  audited, and never waives the wall-clock rule. Plan to be written before
  implementation.
  Landed alongside: a **no-progress budget breaker** on the root run. A weak
  driver model that never converges (e.g. a flash model that emits tool calls
  as text and never signals done) otherwise burns the full 200k ceiling across
  the whole worker tree — the observed failure spent 202k/200k on five
  escalating workers. The breaker stops the run once ≥70% of the budget is
  spent with at least one failed worker and no worker having delivered a
  completed artifact (a `done` outcome with mutated files); it never fires on a
  run that is actually producing, and the remaining budget would not fund a
  fresh successful worker anyway. This does not fix a weak model — the real fix
  is a stronger driver — it only caps the wasted spend fail-fast.
- **Lines-of-substrate vs lines-of-skill ratio.** Track whether capability
  growth is moving into durable substrate and reusable skills rather than
  one-off prompt scaffolding.
- **Relocate substrate out of `.github/`.** Move skills, memory, agents, and
  pipeline definitions to a platform-neutral root now that the extension is
  gone and nothing pins the `.github/` location. Coordinate this before a large
  skill-catalog expansion so new catalog entries do not create avoidable move
  churn.

---

## Postponed

- **Dissolve the 4-stage pipeline shape.** The staged pipeline shape remains
  supported for now. If revisited, re-home its boundary primitives onto
  sub-agent and tool-call boundaries before removing the shape.

---

## Completed Tracks

- Conversation-aware routing, progress accounting, and deferred unknowns —
  four follow-ups to the advisory route. (1) `classify_task` takes a
  `has_history` boolean so a bare follow-up ("Okta", "skill?") is answered
  rather than planned; the flag says only that prior turns exist and is used
  only to route toward the cheaper answer, so it can never open a mutation
  path. (2) `agent_turns` gains `delivered_artifact`, and `chat_turn_usage`
  aggregates a conversation's turns, tokens, and trailing run of turns that
  wrote no file — the per-turn budget is process-scoped and resets on every
  message, so nothing could previously see a multi-turn spend loop. The root
  is warned at three barren turns and told to deliver or ask, not to plan
  again; it steers rather than halts. (3) Planner `unknowns` still block,
  except on a change with no critical flag and at most one file, where they
  ride to the next worker as `choose_sensible_defaults` — a wrong palette
  costs one turn to redo, while halting discarded the whole plan. (4) The
  chat surface accepts the pipeline command after conversational filler
  ("ok then run pipeline"), and the recommendation names that in-chat phrase
  before the shell command; the picker still requires the user to send, so
  locked decision #4 is untouched. Plan:
  [`2026-07-25-conversation-aware-routing-and-progress.md`](./superpowers/plans/2026-07-25-conversation-aware-routing-and-progress.md)

- Advisory routing and single-file manifest precedence — a consultative
  request ("explain each", "choose the best for me", "which auth provider
  should I choose?") is now its own scope kind instead of falling through two
  catch-alls into `medium_change`/`planner_then_coder_check`. The root answers
  it in one model call with an empty tool catalog: no planner spawn for a
  question that names no file, and no `musubi_recommend_skills` round trip.
  The branch is gated on the absence of a mutation verb, a diagnostic signal,
  and any path target, so edits, failure diagnosis, and codebase questions
  still route to workers. It is deliberately not routed through
  `_deterministic_scope_answer` — the model still reasons, it just gets no
  tools. Separately, the manifest subsystem ceiling now applies only above
  `MAX_SIMPLE_FILES`, so a one-file plan can no longer be escalated to the
  large workflow by subsystem count alone (which stranded the change: the
  orchestrator may not launch a pipeline, so no coder ever wrote the file).
  Critical flags and the file ceiling keep absolute precedence. Plan:
  [`2026-07-25-advisory-route-and-manifest-precedence.md`](./superpowers/plans/2026-07-25-advisory-route-and-manifest-precedence.md)

- Governed change assessment and recovery liveness — lexical-only mutation
  scope guesses are replaced by a deterministic ambiguity/impact/risk
  assessment (`agent/change_assessment.py`). A broad product request without
  deliverable constraints (`create a new website`) stops at one clarification
  before any parent session, model call, or worker spawn; a bounded nine-field
  planner `<change_manifest>` (4 KiB cap, fail-closed parse) reclassifies blast
  radius after planning, so an eleven-file/four-subsystem plan can no longer
  proceed as a medium change through a direct coder — it halts with a
  user-invoked `--pipeline feature-dev` recommendation and never auto-launches.
  Direct-worker role frontmatter is the sole owner of the turn cap
  (model-supplied `max_turns` is ignored, closing the starve-below-role-budget
  gap), and a genuinely unfinished turn-capped mutate worker with surviving
  files gets exactly one audited same-role continuation through the normal
  `_dispatch`/firewall/audit path (typed `FailureKind` + `decide_recovery`) —
  a second same-role failure, a spent worker slot, or a BUDGET/POLICY failure
  halts fail-closed. Recovery liveness no longer depends on the root
  voluntarily re-selecting the spawn tool. Plan:
  [`2026-07-22-governed-scope-budget-recovery.md`](./superpowers/plans/2026-07-22-governed-scope-budget-recovery.md)

- Bounded standalone pipeline runtime — one stage turn cap across
  runtime/state/audit, a hard 16k-character model-input cap including tool
  definitions, and reserved token capacity so planner/designer cannot consume
  coder/reviewer shares. A validated `PipelineWorkerSpec` resolves each stage's
  contract before spawn, so its declared `maxTurns` (clamped to [1, 12]) is the
  single cap flowing through the spawn row, `run_unit`, and the completion audit
  — the stage tool echoes `max_turns` and the driver fails closed on divergence.
  `fit_model_input` gives every explicit-budget worker (each pipeline stage) a
  hard input cap that counts tool definitions and raises before the model call
  rather than sending an over-budget request; the root keeps soft best-effort
  fitting. `ChildTokenBudget` + `pipeline_stage_allowance` give each stage a
  fair-share slice of the run budget charged through to the parent, so an early
  stage cannot spend a later stage's reserve, and allowance exhaustion finalizes
  the run once as `escalated`. The one-cap rule also covers direct workers: a
  role's `maxTurns:` frontmatter clamps the spawn's turn budget
  (direct-worker role frontmatter is authoritative; model-supplied turn
  counts are ignored), and a stage or worker that finishes on its
  last allowed turn attaches a substrate-verified artifact manifest so the
  audit records done instead of a false escalation. Plan:
  [`2026-07-12-bounded-standalone-pipeline-runtime.md`](./superpowers/plans/2026-07-12-bounded-standalone-pipeline-runtime.md)

- Root goal-state controller and token economics — the root now owns a
  current-run-only `GoalState` containing the exact user intent, deterministic
  scope/route, root usage, and bounded `OutcomePacket` feedback. After a worker
  terminates, the driver retains the stable system contract but replaces raw
  tool transcripts with one decision delta; model-visible root tools are
  reduced by phase (spawn plus skill *selection* in every scope, the
  content-loading skill tools added for broader work, recovery tools only
  inside the bounded recovery window, and **no tools once the worker ceiling
  is spent** — a root that all-succeeded into its `max_root_workers` cap is
  forced to conclude from the evidence it has instead of spinning refused
  spawns to the cycle limit, which previously wasted the whole budget and, on
  pre-salvage builds, surfaced as an `exceeded N cycles` failure). Skill
  selection is deliberately
  available even for simple artifacts: the root ranks a worker's skills with
  `musubi_recommend_skills(for_role=…)` and pushes the chosen `pushed_skill_id`
  into the spawn, so a direct worker (which carries no skill tool of its own)
  still receives role-appropriate procedure. The spawn re-validates the id
  against the worker role's `AGENT_SKILL_ALLOWLIST` entry (HI #3), so the root
  can never push a skill the role could not itself load. Adding the selection
  tool to a simple root costs ~1k tokens across the two-call projection, so the
  simple-root guard moved from 3k to ~4.5k; 20k remains the hard regression
  guard, not a normal budget. Plan and design:
  [`2026-07-15-root-goal-state-controller.md`](./superpowers/plans/2026-07-15-root-goal-state-controller.md) and
  [`2026-07-15-root-goal-state-controller-design.md`](./superpowers/specs/2026-07-15-root-goal-state-controller-design.md)

- Musubi System Atlas — a self-contained Vietnamese maintainer guide maps the
  driver, zero-LLM governance substrate, operator projection, and external
  boundaries; documents component rationale, invariants, token economics, and
  architecture evolution; and includes 13 interactive execution traces plus a
  24-question scored review. The artifact uses an explicit light palette so
  host dark-mode settings cannot obscure the content. [Open the atlas](../artifacts/musubi-system-atlas.html).
  Design and plan:
  [`2026-07-16-musubi-system-atlas-design.md`](./superpowers/specs/2026-07-16-musubi-system-atlas-design.md) and
  [`2026-07-16-musubi-system-atlas.md`](./superpowers/plans/2026-07-16-musubi-system-atlas.md)

- GUI/CLI orchestrator token economics — every logical root, child, pipeline,
  retry, and forced-final LM cycle records input, cached-input subset, output,
  LM time, usage source, worker identity, and tool names. The CLI and
  Orchestrator project selected-session totals from the same rows. The live contract is
  token-only; obsolete pricing and history-attribution fields are ignored in
  existing databases rather than destructively dropped. Plans:
  [`2026-07-13-orchestrator-token-economics.md`](./superpowers/plans/2026-07-13-orchestrator-token-economics.md)
- Console workspace separation — Orchestrator is the only GUI execution
  surface, with Direct/Pipeline launch modes, durable conversation, minimal
  summon topology, node-filtered runtime evidence, and evidence-backed skill
  provenance. Pipeline Studio is builder-only: create, drag/reorder, configure
  nested spawn allowlists, validate, and atomically save deterministic recipes.
  Design and plan:
  [`2026-07-14-console-workspace-separation-design.md`](./superpowers/specs/2026-07-14-console-workspace-separation-design.md) and
  [`2026-07-14-console-workspace-separation.md`](./superpowers/plans/2026-07-14-console-workspace-separation.md)
- Console request runtime history — every Orchestrator launch receives one
  durable `request_id`; host, root, and exact worker output is line-framed and
  appended to `runtime_log_events` without replacing prior requests. A session
  now projects `Request 01 → agents → Request 02 → agents`; selecting a request
  opens whole-request Overview/Request log, while selecting an agent opens
  Overview/Agent log filtered by exact handle, with one Back-to-graph path.
  Sessions fully hides instead of collapsing, and Conversation keeps chat,
  skills, and token economics without duplicate Summary/Verbose evidence.
  Design and plan:
  [`2026-07-26-console-request-runtime-history-design.md`](./superpowers/specs/2026-07-26-console-request-runtime-history-design.md) and
  [`2026-07-26-console-request-runtime-history.md`](./superpowers/plans/2026-07-26-console-request-runtime-history.md)
- Per-worker effort ceiling and output budget — mutate workers open at the
  shared 16,384-token per-call brake while read-only workers retain the cheap
  2,048-token floor and sticky escalation. Worker frontmatter may declare
  `maxOutputTokens`; an optional profile `max_output_tokens` clamps it. Empty
  create/append content is rejected before dispatch, replay elision markers
  instruct regeneration, and worker prompts identify the host shell. The
  cumulative root worker ceiling is classifier-independent; terminal worker
  outcomes now feed bounded replacement recovery. Plan:
  [`2026-07-13-agent-effort-ceiling-per-worker.md`](./superpowers/plans/2026-07-13-agent-effort-ceiling-per-worker.md)
- Project-scoped GUI sessions — exact runtime ownership, retained logs,
  cancellation, pipeline ancestry, shared project writer lease, durable session
  selection, and read-only browsing while another session runs. Historical
  Orchestrator sessions become resumable when the driver is idle; the follow-up
  atomically promotes and continues the viewed chat ID. Plans:
  [`2026-07-12-project-scoped-session-runtime.md`](./superpowers/plans/2026-07-12-project-scoped-session-runtime.md),
  [`2026-07-12-orchestrator-session-list.md`](./superpowers/plans/2026-07-12-orchestrator-session-list.md), and
  [`2026-07-14-resumable-historical-session.md`](./superpowers/plans/2026-07-14-resumable-historical-session.md)
- Per-cycle LM audit — `agent_cycles` persistence, query API, tool-surface
  exposure, and regression coverage
- Scope-aware root routing / agent gearbox — deterministic scope hints remain
  advisory and planning is explicit via `--plan`. Simple artifacts start with
  one coder without imposing a lifetime classifier cap; all direct runs share
  a three-worker ceiling, structured replacement handoff, and a two-cycle root
  recovery-analysis window. Read-only requests (reach/open/read/list/show a
  concrete path) now classify as `inspect` and route to a single read-only
  explorer instead of a `planner→coder` change — a mutation verb or the absence
  of a path target keeps the old routing. Plans:
  [`2026-07-04-scope-aware-root-routing-gearbox.md`](./superpowers/plans/2026-07-04-scope-aware-root-routing-gearbox.md)
  and [`2026-07-13-simple-artifact-recovery.md`](./superpowers/plans/2026-07-13-simple-artifact-recovery.md)
- MCP tool surface profiles — model-visible internal and external catalogs are
  trimmed without removing substrate tools. Plan:
  [`2026-07-01-mcp-tool-surface-trimming.md`](./superpowers/plans/2026-07-01-mcp-tool-surface-trimming.md)
- VS Code extension removal — one driver host. The embedded Copilot surface
  (`copilot-harness-extension/`, its slash commands, CI job, and ceremony
  prompts) was deleted; the CLI + Console are the surfaces. With it landed
  stage-nesting parity (pipeline stages spawn their declared helpers via
  `spawn_roles` = pipeline.yaml `spawns:` ∩ firewall) and full worker-prompt
  unification (code-review runs standalone: `agent "<diff>" --pipeline
  code-review`). Plan:
  [`2026-07-12-stage-nesting-worker-unification-extension-removal.md`](./superpowers/plans/2026-07-12-stage-nesting-worker-unification-extension-removal.md)
- Root prompt catalog cleanup — the purpose-dir catalog (root/, workers/,
  meta/) is canonical; no flat agent files remain
- Standalone worker model
- Model-agnostic `LMRouter` vendors
- Boundary policy and audit controls
- Reversible compression and deterministic compression eval
- Skill recommendation router and compression-aware context skill
- Root-agent mutation steering through bounded workers
- Token/context economics controls
- Windows GUI installer bootstrap
- Setup-aware GUI first run
- Agent catalog worker modes and chunk-safe large-file writes
- GUI audit/orchestrator Console first-run slice — the separate task launcher
  was removed so Orchestrator chat remains the single interactive session
  surface. Current operation is documented in [`guide.md`](./guide.md).
- Deterministic pipeline run: `agent --pipeline <name>` CLI entry point. The
  root agent does NOT auto-summon whole pipelines (`musubi_spawn_pipeline` is
  off the agent tool surface — a pipeline is a user-invoked run via the CLI
  flag, per policy locked decision #4), so a simple task can't be silently
  routed into a multi-stage pipeline. An operator may launch a registered
  recipe from Orchestrator Pipeline mode under the same durable conversation;
  Pipeline Studio only builds and saves recipes. Legacy Pipeline Studio chat
  rows remain readable for compatibility. Original implementation plan:
  [`2026-07-10-gui-pipeline-studio-sessions.md`](./superpowers/plans/2026-07-10-gui-pipeline-studio-sessions.md).
- Read-only discovery substrate: `musubi_glob` / `musubi_grep` MCP tools map the
  Grep/Glob capabilities, so standalone pipeline stages (and the root agent)
  find files deterministically instead of blind-guessing paths — closing the gap
  where stages authored for the embedded harness's injected workspace tree ran
  blind in the standalone runner
