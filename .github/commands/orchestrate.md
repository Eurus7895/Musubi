---
name: orchestrate
description: Run a turn through the orchestrator (spawns sub-agents on demand, no pipeline)
action: orchestrator
---

# /orchestrate

Run one user turn through the orchestrator. The orchestrator holds the
chat conversation across turns, decides whether the turn needs a sub-agent
at all, and spawns the smallest one that can do the work (explorer,
investigator, reviewer-aux, planner, coder, reviewer).

The orchestrator never writes to disk itself and never invokes a
pipeline. If you want a fully-evaluated, multi-stage workflow, type
`/feature-dev` instead.

## Usage

```
@harness /orchestrate <your question or task>
```

Phase D will pivot routing so any non-pipeline message is handled by
the orchestrator automatically; until then this slash is the entry
point.

## See also

- `.github/agents/orchestrator.agent.md` — agent contract + spawn allow-list
- `.github/skills/orchestrator-routing/SKILL.md` — routing rules (pushed)
- `docs/roadmap.md` § Phase B — orchestrator design
