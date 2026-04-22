# CLAUDE.md — CopilotHarness
### Full Design Document

> This is the design doc. Read it when making architecture decisions.
> For session-start orientation, read AGENTS.md instead.

---

## One Sentence

CopilotHarness is a Python MCP server that acts as the harness layer for GitHub
Copilot Chat — it controls what each agent sees, validates what each agent
produces, enforces the correction loop, injects skills, and runs code verification.

**Copilot Chat reasons. CopilotHarness controls the environment it reasons about.**

Simple requests get a direct response. Complex workflows route to governed
pipelines with validation, correction loops, and audit trails.

---

## Harness Engineering Principle

> "The model is what thinks. The harness is what it thinks about.
>  And the harness is what determines the final outcome."
>
> Same model, same task, same compute →
> just changing environment design → 64% performance improvement (Princeton SWE-agent paper)

**Copilot Chat is the LLM. CopilotHarness is the harness. Zero LLM calls inside the harness.**

```
What Copilot Chat does:    reasoning, planning, coding, reviewing
What CopilotHarness does:  routing, state, context firewall, skill injection,
                           validation, execution, correction loop, policy enforcement
```

---

## Why MCP, Not CLI Bridge

The harness must be the environment Copilot **operates within** — not a helper
tool the developer manually bridges. With MCP stdio:

- Copilot agents call `harness_read_stage` → harness enforces firewall, injects skills
- Copilot agents call `harness_write_stage` → harness validates before storing
- Agents cannot skip the harness — it is the only path to read inputs and write outputs

The MCP stdio server runs **entirely locally** as a subprocess. No network calls.
No `api.githubcopilot.com`. The corporate firewall is irrelevant.

**Phase 1 (dev mode):** VS Code reads `.vscode/mcp.json` → spawns `python server.py`
→ Copilot Chat agents call harness_* tools manually.

**Phase 2 (extension mode, current):** The VS Code extension spawns the bundled
server binary directly via `McpClient` (JSON-RPC over stdio). No MCP panel, no user
action needed. The extension drives agents automatically via `vscode.lm.sendRequest`.

---

## How It Works

```
User types "@harness add a login endpoint" in Copilot Chat
        ↓
Intent Router (extension.ts)
  Slash command (/feature-dev) → pipeline mode (deterministic, no LLM call)
  Everything else → direct mode (no LLM routing call needed)
  User override: --pipeline forces pipeline for non-slash input
        ↓
  ┌─────────────────────┬────────────────────────────────┐
  │  DIRECT MODE        │  PIPELINE MODE                 │
  │                     │                                │
  │  Single LLM call    │  Governed pipeline             │
  │  No schema, no      │  Agents + evaluator +          │
  │  evaluator, no      │  validation + correction       │
  │  plan JSON, no hooks│  + plan JSON + audit           │
  │                     │                                │
  │  "@harness explain  │  /feature-dev add OAuth        │
  │   this error"       │  /code-review PR-421           │
  │  "@harness how do   │  /refactor extract service     │
  │   I run migrations?"│                                │
  └─────────────────────┴────────────────────────────────┘
        ↓
DIRECT MODE:
  vscode.lm.sendRequest(copilot, prompt)
  Stream response to chat. Done.
  No MCP, no harness, no plan JSON.

PIPELINE MODE:
  extension.ts spawns bin/copilot-harness.exe via McpClient
        ↓
    McpClient.callTool("harness_get_active_session")
        → crash recovery: resume interrupted session or start fresh
    ↓
    McpClient.callTool("harness_new_session", { request })
        → harness creates session, locks agent versions, returns session_id
    ↓
    McpClient.callTool("harness_read_stage", { session_id, stage, agent_name })
        → context firewall enforced, skills auto-injected, memory injected
    ↓
    vscode.lm.sendRequest(copilot, agentPrompt + context)
        → Copilot reasons, returns JSON output
    ↓
    McpClient.callTool("harness_write_stage", { session_id, stage, output })
        → injection scan → schema check → append-only store
    ↓
    Evaluator (separate vscode.lm.sendRequest, fresh context) → verdict
    ↓
    Fail → correction loop (max 3 retries) → escalate
```

