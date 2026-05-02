# AGENTS.md — CopilotHarness

> Read this file first, every session. It is a map — not a manual.
> Under 120 lines. Always.
> Rules and conventions → `CLAUDE.md`. Architecture and roadmap → `docs/design.md`.

---

## What CopilotHarness Is

Harness layer for GitHub Copilot Chat in VS Code. Three modes:
- **Direct:** simple requests → single LLM call → fast answer, no overhead
- **Pipeline:** repeatable high-stakes workflows → predetermined chain in `pipeline.yaml` → full guardrails (enterprise feature, frozen in current scope)
- **Agent:** structured tasks → planner-led delegation across agent catalog → harness still enforces firewall, validation, skills, retry, audit *(Week 6 — planned, see `docs/design.md`)*

The `@harness` chat participant routes automatically: slash commands go to
their declared action; bare `@harness <prompt>` goes direct.

---

## Where Everything Lives

```
AGENTS.md / CLAUDE.md / README.md / docs/design.md   map / rules / quickstart / design
.github/pipelines/feature-dev/        pipeline.yaml + agents/*.agent.md
.github/commands/                     slash commands (*.md frontmatter)
.github/agents/                       cross-pipeline: skill-builder + sub agent roles
.github/{instructions,skills,memory}/ rules · global skills · 3-tier memory
copilot-harness/                      Python MCP server (zero LLM)
copilot-harness-extension/            VS Code extension (@harness + Tasks TreeView)
hooks.json + scripts/                 SessionStart / PreToolUse / PostToolUse
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
PIPELINE: orient → baseline → generator → evaluator (fresh session)
          → fail ≤ 3 retries → persist (SQLite + plan.md) → never exit silent
```

Renders inline in Copilot Chat (per-stage sections, tag lines, retry
blockquote, plan.md anchor) and in the activity-bar Tasks TreeView
(Active session + clickable History). Details in README.md.

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

`on-eval-fail` and `on-escalate` are reserved hook events — not wired yet.

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

# Pipeline mode (4 agents + correction loop, enterprise / frozen)
@harness /feature-dev add a login endpoint
@harness add a login endpoint --pipeline          # flag form

# Agent mode (planner-led delegation, Week 6 — planned)
@harness /agent <task>

# Single step / status
@harness /planner <task>  /coder  /continue  /status
```

Commands are `.github/commands/*.md` frontmatter (loaded by
`slashCommands.ts`). Add a command by dropping a new `.md` — no
code change.

---

*CopilotHarness | v0.4.0 | 441 tests (Phase A.1 ✅) | May 2026*
*Status, roadmap, and full design → docs/design.md.*
