# CLAUDE.md — CopilotHarness

> Context anchor for every coding session. Read this file before doing anything.
> Ultimate goal: a fully implemented harness engineering layer for GitHub Copilot Chat
> running in VS Code. The harness is the environment Copilot operates within —
> not a wrapper around it.

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
Intent Router (extension.ts — classifies request)
  classifies: direct | pipeline
        ↓
  ┌─────────────────────┬────────────────────────────────┐
  │  DIRECT MODE        │  PIPELINE MODE                 │
  │                     │                                │
  │  Simple request     │  Complex workflow              │
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
extension.ts spawns bin/copilot-harness.exe via McpClient (JSON-RPC stdio)
        ↓
PIPELINE MODE:
    McpClient.callTool("harness_get_active_session")
        → crash recovery: resume interrupted session or start fresh
    ↓
    McpClient.callTool("harness_new_session", { request })
        → harness creates session, locks agent versions, returns session_id
    ↓
    For each agent in pipeline:
        McpClient.callTool("harness_read_stage", { session_id, stage, agent_name })
            → context firewall enforced, skills auto-injected, memory injected
        ↓
        vscode.lm.sendRequest(copilot, agentPrompt + context)
            → Copilot reasons, returns JSON output
        ↓
        McpClient.callTool("harness_write_stage", { session_id, stage, output })
            → injection scan → schema check → append-only store
    ↓
    Evaluator "fail" → correction loop (max 3 retries) → escalate
```

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

Level 1    Single agent + separate evaluator (fresh session).
           Full governance. Correction loop (max 3).
           When: wrong output has real cost, quality must be structural.
           Current: feature-dev pipeline (planner+designer+coder = single generator,
                    reviewer = evaluator in separate session)

Level 2    Multi-agent pipeline + separate evaluator.
           Multiple specialized agents in sequence (same session).
           Evaluator in separate session.
           When: ONLY when single agent demonstrably fails at a subtask.
           Not in v1 — promotion checklist required.
```

**Promotion rule:** A pipeline starts at the lowest viable level. Promotion
requires 3+ observed failures that cannot be fixed by updating the skill file.

**Key principle from celesteanders/harness:** "Start with a single-agent loop
before reaching for multi-agent orchestration. Multi-agent systems introduce
the same complexity as microservices, compounded by non-determinism."

**Key principle from Claude Code:** Evaluator/reviewer must run in a completely
separate session — fresh context, no shared state. "Subagents maintain isolated
context, preventing information from earlier tasks from interfering with new analysis."

---

## The Two Layers

### Layer 1 — Copilot Native Files (loaded automatically by VS Code)

Architecture follows Claude Code plugin conventions:

```
.github/
    AGENTS.md                            ← P1: global always-on rules (map, not encyclopedia)
    copilot-instructions.md              ← P1: global team conventions

    instructions/                        ← RULES AND STANDARDS (priority-ranked)
        universal/                       ← P1: never overridden
            security.instructions.md
            ethics.instructions.md
        org/                             ← P2: team-wide standards
            git-conventions.instructions.md
            code-review-standards.instructions.md
        domain/                          ← P3: file-scoped via applyTo
            python.instructions.md       ← applyTo: **/*.py
            api.instructions.md          ← applyTo: **/api/**
            database.instructions.md     ← applyTo: **/storage/**,**/models/**
            testing.instructions.md      ← applyTo: **/test_*.py
        project/                         ← P4: repo-specific
            architecture-decisions.instructions.md
            naming-conventions.instructions.md

    pipelines/                           ← self-contained pipeline directories
        feature-dev/
            pipeline.yaml                ← level, agents, skills, correction config
            agents/
                generator.md             ← YAML frontmatter + system prompt
                evaluator.md             ← Level 1: skeptical QA (separate session)
            skills/
                SKILL.md                 ← domain knowledge, common mistakes
            schemas/
                output.json              ← evaluator grading criteria
            README.md
        code-review/
            ...same structure...

    commands/                            ← slash commands (Claude Code format)
        feature-dev.md                   ← /feature-dev — bypasses router
        code-review.md
        refactor.md

    skills/                              ← global skills (shared across pipelines)
        code-review/    SKILL.md + assets/ + references/
        api-design/     SKILL.md + assets/ + references/
        python/         SKILL.md + assets/ + references/
        testing/        SKILL.md + assets/ + references/

    agents/                              ← legacy agent definitions (migrating to pipelines/)
        proposed/                        ← Skill-Builder writes here, human approves

    memory/                              ← 3-tier memory architecture
        MEMORY.md                        ← Tier 1: index (~200 tokens, always loaded)
        architecture.md                  ← Tier 2: decisions
        failure-patterns.md              ← Tier 2: recurring failures (auto-appended)
```