**Routing rule (zero cost — no LLM call):**
- Input starts with `/` → pipeline (slash command)
- Input has `--pipeline` flag → pipeline
- Everything else → direct

---

## Agent Complexity Levels

```
Direct     Single LLM call. No pipeline, no schema, no evaluator.
           Just: prompt → Copilot → stream response → done.
           When: simple questions, explanations, quick lookups.
           Speed: fastest possible — no harness overhead.

Level 0    Single agent pipeline. Baseline checks, skill injection, plan JSON.
           No evaluator. Team feedback is the evaluator.
           When: task is well-defined, output schema is simple.
           YAML: generator.agent (singular)

Level 1    Single agent + separate evaluator (fresh session).
           Full governance. Correction loop (max 3).
           When: wrong output has real cost, quality must be structural.
           YAML: generator.agent (singular) + evaluator.agent

Level 2    Multi-agent pipeline + separate evaluator.
           Multiple specialized agents in sequence.
           Evaluator in separate session.
           When: ONLY when single agent demonstrably fails at a subtask.
           YAML: generator.agents (plural list)
           Gate: promotion checklist required. Not in current version.
```

**Promotion rule:** A pipeline starts at the lowest viable level. Promotion
requires 3+ observed failures that cannot be fixed by updating the skill file.

**The YAML format enforces the level:**
```yaml
# Level 0 or Level 1 — singular agent
generator:
  agent: agents/generator.md       # one agent file

# Level 2 only — plural agents list
generator:
  agents:                          # multiple agent files
    - name: planner
      agent: agents/planner.md
    - name: coder
      agent: agents/coder.md
```

If the pipeline.yaml uses `agents:` (plural), the level MUST be 2. The
pipeline runner validates this. You cannot have Level 1 with multiple agents.

---

## Current State vs Future State

### Current State (Week 2 complete, v0.2.0)

```
WHAT EXISTS NOW:
  ✅ 260 tests passing
  ✅ Harness core: state, context_builder, verifier, executor,
       correction_loop, skill_loader
  ✅ 3-tier memory: MEMORY.md, memory_loader, session_distiller
  ✅ Pattern detector + proposed patch applier
  ✅ VS Code extension v0.2.0 with McpClient
  ✅ PyInstaller binary distribution

WHAT THE FEATURE-DEV PIPELINE LOOKS LIKE NOW:
  5 agents: planner → designer → coder → reviewer → skill-builder
  All run in same session via pipeline.ts
  Reviewer shares context with generator agents
  Agent files live in .github/agents/*.agent.md (flat)

THIS IS LEVEL 2 ARCHITECTURE.
  We built multi-agent before proving single-agent fails.
  Week 3a will test whether a single generator can replace
  planner+designer+coder. If it can → collapse to Level 1.
  If it demonstrably cannot → keep Level 2, document why.
```

### Future State (after Week 3)

```
WHAT WILL CHANGE:
  Direct mode in extension.ts (zero-cost routing)
  Pipeline directories: .github/pipelines/feature-dev/
  hooks.json wiring for PreToolUse policy enforcement
  Separate evaluator session (reviewer in fresh context)
  Agent frontmatter (YAML --- block + markdown body)
  Slash commands: .github/commands/feature-dev.md

WHAT WILL NOT CHANGE:
  All harness core modules (zero code changes)
  3-tier memory architecture
  McpClient + PyInstaller binary
  260 tests (only additions, no removals)
```

---

## File Structure — Current (v0.2.0)

