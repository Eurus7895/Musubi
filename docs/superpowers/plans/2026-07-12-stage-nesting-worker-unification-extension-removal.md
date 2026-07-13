# Stage nesting, worker-prompt unification, extension removal

Date: 2026-07-12 · Branch: `chore/agents-catalog-cleanup`

## Context

Three coupled decisions landed together:

1. **Stage nesting (parity).** Standalone pipeline stages ran as strict
   leaves — `run_pipeline` never passed an orchestration into `run_unit` —
   while `pipeline.yaml` declared `spawns:` for coder (explorer,
   investigator) and the evaluators (reviewer-aux). The declaration was
   silently ignored by the CLI host. The decision was challenged ("each
   stage already has its agent — why spawn more?") and confirmed: this is
   context offloading *within* a stage, not extra stages, and the server
   policy (`spawns ∩ MAIN_SUBAGENT_ALLOWLIST`, resolved per pipeline from
   the stage's parent session) already existed.
2. **Worker-prompt unification.** The `pipeline-stages/` prompts carried
   the embedded host's ceremony (`maxTurns: 1`, no tool calls, JSON
   `file_contents` manifests materialised by the TS runner). Wrong
   execution model for the CLI's interactive worker loop; running them
   there would produce dead JSON and no files. Every role now has exactly
   one worker-style prompt under `.github/agents/workers/`, including new
   `scoper` / `finder` / `synthesizer` prompts that make code-review a
   real standalone pipeline (the roles also joined `SUBAGENT_POLICIES` —
   `build_subagent_context` used to raise for them — and
   `SUBAGENT_ROLE_SKILLS` pushes pr-scope-detection / per-file-review /
   code-review).
3. **Extension removal.** The user decided to stop supporting the VS Code
   embedded host entirely. `copilot-harness-extension/`, its slash-command
   definitions (`.github/commands/`), its CI job, and the ceremony prompts
   were deleted. This is what unlocked (2): with an extension in place,
   deleting or rewriting the ceremony prompts would have broken its next
   packaged build (the .vsix bundles a build-time snapshot of
   `.github/agents/` and its runner hard-fails on non-JSON stage output).

## Goal

One driver host (`agent` CLI over `LMRouter`), one observer (Console),
one prompt catalog (purpose dirs, no flat files), one skill-push path
(spawn context), and pipelines whose declared stage spawns actually work.

## Tech stack

Python (musubi core + policy engine), YAML pipeline recipes, markdown
agent prompts, GitHub Actions CI.

## Implementation steps (landed)

1. `musubi_spawn_pipeline_stage` returns `spawn_roles`
   (= `list_subagent_roles(role, pipeline)`); the runner hands a stage the
   spawn tool + a pipeline-parented `Orchestration.stage_child(role, psid)`
   only when `spawn_roles` is non-empty and depth budget remains. The
   envelope adds no depth level: root(0) → stage(1) → stage child(2).
2. New `workers/{scoper,finder,synthesizer}.agent.md`; the three roles
   join `SUBAGENT_POLICIES` (tool sets equal to
   `PIPELINE_POLICIES["code-review"]`, boot-time sync-checked) and
   `SUBAGENT_ROLE_SKILLS`; they stay OUT of
   `MAIN_SUBAGENT_ALLOWLIST["agent"]` (pipeline-internal, locked
   decision #4).
3. Catalog: `pipeline-stages/` deleted; flat twins deleted; the four
   flat-only worker prompts moved into `workers/`. Both resolvers already
   preferred the purpose dirs.
4. Extension: directory, `.github/commands/`, CI `typescript` job, and
   slash-command tests deleted; setup wizard's `.vscode/mcp.json` step
   kept (generic VS Code MCP clients) with reworded prompts.
5. Hard Invariant wording updated in the same PR: HI #1 (single inject
   point — `LMRouter`), HI #2 (one push mechanism — spawn context), HI #3
   (two enforcement points — `_STAGE_PERMISSIONS` + last-stage brief).

## Invariants touched

HI #1/#2/#3 wording narrowed (embedded host clauses removed); HI #5, #7,
#8, #9 unchanged. Enforcement got stronger, not weaker: the sync check,
membership re-check, and fail-closed stage prompts all remain.