### Layer 2 — CopilotHarness (pure Python MCP server, zero LLM)

```
copilot-harness/
    server.py          ← FastMCP stdio server — all harness_* tools
    cli.py             ← entry point: copilot-harness serve
    state.py           ← append-only session state (SQLite)
    context_builder.py ← context firewall (returns dict) + injection detection
    verifier.py        ← schema validation + secrets scan          ✅ built
    executor.py        ← lint + typecheck + test runner            ✅ built
    correction_loop.py ← evaluator → generator retry (max 3)      ✅ built
    skill_loader.py    ← serves SKILL.md + references             ✅ built
    memory/
        cross_session.db
        pattern_detector.py                                        ✅ built
        memory_loader.py                                           ✅ built
        session_distiller.py                                       ✅ built
    storage/
        db.py          ← SQLite CRUD; schema embedded as string
        schema.sql     ← reference copy (not read at runtime)
    tests/             ← 260 tests passing
    pyproject.toml
    README.md
    CLAUDE.md

hooks.json             ← hook wiring config (Claude Code format)

.vscode/
    mcp.json           ← dev mode: python server.py for manual Copilot Chat use
```

**VS Code Extension (✅ built, v0.2.0):**
```
copilot-harness-extension/
    src/
        extension.ts         ← activates on VS Code start (onStartupFinished)
                               spawns bin/copilot-harness.exe via McpClient
                               registers @harness chat participant
                               routes: direct vs pipeline
        mcpClient.ts         ← minimal MCP stdio client (newline JSON-RPC)
        pipeline.ts          ← drives agents via McpClient.callTool() +
                               vscode.lm.sendRequest(); correction loop (max 3)
    bin/
        copilot-harness.exe  ← PyInstaller one-file binary (Windows)
        copilot-harness      ← PyInstaller one-file binary (Linux/Mac)
        launch.js            ← cross-platform launcher
    package.json             ← VS Code ^1.93.0, activationEvents: onStartupFinished
    tsconfig.json
```

---

## Execution Modes

### Direct Mode

```
User types "@harness explain this error"
        ↓
extension.ts classifies → direct
        ↓
vscode.lm.sendRequest(copilot, prompt)
        ↓
Stream response directly to chat
No MCP, no harness, no plan JSON, no hooks
```

When: simple questions, explanations, "how do I run X?", quick lookups.
Fast. No overhead. The harness is not involved.

### Pipeline Mode (Level 0)

Single agent. Baseline checks, skill injection, plan JSON.
No evaluator. Team feedback is the evaluator.

### Pipeline Mode (Level 1 — current feature-dev flow)

