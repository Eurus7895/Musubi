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

3. **Project-scoped sessions.** Bind process ownership, retained logs, budget,
   cancellation, and pipeline ancestry to exact sessions while all sessions in
   one project share the canonical workspace, dependencies, and databases.
   Keep one writer process and never create per-session directories,
   worktrees, clones, virtualenvs, or containers. Plan:
   [`2026-07-12-project-scoped-session-runtime.md`](./superpowers/plans/2026-07-12-project-scoped-session-runtime.md).
   The GUI now serializes the exact runtime-owner `chat_id`, scopes live and
   retained process state to that ID, preserves other sessions when clearing
   or re-minting one session, and rejects a second run through the shared
   project writer lease. Prior chat rows and live worker ancestry remain
   session-scoped without changing the shared filesystem root. The
   Orchestrator now indexes non-empty project conversations by exact `chat_id`,
   retains prior sessions after New session, reopens an idle selected session,
   and renders the latest turn as a root-first agent flow. Operators can browse
   prior chat and worker history read-only while another session keeps its
   driver ownership; design and implementation plan:
   [`2026-07-13-read-only-session-browsing-design.md`](./superpowers/specs/2026-07-13-read-only-session-browsing-design.md) and
   [`2026-07-13-read-only-session-browsing.md`](./superpowers/plans/2026-07-13-read-only-session-browsing.md).
   Earlier session-list follow-up plan:
   [`2026-07-12-orchestrator-session-list.md`](./superpowers/plans/2026-07-12-orchestrator-session-list.md).

4. **Bounded standalone pipeline runtime.** Use one stage turn cap across
   runtime/state/audit, enforce a hard 16k-character model-input cap including
   tool definitions, and reserve token capacity so planner/designer cannot
   consume coder/reviewer shares. Plan:
   [`2026-07-12-bounded-standalone-pipeline-runtime.md`](./superpowers/plans/2026-07-12-bounded-standalone-pipeline-runtime.md).

### Backlog

- **Skill catalog growth.** Skills remain the cheapest optimization surface.
  Each new skill should carry useful metadata such as `applies-to`, `triggers`,
  and relevant tools.
- **Per-cycle audit (`agent_cycles`).** Persist one row per LM call so
  architecture decisions can be empirical rather than guessed.
- **Scope-aware root routing / agent gearbox.** Add hybrid scope hints from
  the substrate while keeping the root agent responsible for the final route:
  simple edits and artifacts should use bounded single-worker/default-direct
  flows, larger features should require plan/design/pipeline-style structure,
  and route decisions, skill use, worker spawns, and budget halts should be
  visible in logs and audit. Implementation plan:
  [`2026-07-04-scope-aware-root-routing-gearbox.md`](./superpowers/plans/2026-07-04-scope-aware-root-routing-gearbox.md).
  Direction update: scope no longer forces particular roles or planner-first
  sequencing. Those route hints stay advisory and plan-first remains explicit
  opt-in via `--plan`. The deterministic `max_workers` value is now a
  cumulative root-run ceiling, in addition to the flat per-role batch width,
  so a `single_coder` turn cannot silently retry with multiple coders.
- **GUI/CLI orchestrator token economics.** Close the gap where stateful GUI
  turns cost far more than the stateless CLI: scope chat history per session
  with a new-session reset (an immortal per-project `chat_id` was replaying the
  whole thread every turn), surface tool-name / replay-token / seed-cost
  observability, add a deterministic mechanical validation gate at the worker
  boundary so the goal-holding root accepts on a trustworthy signal instead of
  re-ingesting artifacts, keep role selection advisory with explicit `--plan`
  opt-in, and enforce only the deterministic worker-count ceiling.
  Empty writes can no longer truncate an existing non-empty artifact; truncated
  tool calls are returned to the same worker for a chunked retry instead of
  ending that worker and encouraging a replacement. Implementation plan:
  [`2026-07-09-gui-cli-orchestrator-tokens.md`](./superpowers/plans/2026-07-09-gui-cli-orchestrator-tokens.md).
- **MCP tool surface profiles.** Trim model-visible tool catalogs for internal
  and external drivers without removing substrate tools. Implementation plan:
  [`2026-07-01-mcp-tool-surface-trimming.md`](./superpowers/plans/2026-07-01-mcp-tool-surface-trimming.md).
- **Per-worker effort ceiling & output budget.** The effort-routing floor
  (2048) opens every cycle low on a distributional bet that a coder emitting a
  whole file loses with probability 1.0, guaranteeing a truncated first mutate
  call, a double-billed retry, and — because the 4096 ceiling sits below a
  one-shot dashboard's natural size — an empty write. Key the floor to the
  worker's actual tool surface (mutate workers open at their ceiling), let each
  worker declare `maxOutputTokens:` in `.agent.md` frontmatter, and size the
  ceiling from universal tier defaults (mutate 8192 / read-only 4096) rather
  than a per-model physical-limit table — the true cap is undefined for
  ollama/on-prem and uniform-and-high where discoverable, so the vendor enforces
  it at call time; an optional per-model `max_output_tokens` in `.musubi/llm.json`
  remains for deliberate operator cost-capping only. Keep `EFFORT_CEILING` as
  the per-call runaway brake. Effort-economics follow-up to the 2026-07-09
  orchestrator-tokens work. Implementation plan:
  [`2026-07-13-agent-effort-ceiling-per-worker.md`](./superpowers/plans/2026-07-13-agent-effort-ceiling-per-worker.md).
- **Lines-of-substrate vs lines-of-skill ratio.** Track whether capability
  growth is moving into durable substrate and reusable skills rather than
  one-off prompt scaffolding.
- **Relocate substrate out of `.github/`.** Move skills, memory, agents, and
  pipeline definitions to a platform-neutral root now that the extension is
  gone and nothing pins the `.github/` location.

---

## Postponed

- **Dissolve the 4-stage pipeline shape.** The staged pipeline shape remains
  supported for now. If revisited, re-home its boundary primitives onto
  sub-agent and tool-call boundaries before removing the shape.

---

## Completed Tracks

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
