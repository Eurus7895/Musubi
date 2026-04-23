# .github/agents/ — Cross-Pipeline Agents

This directory holds agents that are **not scoped to a single pipeline**.
Pipeline-specific stage agents live under `.github/pipelines/<name>/agents/`
(e.g. `planner`, `designer`, `coder`, `reviewer` for `feature-dev`, moved
there in Week 3b).

## Who lives here

- **`skill-builder.agent.md`** — meta-agent that proposes patches to
  other agents. Not part of any pipeline; spawned on its own.
- **`proposed/`** — skill-builder's output directory for proposed
  patches. Validated by `context_builder.validate_skill_builder_write`.
- **Sub agent roles** (planned, Week 5) — `explorer`, `investigator`,
  `reviewer-aux`. Same `.agent.md` format, invoked by a *main* agent
  mid-task to offload evidence gathering. See `CLAUDE.md § Week 5`.

A "sub agent" here means the *invocation contract*, not the agent file:
the same `.agent.md` can be spawned by another agent (firewalled context,
returns summary only) or — in principle — run as a stand-alone main. The
three Week-5 role files are authored specifically for the sub-agent
invocation contract.

## Loader behavior

Both `copilot-harness/state.py::lock_agent_versions` and
`copilot-harness-extension/src/pipeline.ts::loadAgentPrompt` check the
pipeline-scoped directory first (`.github/pipelines/<name>/agents/`) and
fall back to this directory. Cross-pipeline agents are only found here.

## Naming

`<role>.agent.md`. Role is the invocation name (`skill-builder`,
`explorer`, etc.). Policy lookups and slash-command routing use the
role name as-is.