```python
# pipeline.ts — Level 1 execution

# Generator session
for each agent in pipeline.agents:
    context = McpClient.callTool("harness_read_stage", {
        session_id, stage, agent_name
    })
    # context firewall enforced, skills injected, memory injected
    output = await vscode.lm.sendRequest(copilot, agentPrompt + context)
    McpClient.callTool("harness_write_stage", {
        session_id, stage, output, agent_name
    })
    # injection scan → schema check → append-only store

# Evaluator — separate session (fresh context)
evaluator_context = McpClient.callTool("harness_read_stage", {
    session_id, "review", "reviewer"
})
# evaluator sees ONLY: plan + design + code output + review skill
# evaluator does NOT see: generator instructions, memory, pipeline history
verdict = await vscode.lm.sendRequest(copilot, evaluatorPrompt + evaluator_context)

if verdict.status == "fail":
    # correction loop: max 3 retries
    fire_hook("on-eval-fail")
    # retry generator with fix_instructions
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

## Hooks

Follows Claude Code `hooks.json` format. Deterministic code at lifecycle points.

**Key rule:** "Never send an LLM to do a linter's job." Anything that must happen
the same way every time belongs in a hook, not in the agent's instructions.

| Hook | Claude Code equivalent | When | v1 behavior |
|---|---|---|---|
| `SessionStart` | `SessionStart` | Before pipeline run | Orient: read progress, baseline checks |
| `PreToolUse` | `PreToolUse` | Before every tool call | Policy enforcement per agent |
| `PostToolUse` | `PostToolUse` | After every tool call | SQLite audit logging |
| `on-eval-fail` | (custom) | Evaluator rejects | Log failure + send fix_instructions |
| `on-escalate` | (custom) | Max retries exceeded | Escalate with full context |

**hooks.json:**
```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "",
      "hooks": [{ "type": "command", "command": "python3 scripts/pre_tool_use.py" }]
    }],
    "PostToolUse": [{
      "matcher": "",
      "hooks": [{ "type": "command", "command": "python3 scripts/post_tool_use.py" }]
    }],
    "SessionStart": [{
      "matcher": "",
      "hooks": [{ "type": "command", "command": "python3 scripts/session_start.py" }]
    }]
  }
}
```

v1: Python scripts that log and enforce policy.
v2+: `type: "command"` (shell), `type: "http"` (webhook), `type: "agent"`.

---

## MCP Tools

### State Tools
```
harness_get_active_session()
    → state.get_active_session()
    → returns { session_id, request, resume_stage, attempt } | { session_id: null }
    → call FIRST — before harness_new_session — to detect interrupted sessions

harness_new_session(request)
    → create_session() + lock_agent_versions() + set_active_session()
    → returns { session_id, locked_agent_versions }

harness_read_stage(session_id, stage, agent_name)
    → auto-marks agent's output stage in_progress (crash recovery marker)
    → context_builder.read_stage_for_agent() — per-stage firewall
    → auto-injects skills from pipeline config
    → auto-injects Tier 1 memory (MEMORY.md) + Tier 2 availability list
    → returns { data, injected_skills?, memory? }

harness_write_stage(session_id, stage, output, agent_name)
    → scan_injection() + verifier.validate() (schema + secrets + contracts)
    → state.write_stage()
    → returns { status: "stored" | "error" }

harness_get_status(session_id)
    → state.get_status()
    → returns pipeline stage summary

harness_increment_attempt(session_id, stage)
    → state.increment_attempt() — creates new attempt row
    → returns { status: "incremented", attempt: N }
```

### Skill Tools
```
harness_get_skill(skill_id)
    → skill_loader.get_skill() → reads skills/{id}/SKILL.md
harness_get_reference(skill_id, reference_name)
    → skill_loader.get_reference() → reads skills/{id}/references/{name}
```

### Execution Tools ✅ built
```
harness_run_lint(files)       → executor.run_lint()      → LintResult (ruff JSON)
harness_run_typecheck(files)  → executor.run_typecheck()  → TypeCheckResult (mypy)
harness_run_tests(test_dir)   → executor.run_tests()      → RunResult (pytest -v)
```

### Memory Tools ✅ built
```
harness_get_memory_entry(name)
    → memory_loader.get_tier2_entry(name) → Tier 2 content on demand
harness_distill_session(session_id)
    → session_distiller.distill_session() → appends to failure-patterns.md
```

---

## Pipeline Directory Layout

Each pipeline is a self-contained directory (Claude Code plugin convention):

```
.github/pipelines/
    feature-dev/
        pipeline.yaml              ← level, agents, skills, correction config
        agents/
            generator.md           ← YAML frontmatter + system prompt
            evaluator.md           ← Level 1: skeptical QA (separate session)
        skills/
            SKILL.md               ← domain knowledge, common mistakes
        schemas/
            output.json            ← evaluator grading criteria
        README.md                  ← documentation
