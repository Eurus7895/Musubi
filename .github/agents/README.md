# .github/agents/ — Shared Agent Catalog

All agents live here in a flat catalog. Pipelines compose them by
reference from `pipeline.yaml`. An agent is never bound to one pipeline
by file location — if two pipelines want the same reviewer, they both
point at the same file.

## Layout

```
.github/agents/
├── planner.agent.md                       (canonical = feature-dev's)
├── designer.agent.md
├── coder.agent.md
├── reviewer.agent.md
├── pipeline-builder-planner.agent.md      (variant — different prompt)
├── pipeline-builder-designer.agent.md
├── pipeline-builder-coder.agent.md
├── pipeline-builder-reviewer.agent.md
├── skill-builder.agent.md                 (cross-pipeline meta-agent)
└── proposed/                              (skill-builder's patch outputs)
```

- **Bare role names** (`planner.agent.md`) are the canonical / default
  variant. feature-dev uses these directly.
- **`<pipeline>-<role>.agent.md`** is a pipeline-specific variant — same
  role, different prompt. pipeline-builder's coder writes pipeline
  scaffolds, not feature code, so it needs its own file.
- **Other top-level files** are pipeline-agnostic agents (skill-builder,
  plus the planned Week-5 sub agents `explorer`/`investigator`/`reviewer-aux`).

## Composition from pipeline.yaml

```yaml
generator:
  agents:
    - name: planner                          # canonical role name
      agent: agents/planner.agent.md         # path under .github/
```

pipeline-builder pulls its variants:

```yaml
generator:
  agents:
    - name: planner
      agent: agents/pipeline-builder-planner.agent.md
```

To reuse another pipeline's agent, point at its file directly — no
duplication needed.

## Loader behavior

- `copilot-harness/session/state.py::lock_agent_versions` reads every
  `*.agent.md` in `.github/agents/` and locks one version per file. The
  agent name is the filename stem (with `.agent` stripped).
- `copilot-harness-extension/src/pipeline.ts::loadAgentPrompt` resolves
  in this order:
  1. `agents/<pipelineName>-<agentName>.agent.md` — pipeline-specific variant
  2. `agents/<agentName>.agent.md` — canonical / shared agent
  (For `pipelineName == "feature-dev"` the prefixed lookup is skipped
  since feature-dev uses canonical names.)

## Naming

`<role>.agent.md` for canonical / shared agents.
`<pipeline>-<role>.agent.md` for pipeline-specific variants.
The `name:` field in the agent's frontmatter must match the filename stem.
