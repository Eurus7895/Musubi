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

The standalone `agent` host is the primary driver surface. The VS Code
extension remains a supported Copilot surface. Both drive the same substrate:
policy, audit, skill catalog, compression, memory, and boundary controls.

---

## Current Focus

1. **Fix the VS Code extension rename.** Update the extension's hardcoded
   `harness_*` tool calls to `musubi_*` so the supported Copilot surface works
   against the renamed server.

2. **Root prompt catalog cleanup.** Finish the post-worker-modes migration by
   keeping the canonical root prompt under the purpose-specific catalog path,
   removing the temporary flat legacy root prompt from this repo, and keeping
   artifact routing skill-first instead of task-template-specific.

3. **Installer runtime reduction.** Prefer a bundled or locally repairable
   Python core payload so first run does not depend on global `pip install` or
   manual `PATH` edits. Keep network install as a fallback for development
   builds.

4. **Signing and release hardening.** Sign the Windows installer and document
   the expected Defender / SmartScreen path for non-developer installs.

---

## Postponed

- **Pipeline parity eval suite.** A dual-mode eval suite comparing the VS Code
  pipeline and standalone host is deferred until we are ready to revisit
  pipeline dissolution.
- **Dissolve the 4-stage pipeline shape.** The staged pipeline shape remains
  supported for now. If revisited, re-home its boundary primitives onto
  sub-agent and tool-call boundaries before removing the shape.

---

## Live Substrate Work

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
  visible in logs and audit.
- **MCP tool surface profiles.** Trim model-visible tool catalogs for internal
  and external drivers without removing substrate tools. Implementation plan:
  [`2026-07-01-mcp-tool-surface-trimming.md`](./superpowers/plans/2026-07-01-mcp-tool-surface-trimming.md).
- **Lines-of-substrate vs lines-of-skill ratio.** Track whether capability
  growth is moving into durable substrate and reusable skills rather than
  one-off prompt scaffolding.
- **Relocate substrate out of `.github/`.** Move skills, memory, agents, and
  pipeline definitions to a platform-neutral root when the standalone host and
  extension split makes the migration cheap enough.

---

## Completed Tracks

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
- Deterministic pipeline run: `agent --pipeline <name>` CLI entry point plus the
  GUI Pipeline studio wired to spawn it (Run/Stop), with stage workers surfaced
  through the Orchestrator/Audit views
- Read-only discovery substrate: `musubi_glob` / `musubi_grep` MCP tools map the
  Grep/Glob capabilities, so standalone pipeline stages (and the root agent)
  find files deterministically instead of blind-guessing paths — closing the gap
  where stages authored for the embedded harness's injected workspace tree ran
  blind in the standalone runner
