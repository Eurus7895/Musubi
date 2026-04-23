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

> Public-facing summary lives in [`README.md`](./README.md). This file is the
> internal source of truth for architecture, schemas, and the build roadmap.

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
    Chat stream emits one-line marker + "Show Harness Dashboard" button.
    Extension opens the Dashboard webview and posts session_start.
    ↓
    McpClient.callTool("harness_get_active_session")
        → crash recovery: resume interrupted session or start fresh
    ↓
    McpClient.callTool("harness_new_session", { request })
        → harness creates session, locks agent versions, returns session_id
    ↓
    For each agent: Dashboard ← stage_start (name, attempt, tags)
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
    Dashboard ← stage_complete (durationMs, summary)
    ↓
    Evaluator (separate vscode.lm.sendRequest, fresh context) → verdict
    ↓
    Fail → Dashboard ← correction_retry (attempt, fix_instructions)
         → correction loop (max 3 retries) → escalate
    ↓
    Dashboard ← pipeline_complete (success, escalated)
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

## Current State (Week 4 complete + Dashboard, v0.3.0)

```
WHAT EXISTS NOW:
  ✅ 379 tests passing (Python harness — TS extension has no unit tests)
  ✅ Harness core: state, context_builder, verifier, executor,
       correction_loop, skill_loader
  ✅ 3-tier memory: MEMORY.md, memory_loader, session_distiller
       + Tier 2 compaction + cross-session query (Week 4 Day 4)
  ✅ Pattern detector + proposed patch applier
  ✅ VS Code extension v0.3.0 with McpClient + Harness Dashboard webview
  ✅ PyInstaller binary distribution
  ✅ Separate evaluator session (Week 3a)
  ✅ Pipeline directory layout at .github/pipelines/feature-dev/ (Week 3b)
  ✅ Direct mode + slash commands + hooks.json (Week 3c)
  ✅ /help slash command (dynamic, data-driven)           (Week 4 Day 1)
  ✅ .claude-plugin/plugin.json manifest                  (Week 4 Day 2)
  ✅ Direct-mode skill catalog + pull-on-demand           (Week 4 Day 3)
  ✅ harness_query_sessions + harness_compact_memory      (Week 4 Day 4)
  ✅ feature-dev-level1-probe (built, not yet run)        (Week 4 Day 5)
  ✅ Harness Dashboard webview — pipeline card, stage dots, skill/memory/
       firewall/schema/policy tags, retry block with reviewer fix_instructions,
       live elapsed timer, footer actions (/status, Cancel, View plan.md)

FEATURE-DEV PIPELINE TODAY:
  4 agents: planner → designer → coder → reviewer (evaluator firewall)
  + skill-builder (meta-agent, not in feature-dev)
  Agent files live in .github/pipelines/feature-dev/agents/*.agent.md
  pipeline.yaml declares level: 2. Level-1 probe infrastructure built
  at .github/pipelines/feature-dev-level1-probe/, awaiting a measurement
  run.

ROUTING:
  Slash-command input          → pipeline mode (harness_* tools, session)
  Input with --pipeline flag   → pipeline mode
  Everything else              → direct mode (one MCP round-trip for
                                 skill catalog, then vscode.lm call;
                                 LLM may pull skills on demand)

CHAT OUTPUT vs DASHBOARD:
  Chat stream (Copilot Chat) — one-line marker + "Show Harness Dashboard"
                               button. Minimal surface.
  Dashboard webview           — rich pipeline card rendered verbatim from
                               the design mockup; extension drives it via
                               typed postMessage events at the existing
                               pipeline.ts instrumentation points.
```

---

## File Structure (v0.3.0, post Week 4 + Dashboard)

