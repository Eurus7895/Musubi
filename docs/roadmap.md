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

1. **Installer runtime reduction.** Prefer a bundled or locally repairable
   Python core payload so first run does not depend on global `pip install` or
   manual `PATH` edits. Keep network install as a fallback for development
   builds.

2. **Signing and release hardening.** Sign the Windows installer and document
   the expected Defender / SmartScreen path for non-developer installs.

3. **Bounded standalone pipeline runtime.** Use one stage turn cap across
   runtime/state/audit, enforce a hard 16k-character model-input cap including
   tool definitions, and reserve token capacity so planner/designer cannot
   consume coder/reviewer shares. Plan:
   [`2026-07-12-bounded-standalone-pipeline-runtime.md`](./superpowers/plans/2026-07-12-bounded-standalone-pipeline-runtime.md).

Runtime limits have one owner per dimension: this track owns pipeline-stage
turn caps, model-input characters, and total stage allowances; per-worker
effort owns output tokens for one LM call; root routing owns worker-count and
continuation-spawn policy. Do not introduce a second parser or enforcement path
for the same dimension.

### Backlog

- **Skill catalog growth.** Skills remain the cheapest optimization surface.
  Each new skill should carry useful metadata such as `applies-to`, `triggers`,
  and relevant tools.
- **GUI/CLI orchestrator token economics.** Enrich the existing per-cycle audit
  with tool-name, replay-token, and seed-cost fields and project those fields in
  the Console. Add a deterministic mechanical validation gate at the worker
  boundary so the goal-holding root can accept a compact validator signal,
  diff/summary, and artifact path instead of re-ingesting whole artifacts.
  Session isolation, advisory routing with explicit `--plan`, cumulative
  worker-count enforcement, empty-write protection, and chunked retry are
  completed dependencies rather than work owned by this track. Implementation
  plan:
  [`2026-07-09-gui-cli-orchestrator-tokens.md`](./superpowers/plans/2026-07-09-gui-cli-orchestrator-tokens.md).
- **Per-worker effort ceiling & output budget.** The effort-routing floor
  (2048) opens every cycle low on a distributional bet that a coder emitting a
  whole file loses with probability 1.0, guaranteeing a truncated first mutate
  call, a double-billed retry, and — because the 4096 ceiling sits below a
  one-shot dashboard's natural size — an empty write. Key the floor to the
  worker's actual tool surface (mutate workers open at the ceiling), let each
  worker optionally declare `maxOutputTokens:` in `.agent.md` frontmatter, and
  size the ceiling from a single shared default (`16384`) rather than a
  mutate/read-only split or a per-model physical-limit table — `max_tokens` is a
  cap not a price, so read-only workers (tiny outputs) cost nothing under a high
  ceiling, and the true cap is undefined for ollama/on-prem and uniform-and-high
  where discoverable, so the vendor enforces it at call time. 16384 stays an
  order of magnitude below the physical maxes so it is still a real per-call
  runaway brake, backstopped by the 200K run budget. An optional per-model
  `max_output_tokens` in `.musubi/llm.json` remains for deliberate operator
  cost-capping only. Effort-economics follow-up to the 2026-07-09
  orchestrator-tokens work. Implementation plan:
  [`2026-07-13-agent-effort-ceiling-per-worker.md`](./superpowers/plans/2026-07-13-agent-effort-ceiling-per-worker.md).
- **Incomplete-artifact continuation policy.** Decide whether an exhausted
  mutate worker may receive exactly one audited continuation spawn without
  weakening the cumulative root-run worker ceiling. Root routing owns this
  policy; it is design-gated and is not part of per-call output-token sizing.
  The continuation brief must remain firewalled and bounded to audited artifact
  state such as path, bytes, and digest.
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

- Project-scoped GUI sessions — exact runtime ownership, retained logs,
  cancellation, pipeline ancestry, shared project writer lease, durable session
  selection, and read-only browsing while another session runs. Plans:
  [`2026-07-12-project-scoped-session-runtime.md`](./superpowers/plans/2026-07-12-project-scoped-session-runtime.md),
  [`2026-07-12-orchestrator-session-list.md`](./superpowers/plans/2026-07-12-orchestrator-session-list.md), and
  [`2026-07-13-read-only-session-browsing.md`](./superpowers/plans/2026-07-13-read-only-session-browsing.md)
- Per-cycle LM audit — `agent_cycles` persistence, query API, tool-surface
  exposure, and regression coverage
- Scope-aware root routing / agent gearbox — deterministic scope hints remain
  advisory, planning is explicit via `--plan`, and `max_workers` is the
  cumulative root-run ceiling. Plan:
  [`2026-07-04-scope-aware-root-routing-gearbox.md`](./superpowers/plans/2026-07-04-scope-aware-root-routing-gearbox.md)
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
- GUI audit/orchestrator console first-run slice (the separate task launcher
  was removed so the Orchestrator remains the single session surface; plan:
  [`2026-07-01-gui-on-demand-task-launcher.md`](./superpowers/plans/2026-07-01-gui-on-demand-task-launcher.md))
- Deterministic pipeline run: `agent --pipeline <name>` CLI entry point. The
  root agent does NOT auto-summon whole pipelines (`musubi_spawn_pipeline` is
  off the agent tool surface — a pipeline is a user-invoked run via the CLI
  flag, per policy locked decision #4), so a simple task can't be silently
  routed into a multi-stage pipeline. Pipeline Studio invokes this entry point
  directly for registered recipes, owns an exact isolated chat session, and
  renders pipeline envelopes plus child stages separately from Orchestrator;
  implementation plan:
  [`2026-07-10-gui-pipeline-studio-sessions.md`](./superpowers/plans/2026-07-10-gui-pipeline-studio-sessions.md).
- Read-only discovery substrate: `musubi_glob` / `musubi_grep` MCP tools map the
  Grep/Glob capabilities, so standalone pipeline stages (and the root agent)
  find files deterministically instead of blind-guessing paths — closing the gap
  where stages authored for the embedded harness's injected workspace tree ran
  blind in the standalone runner
