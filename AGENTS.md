# AGENTS.md — Musubi

> Read this file first, every session. It is a map — not a manual.
> Under 120 lines. Always.
> Rules and conventions → `CLAUDE.md`. MCP tools + schema → `musubi/server.py` + `musubi/storage/schema.sql`. Direction, roadmap and status → `docs/roadmap.md`.

---

## What Musubi Is

A **governed-orchestration substrate** for agentic SE work — evaluator
firewall, fail-closed policy engine, append-only audit, skill catalog,
3-tier memory, reversible input compression — exposed as an MCP server that
makes **zero LLM calls** (HI #1). Two supported surfaces drive it:

- **Standalone `agent` CLI (active — the north star):** `musubi/agent/`
  reaches the model through the vendor-agnostic `LMRouter` — anthropic /
  openai / deepseek / azure-on-prem (via curl) / genai_farm (on-prem; SDK by
  default, curl fallback) / ollama, selected by `.musubi/llm.json` profiles.
  One **worker model** (`agent/run.py::run_unit`): no main-vs-sub split —
  only workers at a depth, run **in parallel**, nesting to depth 2, able to
  **summon pipelines** (incl. user-defined preset pipelines). Workers offload
  bounded work and return compact summaries so the orchestrator's context
  stays small. Model-agnostic, no `vscode.lm` quota. First run: `musubi setup`.
- **VS Code pipeline (active):** `@harness /feature-dev <task>` runs the
  4-stage governed pipeline (planner → designer → coder → reviewer +
  evaluator firewall, correction loop, append-only stage store) on Copilot's
  model. The bare `@harness` chat agent (embedded, via `vscode.lm`) is
  **feature-frozen** — 3-5× cost because provider prompt caching isn't
  reachable through `vscode.lm.sendRequest`; use plain Copilot Chat for
  casual chat. The freeze is scoped to this embedded host only (the
  standalone CLI is a different inject point).

The `@harness` participant routes by pure prefix match (zero LLM call):
`/<pipeline-name>` → that pipeline; everything else → the (frozen) agent.

---

## Current Pipelines

| Pipeline | Command | Level | Status |
|---|---|---|---|
| feature-dev | `/feature-dev` | 2 | ✅ planner → designer → coder → reviewer + evaluator firewall (VS Code) |
| dev-lite | (worker host) | — | ✅ plan → build → check, composed from presets — sample user pipeline |

Level-1 probe deferred from Week 3a; feature-dev stays at Level 2. In the
standalone worker host, pipelines are recipes of workers composed from presets
(`.github/pipelines/presets/`) and summoned via `musubi_spawn_pipeline`.

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
# First-time setup (env doctor, .musubi/llm.json, .vscode/mcp.json)
musubi setup

# Standalone CLI (active — any vendor; spawns sub-agents on demand)
agent "add a login endpoint and a test"
agent "<task>" --profile azure.work          # on-prem endpoint
agent "<task>" --profile deepseek.cloud      # DeepSeek API
agent "<task>" --vendor ollama --model llama3.1

# VS Code pipeline (governed pipeline + evaluator firewall)
@harness /feature-dev add a login endpoint

# Embedded @harness chat agent (frozen — casual chat only)
@harness explain this error
```

Setup wizard lives in `musubi/setup_wizard.py`, dispatched from `cli.py`
alongside `serve`. Pipeline commands are `.github/commands/*.md` frontmatter
(loaded by `slashCommands.ts`) — add one by dropping a new `.md`, no code change.

Hard rules (evaluator firewall, sub-agent firewall, fail-closed policy,
append-only stage store, no silent sub-agents) live in `CLAUDE.md`
§ Hard Invariants. Do not duplicate them here.

---