```
.github/
    AGENTS.md                    ← session-start map
    copilot-instructions.md
    instructions/                ← priority-ranked rules (P1-P4)

    pipelines/                   ← Week 3b: self-contained pipeline directories
        feature-dev/
            pipeline.yaml        ← level, baseline_checks, correction
            README.md
            agents/              ← agent .md with extended frontmatter
                planner.agent.md
                designer.agent.md
                coder.agent.md
                reviewer.agent.md

    commands/                    ← Week 3c: slash commands
        feature-dev.md           ← /feature-dev — full pipeline
        continue.md, status.md
        planner.md, designer.md, coder.md, reviewer.md

    agents/                      ← cross-pipeline agents (un-deprecated Week 5)
        skill-builder.agent.md   ← meta-agent
        explorer.agent.md        ← Week 5: sub agent role (read-only scan)
        investigator.agent.md    ← Week 5: sub agent role (debug, run tests)
        reviewer-aux.agent.md    ← Week 5: sub agent role (per-file review)
        proposed/                ← Skill-Builder output
        README.md                ← cross-pipeline agents vs pipeline-scoped stages

    skills/                      ← domain skills (unchanged)
        code-review/, api-design/, python/, testing/, database-patterns/,
        documentation/   each: SKILL.md + assets/ + references/

    memory/                      ← 3-tier memory (unchanged)
        MEMORY.md, architecture.md, failure-patterns.md

copilot-harness/                 ← Python MCP server
    server.py                    ← FastMCP stdio — harness_* tools
                                   (harness_run_hook added Week 3c)
    cli.py, state.py, context_builder.py, verifier.py, executor.py
    correction_loop.py, skill_loader.py
    memory/, storage/, tests/    (379 tests)

copilot-harness-extension/       ← VS Code extension (TypeScript, v0.3.0)
    src/
        extension.ts             ← direct-mode routing + slash dispatch
                                   (threads dashboard + CancellationTokenSource)
        mcpClient.ts             ← JSON-RPC stdio client
        pipeline.ts              ← agent driver + correction loop
                                   emits typed events to the dashboard
        slashCommands.ts         ← Week 3c: slash-command loader
        dashboard.ts             ← v0.3.0: HarnessDashboard webview owner
                                   (event bus, 1Hz tick, CSP + nonce)
    media/
        dashboard/               ← v0.3.0: webview assets (stripped of VS
                                   Code chrome — just the chat-panel surface)
            index.html           ← mounts #chat-body + #suggestions
            style.css            ← extracted from the mockup
            app.js               ← DOM mutator, consumes postMessage events
    bin/
        copilot-harness.exe      ← PyInstaller binary

hooks.json                       ← Week 3c: lifecycle hook config
scripts/                         ← Week 3c: hook implementations
    policy_engine.py             ← PIPELINE_POLICIES (fail-closed)
    pre_tool_use.py              ← policy gate (exit 0=allow, 1=deny)
    post_tool_use.py             ← SQLite audit log
    session_start.py             ← baseline_checks runner
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

## Harness Dashboard Webview (v0.3.0)

`vscode.ChatResponseStream` accepts only CommonMark + a few primitives
(button, anchor, filetree). Colored status dots, flex layout, pulse
animations — none of that renders in the chat panel. We ship a dedicated
webview instead.

```
Copilot Chat               ← one-line marker + "Show Harness Dashboard" button
Harness Dashboard panel    ← the full card from the design mockup
                             (route pill, stage dots, skill/memory/firewall/
                              schema/policy tags, retry block with reviewer
                              fix_instructions, live elapsed timer, footer
                              actions: /status · Cancel · View plan.md)
```

**Event bus (extension → webview, via `postMessage`):**
`session_start` · `stage_start` · `stage_progress` · `stage_complete` ·
`stage_failed` · `correction_retry` · `pipeline_complete` · `hook_event` ·
`tick` · `direct_start` · `direct_pull_skill` · `direct_complete`.

**Actions (webview → extension):**
`ready` · `action_cancel` · `action_status` · `action_view_file` ·
`action_run_slash` · `action_open_chat`.

**Invariants:**
- Events are queued before the webview posts `ready`, then flushed.
- A `CancellationTokenSource` linked to the chat request AND the
  dashboard's Cancel button lets either side abort the in-flight pipeline.
- CSP + nonce'd script tag; webview only reads from `media/dashboard/`.
- No new LLM calls — the dashboard is purely a renderer of events emitted
  from the existing `pipeline.ts` instrumentation points.

**Rendering boundary.** The Python harness does not know about the
dashboard. All events originate in `copilot-harness-extension/src/pipeline.ts`
and `extension.ts`. `HarnessDashboard` in `src/dashboard.ts` owns the
`WebviewPanel` lifecycle and the event-to-DOM translation table.

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
harness_get_skill(skill_id, agent_name)
harness_get_reference(skill_id, reference_name, agent_name)
harness_list_skills(agent_name)        # Week 4 Day 3 — per-caller filtered catalog
```

