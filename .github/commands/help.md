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
  guardrails, evaluator firewall, and a **review gate after every stage**
  (Phase G.1.5) so the user can approve, retry-with-hint, or abort before
  the next stage runs.
- `@harness <prompt>` — **orchestrator**. Persistent conversation, spawns
  sub-agents on demand. The default for anything that isn't a slash command.
- Legacy bare keywords (`continue`, `status`, `full`, `planner`, `designer`,
  `coder`, `reviewer`) still work for muscle memory but are deprecated in
  favour of the slash form.

## Review gate (Phase G.1.5)

`/feature-dev` and successors pause between stages and render four buttons:
**✓ Approve & continue · ↻ Retry this stage · ✕ Abort · ⚡ Run remaining
without review**. Per-pipeline auto-approve persists via the
`copilotHarness.autoApprove.<pipeline>` setting (toggle from the chat
button or directly in VS Code settings).

## Usage

```
@harness /help
```

## See also

- `.github/commands/*.md` — every slash command is defined here
- `/CLAUDE.md` — full design doc
- `/docs/roadmap.md` § Phase G — sub-agent runners + review gate
