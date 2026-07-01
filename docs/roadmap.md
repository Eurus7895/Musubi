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

2. **Skill recommendation router.** Add deterministic skill recommendations for
   the standalone agent so it can pull the right procedural knowledge without
   inflating the system prompt or weakening skill allowlists. Implementation
   plan:
   [`2026-07-01-skill-recommender.md`](./superpowers/plans/2026-07-01-skill-recommender.md).

3. **GUI on-demand task launcher.** Add a Tauri command that launches one
   governed `agent "<task>"` process only when the user presses Run. The GUI
   passes project root, profile, and audit DB path through the child process
   environment, streams stdout/stderr, and supports cancellation.

4. **Installer runtime reduction.** Prefer a bundled or locally repairable
   Python core payload so first run does not depend on global `pip install` or
   manual `PATH` edits. Keep network install as a fallback for development
   builds.

5. **Signing and release hardening.** Sign the Windows installer and document
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
- **Standalone tool-catalog trimming.** Reduce model-visible `musubi_*` tool
  schemas for the root standalone agent without removing any substrate tools.
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
- Token/context economics controls
- Windows GUI installer bootstrap
- Setup-aware GUI first run