### Execution Tools ✅ built
```
harness_run_lint(files)       → LintResult (ruff)
harness_run_typecheck(files)  → TypeCheckResult (mypy)
harness_run_tests(test_dir)   → RunResult (pytest)
harness_run_hook(event, payload)      # Week 3c — shells out to scripts/<hook>.py
```

### Memory Tools ✅ built
```
harness_get_memory_context()           → Tier 1 index + Tier 2 available
harness_get_memory_entry(name)         → Tier 2 content on demand
harness_query_sessions(query, limit)   # Week 4 Day 4 — cross-session substring search
harness_distill_session(session_id)    → appends to failure-patterns.md
harness_compact_memory()               # Week 4 Day 4 — prunes failure-patterns.md when > 5 KB
```

**Total: 18 MCP tools** (harness_get_active_session, harness_new_session,
harness_read_stage, harness_write_stage, harness_get_status,
harness_increment_attempt + the 12 above).

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
| Tool Design | ✅ hooks.json PreToolUse (Week 3c) | — |
| Feedback Loops | ✅ Built | — |
| State Management | ✅ Built + crash recovery | — |
| Multi-Agent Coordination | ⚠️ Handoff schemas still missing | Conditional on Week 4 Day 5 probe outcome |
| Discoverability (/help) | ✅ Built (Week 4 Day 1) | — |
| Plugin manifest | ✅ Built (Week 4 Day 2) | — |
| Pipeline-as-install-unit | ⚠️ Half | Skill locality = global; revisit if portability needed |
| Direct-mode skill pull | ✅ Built (Week 4 Day 3) | — |
| Security & Permissions | ✅ Built + policy engine (Week 3c) | — |
| Verification | ✅ Built | — |
| Architecture Enforcement | ✅ Built | — |
| Memory Architecture | ✅ Built (3-tier) + compaction + cross-session query (Week 4 Day 4) | — |
| Extension (@harness) | ✅ Built + evaluator firewall (Week 3a) | — |
| Direct Mode | ✅ Shipped (Week 3c) + skill catalog (Week 4 Day 3) | — |
| Harness Dashboard (webview) | ✅ Built (v0.3.0) — pipeline card, stage dots, tags, retry block, live timer | — |
| Level decision for feature-dev | ⚠️ Probe built, not run (Week 4 Day 5) | Run 5 representative requests through both pipelines |
| Context Management | ⚠️ Missing | Week 5: sub agents (main-context preservation) |

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

### Week 3b ✅ Complete — Pipeline Directory Migration
**Structural cleanup. No behavior change.**
```
[x] Create .github/pipelines/feature-dev/ directory
[x] Move planner/designer/coder/reviewer into pipeline directory;
      skill-builder stays at .github/agents/ (meta-agent, not pipeline-scoped)
[x] Add pipeline.yaml with level: 2 (Week 3a Level-1 probe deferred)
[x] Add YAML frontmatter to agent .md files (model, maxTurns, tools,
      disallowedTools)
[x] state.AGENTS_DIRS globs both pipeline dir + legacy dir (first wins)
[x] pipeline.ts loadAgentPrompt falls back to legacy path
[x] Mark .github/agents/ deprecated via README (keep for rollback, remove Week 5)
```

### Week 3c ✅ Complete — Direct Mode + Hooks + Commands
```
[x] Direct mode routing in extension.ts
      Slash commands → pipeline. --pipeline flag → pipeline.
      Everything else → direct (vscode.lm.sendRequest, no harness).
      No LLM routing call. Zero cost.
[x] hooks.json at repo root (SessionStart / PreToolUse / PostToolUse)
[x] scripts/pre_tool_use.py — policy engine (PIPELINE_POLICIES fail-closed)
[x] scripts/post_tool_use.py — SQLite audit log (storage/audit.db)
[x] scripts/session_start.py — runs pipeline.yaml baseline_checks
[x] harness_run_hook MCP tool in server.py (shells out, reports results)
[x] .github/commands/*.md — 7 slash command files, frontmatter-driven
[x] copilot-harness-extension/src/slashCommands.ts — loader + lister
[x] --pipeline flag detection in parseCommand()
[x] Tests: +59 new (pipeline YAML shape, policy engine, hooks, slash
      commands, multi-dir agent glob). Total now 334.
```

### Week 4 ✅ Complete — Multi-Agent Coordination + Unblock Week 5

