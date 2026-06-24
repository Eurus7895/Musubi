# AGENTS.md — Musubi

> Read this file first, every session. It is a map — not a manual.
> Under 120 lines. Always.
> Rules and conventions → `CLAUDE.md`. MCP tools + schema → `musubi/server.py` + `musubi/storage/schema.sql`. Direction, roadmap and status → `docs/roadmap.md`.

---

## What Musubi Is

Harness layer for GitHub Copilot Chat in VS Code. The product is
**governed pipelines** — multi-stage workflows with evaluator firewall,
correction loop, and append-only audit. The agent chat mode
exists in the codebase but is **feature-frozen**; new development
goes into pipelines.

- **Pipeline (active development):** repeatable high-stakes workflows →
  predetermined chain in `pipeline.yaml` → full guardrails (evaluator
  firewall, validation, correction loop, append-only stage store, audit).
  Invoked via `/<pipeline-name> <task>`.
- **Standalone agent (active):** the `agent` CLI (`musubi/agent/`) reaches
  the model through the vendor-agnostic `LMRouter`, *not* `vscode.lm` — so the
  caching-cost reason above doesn't apply. This is the roadmap's north star
  (Steps 4–5): model-agnostic vendors (anthropic / openai / azure-on-prem via
  curl / ollama, selected by `.musubi/llm.toml` profiles) plus a sub-agent
  orchestrator (`agent/subagent.py`) that runs spawned roles to completion.

The `@harness` chat participant routes automatically: input that starts with
`/<pipeline-name>` goes to that pipeline; everything else goes to the
(frozen) agent. Zero LLM call decides which mode — pure prefix match.

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

# Agent mode (default — persistent chat, spawns sub-agents on demand)
@harness explain this error
@harness add a login endpoint

```

First-time setup: `musubi setup` (a guided wizard — env doctor, `.musubi/llm.toml`
endpoint profile, optional connection test, `.vscode/mcp.json`). Code in
`musubi/setup_wizard.py`, dispatched from `cli.py` alongside `serve`.

Commands are `.github/commands/*.md` frontmatter (loaded by
`slashCommands.ts`). Add a command by dropping a new `.md` — no code change.

Hard rules (evaluator firewall, sub-agent firewall, fail-closed policy,
zero-LLM-cost routing, append-only stage store, no silent sub-agents) live
in `CLAUDE.md` § Hard Invariants. Do not duplicate them here.

---
