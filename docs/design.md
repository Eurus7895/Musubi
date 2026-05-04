# Design — CopilotHarness

> Full design doc: architecture, schemas, roadmap, status, compliance.
> For Claude Code rules and conventions, read `/CLAUDE.md`.
> For agent session-start orientation, read `/AGENTS.md`.
> For install / quickstart, read `/README.md`.

---

## One Sentence

CopilotHarness is a Python MCP server that acts as the harness layer for GitHub
Copilot Chat — it controls what each agent sees, validates what each agent
produces, enforces the correction loop, injects skills, and runs code verification.

**Copilot Chat reasons. CopilotHarness controls the environment it reasons about.**

Simple requests get a direct response. Complex workflows route to governed
pipelines with validation, correction loops, and audit trails.

> Public-facing summary lives in [`README.md`](./README.md). This file is the
> internal source of truth for architecture and schemas. Build roadmap and
> status live in [`docs/roadmap.md`](./roadmap.md).

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
    Chat stream emits pipeline header: route, pipelineName, level, session.
    ↓
    McpClient.callTool("harness_get_active_session")
        → crash recovery: resume interrupted session or start fresh
    ↓
    McpClient.callTool("harness_new_session", { request })
        → harness creates session, locks agent versions, returns session_id
    ↓
    For each agent: Chat ← ### ⏳ <agent> (+ attempt x/3 when retrying)
                         ← tag line (skill · memory · firewall · schema · policy)
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
    Chat ← ✓ <agent> — 3.1s — <schema-summary>
    ↓
    Evaluator (separate vscode.lm.sendRequest, fresh context) → verdict
    ↓
    Fail → Chat ← > ⚠️ reviewer → fail · N issues + fix_instructions
         → correction loop (max 3 retries) → escalate
    ↓
    Chat ← *total: 18.4s* + ✅/⚠️ summary + [View plan.md] anchor
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

## Current State (Week 4 complete + Tasks sidebar, v0.4.0)

```
WHAT EXISTS NOW:
  ✅ 379 tests passing (Python harness — TS extension has no unit tests)
  ✅ Harness core: state, context_builder, verifier, executor,
       correction_loop, skill_loader
  ✅ 3-tier memory: MEMORY.md, memory_loader, session_distiller
       + Tier 2 compaction + cross-session query (Week 4 Day 4)
  ✅ Pattern detector + proposed patch applier
  ✅ VS Code extension v0.4.0 with McpClient + rich in-chat rendering + sidebar Tasks view
  ✅ PyInstaller binary distribution
  ✅ Separate evaluator session (Week 3a)
  ✅ Pipeline directory layout at .github/pipelines/feature-dev/ (Week 3b)
  ✅ Direct mode + slash commands + hooks.json (Week 3c)
  ✅ /help slash command (dynamic, data-driven)           (Week 4 Day 1)
  ✅ .claude-plugin/plugin.json manifest                  (Week 4 Day 2)
  ✅ Direct-mode skill catalog + pull-on-demand           (Week 4 Day 3)
  ✅ harness_query_sessions + harness_compact_memory      (Week 4 Day 4)
  ✅ feature-dev-level1-probe (built, not yet run)        (Week 4 Day 5)
  ✅ Rich in-chat pipeline rendering — per-stage ### markdown sections,
       status emoji (⏳ ↻ ✓ ✗), governance tags (◆ memory / ◈ skill /
       { } schema / ⟡ firewall / ◇ policy), elapsed seconds, retry
       blockquote with reviewer verdict + fix_instructions, plan.md anchor.
  ✅ Tasks sidebar TreeView (v0.4.0) — native vscode.TreeDataProvider under
       a new activity-bar container. Two sections:
         Active session — stages (pending / in_progress / complete / failed),
                          codicon status markers, attempt counter,
                          auto-refresh on pipeline stage transitions.
         History        — past sessions from .harness/sessions/, outcome
                          inferred from the latest review.md. Expand a row
                          to see its stages; click a stage to open the
                          materialised artifact in an editor tab.

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

CHAT SURFACE:
  Every @harness interaction — direct AND pipeline — lives inline in
  Copilot Chat. No separate panel. The VS Code chat API supports
  markdown + progress + button + anchor; we use all four:
    - markdown: pipeline header, per-stage ### sections, tag lines,
                retry blockquote, stage-complete lines, footer summary
    - progress: ephemeral "Resuming session ..." hints
    - anchor:   final "View plan.md" link to the materialised artifact
  No colors, no animations — the chat renders CommonMark — but all
  governance information the pipeline enforces is visible inline.
```