```
Day 1 ✅ /help slash command (dynamic, data-driven)
  [x] Added "help" to SlashAction in slashCommands.ts + VALID_ACTIONS
  [x] .github/commands/help.md (action: help)
  [x] extension.ts buildHelpMarkdown() renders a table from
      listSlashCommands(workspaceRoot) — stays in sync with new commands
  [x] USAGE_HEADER + USAGE_FOOTER reused by both cmd.type=="help" and
      the slash "/help" route
  [x] test_slash_commands.py: +2 assertions (help.md has action=help;
      help action carries no pipeline/agent)

Day 2 ✅ Plugin manifest + skill locality decision
  [x] .github/pipelines/feature-dev/.claude-plugin/plugin.json with
      { name, version, description, commands, agents, skills, hooks,
        mcpServers, pipeline, skillLocality } — purely declarative
  [x] Decision recorded in plugin.json.skillLocality: mode="global".
      Rationale: multiple pipelines reuse the same skills (python,
      testing, code-review); per-pipeline duplication would fragment
      the knowledge base. Revisit when a pipeline-specific skill
      appears or a second repo needs copy-paste install without skills.
  [x] NOT wired: skill_loader multi-dir fallback — deferred until the
      locality decision flips to pipeline-local.
  [x] test_plugin_manifest.py — 10 assertions (JSON parses + every
      referenced path resolves + skillLocality decision is recorded)

Day 3 ✅ Direct-mode pull-on-demand skills
  [x] context_builder.AGENT_SKILL_ALLOWLIST["direct"] = designer ∪ coder.
      Deliberately excludes reviewer's code-review skill (that's an
      evaluator checklist, not generator knowledge)
  [x] New MCP tool harness_list_skills(agent_name) in server.py —
      returns catalog filtered through caller's allowlist
  [x] extension.ts runDirect(): one-shot MCP call to harness_list_skills,
      injects catalog into system prompt, pull-on-demand loop with
      {"action":"pull_skill","skill_id":...} marker (max 3 rounds).
      Simpler than registering vscode.lm tools; the marker form keeps
      direct mode harness-free at the tool layer.
  [x] Pipeline mode untouched — still push-only, firewall intact
  [x] test_skill_access.py: +8 assertions (direct allowlist rejects
      code-review; list_skills filters per caller; planner catalog empty;
      reviewer catalog ⊆ {code-review, testing}; regression on
      harness_read_stage for pipeline agents)

Day 4 ✅ Memory: Tier 2 compaction + cross-session query
  [x] session_distiller.compact_failure_patterns() — fires when
      .github/memory/failure-patterns.md > 5 KB. Keeps union of
      top-10 most-frequent + top-10 most-recent. Auto-called from
      distill_session after every append.
  [x] memory_loader.query_sessions(query, limit) — case-insensitive
      substring match against request + stored review output.
      Returns structured excerpts (never full transcripts).
  [x] harness_query_sessions + harness_compact_memory MCP tools
  [x] test_session_distiller.py: +6 assertions (noop below threshold;
      fires above; preserves most-frequent; idempotent; distill triggers
      compaction; survives churn)
  [x] test_memory_loader.py: +7 assertions (request match; review match;
      empty query; limit; case-insensitive; no match; truncation)

Day 5 ✅ Level-1 probe for feature-dev (infrastructure built)
  [x] Built .github/pipelines/feature-dev-level1-probe/ with
      pipeline.yaml (level: 1, singular generator.agent), composite
      agent file producing plan+design+code in one shot, README
      documenting the 80% threshold and measurement protocol
  [x] Re-uses production reviewer via ../feature-dev/agents/reviewer.agent.md
      so generator-side changes are the only variable
  [x] probe.target_pass_rate: 0.80, sample_size: 5, baseline: feature-dev
  [x] test_level1_probe.py: 9 assertions (level=1, singular generator,
      reviewer reuse, probe metadata, README decision rule, production
      pipeline stays Level 2)
  [ ] STILL DEFERRED: actually running the probe. The infrastructure is
      built; 5 representative /feature-dev requests still need to be
      selected and run through both pipelines. Decision log in the
      probe README is empty until that happens. feature-dev stays
      Level 2 until we have that evidence.

Tests: +45 new across Days 1–5. Total now 379 (was 334).

CONDITIONAL (triggered by Day 5 outcome, once probe is run):
  [ ] Handoff schemas (plan→design, design→code, code→review)
      Only if Level-2 stays. Bumps to Week 4.5 or top of Week 5.
  [ ] Pipeline-as-install-unit (copy-paste install)
      Day 2 decision was "global skills" — portability still partial.
      Revisit if second-repo install becomes a real requirement.
```