```
.github/
    AGENTS.md
    copilot-instructions.md
    instructions/                ← priority-ranked rules (P1-P4)
    agents/                      ← current agent definitions (flat)
        planner.agent.md
        designer.agent.md
        coder.agent.md
        reviewer.agent.md
        skill-builder.agent.md
        proposed/                ← Skill-Builder writes here
    skills/                      ← domain skills
        code-review/    SKILL.md + assets/ + references/
        api-design/     SKILL.md + assets/ + references/
        python/         SKILL.md + assets/ + references/
        testing/        SKILL.md + assets/ + references/
    memory/                      ← 3-tier memory
        MEMORY.md                ← Tier 1 index
        architecture.md          ← Tier 2 decisions
        failure-patterns.md      ← Tier 2 recurring failures

copilot-harness/                 ← Python MCP server
    server.py                    ← FastMCP stdio — all harness_* tools
    cli.py                       ← entry point: copilot-harness serve
    state.py                     ← session state (SQLite)          ✅
    context_builder.py           ← context firewall + injection     ✅
    verifier.py                  ← schema + secrets validation      ✅
    executor.py                  ← lint + typecheck + test runner   ✅
    correction_loop.py           ← evaluator → generator retry     ✅
    skill_loader.py              ← SKILL.md + references            ✅
    memory/
        pattern_detector.py                                         ✅
        memory_loader.py                                            ✅
        session_distiller.py                                        ✅
    storage/
        db.py                    ← SQLite WAL, schema embedded      ✅

copilot-harness-extension/       ← VS Code extension (TypeScript)
    src/
        extension.ts             ← @harness chat participant        ✅
        mcpClient.ts             ← JSON-RPC stdio client            ✅
        pipeline.ts              ← agent driver + correction loop   ✅
    bin/
        copilot-harness.exe      ← PyInstaller binary               ✅

tests/                           ← 260 tests passing               ✅
```

## File Structure — After Week 3 Migration

```
.github/
    AGENTS.md                    ← session-start map (~100 lines)
    copilot-instructions.md
    instructions/                ← same (no changes)

    pipelines/                   ← NEW: self-contained pipeline directories
        feature-dev/
            pipeline.yaml        ← level, correction config
            agents/
                generator.md     ← YAML frontmatter + system prompt
                evaluator.md     ← skeptical QA (separate session)
            skills/
                SKILL.md
            schemas/
                output.json
            README.md

    commands/                    ← NEW: slash commands
        feature-dev.md           ← /feature-dev — bypasses router

    skills/                      ← global skills (unchanged)
    memory/                      ← 3-tier memory (unchanged)
    agents/                      ← DEPRECATED — migrated to pipelines/

copilot-harness/                 ← UNCHANGED (zero code changes)
copilot-harness-extension/       ← MODIFIED:
    src/
        extension.ts             ← adds direct mode routing
        pipeline.ts              ← adds separate evaluator session

hooks.json                       ← NEW: hook wiring config
scripts/                         ← NEW: hook implementations
    pre_tool_use.py
    post_tool_use.py
    session_start.py
```

---

## Key Distinction: instructions vs skills

```
instructions/   = RULES AND STANDARDS (always loaded, priority-ranked)
                  → P1 universal > P2 org > P3 domain > P4 project
                  → P1 can never be overridden

skills/         = PROCEDURES AND KNOWLEDGE (injected by harness or loaded on demand)
                  → auto-injected: harness_read_stage pushes skills per pipeline config
                  → on demand: agents call harness_get_skill / harness_get_reference
                  → assets/ run by executor.py only, never by agent directly
```

---

## Skill Injection — Skills Are Pushed, Not Pulled

Copilot can decide "I don't need that skill." The harness prevents this by
injecting skill content directly into `harness_read_stage` responses.

Pipeline YAML defines which skill to inject per agent. Agent cannot opt out.
Skill content is part of the tool response.
Agent loads additional references on demand via `harness_get_reference()`.

---

## MCP Tools

### State Tools
```
harness_get_active_session()
    → returns { session_id, request, resume_stage, attempt } | { session_id: null }

harness_new_session(request)
    → create_session() + lock_agent_versions() + set_active_session()

harness_read_stage(session_id, stage, agent_name)
    → context firewall + skill injection + memory injection

harness_write_stage(session_id, stage, output, agent_name)
    → injection scan + schema check + append-only store

harness_get_status(session_id)
harness_increment_attempt(session_id, stage)
```

