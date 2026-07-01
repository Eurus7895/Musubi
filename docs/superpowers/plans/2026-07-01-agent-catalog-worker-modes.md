# Agent Catalog Worker Modes Implementation Plan

## Context

The standalone root agent now has a smaller default tool surface, which prevents
it from directly calling mutating tools. That fixed the first waste pattern, but
it exposed two deeper issues:

- Worker loops currently inherit the root-visible tool catalog, so a `coder`
  worker can lose access to `musubi_write_file` even though its role policy
  allows Write.
- Standalone workers reuse pipeline-stage prompts from `.github/agents/`, so
  `coder` behaves like a feature-dev stage and looks for plan/design context
  instead of directly completing a small file task.

The agent catalog needs a structure that separates root routing, standalone
workers, pipeline stages, and meta agents.

## Goal

Make standalone workers reliable without breaking the existing VS Code and
pipeline surfaces:

- Root agent keeps the small read/routing surface.
- Workers select tools from the full local Musubi catalog, then role policy
  narrows the surface.
- Standalone `coder` can directly write/edit files for simple tasks.
- Pipeline-stage prompts remain available for `/feature-dev` and `/code-review`.
- Agent files are organized by runtime purpose instead of one flat mixed
  catalog.

## Tech Stack

- Python standalone host: `musubi/agent/run.py`, `musubi/agent/subagent.py`
- Policy engine: `scripts/policy_engine.py`
- Agent prompts: `.github/agents/**/*.agent.md`
- VS Code extension prompt loaders under `copilot-harness-extension/src`
- Tests under `musubi/tests` and extension TypeScript tests

## Implementation Steps

1. **Fix worker tool catalog flow**
   - Keep `tool_surface=agent` small for the root model.
   - Pass the full local Musubi catalog as the worker source catalog.
   - Keep external MCP tools additive only where already routed.
   - Add tests proving root does not see write tools while `coder` does.

2. **Add an agent prompt resolver**
   - Resolve prompts by purpose:
     - `root/`
     - `workers/`
     - `pipeline-stages/<pipeline>/`
     - `meta/`
   - Keep fallback to the legacy flat `.github/agents/<role>.agent.md` path
     during migration.
   - Update Python and VS Code loaders to call the resolver or mirror its
     precedence.

3. **Introduce direct standalone workers**
   - Add `workers/coder.agent.md` for direct implementation:
     - use write/edit tools directly;
     - do not look for plan/design stages;
     - do not spawn designer for simple tasks;
     - report failure instead of claiming a file was created when no write
       happened.
   - Add optional direct worker prompts for planner, designer, and reviewer:
     - planner: scope and acceptance criteria for large or vague tasks;
     - designer: architecture/API/schema/data-flow decisions;
     - reviewer: post-change verification over actual changed files.

4. **Preserve pipeline-stage prompts**
   - Move or copy existing feature-dev stage prompts into
     `pipeline-stages/feature-dev/`.
   - Move code-review stage prompts into `pipeline-stages/code-review/`.
   - Keep pipeline YAML references working through resolver fallback or update
     them in the same change.

5. **Tighten worker nesting**
   - Direct `coder` is a leaf by default.
   - Enable worker nesting only when the prompt mode and parent orchestration
     explicitly allow it.
   - Keep pipeline-specific nesting behavior where it is already intentional.

6. **Add result-grounding checks**
   - Worker summaries must include written/edited file paths.
   - Root final answers should only claim a file was created or changed when a
     worker summary or audit row confirms that path.
   - If no write happened, the final answer must report failure or incomplete
     work.

7. **Update docs and tests**
   - Update `.github/agents/README.md`, `docs/guide.md`, and `docs/roadmap.md`.
   - Add tests for resolver precedence, worker catalog access, direct coder
     prompt shape, no default direct-coder nesting, and final-answer grounding.
   - Add a manual check for:
     `agent "create html to show hello world"`.

## Success Criteria

- `agent "create html to show hello world"` creates the reported file path.
- Root model-visible tools remain small.
- `coder` receives write/edit tools when policy allows them.
- Direct `coder` no longer looks for `plan.json` or `design.json`.
- Existing pipeline tests continue to pass.
- VS Code prompt loading still finds supported agent prompts.