### Week 5 — Sub Agents (5-day plan, main feature)

```
Day 1 — Phase A.1: MCP plumbing + policy
  [ ] harness_spawn_subagent(main_session_id, role, brief, allowed_tools,
      max_turns, output_schema) → handle in server.py
  [ ] harness_await_subagent(handle) → { summary, structured, tools_used,
      turns, escalated }
  [ ] harness_list_subagents(main_agent_name) → allowed roles for this main
  [ ] SUBAGENT_POLICIES in scripts/policy_engine.py (fail-closed)
  [ ] Ephemeral sub-session storage (auto-cleaned when main session ends)
  [ ] Tests: policy intersection (role ∩ main_allowlist);
      list_subagents filters by caller; unknown role rejected

Day 2 — Phase A.2: Firewall + result verification
  [ ] context_builder.py: sub agent read path — brief + role skill +
      allowed tools only. NO main plan, NO memory, NO sibling subs,
      NO main session_id
  [ ] verifier.py: summary token cap + optional JSON schema validation
      on return
  [ ] Tests: sub agent cannot read main session state;
      summary over token cap is truncated with marker;
      schema validation rejects malformed structured returns

Day 3 — Phase A.3: Role files + spawn-event surface
  [ ] .github/agents/explorer.agent.md (Read + Grep + Glob)
  [ ] .github/agents/investigator.agent.md (+ Bash for test runs)
  [ ] .github/agents/reviewer-aux.agent.md (Read + View, per-file checklist)
  [ ] mcpClient.ts emits "subagent_spawned" / "subagent_done" notifications
  [ ] extension.ts renders visible chat markers (brief, turn count,
      tool histogram, final summary line; full summary collapsible)
  [ ] post_tool_use.py audit log records every spawn (caller, role, brief,
      turns, tools, result) in storage/audit.db
  [ ] Tests: every spawn produces a chat marker; audit row written per spawn;
      no silent sub agents

Day 4 — Phase B: Pipeline-main spawning (highest ROI)
  [ ] pipeline.yaml schema extension: `subagents:` block per stage
      (opt-in, whitelist of roles + max_concurrent)
  [ ] pipeline.ts wires harness_spawn_subagent into stage agent tool list
      when stage has `subagents:`; otherwise tool is unavailable
  [ ] Enforce SUBAGENT_POLICIES[role] ∩ main_agent_allowlist
  [ ] Integration test: feature-dev coder spawns explorer on a real brief,
      receives summary, main context size unchanged (baseline check)
  [ ] Tests: main never sees transcript; max_turns is a hard cap;
      max_concurrent honored; stages without `subagents:` cannot spawn

Day 5 — Phase C: Direct-mode spawning
  [ ] Extend Week 4 Day 3 direct-mode MCP round-trip to ALSO return
      harness_list_subagents("direct") catalog (same round-trip, one shot)
  [ ] Register harness_spawn_subagent as vscode.lm tool in runDirect()
  [ ] Direct main's sub-agent allowlist defined in SUBAGENT_POLICIES
      (expect: explorer + investigator; reviewer-aux requires pipeline context)
  [ ] Tests: direct main spawns sub end-to-end; sub spawned from direct
      cannot read any direct state; direct mode still has no evaluator
  [ ] Update Known TODOs, audit table, footer: mark Week 5 complete

END-OF-WEEK checkpoint:
  [ ] 334 existing + new tests green
  [ ] Feature-dev run with a sub agent in the loop, full trace
  [ ] Audit log shows every spawn; chat markers rendered
  [ ] Memory of one real failure pattern (if encountered) distilled
```

### Week 5 — Sub Agents (planned, main feature)

**Why.** A main agent doing heavy evidence gathering (scan 50 files, run 20
test cases, aggregate grep results) dumps irrelevant content into its own
context window. Sub agents do that work in isolation and return a compressed
summary, keeping the main agent's context clean for reasoning.

**Definition.** A *sub agent* is an agent spawned **by another agent**
mid-task. Same `.agent.md` file as any other agent. The "sub" refers to the
invocation — an agent runs inside another agent's execution.