### Skill Tools
```
harness_get_skill(skill_id)
harness_get_reference(skill_id, reference_name)
```

### Execution Tools ✅ built
```
harness_run_lint(files)       → LintResult (ruff)
harness_run_typecheck(files)  → TypeCheckResult (mypy)
harness_run_tests(test_dir)   → RunResult (pytest)
```

### Memory Tools ✅ built
```
harness_get_memory_entry(name) → Tier 2 content on demand
harness_distill_session(session_id) → appends to failure-patterns.md
```

---

## Hooks

Follows Claude Code `hooks.json` format. Deterministic code at lifecycle points.

**Key rule:** "Never send an LLM to do a linter's job."

| Hook | When | v1 behavior |
|---|---|---|
| `SessionStart` | Before pipeline run | Orient: read progress, baseline checks |
| `PreToolUse` | Before every tool call | Policy enforcement per agent |
| `PostToolUse` | After every tool call | SQLite audit logging |
| `on-eval-fail` | Evaluator rejects | Log + send fix_instructions |
| `on-escalate` | Max retries exceeded | Escalate with full context |

---

## Context Firewall (context_builder.py)

```python
build_context(session_id, agent_name) → dict
read_stage_for_agent(session_id, stage, agent_name) → dict | None
    # Evaluator → sees output + schema ONLY. No generator instructions, no memory.
    # Coder retry → fix_instructions only, not full review JSON.
```

---

## Memory Architecture ✅ Built

```
Tier 1 — MEMORY.md (~200 tokens, always loaded by harness_read_stage)
Tier 2 — .github/memory/*.md (loaded on demand)
Tier 3 — storage/sessions/ (raw history, never auto-loaded)
```

memory_loader.py ✅ | session_distiller.py ✅ | harness_get_memory_entry ✅

---

## Pipeline YAML Format

```yaml
# Level 1 — single generator + separate evaluator
name: feature-dev
description: Guided feature development with review
version: 1.0.0
level: 1                         # 0, 1, or 2

baseline_checks:
  - type: file_read
    path: src/
    error: "Cannot read src/"

generator:
  agent: agents/generator.md     # SINGULAR — one agent (Level 0/1)
  skill: skills/SKILL.md
  output_schema: schemas/output.json

evaluator:                        # null for Level 0
  agent: agents/evaluator.md
  schema: schemas/review-criteria.json

correction:
  max_retries: 3
  escalate_message: "Feature requires human review"
```

```yaml
# Level 2 ONLY — multiple generators (requires promotion checklist)
name: feature-dev-v2
level: 2

generator:
  agents:                         # PLURAL — multiple agents (Level 2 only)
    - name: planner
      agent: agents/planner.md
      skill: null
    - name: coder
      agent: agents/coder.md
      skill: skills/python/SKILL.md

evaluator:
  agent: agents/evaluator.md
```

**The runner validates:** `generator.agents` (plural) requires `level: 2`.
Level 0/1 with `agents:` list is rejected at load time.

---

## Policy Engine

```python
PIPELINE_POLICIES = {
    "feature-dev": {
        "generator": ["Read", "View", "Grep", "Glob", "Write", "Edit", "Bash"],
        "evaluator": ["Read", "View"],
    },
}
```

Enforced via `PreToolUse` hook. Never removed regardless of model capability.

---

## LLM Usage — Zero Inside Harness

```
Component              LLM?   Status
──────────────────────────────────────
server.py              ❌     ✅
state.py               ❌     ✅
context_builder.py     ❌     ✅
verifier.py            ❌     ✅
correction_loop.py     ❌     ✅
skill_loader.py        ❌     ✅
executor.py            ❌     ✅
pattern_detector.py    ❌     ✅
patch_applier.py       ❌     ✅
memory_loader.py       ❌     ✅
session_distiller.py   ❌     ✅
mcpClient.ts           ❌     ✅
pipeline.ts            ❌     ✅
extension.ts           ❌     ✅

Copilot Chat / vscode.lm  ✅   agent reasoning only
```

---

## Harness Audit Summary

