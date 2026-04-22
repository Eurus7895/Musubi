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
AGENTS.md                        ← this file — read first, every session
CLAUDE.md                        ← full design doc — architecture decisions

.github/
    pipelines/                   ← self-contained pipeline directories
        feature-dev/
            pipeline.yaml        ← level, correction config
            agents/              ← generator.md + evaluator.md
            skills/              ← SKILL.md
            schemas/             ← evaluator grading criteria
            README.md
    commands/                    ← slash commands (/feature-dev, /code-review)
    instructions/                ← rules (priority-ranked: universal > org > domain > project)
    skills/                      ← global skills (shared across pipelines)
    memory/                      ← 3-tier memory (MEMORY.md + Tier 2 files)

copilot-harness/                 ← Python MCP server (zero LLM)
copilot-harness-extension/       ← VS Code extension (@harness chat participant)

hooks.json                       ← hook wiring (SessionStart, PreToolUse, PostToolUse)
scripts/                         ← hook implementations (Python)
```

---

## Agent Complexity Levels

```
Direct   Single LLM call. No pipeline. No harness. Fast.
Level 0  Single agent pipeline. Skill injection, plan JSON. No evaluator.
Level 1  Single agent + separate evaluator (fresh session). Correction loop.
Level 2  Multi-agent + evaluator. Not in v1. Promotion checklist required.
```

---

## Session Protocol

```
DIRECT MODE:
  @harness "explain this error" → LLM → stream response → done

PIPELINE MODE:
  1. ORIENT       harness_get_active_session() — resume or start fresh
  2. BASELINE     Pipeline baseline_checks[] — files accessible? MCP alive?
  3. RUN          Generator → output
                  Evaluator (fresh session) → verdict
                  Fail → correction loop (max 3) → escalate
  4. STATE        Plan JSON + progress.md + SQLite
  5. EXIT         Confirm output exists. Never exit silently.
```

---

## Current Pipelines

| Pipeline | Command | Level | Status |
|---|---|---|---|
| feature-dev | `/feature-dev` | 1 | ✅ Built (Week 3a: evaluator separation) |

More pipelines added only after feature-dev is validated.

---

## Hooks

| Hook | When | What it does |
|---|---|---|
| SessionStart | Before pipeline run | Orient + baseline checks |
| PreToolUse | Before every tool call | Policy enforcement (deterministic) |
| PostToolUse | After every tool call | SQLite audit logging |
| on-eval-fail | Evaluator rejects | Log failure + fix_instructions |
| on-escalate | Max retries exceeded | Escalate with full context |

---

## Rules That Cannot Be Broken

```
✅ Evaluator always in separate session — never shares context with generator
✅ Baseline checks before every pipeline run — never silently skip
✅ Bad output → fix skill file first, before promoting pipeline level
✅ Level 2 requires promotion checklist (3+ observed failures documented)
✅ PreToolUse hook enforces policy — never removed regardless of model capability
✅ Each pipeline is a self-contained directory
❌ Do not add pipelines until feature-dev is validated with real usage
❌ Do not promote to Level 2 without 3+ observed failures
```

---

## Key Interactions

```
# Direct mode (fast)
@harness explain this error
@harness how do I run the migrations?

# Pipeline mode (governed)
/feature-dev add a login endpoint
/code-review PR-421

# Session management
harness_get_status(session_id)
harness_get_active_session()
```

---

*CopilotHarness | April 2026 | v0.2.0 | 260 tests*
*Current: Week 2 complete*
*Next: Week 3a — separate evaluator session*