Terminology:
- **Agent** = the `.agent.md` file
- **Main agent** = currently driving the work (a pipeline stage, or direct
  mode `@harness`)
- **Sub agent** = agent spawned by the main agent to offload work

Invocations that are **not** sub agents: a pipeline running its stage agent,
or a user slash-invoking an agent directly — in those cases the agent *is*
the main, not a sub.

**Goal: preserve main context.** Spawn a sub agent when the main only needs
the *conclusion* of some exploration, not the raw evidence.

```
Good fits (ship):
  - Scan N files for pattern X, return match count + locations
  - Run the same lint / grep / check across a directory, aggregate
  - Read a file, return structured facts (imports, exported symbols)
  - Execute one test, report pass/fail + tail
  - Per-file review against a fixed checklist

Bad fits (do NOT build a role for these):
  - "Explore the architecture" (open-ended reasoning)
  - "Design a new feature" (that's a planner stage)
  - "Decide which approach is better" (decision belongs to the main)
  - "Implement this" (needs a full pipeline + evaluator)
```

Principle: **sub agents gather facts. The main agent reasons.** If a role
needs chain-of-thought beyond its brief, it's the wrong role — promote it to
a pipeline stage with an evaluator, or keep the work in the main.

**First three sub agent roles** (files under `.github/agents/`):
```
explorer       Read + Grep + Glob             (read-only exploration)
investigator   Read + Grep + Glob + Bash      (debug, run tests)
reviewer-aux   Read + View                    (strict per-file review)
```

Cross-pipeline agents live at `.github/agents/` (un-deprecated). Pipeline-
specific stage agents stay at `pipelines/<name>/agents/`.

**Spawn visibility — always shown to the user.** Every sub agent spawn is
surfaced in Copilot Chat, never hidden:

```
▶ explorer  "scan src/**/*.py for JWTValidator.verify usages"  (max 8 turns)
   …running…
✓ explorer  14 matches across 9 files  (turns: 3, tools: Grep×3, Read×4)
```

Non-negotiable:
- User sees which sub agent was spawned, with what brief, for how long
- User sees final summary line; full summary inlined (collapsible)
- Transcript stays firewalled — only the spawn event + summary are visible
- `post_tool_use.py` audit log records every spawn with caller, role,
  brief, turns, tools, result

No silent sub agents. If one is running, the user knows.

**Firewall rule** (preserves "harness pushes, agent cannot opt out"):
```
sub agent sees:       brief + role skill + allowed tools
                      NO main plan, NO memory, NO sibling sub agents,
                      NO session_id of the main
main sees on return:  summary (token-capped) + optional structured JSON
                      NEVER the sub agent's transcript
```

**Policy engine — new table:**
```python
SUBAGENT_POLICIES = {
    "explorer":     ["Read", "Grep", "Glob"],
    "investigator": ["Read", "Grep", "Glob", "Bash"],
    "reviewer-aux": ["Read", "View"],
}
# effective = SUBAGENT_POLICIES[role] ∩ main_agent_allowlist
```

**MCP tools:**
```
harness_spawn_subagent(main_session_id, role, brief, allowed_tools,
                       max_turns, output_schema)   → handle
harness_await_subagent(handle)
    → { summary, structured, tools_used, turns, escalated }
harness_list_subagents(main_agent_name)  # which roles can this main spawn?
```

**`pipeline.yaml` extension** (opt-in per stage — keeps push-not-pull):
```yaml
generator:
  agent: agents/coder.md
  subagents:                       # stage-declared whitelist
    - role: explorer
      max_concurrent: 2
    - role: investigator
      max_concurrent: 1
```
Stages without `subagents:` cannot spawn.

