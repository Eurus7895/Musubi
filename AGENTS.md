# AGENTS.md — Musubi

> Read this file first, every session. It is a map — not a manual.
> Under 120 lines. Always.
> Rules and conventions → `CLAUDE.md`. MCP tools + schema → `musubi/server.py` + `musubi/storage/schema.sql`. Direction, roadmap and status → `docs/roadmap.md`.

---

## What Musubi Is

A **governed-orchestration substrate** for agentic SE work — evaluator
firewall, fail-closed policy engine, append-only audit, skill catalog,
3-tier memory, reversible input compression — exposed as an MCP server that
makes **zero LLM calls** (HI #1). One driver host exposed through CLI and
native operator surfaces:

- **Standalone `agent` CLI (the host):** `musubi/agent/` reaches the model
  through the vendor-agnostic `LMRouter` — anthropic / openai / deepseek /
  azure-on-prem (via curl) / genai_farm (on-prem; SDK by default, curl
  fallback) / ollama, selected by `.musubi/llm.json` profiles. One **worker
  model** (`agent/run.py::run_unit`): no main-vs-sub split — only workers at
  a depth, run **in parallel**, nesting to depth 2; pipeline stages nest
  when their pipeline.yaml declares `spawns:`. Workers offload bounded work
  and return compact summaries so the orchestrator's context stays small.
  First run: `musubi setup`.
- **Console (GUI, operator):** the Tauri desktop app reads `audit.db` directly.
  Only an explicit Orchestrator submission may launch the standalone `agent`
  CLI; Pipeline Studio only creates and edits deterministic recipes. The GUI
  shell and substrate make zero model calls; the launched driver reaches the
  model through `LMRouter`. It exposes orchestrator sessions, policy, audit,
  models, skills, and deterministic pipeline runs.

---

## Current Pipelines

| Pipeline | Command | Status |
|---|---|---|
| feature-dev | `agent "<task>" --pipeline feature-dev` | ✅ planner → designer → coder → reviewer + evaluator firewall |
| code-review | `agent "<diff>" --pipeline code-review` | ✅ scoper → finder → synthesizer (evaluator, reviewer-aux fan-out) |
| dev-lite | `agent "<task>" --pipeline dev-lite` | ✅ plan → build → check, composed from presets — sample user pipeline |

Pipelines are recipes of workers composed from presets
(`.github/pipelines/presets/`), run deterministically via `--pipeline` or
launched from Console Orchestrator Pipeline mode — user-invoked only;
`musubi_spawn_pipeline` stays off the agent tool surface (policy locked
decision #4, `musubi/tool_surface.py`). Stages nest (spawn helper
workers) only when their pipeline.yaml declares `spawns:` for the role.

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
agent "<task>" --profile ollama.local        # local Ollama, no key

# Deterministic staged run (governed pipeline + evaluator firewall)
agent "add a login endpoint" --pipeline feature-dev
agent "$(git diff origin/dev)" --pipeline code-review
```

Setup wizard lives in `musubi/setup_wizard.py`, dispatched from `cli.py`
alongside `serve`. Pipelines are declared in `.github/pipelines/<name>/`
(presets + pipeline.yaml) — add one by dropping files, no code change.

Hard rules (evaluator firewall, sub-agent firewall, fail-closed policy,
append-only stage store, no silent sub-agents) live in `CLAUDE.md`
§ Hard Invariants. Do not duplicate them here.

---
