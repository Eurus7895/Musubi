# AGENTS.md — CopilotHarness

> Read this file first, every session. It is a map — not a manual.
> Under 120 lines. Always.
> Rules and conventions → `CLAUDE.md`. Architecture → `docs/design.md`. Build roadmap and status → `docs/roadmap.md`.

---

## What CopilotHarness Is

Harness layer for GitHub Copilot Chat in VS Code. Two modes:
- **Orchestrator (default):** anything that isn't a slash-invoked pipeline →
  one main agent with persistent chat per `chat_id`, replay on every turn,
  reactive compaction, Tier-1 memory auto-injected, spawns sub-agents
  (explorer / investigator / reviewer-aux / summarizer) on demand.
- **Pipeline:** repeatable high-stakes workflows → predetermined chain in
  `pipeline.yaml` → full guardrails (evaluator firewall, validation,
  correction loop, append-only stage store, audit).

The `@harness` chat participant routes automatically: input that starts with
`/<pipeline-name>` goes to that pipeline; everything else goes to the
orchestrator. Zero LLM call decides which mode — pure prefix match.

---

## Where Everything Lives

```
AGENTS.md / CLAUDE.md / README.md / docs/design.md   map / rules / quickstart / design
.github/pipelines/feature-dev/        pipeline.yaml + agents/*.agent.md
.github/commands/                     slash commands (*.md frontmatter)
.github/agents/                       orchestrator, skill-builder, sub-agent roles
.github/{instructions,skills,memory}/ rules · global skills · 3-tier memory
copilot-harness/                      Python MCP server (zero LLM)
copilot-harness-extension/            VS Code extension (@harness + Tasks TreeView)
hooks.json + scripts/                 SessionStart / PreToolUse / PostToolUse
```

---

## Agent Complexity Levels

```
Orchestrator  One main agent + on-demand sub-agents (read-only by default).
              Persistent chat per chat_id, replay, reactive compaction.
              Default for non-pipeline turns.
Level 0       Single-agent pipeline + skill injection + plan JSON. No evaluator.
Level 1       Single agent + separate evaluator + correction loop.
Level 2       Multi-agent + evaluator. Promotion checklist required.
```

---

## Session Protocol

```
ORCHESTRATOR: @harness <text> → harness_append_message → harness_get_conversation
              → vscode.lm.sendRequest with replayed history + Tier-1 memory
              → tool-call loop (spawn / await / list sub-agents)
              → reactive compaction at 80% / 90% / 99% of model context
              → persist assistant + tool turns; never exit silent
PIPELINE:     orient → baseline → generator → evaluator (fresh session)
              → fail ≤ 3 retries → persist (SQLite + plan.md) → never exit silent
```

Renders inline in Copilot Chat (per-stage sections, tag lines, retry
blockquote, plan.md anchor; sub-agent spawn / done markers in orchestrator
mode) and in the activity-bar Tasks TreeView (Active session + clickable
History). Details in README.md.

---

## Current Pipelines

| Pipeline | Command | Level | Status |
|---|---|---|---|
| feature-dev | `/feature-dev` | 2 | ✅ planner → designer → coder → reviewer + evaluator firewall |

Level-1 probe deferred from Week 3a; feature-dev stays at Level 2.
No new pipelines until feature-dev is validated.

---

## Hooks

| Hook | When | What it does |
|---|---|---|
| SessionStart | Before pipeline run | baseline_checks from pipeline.yaml |
| PreToolUse | Before tool call | Policy gate (deterministic, fail-closed) |
| PostToolUse | After tool call | SQLite audit log (storage/audit.db) |

---

## Key Interactions

```
# Pipeline mode (governed pipeline + evaluator firewall)
@harness /feature-dev add a login endpoint

# Orchestrator mode (default — persistent chat, spawns sub-agents on demand)
@harness explain this error
@harness add a login endpoint

# Single step / status
@harness /planner <task>  /coder  /continue  /status
```

Commands are `.github/commands/*.md` frontmatter (loaded by
`slashCommands.ts`). Add a command by dropping a new `.md` — no code change.

Hard rules (evaluator firewall, sub-agent firewall, fail-closed policy,
zero-LLM-cost routing, append-only stage store, no silent sub-agents) live
in `CLAUDE.md` § Hard Invariants. Do not duplicate them here.

---

*CopilotHarness | 586 Py + 112 TS tests | Phases A–E shipped | May 2026*
*Full design → docs/design.md. Status and roadmap → docs/roadmap.md.*