| Component | Status | Next action |
|---|---|---|
| Tool Design | ⚠️ Prompt-level | Week 3c: hooks.json PreToolUse |
| Feedback Loops | ✅ Built | — |
| State Management | ✅ Built + crash recovery | — |
| Multi-Agent Coordination | ❌ Missing | Week 4: handoff schemas |
| Security & Permissions | ✅ Built | Week 3c: hooks.json enforcement |
| Verification | ✅ Built | — |
| Architecture Enforcement | ✅ Built | — |
| Memory Architecture | ✅ Built (3-tier) | — |
| Extension (@harness) | ✅ Built + evaluator firewall (Week 3a) | — |
| Direct Mode | ❌ Missing | Week 3c: routing in extension.ts |
| Context Management | ⚠️ Missing | Future: subagent offloading |

---

## Build Roadmap

### Day 1–5 ✅ Complete
All core harness modules built. 260 tests passing.

### Week 2 ✅ Complete
3-tier memory. Edge case hardening. 260 tests.

### Week 3a ✅ Complete — Separate Evaluator Session
**Shipped.** Reviewer now runs as an evaluator with an isolated context.
```
[x] Reviewer context firewalled to {code} only — no request, plan, design,
      or prior review. Enforced in copilot-harness/context_builder.py
      (_STAGE_PERMISSIONS["reviewer"] = {"code"}; _context_reviewer()).
[x] Memory injection skipped for reviewer in server.py harness_read_stage.
[x] Dynamic plan.required_skills injection skipped for reviewer; the
      code-review skill static injection is retained (that IS the checklist).
[x] pipeline.ts AGENT_PIPELINE + runCorrectionLoop: reviewer readStages
      tightened to ["code"].
[x] reviewer.agent.md rewritten for the evaluator contract.
[x] Tests: 11 new assertions covering reviewer isolation
      (test_context_builder.py, test_skill_access.py).
[ ] Deferred — Level 1 vs Level 2 decision. Requires running the LM against
      the eval set and comparing pass rates. Plan: build a one-off
      single-generator probe, run 3–5 representative requests, decide.
      Threshold: ≥ 80% first-attempt pass → Level 1 viable.
```

**Known trade-off:** `wrong_plan` status now rarely fires — the reviewer
cannot see the plan. Accepted for Week 3a. If this produces regressions,
Week 3b+ can add a dedicated planner-feedback channel.

### Week 3b — Pipeline Directory Migration
**Structural cleanup. No behavior change.**
```
[ ] Create .github/pipelines/feature-dev/ directory
[ ] Move agent definitions into pipeline directory
[ ] Add pipeline.yaml with level: 1 (or 2 if 3a proves multi-agent needed)
[ ] Add YAML frontmatter to agent .md files (name, description, model,
      maxTurns, disallowedTools)
[ ] Verify: harness_read_stage loads skills from new paths
[ ] Mark .github/agents/ as deprecated (keep for rollback, remove Week 5)
```

### Week 3c — Direct Mode + Hooks + Commands
```
[ ] Direct mode routing in extension.ts
      Slash commands → pipeline. Everything else → direct.
      No LLM routing call. Zero cost.
[ ] hooks.json wiring — PreToolUse policy enforcement as Python script
[ ] Slash command files: .github/commands/feature-dev.md
[ ] --pipeline flag to force pipeline mode for non-slash input
```

### Week 4 — Multi-Agent Coordination
```
[ ] Handoff schemas (plan→design, design→code, code→review)
      Only needed if feature-dev stays Level 2 after Week 3a test
[ ] Shared vocabulary / glossary injection
[ ] Cross-stage contract validation in verifier.py
[ ] Cross-session memory query
[ ] Tier 2 compaction (failure-patterns.md > 5KB)
```

---

## Promotion Checklist — Adding an Agent

Before promoting any pipeline from Level 0→1 or Level 1→2:

