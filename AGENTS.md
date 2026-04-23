# AGENTS.md — CopilotHarness

> Read this file first, every session. It is a map — not a manual.
> Under 120 lines. Always.
> For architecture decisions, read CLAUDE.md instead.

---

## What CopilotHarness Is

Harness layer for GitHub Copilot Chat in VS Code. Two modes:
- **Direct:** simple requests → single LLM call → fast answer, no overhead
- **Pipeline:** complex workflows → governed agents → validated, auditable output

The `@harness` chat participant routes automatically. Slash commands always
go to pipeline. Everything else goes direct unless overridden.

---

## Where Everything Lives

```
AGENTS.md / CLAUDE.md / README.md     ← session map / design doc / quickstart

.github/
    pipelines/feature-dev/            ← pipeline.yaml + agents/{planner,designer,coder,reviewer}.agent.md
    commands/                         ← slash commands (*.md, frontmatter-driven)
    agents/                           ← cross-pipeline home: skill-builder + Week 5 sub agent roles
    instructions/                     ← rules (universal > org > domain > project)
    skills/                           ← global skills, shared across pipelines
    memory/                           ← 3-tier memory (MEMORY.md + Tier 2)

copilot-harness/                      ← Python MCP server (zero LLM)
copilot-harness-extension/            ← VS Code extension (@harness chat participant)
    src/dashboard.ts                  ← HarnessDashboard webview owner
    media/dashboard/                  ← webview assets (HTML/CSS/JS from mockup)

hooks.json                            ← SessionStart / PreToolUse / PostToolUse wiring
scripts/                              ← hook impls: policy_engine, pre/post_tool_use, session_start
```

---

## Agent Complexity Levels

```
Direct   Single LLM call. No pipeline. No harness.
Level 0  Single agent + skill injection + plan JSON. No evaluator.
Level 1  Single agent + separate evaluator + correction loop.
Level 2  Multi-agent + evaluator. Promotion checklist required.
```

---

## Session Protocol

```
DIRECT:   @harness <text> → vscode.lm → stream → done
PIPELINE: orient (resume/new) → baseline → generator → evaluator (fresh session)
          → fail ≤ 3 retries → persist (SQLite + plan.md) → never exit silent
          Chat streams a one-line marker + "Show Harness Dashboard" button;
          the Dashboard webview renders the live pipeline card.
```

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

`on-eval-fail` and `on-escalate` are reserved for Week 4+ — not wired yet.

---

## Rules That Cannot Be Broken

```
✅ Evaluator runs in a separate session — reviewer sees code only (Week 3a firewall)
✅ Baseline checks before every pipeline run — never silently skip
✅ Bad output → fix skill file first, before promoting pipeline level
✅ Level 2 needs promotion checklist; feature-dev grandfathered pending Week 4 probe
✅ PreToolUse policy (scripts/policy_engine.py) fail-closed, never removed
✅ Each pipeline is self-contained under .github/pipelines/<name>/
❌ Do not add pipelines until feature-dev is validated with real usage
❌ Do not add routing paths without preserving zero-LLM-cost routing
```

---

## Key Interactions

```
# Direct mode (single vscode.lm call, no harness)
@harness explain this error

# Pipeline mode (4 agents + correction loop)
@harness /feature-dev add a login endpoint
@harness add a login endpoint --pipeline          # flag form

# Single step / status
@harness /planner <task>  /coder  /continue  /status
```

Commands are `.github/commands/*.md` frontmatter (loaded by
`slashCommands.ts`). Add a command by dropping a new `.md` — no
code change.

---

*CopilotHarness | April 2026 | v0.3.0 | 379 tests (Python harness)*
*Current: Week 4 complete + Harness Dashboard webview — /help, plugin manifest,*
*direct-mode pull-skills, Tier 2 compaction, cross-session memory, Level-1 probe*
*infrastructure, live pipeline card rendered in a VS Code webview panel.*
*Next: Run the Level-1 probe (5 requests through both pipelines) → decide handoff schemas.*
*Planned (main feature): Week 5 (5-day plan) — Day 1–3 sub agent core primitives*
*(MCP, firewall, role files, spawn-event surface — Dashboard already has the event*
*surface to render spawns) · Day 4 pipeline-main spawning · Day 5 direct-mode spawning.*
*Full day-by-day plan in CLAUDE.md § Build Roadmap.*
