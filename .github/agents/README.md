# .github/agents/ — DEPRECATED for feature-dev agents

The four feature-dev pipeline agents — `planner`, `designer`, `coder`,
`reviewer` — moved to `.github/pipelines/feature-dev/agents/` in
Week 3b (pipeline directory migration).

This directory now holds only:

- **`skill-builder.agent.md`** — meta-agent that proposes patches to
  other agents. Not part of the `feature-dev` pipeline; stays here.
- **`proposed/`** — skill-builder's output directory for proposed
  patches. Validated by `context_builder.validate_skill_builder_write`.

## Loader behavior

Both `copilot-harness/state.py::lock_agent_versions` and
`copilot-harness-extension/src/pipeline.ts::loadAgentPrompt` check
`.github/pipelines/feature-dev/agents/` **first**, then fall back to
this directory. If you restore a removed agent file here, it will
only be picked up when no copy exists in the pipeline dir.

## Removal timeline

Per `CLAUDE.md`, the rollback fallback path is removed in **Week 5**.
By then the pipeline dir is the only source of truth.