```
[ ] Observed the specific failure at least 3 times
[ ] Failure is reproducible, not random
[ ] Better prompt or skill file cannot fix it
[ ] A specialized agent would demonstrably handle the subtask better
[ ] Added complexity is worth added non-determinism
[ ] Documented: which subtask failed, what the single agent produced,
      why a separate agent would do better
```

If any item is unchecked: do not promote. Fix the skill file or schema first.

**Current feature-dev status:** Built as multi-agent (planner+designer+coder+reviewer).
Week 3a tests whether collapsing to single generator is viable. Decision
documented after test results.

---

## Evolution Path

Designed now. Not building until validated.

### Phase 2 — Pipeline Install (Month 2+)
Copy a pipeline directory to add a new pipeline. The v1 format IS the install format.

### Phase 3 — Auto-Invoke Skills (Month 3+)
Skills register trigger descriptions in frontmatter. LLM decides which to load.

### Phase 4 — Custom Hooks (Month 3+)
`type: command` (shell), `type: http` (webhook), `type: agent` (spawn agent).

### Phase 5 — Plugin Marketplace (Month 6+)
GitHub repo of validated pipelines. Pipeline-as-directory = marketplace entry.

### Phase 6 — Crew Integration
CopilotHarness is VS Code. Crew is terminal. Same harness core, same pipeline
format, different surfaces. Crew uses Copilot SDK as execution engine.
CopilotHarness uses VS Code LM API. Both share:
- Pipeline directory layout
- Skill format (SKILL.md)
- Hook format (hooks.json)
- Evaluator pattern (separate session)
- Policy engine
Neither product replaces the other.

---

## Boundaries

**vs AgentShield:** AgentShield secures individual tool calls.
CopilotHarness governs what a pipeline produces across a full workflow.

**vs Crew:** Crew is terminal-native for team workflows (standup, incidents,
tickets, release notes). CopilotHarness is IDE-native for coding workflows.
Same harness core. Same pipeline format. Different surfaces.

**vs Claude Code:** Freeform agentic chat with plugins. CopilotHarness is
governed pipelines. Same plugin primitives (commands, agents, skills, hooks, MCP),
different execution model.

---

## Known TODOs

```
WEEK 3a ✅ Complete — evaluator firewall shipped:
  DONE: Reviewer context isolated to {code} (Python firewall + pipeline.ts)
  DONE: No memory / no dynamic skill injection for reviewer
  DONE: reviewer.agent.md rewritten for evaluator contract
  DEFERRED: Test single-generator vs multi-agent for feature-dev
  DEFERRED: Document Level decision with evidence

WEEK 3b (next):
  TODO: Pipeline directory migration (.github/agents/ → .github/pipelines/)
  TODO: Agent frontmatter (YAML --- block)
  TODO: pipeline.yaml format with level enforcement

WEEK 3c:
  TODO: Direct mode routing in extension.ts
  TODO: hooks.json + scripts/pre_tool_use.py
  TODO: Slash command files (.github/commands/)

WEEK 4:
  TODO: Handoff schemas (only if feature-dev stays Level 2)
  TODO: Cross-session memory query
  TODO: Tier 2 compaction
```

---

## Resources

- .agent.md format: https://learn.microsoft.com/en-us/visualstudio/ide/copilot-specialized-agents
- FastMCP docs: https://gofastmcp.com
- VS Code Language Model API: https://code.visualstudio.com/api/extension-guides/language-model
- Claude Code plugins reference: https://code.claude.com/docs/en/plugins-reference
- Claude Code skills deep dive: https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
- Harness best practices: https://github.com/celesteanders/harness/blob/main/docs/best-practices.md
- Harness Engineering: https://mitchellh.com/writing/harness-engineering
- SWE-agent paper: https://arxiv.org/abs/2405.15793
- OWASP Top 10 Agentic AI: https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/

---

*Updated: April 2026*
*Project: CopilotHarness*
*Repo: https://github.com/Eurus7895/CopilotHarness*
*Runtime: Extension mode (v0.2.0) — @harness in Copilot Chat*
*Current: Week 3a complete — evaluator firewall, 275 tests*
*Next: Week 3b — pipeline directory migration*
