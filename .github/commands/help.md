---
name: help
description: List every available slash command with its description and action
action: help
---

# /help

Lists every slash command that lives under `.github/commands/`. The table is
built at runtime from the on-disk frontmatter, so new command files show up
automatically — nothing to rebuild, nothing to re-register.

The listing also reminds users of the non-slash entry points:

- `@harness <question>` — **direct mode**, a single Copilot call with no pipeline.
- `@harness <task> --pipeline` — force pipeline mode on free-form input.
- Legacy bare keywords (`continue`, `status`, `full`, `planner`, `designer`,
  `coder`, `reviewer`) still work but are deprecated in favour of the
  slash form.

## Usage

```
@harness /help
```

## See also

- `.github/commands/*.md` — every slash command is defined here
- `/CLAUDE.md` — full design doc