---

## File Structure (v0.4.0, post Week 4)

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

copilot-harness-extension/       ← VS Code extension (TypeScript, v0.4.0)
    src/
        extension.ts             ← direct-mode routing + slash dispatch
                                   + pipeline/step summary + plan.md anchor
                                   + Tasks view provider registration
        mcpClient.ts             ← JSON-RPC stdio client
        pipeline.ts              ← agent driver + correction loop
                                   + rich in-chat stage rendering
                                   (STAGE_TAGS, emitStageStart/Complete,
                                   fmtSeconds, renderTags)
                                   + onChange callback fired at stage
                                   transitions to refresh the Tasks view
        slashCommands.ts         ← Week 3c: slash-command loader
        tasksView.ts             ← v0.4.0: HarnessTasksProvider
                                   (vscode.TreeDataProvider)
                                   Active section via harness_get_active_session
                                   + harness_get_status;
                                   History section via filesystem scan of
                                   .harness/sessions/<sid>/*.md
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

## In-Chat Pipeline Rendering (v0.3.1)

The whole harness experience — direct mode AND pipeline mode — renders
inline in Copilot Chat. No separate panel, no webview. This matches the
Copilot Chat / Claude Chat idiom: the assistant speaks, you read in the
same surface.

**What VS Code's ChatResponseStream gives us:**
- `stream.markdown(md)` — CommonMark with GFM tables
- `stream.progress(text)` — ephemeral one-line hints
- `stream.button({ command, title })` — action buttons
- `stream.anchor(uri, title)` — file/line links

**Per-stage template (pipeline.ts `emitStageStart` / `emitStageComplete`):**

```markdown
### ⏳ coder   *(attempt 2/3 on retry)*
◈ skill: `python` · ⟡ firewall: `fix_instructions only`

✓ **coder** — 9.2s — 3 files, schema ✓
```

**Retry blockquote (pipeline.ts `runCorrectionLoop`):**

```markdown
> ⚠️ **reviewer → fail** · 2 issues
>
> Fix: Reorder auth → ownership → fetch. Add test_cancel_403_when_not_owner.
```

**Pipeline header + footer:**

```markdown
🎛 **/feature-dev** — feature-dev · level 2 · session `s/9f3a2c`
[ $(checklist) Show Tasks ]     ← stream.button → workbench.view.extension.copilotHarness
...
*total: 18.4s*

✅ **Pipeline complete.** Session: `s/9f3a2c`
[View plan.md →]
```

**Per-stage output (`emitStageOutputDetails`, v0.4.0):**

Under each stage-complete line, a collapsible `<details>` block renders
the agent's structured output as markdown — tasks for planner, modules
for designer, files_modified + implementation_notes for coder, status +
issues + fix_instructions for reviewer. Emitted as one markdown call so
streaming can't break the HTML.

```markdown
✓ **planner** — 3.1s — 5-step plan, schema ✓

<details><summary>output</summary>

**Summary:** Add /jobs/:id/cancel with 401/403/404 guards