```

**Pipeline YAML format:**
```yaml
name: feature-dev
description: Guided feature development with planning, design, coding, and review
version: 1.0.0
level: 1

baseline_checks:
  - type: file_read
    path: src/
    error: "Cannot read src/ — check permissions"

generator:
  agents:
    - name: planner
      agent: agents/planner.md
      skill: null
    - name: designer
      agent: agents/designer.md
      skill: api-design
    - name: coder
      agent: agents/coder.md
      skill: python
  output_schema: schemas/code-output.json

evaluator:
  agent: agents/evaluator.md
  skill: code-review
  schema: schemas/review-criteria.json

correction:
  max_retries: 3
  escalate_message: "Feature requires human review — 3 automated attempts failed"

output:
  stream: true
```

---

## Context Firewall (context_builder.py)

```python
build_context(session_id, agent_name) → dict
    # Full allowed context for an agent in one call.

read_stage_for_agent(session_id, stage, agent_name) → dict | None
    # Per-stage access with permission check.
    # Coder reading "review" → fix_instructions only (not full review JSON).
    # Evaluator → sees output + schema ONLY. No generator instructions, no memory.
```

---

## Memory Architecture ✅ Built (Week 2)

```
Tier 1 — MEMORY.md (~200 tokens, always loaded)
    Pointers to where knowledge lives. Injected by harness_read_stage.

