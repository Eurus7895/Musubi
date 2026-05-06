---
name: help
description: List every available slash command with its description and action
action: help
---

# /help

Lists every slash command that lives under `.github/commands/`. The table is
built at runtime from the on-disk frontmatter, so new command files show up
automatically — nothing to rebuild, nothing to re-register.

The listing also reminds users of the two routing modes:

- `/<pipeline-name> <task>` — run a pipeline (e.g. `/feature-dev`). Full
  guardrails, evaluator firewall.
- `@harness <prompt>` — **orchestrator**. Persistent conversation, spawns
  sub-agents on demand. The default for anything that isn't a slash command.
- Legacy bare keywords (`continue`, `status`, `full`, `planner`, `designer`,
  `coder`, `reviewer`) still work for muscle memory but are deprecated in
  favour of the slash form.

## Usage

```
@harness /help
```

## See also

- `.github/commands/*.md` — every slash command is defined here
- `/CLAUDE.md` — full design doc
