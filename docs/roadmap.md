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

1. **Bounded standalone pipeline runtime.** Use one stage turn cap across
   runtime/state/audit, enforce a hard 16k-character model-input cap including
   tool definitions, and reserve token capacity so planner/designer cannot
   consume coder/reviewer shares. Plan:
   [`2026-07-12-bounded-standalone-pipeline-runtime.md`](./superpowers/plans/2026-07-12-bounded-standalone-pipeline-runtime.md).
   Landed: a validated `PipelineWorkerSpec` resolves each stage's contract
   before spawn, so its declared `maxTurns` (clamped to [1, 12]) is the single
   cap flowing through the spawn row, `run_unit`, and the completion audit — the
   stage tool echoes `max_turns` and the driver fails closed on divergence.
   `fit_model_input` gives every explicit-budget worker (each pipeline stage) a
   hard input cap that counts tool definitions and raises before the model call
   rather than sending an over-budget request; the root keeps soft best-effort
   fitting. `ChildTokenBudget` + `pipeline_stage_allowance` give each stage a
   fair-share slice of the run budget charged through to the parent, so an early
   stage cannot spend a later stage's reserve, and allowance exhaustion
   finalizes the run once as `escalated`. The one-cap rule also covers direct
   workers: a role's `maxTurns:` frontmatter clamps the spawn's turn budget
   (the model may request fewer turns, never more), and a stage or worker that
   finishes on its last allowed turn attaches a substrate-verified artifact
   manifest so the audit records done instead of a false escalation.

2. **Root goal-state controller and token economics.** The root now owns a
   current-run-only `GoalState` containing the exact user intent, deterministic
   scope/route, root usage, and bounded `OutcomePacket` feedback. After a worker
   terminates, the driver retains the stable system contract but replaces raw
   tool transcripts with one decision delta; model-visible root tools are
   reduced by phase (spawn-only for simple work, spawn plus skill lookup for
   broader work, recovery tools only inside the bounded recovery window).
   Simple successful runs target at most 3k root tokens and 8–12k total task
   tokens; 20k is a regression guard, not a normal budget. Plans:
   [`2026-07-15-root-goal-state-controller.md`](./superpowers/plans/2026-07-15-root-goal-state-controller.md)
   and design
   [`2026-07-15-root-goal-state-controller-design.md`](./superpowers/specs/2026-07-15-root-goal-state-controller-design.md).

Runtime limits have one owner per dimension: this track owns pipeline-stage
turn caps, model-input characters, and total stage allowances; per-worker
effort owns output tokens for one LM call; root routing owns worker-count and
continuation-spawn policy. Do not introduce a second parser or enforcement path
for the same dimension.

### Backlog

- **Installer runtime reduction.** Prefer a bundled or locally repairable
  Python core payload so first run does not depend on global `pip install` or
  manual `PATH` edits. Keep network install as a fallback for development
  builds.
- **Signing and release hardening.** Sign the Windows installer and document
  the expected Defender / SmartScreen path for non-developer installs.
- **Skill catalog growth.** Skills remain the cheapest optimization surface.
  Each new skill should carry useful metadata such as `applies-to`, `triggers`,
  and relevant tools.
- **Incomplete-artifact continuation policy.** Decide whether an exhausted
  mutate worker may receive exactly one audited continuation spawn without
  weakening the cumulative root-run worker ceiling. Root routing owns this
  policy; it is design-gated and is not part of per-call output-token sizing.
  The continuation brief must remain firewalled and bounded to audited artifact
  state such as path, bytes, and digest.
  Narrowed: a worker force-concluded at its turn cap that self-declares done
  now completes as done when its surviving mutated files verify non-empty —
  claimed by the driver as an `artifacts` manifest and independently re-checked
  on disk by the substrate (`sub_sessions.complete`), which otherwise keeps its
  fail-closed turn-cap coercion; the wall-clock rule is never waived. So
  continuation is only for genuinely unfinished artifacts, not for runs that
  merely spent their last turns on post-write verification.
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
  recovery-analysis window. Plans:
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