Tier 2 — .github/memory/*.md (loaded on demand)
    architecture.md       ← SQLite, MCP, PyInstaller decisions
    failure-patterns.md   ← recurring failures (auto-appended by distiller)

Tier 3 — storage/sessions/ (raw history, never auto-loaded)
    Full session transcripts. Accessed only via search.
```

**memory_loader.py ✅ built:**
```
get_tier1_index(repo_root?) → str | None
get_tier2_entry(name, repo_root?) → str | None  (path-traversal blocked)
list_tier2_entries(repo_root?) → list[str]
get_memory_context(repo_root?) → dict
```

**session_distiller.py ✅ built:**
```
distill_session(session_id, db_path?, repo_root?) → list[str]
distill_all_completed(db_path?, repo_root?) → dict[str, list[str]]
Appends critical/high severity issues to failure-patterns.md; deduplicates
```

---

## Policy Engine

```python
PIPELINE_POLICIES = {
    "feature-dev": {
        "planner":   ["Read", "View", "Grep", "Glob"],
        "designer":  ["Read", "View", "Grep", "Glob"],
        "coder":     ["Read", "View", "Grep", "Glob", "Write", "Edit", "Bash"],
        "evaluator": ["Read", "View"],
    },
    "code-review": {
        "generator": ["Read", "View", "Grep", "Glob"],
        "evaluator": ["Read", "View"],
    },
}
```

Enforced via `PreToolUse` hook — deterministic, not prompt-based.
Policy engine is never removed regardless of model capability.

---

## LLM Usage — Zero Inside Harness

```
Component              LLM?   Implementation
──────────────────────────────────────────────────────
server.py              ❌     FastMCP stdio routing           ✅
state.py               ❌     Python + SQLite                 ✅
context_builder.py     ❌     Python dict filtering + regex   ✅
verifier.py            ❌     jsonschema + regex              ✅
correction_loop.py     ❌     Python orchestration            ✅
skill_loader.py        ❌     file I/O                        ✅
executor.py            ❌     subprocess: ruff, mypy, pytest  ✅
pattern_detector.py    ❌     SQLite count threshold          ✅
proposed_patch_applier ❌     regex validate + file I/O       ✅
memory_loader.py       ❌     file I/O + path validation      ✅
session_distiller.py   ❌     SQLite query + file append      ✅
extension/mcpClient.ts ❌     JSON-RPC stdio child process    ✅
extension/pipeline.ts  ❌     McpClient.callTool + sendRequest ✅
extension/extension.ts ❌     @harness chat participant       ✅

Copilot Chat (VS Code) ✅     agent reasoning
vscode.lm API          ✅     agent reasoning via extension
```

---

## Harness Audit Summary

| Component | Status | Notes |
|---|---|---|
| Tool Design | ⚠️ Prompt-level (migrating to PreToolUse hook) | hooks.json wiring needed |
| Feedback Loops | ✅ Built (correction_loop.py + extension loop) | — |
| State Management | ✅ Built + crash recovery | — |
| Multi-Agent Coordination | ❌ Missing | handoff schemas (Week 3) |
| Security & Permissions | ✅ Built (injection scan + verifier + patch guard) | hooks.json enforcement next |
| Verification | ✅ Built (verifier.py + executor.py) | — |
| Architecture Enforcement | ✅ Built (patch applier + archive + proposed/ guard) | — |
| Memory Architecture | ✅ Built (3-tier + memory_loader + session_distiller) | — |
| Extension (@harness) | ✅ Built (McpClient + direct callTool) | add direct mode routing |
| Context Management | ⚠️ Missing | subagent offloading for expensive reads |

---

## Build Roadmap

### Day 1–5 ✅ Complete
All core harness modules built. 260 tests passing.

### Week 2 ✅ Complete
3-tier memory (MEMORY.md + memory_loader + session_distiller).
Edge case hardening. Verifier bug fix. 260 tests.

### Week 3 (next) — Crew Architecture Adoption
```
[ ] Direct mode routing in extension.ts
      Simple requests → vscode.lm.sendRequest() directly, no harness
[ ] hooks.json wiring — PreToolUse policy enforcement via hook scripts
[ ] Migrate .github/agents/ → .github/pipelines/ directory layout
      Each pipeline becomes self-contained directory
[ ] pipeline.yaml format per pipeline
[ ] Slash commands: .github/commands/feature-dev.md, code-review.md
[ ] Agent frontmatter migration (YAML frontmatter + markdown body)
[ ] Agent complexity level field in pipeline.yaml
[ ] Separate evaluator session — reviewer runs in fresh context
      (currently shares session with generator via pipeline.ts)
[ ] Promotion checklist enforcement
```

### Week 4 — Multi-Agent Coordination
```
[ ] Handoff schemas: plan→design, design→code, code→review
[ ] Shared vocabulary / glossary injection
[ ] Cross-stage contract validation in verifier.py
[ ] Cross-session memory query — search past failure patterns
[ ] Tier 2 compaction — auto-merge entries when failure-patterns.md > 5KB
```

### Month 2+ — Evolution Path
```
Phase 2: Pipeline install — crew install = copy pipeline directory
Phase 3: Auto-invoke skills — skills register trigger descriptions
Phase 4: Custom hooks — type: command / http / agent
Phase 5: Plugin marketplace — GitHub repo of validated pipelines
Phase 6: Copilot SDK migration — terminal mode alongside VS Code
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
```

If any item is unchecked: do not promote. Fix the skill file or schema first.

---

## Known TODOs

```
WEEK 3 (next):
  TODO: Direct mode routing in extension.ts
  TODO: hooks.json — PreToolUse policy enforcement as hook script
  TODO: Migrate agents to pipeline directories
  TODO: pipeline.yaml format
  TODO: Slash command files (.github/commands/)
  TODO: Agent frontmatter (YAML --- block)
  TODO: Separate evaluator session (reviewer in fresh context)

WEEK 4:
  TODO: Handoff schemas (plan→design, design→code, code→review)
  TODO: Shared context block injection
  TODO: Cross-stage contract validation
  TODO: Cross-session memory query
  TODO: Tier 2 compaction (failure-patterns.md > 5KB)
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
- SWE-agent paper (Princeton NeurIPS 2024): https://arxiv.org/abs/2405.15793
- OWASP Top 10 Agentic AI: https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
- CopilotHarness: https://github.com/Eurus7895/CopilotHarness

---

*Updated: April 2026*
*Project: CopilotHarness*
*Repo: https://github.com/Eurus7895/CopilotHarness*
*Runtime: Extension mode (v0.2.0) — @harness in Copilot Chat*
*Current milestone: Week 2 complete — 260 tests, 3-tier memory, edge case hardening*
*Next milestone: Week 3 — Crew architecture adoption (direct mode, pipeline directories, hooks.json, separate evaluator)*
