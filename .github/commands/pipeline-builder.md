---
name: pipeline-builder
description: Scaffold a NEW CopilotHarness pipeline from a brief — plan → design → code → review
action: pipeline
pipeline: pipeline-builder
---

# /pipeline-builder

Runs the `pipeline-builder` pipeline: a 4-agent sequence (planner, designer,
coder, reviewer) that authors the directory layout for a NEW pipeline under
`.github/pipelines/<new-name>/` plus its slash command at
`.github/commands/<new-name>.md`.

## Usage

```
@harness /pipeline-builder <brief>
```

The brief should at minimum include the new pipeline's purpose. Optional but
recommended: target level (0 / 1 / 2), the stages and what each does, any
existing skills the pipeline should reference.

Examples:

```
@harness /pipeline-builder build a /code-review pipeline that runs static analysis on changed files
```

```
@harness /pipeline-builder Level 1, single agent that drafts release notes from git log between two refs
```

## Output

On a successful run, the new pipeline directory is written to
`.github/pipelines/<name>/` with `pipeline.yaml`, `README.md`, four agent
files under `agents/`, and `.claude-plugin/plugin.json`. The slash command
file is written to `.github/commands/<name>.md`.

The branch is the audit trail — review the diff, then merge or discard.

## See also

- `.github/pipelines/pipeline-builder/pipeline.yaml` — pipeline definition
- `.github/pipelines/pipeline-builder/README.md` — stage + checklist overview
- `/CLAUDE.md` — full design doc
