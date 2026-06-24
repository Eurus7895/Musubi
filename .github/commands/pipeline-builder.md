---
name: pipeline-builder
description: Scaffold a NEW Musubi pipeline from a brief — single composite agent
action: one-shot
agent: pipeline-builder
---

# /pipeline-builder

One-shot composite agent that scaffolds a new Musubi pipeline from
a brief. **No 4-stage pipeline ceremony** — a single LLM call produces
`pipeline.yaml`, the README, and the slash command file. Aligns with
the project rule "do not invent agents speculatively"; pipeline authoring
is bounded enough that one careful agent does the job.

## Usage

```
@harness /pipeline-builder <brief>
```

The brief should at minimum include the new pipeline's purpose. Optional
but useful: target level (1 / 2), what each stage does, any existing
skills the pipeline should reference.

Examples:

```
@harness /pipeline-builder build a /code-review pipeline that runs static analysis on changed files
```

```
@harness /pipeline-builder Level 1, drafts release notes from git log between two refs
```

## Output

On a successful run the agent writes:

- `.github/pipelines/<name>/pipeline.yaml`
- `.github/pipelines/<name>/README.md`
- `.github/commands/<name>.md`

Variant agent files (`.github/agents/<name>-<role>.agent.md`) are written
ONLY if the agent decides the pipeline genuinely needs to override a
canonical agent — most pipelines reuse `agents/planner.agent.md` etc.
directly.

The branch is the audit trail — review the diff, then commit or discard.

## See also

- `.github/agents/pipeline-builder.agent.md` — agent prompt
- `/CLAUDE.md` — design rules and hard constraints