**Tasks:**
- `T1` — Add route handler with RBAC middleware
- `T2` — Add test_cancel_403_when_not_owner
- ...
</details>
```

**Governance tag map (`STAGE_TAGS` in pipeline.ts):**

| stage | tags pushed |
|---|---|
| plan   | ◆ memory: `MEMORY.md` · ◇ policy: `Read·Grep·Glob` |
| design | ◈ skill: `api-design` · { } schema: `design.json` |
| code   | ◈ skill: `python` · ◇ policy: `Read·Write·Edit·Bash` |
| review | ◈ skill: `code-review` · ⟡ firewall: `code only` |
| code (retry) | ◈ skill: `python` · ⟡ firewall: `fix_instructions only` |

These mirror the push-not-pull injection the harness enforces — the
reader sees what was pushed to each stage without leaving the chat.

**Invariants:**
- The Python harness is unchanged by the rendering choice — all emit
  points are in `pipeline.ts` / `extension.ts`.
- No dashboard panel, no postMessage bus, no webview CSP concerns.
- `pipeline.yaml` is the source of truth for skills/tags; the STAGE_TAGS
  table is a render-time mirror and must stay in sync. (Candidate for
  harness-driven push later — out of scope for v0.3.1.)

---

## Tasks Sidebar TreeView (v0.4.0)

Native `vscode.TreeDataProvider` contributed to a dedicated activity-bar
container (`copilotHarness` → view `copilotHarness.tasks`). No webview,
no HTML/CSS — TreeItems, codicons, VS Code's own theming.

```
CopilotHarness            ← activity-bar icon ($(checklist))
└── Tasks
    ├── Active session    ← only when harness_get_active_session returns a live session
    │     ├── ✓ planner    complete
    │     ├── ✓ designer   complete
    │     ├── ↻ coder      in_progress · attempt 2
    │     └── ○ reviewer   pending
    └── History           ← always present
          ├── ✓ s/9f3a2c     review: pass        (expand → stages)
          ├── ⚠ s/8b21a4     review: escalate
          └── ○ s/6a01ee     plan only
