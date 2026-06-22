---
name: orchestrate
description: Run a turn through the agent (spawns sub-agents on demand, no pipeline)
action: agent
---

# /orchestrate

Run one user turn through the agent. The agent holds the
chat conversation across turns, decides whether the turn needs a sub-agent
at all, and spawns the smallest one that can do the work (explorer,
investigator, reviewer-aux, planner, coder, reviewer).

The agent never writes to disk itself and never invokes a
pipeline. If you want a fully-evaluated, multi-stage workflow, type
`/feature-dev` instead.

## Usage

```
@harness /orchestrate <your question or task>
```

Phase D will pivot routing so any non-pipeline message is handled by
the agent automatically; until then this slash is the entry
point.

## See also

- `.github/agents/agent.agent.md` — agent contract + spawn allow-list
- `.github/skills/agent-routing/SKILL.md` — routing rules (pushed)
- `docs/roadmap.md` § Phase B — agent design