**Rollout phases:**
```
Phase A — Core primitives
  [ ] harness_spawn_subagent + harness_await_subagent + harness_list_subagents
  [ ] SUBAGENT_POLICIES in policy_engine.py (fail-closed)
  [ ] Sub agent context firewall in context_builder.py (brief-only path)
  [ ] Ephemeral sub session storage (auto-cleaned on main completion)
  [ ] Summary token cap + schema validation in verifier.py
  [ ] Three role files under .github/agents/
  [ ] Spawn-event surface: mcpClient.ts emits "subagent_spawned" /
      "subagent_done" notifications; extension renders visible chat markers
      (brief, turn count, tool histogram, final summary line)

Phase B — Pipeline-main spawning (highest ROI — ship first)
  [ ] pipeline.yaml `subagents:` whitelist per stage
  [ ] pipeline.ts wires harness_spawn_subagent into stage agent tools
  [ ] Tests: main never sees transcript, policy intersected, max_turns enforced

Phase C — Direct-mode spawning
  [ ] Bundle with Week 4 "Direct-mode pull-on-demand skills"
  [ ] Direct main agent gets harness_list_subagents + harness_spawn_subagent
      in same MCP round-trip as harness_list_skills
```

**Invariant analysis:**
- Sub agent context is pushed by harness, not pulled — firewall intact.
- Main's choice to spawn is a tool call; pipeline YAML `subagents:` bounds
  which roles the main can reach.
- Sub agent can never exceed main's allowlist (intersection).

**Promotion rule:** Add a sub agent role only when 3+ observed cases show
the main agent's context would have been saved by offloading. Do not invent
roles speculatively.

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

WEEK 3b ✅ Complete — pipeline directory migration:
  DONE: .github/pipelines/feature-dev/ with pipeline.yaml (level: 2)
  DONE: 4 agents moved + frontmatter extended (model, maxTurns, disallowedTools)
  DONE: state.AGENTS_DIRS multi-dir glob; pipeline.ts fallback path
  DONE: Legacy .github/agents/ retained (skill-builder + rollback) + README

