# Preset catalog

A **preset** is a reusable worker/stage building block — one role plus its
default stage (and optional skill/params). A **pipeline** is an ordered list of
presets dropped into its `stages:` field; the last stage is the evaluator and
sees only the prior stage's output (HI #3).

This declarative format is the contract a drag-and-drop UI would read and write:
each preset is a draggable block, a pipeline is the dropped sequence.

## Authoring a preset

`presets/<id>.yaml`:

```yaml
id: plan            # unique id (defaults to the file stem)
agent: planner      # references .github/agents/planner.agent.md
stage: plan         # default stage name (defaults to the agent name)
skill: null         # optional skill id/path
```

`agent` must be a known role in `.github/agents/`. Presets add no new tool
permissions — a stage worker's tools come from the agent's own caps.

## Composing a pipeline from presets

`<name>/pipeline.yaml`:

```yaml
name: dev-lite
stages:
  - preset: plan
  - preset: build
  - preset: check        # last entry = evaluator (firewalled to prior stage)
```

A stage may override the preset's stage name (`{preset: build, stage: impl}`) or
skip presets entirely (`{agent: reviewer, stage: review}`). The whole catalog is
validated fail-closed at server boot (`composer.validate_catalog_or_raise`): an
unknown preset, unknown agent, or a chain shorter than two stages aborts startup.

An agent summons a pipeline with `musubi_spawn_pipeline`; each stage runs as a
worker, the prior stage's summary feeding the next.