```

**Data sources (no new MCP tool needed):**
- Active — `harness_get_active_session` + `harness_get_status` (existing).
- History — filesystem scan of `.harness/sessions/<sid>/`; outcome
  inferred from the latest `review.md`'s `**Status:**` header
  (`pass` / `fail` / `escalate`).
- Session stages (when expanded) — list `<stage>.md` + `<stage>.attemptN.md`
  files; click opens the latest attempt.

**Refresh contract:**
- `HarnessTasksProvider.refresh()` fires `onDidChangeTreeData`.
- extension.ts exposes a `refreshTasks()` callback to pipeline.ts via
  optional `onChange` parameter on `runPipeline` / `runStep` /
  `runCorrectionLoop`.
- pipeline.ts calls `onChange?.()` at every stage-start, stage-complete,
  and session-start — a 150 ms debounce in extension.ts collapses bursts.

**Commands:**
- `copilot-harness.refreshTasks` — manual refresh (view title bar).
- `copilot-harness.openSessionArtifact(sessionId, stage)` — opens the
  latest attempt of `<stage>.md` in an editor tab. Bound to each
  TreeItem's `command`.

**Invariants:**
- TreeView never blocks on an MCP call — errors route to the output
  channel; the view degrades to showing just History from disk.
- No LLM calls. No modification of `.harness/` state. Read-only surface.
- File-system heuristics for history must match exactly what
  `materializeStageOutput` writes (`<stage>.md`, `<stage>.attemptN.md`).

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
| In-chat rendering | ✅ Built (v0.3.1) — stage sections, governance tags, retry blockquote, plan.md anchor | — |
| Tasks sidebar view | ✅ Built (v0.4.0) — TreeDataProvider, Active + History sections, click-to-open artifacts | — |
| Level decision for feature-dev | ⚠️ Probe built, not run (Week 4 Day 5) | Run 5 representative requests through both pipelines |
| Context Management | ⚠️ Missing | Week 5: sub agents (main-context preservation) |

---

## Build Roadmap

Moved to [`docs/roadmap.md`](./roadmap.md). Status churn was forcing
this file (architecture + schemas) to recompile on every weekly delta.
Pipeline-specific roadmaps still live alongside their pipeline (e.g.
`.github/pipelines/feature-dev/ROADMAP.md`).

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

## Best Practices Compliance

Cross-checked against the harness best-practices reference cited in
§ Resources (celesteanders/harness `docs/best-practices.md`). Each row
is one of the 30 numbered practices from that doc.

| # | Practice | Status | Where enforced |
|---|---|---|---|
| **1. Architecture** | | | |
| 1 | Specialized agents (Planner / Generator / Evaluator) | ✅ | feature-dev: planner→designer→coder→reviewer; reviewer = evaluator |
| 2 | Start single-agent before multi-agent | ⚠️ | Built Level-2 first; Level-1 probe infra exists, run pending (Wk4 D5) |
| 3 | Fresh context windows + structured handoff | ⚠️ | Evaluator gets fresh session (Wk3a). Handoff schemas still TODO (conditional on probe) |
| **2. State & Persistence** | | | |
| 4 | All state in structured files | ✅ | `.harness/sessions/<sid>/<stage>.md` + SQLite audit (`storage/audit.db`) |
| 5 | Repo as system of record | ✅ | Skills, agents, instructions, memory all in-repo under `.github/` |
| 6 | Top-level instruction files ~100 lines | ✅ | AGENTS.md ≤120 lines (this file = design doc, not entry point) |
| **3. Session Protocol** | | | |
| 7 | Orient → setup → verify → task → impl → test → update → exit | ✅ | SessionStart hook + baseline_checks + correction loop + plan.md persist |
| 8 | One task per session | ✅ | Pipeline = one request; `harness_get_active_session` enforces single live session |
| 9 | Verify existing functionality first | ✅ | `baseline_checks` in pipeline.yaml run before generator |
| **4. Feedback Loops** | | | |
| 10 | Lint / typecheck / tests in feedback loop | ✅ | `harness_run_lint`, `harness_run_typecheck`, `harness_run_tests` (ruff/mypy/pytest) |
| 11 | UI/browser automation for feature validation | ❌ | Out of scope — harness is for code workflows, not app UI testing |
| 12 | Concrete gradable evaluator criteria | ✅ | code-review SKILL.md checklist + `review-criteria.json` schema |
| **5. Context Window** | | | |
| 13 | Treat context as scarce, offload to subagents | ⚠️ | Phase A.1 + A.2 + A.3 Python ✅ (storage + lifecycle + policy + firewall + verifier + 6 MCP tools + role .agent.md / SKILL.md + durable audit). A.3 TS-side chat markers + Phase B pipeline-main spawning still pending. |
| 14 | Deterministically load core files each loop | ✅ | `harness_read_stage` pushes Tier-1 memory + skill on every read |
| 15 | Subagents for parallel reads / summarization | ⚠️ | Phase A complete on the Python side. Phase B (orchestrator + pipeline-main spawning) still pending. |
| **6. Prompt Engineering** | | | |
| 16 | Prohibit placeholders; require complete code | ✅ | reviewer.agent.md rejects placeholders; verifier scans for TODO/FIXME stubs |
| 17 | Document reasoning in code comments | ⚠️ | Coder skill encourages it; not mechanically enforced |
| 18 | Agents improve their own instructions | ✅ | skill-builder meta-agent writes proposals to `.github/agents/proposed/` |
| 19 | Capture bugs immediately in task list | ✅ | session_distiller appends to `failure-patterns.md` (auto-compacted) |
| **7. Security** | | | |
| 20 | Three-layer defense (sandbox / FS / allowlist) | ⚠️ | Layer 3 ✅ (PIPELINE_POLICIES fail-closed). Layers 1–2 inherited from VS Code/OS |
| **8. Code Quality** | | | |
| 21 | Architectural invariants enforced mechanically | ✅ | `policy_engine.py` + PreToolUse hook; injection scan in verifier |
| 22 | Incremental tech debt cleanup | ⚠️ | No recurring cleanup pipeline yet — manual via roadmap |
| 23 | Boring, stable tech for agent reasoning | ✅ | Python + TypeScript + JSON-RPC stdio; no exotic deps |
| 24 | Inspectable via logs / observability | ✅ | CopilotHarness output channel + SQLite audit log + per-session `.md` artifacts |
| **9. Recovery & Resilience** | | | |
| 25 | Descriptive git commits as recovery; read history at start | ⚠️ | Crash recovery via `harness_get_active_session` ✅; git-history read at start ❌ |
| 26 | Plan for failures; reset to known-good state | ✅ | Append-only stage store + `harness_increment_attempt` retry; max-3 escalate |
| 27 | Periodically regenerate plans against spec | ❌ | Not built — single plan per session today |
| **10. Evolving the Harness** | | | |
| 28 | Strip scaffolding after model upgrades | ⚠️ | Wk3a Level-1 probe is exactly this exercise — not yet run |
| 29 | Calibrate evaluator involvement to task difficulty | ⚠️ | Levels 0/1/2 defined; promotion checklist exists; calibration data pending probe |
| 30 | Increase complexity only when ceilings force it | ✅ | Promotion rule: 3+ observed failures before adding an agent (§ Promotion Checklist) |

**Legend:** ✅ done · ⚠️ partial / planned · ❌ out of scope or not yet built.

**Open compliance gaps worth tracking** (each is already in § Known TODOs
or [`docs/roadmap.md`](./roadmap.md) — listed here so the alignment is
auditable, not duplicated):

- BP 2, 3, 28, 29 — **Level-1 probe** (Wk4 D5 deferred). Running it
  unblocks the multi-vs-single-agent calibration.
- BP 13, 15 — **Sub agents** (Week 5 main feature). Phase A→B→C plan
  already specced.
- BP 25 (history half) — **Read git log at SessionStart**. Cheap to
  add to `scripts/session_start.py`; not yet wired.
- BP 27 — **Plan regeneration**. Defer until a real session shows
  spec-vs-code drift. Promotion rule applies (3+ observed cases).

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

WEEK 5+ — Orchestrator Pivot (active plan, supersedes prior Week 5 / Week 6):
  See [`docs/roadmap.md`](./roadmap.md) → "Week 5+ — Orchestrator Pivot" for the canonical plan.
  5 phases (A → E) covering: sub-agent primitives, orchestrator core,
  conversation continuity, routing pivot + deletions, documentation.
  6 of 30 best-practice rows improve. Direct mode and planned Agent Mode
  are deleted (superseded). Memory contract: docs/memory.md.

DEFERRED (needs discussion first):
  - Model-invoked skill loading **inside pipeline mode** (agent decides
    which skill to load during plan/design/code). Breaks the
    "harness pushes, agent cannot opt out" invariant that the evaluator
    firewall and stage-specific injection depend on. (Direct-mode pull
    is deleted by the orchestrator pivot — see [`docs/roadmap.md`](./roadmap.md) Phase D.)

  - Sub agents reading memory. Default is *no memory for sub agents*.
    Opt-in per role via `memory: [<entry>]` in the role file only after
    3+ observed failures of the same pattern. Matches the promotion rule.

  - User-invokable slash commands for sub agent roles (e.g. `/explore`).
    Dropped: sub agents exist to preserve a *main* agent's context; when
    the user is the caller, just talk to the orchestrator and let it spawn
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

*Updated: May 2026*
*Project: CopilotHarness*
*Repo: https://github.com/Eurus7895/CopilotHarness*
*Runtime: Extension mode (v0.4.0) — @harness in Copilot Chat + Tasks sidebar TreeView*
*Current: Week 4 + Phase A.1 + A.2 + A.3 (Python) complete — sub-agent
storage + lifecycle + policy + firewall + verifier + role files +
SKILL.md + durable audit log; 6 MCP tools (spawn / complete / await /
list / get_context / query_events) wired with four-layer timeouts,
runner-side cap enforcement, and durable per-spawn / per-completion
audit rows. 507 tests (was 370 — +71 A.1, +46 A.2, +20 A.3).*
*Next: Phase A.3 extension-side TS work — mcpClient EventEmitter +
subagentRendering.ts chat markers (replaces audit polling). Then Phase
B (orchestrator + pipeline-main spawning).*
*Planned: Week 5+ Orchestrator Pivot — see [`docs/roadmap.md`](./roadmap.md). 5 phases A→E. Two modes after pivot: pipeline + orchestrator. Direct mode + planned Agent Mode deleted.*