WEEK 3c ✅ Complete — direct mode + hooks + slash commands:
  DONE: Direct mode routing in extension.ts (slash → pipeline, bare → direct)
  DONE: hooks.json + scripts/{pre,post}_tool_use,session_start}.py
  DONE: scripts/policy_engine.py with fail-closed PIPELINE_POLICIES
  DONE: .github/commands/*.md — 7 slash commands with frontmatter contracts
  DONE: slashCommands.ts loader (no new npm deps)
  DONE: harness_run_hook MCP tool in server.py
  DONE: --pipeline flag forces pipeline mode for non-slash input
  DONE: +59 tests (334 total)

WEEK 4 ✅ Complete:
  DONE: /help slash command — dynamic table built from listSlashCommands()
        .github/commands/help.md + SlashAction "help" + buildHelpMarkdown()
  DONE: .claude-plugin/plugin.json manifest for feature-dev
        .github/pipelines/feature-dev/.claude-plugin/plugin.json declares
        commands, agents, skills, hooks, mcpServers, pipeline, skillLocality.
        test_plugin_manifest.py validates every referenced path resolves.
  DONE: Skill locality decision = GLOBAL
        Recorded in plugin.json.skillLocality.mode + rationale. Multiple
        pipelines reuse python/testing/code-review; per-pipeline duplication
        would fragment knowledge. Revisit when a pipeline-specific skill
        or second-repo install becomes a real requirement.
  DONE: Direct-mode pull-on-demand skills
        AGENT_SKILL_ALLOWLIST["direct"] = designer ∪ coder (code-review
        deliberately excluded). harness_list_skills MCP tool. runDirect()
        fetches catalog once, injects into system prompt, LLM pulls via
        {"action":"pull_skill","skill_id":...} marker (max 3 rounds).
        Pipeline mode untouched — still push-only.
  DONE: Tier 2 compaction — session_distiller.compact_failure_patterns()
        fires when failure-patterns.md > 5 KB. Keeps top-10 most-frequent
        + top-10 most-recent. Auto-called from distill_session.
        harness_compact_memory MCP tool exposes manual trigger.
  DONE: Cross-session memory query — memory_loader.query_sessions()
        substring match against request + review output; returns structured
        excerpts (never raw transcripts). harness_query_sessions MCP tool.
  DONE: +45 tests (379 total).

WEEK 4 DEFERRED (infrastructure built, measurement still owed):
  TODO: Run the Level-1 probe for feature-dev
        .github/pipelines/feature-dev-level1-probe/ ready. Run 5
        representative /feature-dev requests through both pipelines,
        compare first-attempt pass rates. ≥ 80% → collapse to Level 1;
        else stay Level 2. Record evidence in probe/README.md decision
        log before touching production pipeline.yaml.

  TODO: Handoff schemas (plan→design, design→code, code→review)
        Only if the Level-1 probe keeps feature-dev at Level 2.
        Conditional on the deferred measurement above.

  TODO: Pipeline-as-install-unit (copy-paste install)
        Day 2 decision was "global skills" — portability stays partial.
        Revisit only when a second repo actually needs to copy-paste a
        pipeline without the skills library.

FEATURE-DEV PIPELINE UPGRADES (tiered plan):
  See .github/pipelines/feature-dev/ROADMAP.md for the full plan
  across 5 tiers (observed-bug fixes, handoff contracts, Level-1 probe,
  observability, Week-5 prerequisite). Each tier is independent;
  recommended first slice is Tier 1 + Tier 2.

WEEK 5 — Sub agents for main-context preservation (planned, main feature):
  Goal: main agent's context stays clean. Heavy evidence-gathering runs
  inside a sub agent; main receives only a compressed summary.

  TODO: Core primitives (Phase A)
        - harness_spawn_subagent / harness_await_subagent / harness_list_subagents
        - SUBAGENT_POLICIES in scripts/policy_engine.py (fail-closed)
        - Sub agent firewall path in context_builder.py (brief-only, no memory,
          no main plan, no sibling sub agents)
        - Ephemeral sub session storage + auto-cleanup when main completes
        - Summary token cap + optional JSON schema validation in verifier.py
        - .github/agents/{explorer,investigator,reviewer-aux}.agent.md
        - Spawn-event surface: mcpClient.ts emits "subagent_spawned" /
          "subagent_done"; extension renders visible chat markers (brief,
          turn count, tool histogram, final summary line). Non-negotiable —
          no silent sub agents.

  TODO: Pipeline-main spawning (Phase B — ship first, highest ROI)
        - pipeline.yaml `subagents:` whitelist per stage (opt-in, per-stage)
        - pipeline.ts wires harness_spawn_subagent into stage agent tools
        - Policy = SUBAGENT_POLICIES[role] ∩ main_agent_allowlist
        - Tests: main never sees transcript, only summary + structured;
          max_turns is a hard cap; stages without `subagents:` cannot spawn

  TODO: Direct-mode spawning (Phase C)
        - Bundle with the Week 4 "direct-mode pull-on-demand skills" TODO
        - Same MCP round-trip returns skill catalog + sub agent catalog
        - Direct main agent gets harness_spawn_subagent + harness_list_subagents
        - No new invariants — direct mode already has no evaluator

  Terminology note: "sub agent" is reserved for the agent-spawns-agent case.
  A pipeline running its stage agent, or a user slash-invoking an agent, is
  NOT a sub agent invocation — there the agent IS the main. The `.agent.md`
  file is identical regardless of how it is invoked; only the invocation
  contract and firewall differ. The deprecated folder `.github/agents/` is
  un-deprecated and becomes the home for cross-pipeline agents (skill-builder
  + the three sub agent roles). Pipeline-scoped stage agents still live at
  `.github/pipelines/<name>/agents/`.

DEFERRED (needs discussion first):
  - Model-invoked skill loading **inside pipeline mode** (agent decides
    which skill to load during plan/design/code). Breaks the
    "harness pushes, agent cannot opt out" invariant that the evaluator
    firewall and stage-specific injection depend on. Direct-mode pull
    above does NOT break this invariant because direct mode has no
    evaluator and no stage structure.

  - Sub agents reading memory. Default for Week 5 is *no memory for sub
    agents*. Opt-in per role via `memory: [<entry>]` in the role file only
    after 3+ observed failures of the same pattern. Matches the promotion
    rule.

  - User-invokable slash commands for sub agent roles (e.g. `/explore`).
    Dropped from Week 5 scope: sub agents exist to preserve a *main*
    agent's context; when the user is the caller, there is no main context
    to preserve — just use direct mode and let the direct main agent spawn
    sub agents as needed. Revisit only if a concrete power-user workflow
    demands it.
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
*Runtime: Extension mode (v0.3.0) — @harness in Copilot Chat + Harness Dashboard webview*
*Current: Week 4 complete + Dashboard webview — /help, plugin manifest, direct-mode skill pull, memory compaction + cross-session query, Level-1 probe infrastructure, live pipeline card in a webview panel; 379 tests*
*Next: Run the Level-1 probe (5 requests through both pipelines) → decide handoff schemas*
*Planned: Week 5 — sub agents for main-context preservation (dashboard already has the event surface to display them)*
