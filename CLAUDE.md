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
What CopilotHarness does:  state, context firewall, skill injection,
                           validation, execution, correction loop
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
action needed. The extension drives all 5 agents automatically via `vscode.lm.sendRequest`.

---

## How It Works

```
User types "@harness add a login endpoint" in Copilot Chat
    ↓
VS Code activates copilot-harness-extension (onStartupFinished)
    ↓
extension.ts spawns bin/copilot-harness.exe via McpClient (JSON-RPC stdio)
    ↓
pipeline.ts drives 5 agents automatically:

    McpClient.callTool("harness_get_active_session")
        → crash recovery: resume interrupted session or start fresh
    ↓
    McpClient.callTool("harness_new_session", { request })
        → harness creates session, locks agent versions, returns session_id
    ↓
    For each agent (planner → designer → coder → reviewer):
        McpClient.callTool("harness_read_stage", { session_id, stage, agent_name })
            → context firewall enforced, skills auto-injected
        ↓
        vscode.lm.sendRequest(copilot, agentPrompt + context)
            → Copilot reasons, returns JSON output
        ↓
        McpClient.callTool("harness_write_stage", { session_id, stage, output })
            → injection scan → schema check → append-only store
    ↓
    Reviewer "fail" → correction loop (max 3 retries) → escalate
```

---

## The Two Layers

### Layer 1 — Copilot Native Files (loaded automatically by VS Code)

```
.github/
    AGENTS.md                            ← P1: global always-on rules
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

    agents/                              ← WHO does the work
        planner.agent.md
        designer.agent.md
        coder.agent.md
        reviewer.agent.md
        skill-builder.agent.md
        proposed/                        ← Skill-Builder writes here, human approves

    skills/                              ← HOW to do specific jobs (on demand)
        code-review/    SKILL.md + assets/ + references/
        api-design/     SKILL.md + assets/ + references/
        database-patterns/  SKILL.md + assets/ + references/
        python/         SKILL.md + assets/ + references/
        testing/        SKILL.md + assets/ + references/
        documentation/  SKILL.md + assets/ + references/
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
    correction_loop.py ← reviewer → coder retry (max 3)           ✅ built
    skill_loader.py    ← serves SKILL.md + references             ✅ built
    memory/
        cross_session.db
        pattern_detector.py                                        [Day 5]
    storage/
        db.py          ← SQLite CRUD; schema embedded as string (no file dep);
                          DB path = $HARNESS_ROOT/data/copilot_harness.db when
                          running as extension binary, else alongside db.py (dev)
        schema.sql     ← reference copy (not read at runtime — embedded in db.py)
    tests/
        test_state.py
        test_context_builder.py
        test_verifier.py         ✅ built
        test_correction_loop.py  ✅ built
        test_executor.py         ✅ built
    pyproject.toml
    README.md
    CLAUDE.md

.vscode/
    mcp.json           ← { "command": "copilot-harness", "args": ["serve"] }
                          portable — works in any project once the CLI is installed
```

**VS Code Extension (✅ built, v0.2.0):**
```
copilot-harness-extension/   ← VS Code extension (TypeScript)
    src/
        extension.ts         ← activates on VS Code start (onStartupFinished)
                               spawns bin/copilot-harness.exe via McpClient
                               registers @harness chat participant
        mcpClient.ts         ← minimal MCP stdio client (newline JSON-RPC)
                               McpClient.create() → listTools() → callTool()
                               direct child process — no VS Code MCP panel needed
        pipeline.ts          ← drives 5 agents via McpClient.callTool() +
                               vscode.lm.sendRequest(); correction loop (max 3)
                               NO vscode.lm.invokeTool() — calls server directly
    bin/
        copilot-harness.exe  ← PyInstaller one-file binary (Windows)
        copilot-harness      ← PyInstaller one-file binary (Linux/Mac)
        launch.js            ← cross-platform launcher (picks .exe on Windows)
    package.json             ← VS Code ^1.93.0, activationEvents: onStartupFinished
    tsconfig.json
```

---

## Key Distinction: instructions vs skills

```
instructions/   = RULES AND STANDARDS (always loaded, priority-ranked)
                  → P1 universal > P2 org > P3 domain > P4 project
                  → P1 can never be overridden

skills/         = PROCEDURES AND KNOWLEDGE (injected by harness or loaded on demand)
                  → auto-injected: harness_read_stage pushes skills per STAGE_SKILL_MAP
                  → on demand: agents call harness_get_skill / harness_get_reference
                  → assets/ run by executor.py only, never by agent directly
```

---

## Skill Injection — Skills Are Pushed, Not Pulled

Copilot can decide "I don't need that skill." The harness prevents this by
injecting skill content directly into `harness_read_stage` responses.

```
STAGE_SKILL_MAP (in server.py):
    ("plan",   "designer")  → api-design injected
    ("design", "coder")     → python injected
    ("code",   "reviewer")  → code-review injected  ← reviewer ALWAYS gets this

Agent cannot opt out. Skill content is part of the tool response.
Agent loads additional references on demand via harness_get_reference().
```

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
    → auto-injects skills from STAGE_SKILL_MAP
    → returns { data, injected_skills? }

harness_write_stage(session_id, stage, output, agent_name)
    → scan_injection() + verifier.validate() (schema + secrets + contracts)
    → state.write_stage()
    → returns { status: "stored" | "error" }

harness_get_status(session_id)
    → state.get_status()
    → returns pipeline stage summary

harness_increment_attempt(session_id, stage)
    → state.increment_attempt() — creates new attempt row
    → used by Phase 2 extension correction loop before retry writes
    → returns { status: "incremented", attempt: N }
```

### Skill Tools
```
harness_get_skill(skill_id)
    → skill_loader.get_skill() → reads .github/skills/{id}/SKILL.md
    → use for skills not in STAGE_SKILL_MAP

harness_get_reference(skill_id, reference_name)
    → skill_loader.get_reference() → reads .github/skills/{id}/references/{name}
    → load only when needed (OWASP, patterns, etc.)
```

### Execution Tools ✅ built (Day 4)
```
harness_run_lint(files)       → executor.run_lint()      → LintResult (ruff JSON)
harness_run_typecheck(files)  → executor.run_typecheck()  → TypeCheckResult (mypy)
harness_run_tests(test_dir)   → executor.run_tests()      → RunResult (pytest -v)
```

---

## The 5-Agent Team

| Agent | Reads via harness | Writes via harness | Auto-injected skill |
|---|---|---|---|
| Planner | request only | session.plan | none |
| Designer | plan | session.design | api-design |
| Coder | plan + design / fix_instructions on retry | session.code | python |
| Reviewer | plan + design + code | session.review | code-review (always) |
| Skill-Builder | fail patterns only | .github/agents/proposed/ | none |

---

## Context Firewall (context_builder.py)

Two functions:

```python
build_context(session_id, agent_name) → dict
    # Full allowed context for an agent in one call.
    # Used internally by server.py for multi-stage agents.

read_stage_for_agent(session_id, stage, agent_name) → dict | None
    # Per-stage access with permission check.
    # Used by harness_read_stage MCP tool.
    # Returns None if agent not permitted to read that stage.
    # Coder reading "review" → fix_instructions only (not full review JSON).
```

---

## Harness Engineering — Full Audit

### 1. Tool Design ⚠️ Partial

**What we have:** `tools` field in `.agent.md` enforced by VS Code natively.
Tool boundary reminders injected into every prompt via `.agent.md` Behavior Rules.

**What's missing:** No runtime interception of tool calls — needs Phase 2 extension.

**TODO:**
```
Phase 1:
[ ] Add tool_calls table to schema.sql — developer notes tool calls on submit
[ ] Reminder text in every .agent.md: "You only have access to: [tools listed above]"

Phase 2:
[ ] Intercept tool calls via VS Code extension API
[ ] Auto-log every tool call to tool_calls table
```

### 2. Feedback Loops ✅ Designed

**Feedback schema (Reviewer → Coder):**
```json
{
    "status": "pass | fail | escalate",
    "attempt": 1,
    "issues": [{
        "severity": "critical | high | medium | low",
        "description": "string",
        "fix_instruction": "string — specific, actionable"
    }],
    "escalate_reason": null
}
```

**Loop:** `harness_write_stage("review")` → `correction_loop.py` →
retry Coder (max 3) → escalate with full context.

**TODO:**
```
Day 3:
[ ] Add "wrong_plan" status to feedback schema
    → correction_loop escalates back to Planner stage, not Coder retry
```

### 3. State Management ✅ Built (Day 2 + crash recovery)

**Session schema:**
```json
{
    "session_id": "abc123",
    "request": "add a login endpoint",
    "locked_agent_versions": { "planner": "1.0.0", ... },
    "stages": {
        "plan":   { "status": "complete", "output": {} },
        "code":   { "status": "in_progress", "attempt": 2,
                    "attempt_1": { "output": {} },
                    "output": null }
    }
}
```

**Rules:** pending → in_progress → complete. Write-once per attempt.
`read_stage` returns latest written output — reviewer sees attempt-1 code
even after attempt counter increments.

**Crash recovery (built):**
- `active_session` table — singleton row always pointing to most recent active session
- `harness_get_active_session()` — returns `{ session_id, request, resume_stage, attempt }`
- `harness_read_stage` auto-marks agent's output stage `in_progress` before returning —
  crash between read and write is detectable on resume
- All 5 `.agent.md` Input Contracts start with `harness_get_active_session()` check

**TODO:**
```
Week 2:
[ ] Context compaction — summarise earlier attempts if retry count > 2
[ ] Cross-session memory injection (memory_loader.py)
```

### 4. Multi-Agent Coordination ❌ Missing

**What's missing:**
- No formal handoff schemas between stages
- No shared vocabulary / glossary
- No cross-stage contract validation (coder references files designer never declared)

**TODO:**
```
Day 3:
[ ] Define handoff schemas: plan→design, design→code, code→review
[ ] Add shared_context block to session state
    { "language": "python", "framework": "...", "conventions_summary": "..." }
    → injected into every harness_read_stage response
[ ] Cross-stage contract validation in verifier.py
    → design output must reference all task IDs from plan
    → code output must only modify files declared in design
```

### 5. Security & Permissions ✅ Partial

**What we have:**
- Layer 1: `tools` field in `.agent.md` (VS Code enforces)
- Layer 2: `scan_injection()` on every `harness_write_stage` call
- Layer 3: `validate_skill_builder_write()` path guard (proposed/ only)

**TODO:**
```
Day 3:
[ ] secrets scan in verifier.py (API keys, tokens, private keys)
[ ] scan_injection() on user request in harness_new_session
[ ] proposed_change_validator() — validate Skill-Builder patches
    before they can be applied
```

### 6. Verification ✅ Designed

```
server.py (injection scan):
    scan_injection() on every harness_write_stage

verifier.py (structural, ✅ built):
    schema validation per agent output
    secrets scan (AWS keys, GitHub tokens, private keys, API keys, bearer tokens)
    cross-stage contract validation (design references plan task IDs,
    code only modifies files declared in design)

reviewer.agent.md (domain, Copilot Chat):
    structured checklist — all criteria, architecture match, security,
    error handling, no unused code
    checklist_results required in output schema

executor.py (execution, Day 4):
    ruff check → mypy → pytest
    fail → correction_loop sends errors as fix_instructions
```

### 7. Architecture Enforcement ✅ Designed

```
locked_agent_versions at session start → immutable that session
Skill-Builder → proposed/ only, validate_skill_builder_write() enforces
All stage reads go through context_builder.read_stage_for_agent()
All stage writes go through harness_write_stage validation
```

**TODO:**
```
Day 5:
[ ] proposed_patch_applier.py — validates + applies + archives patches
[ ] .github/agents/archive/ — version history for rollback
```

### 8. Memory Architecture ❌ Missing

Three tiers needed:
```
Tier 1 — MEMORY.md (~200 tokens, always loaded)
    Pointers to where knowledge lives.

Tier 2 — .github/memory/*.md (loaded on demand)
    Distilled decisions: "We use SQLite not PostgreSQL because X"
    Past failure patterns: "Coder always misses error handling on DB calls"

Tier 3 — storage/sessions/ (raw history, never auto-loaded)
    Full session transcripts.
```

**TODO:**
```
Week 2:
[ ] .github/memory/ folder + MEMORY.md (Tier 1)
[ ] memory_loader.py — inject Tier 2 into harness_read_stage responses
[ ] session_distiller.py — convert completed sessions to Tier 2 entries
```

---

## Harness Audit Summary

| Component | Status | Priority |
|---|---|---|
| Tool Design | ⚠️ Prompt-level only (Phase 1) | Phase 2 automates this |
| Feedback Loops | ✅ Built (correction_loop.py + extension loop) | wrong_plan path (Day 5) |
| State Management | ✅ Built + crash recovery | cross-session memory (Week 2) |
| Multi-Agent Coordination | ❌ Missing | handoff schemas (Day 4) |
| Security & Permissions | ✅ Built (injection scan + verifier secrets) | proposed_change_validator (Day 5) |
| Verification | ✅ Built (verifier.py + executor.py) | — |
| Architecture Enforcement | ✅ Designed | patch applier (Day 5) |
| Memory Architecture | ❌ Missing | 3-tier memory (Week 2) |
| Extension (@harness) | ✅ Built (McpClient + direct callTool, no invokeTool) | — |

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
pattern_detector.py    ❌     SQLite count threshold          [Day 5]
extension/mcpClient.ts ❌     JSON-RPC stdio child process        ✅
extension/pipeline.ts  ❌     McpClient.callTool + sendRequest    ✅
extension/extension.ts ❌     @harness chat participant           ✅

Copilot Chat (VS Code) ✅     agent reasoning in Phase 1
vscode.lm API          ✅     agent reasoning in Phase 2
```

---

## Build Roadmap

### Day 1 — Native Copilot Files ✅ Complete
```
[✅] AGENTS.md
[✅] .github/copilot-instructions.md
[✅] .github/instructions/ — all 4 priority levels
[✅] .github/agents/ — all 5 .agent.md files (Input Contracts updated for MCP)
[✅] .github/skills/ — 6 skills with SKILL.md + assets + references
[✅] .github/agents/proposed/
```

### Day 2 — State + Context Firewall + MCP Server + Crash Recovery ✅ Complete
```
[✅] storage/schema.sql — sessions, stage_outputs, agent_versions, fail_patterns,
                          active_session (singleton crash recovery pointer)
[✅] storage/db.py — SQLite WAL, parameterized queries, context managers,
                     get_active_session_id / set_active_session_id
[✅] state.py — create_session (auto-sets active pointer), lock_agent_versions,
                write_stage (write-once), read_stage (latest written output),
                increment_attempt, resume, mark_in_progress,
                get_active_session() → { session_id, request, resume_stage, attempt }
[✅] context_builder.py — build_context() → dict, read_stage_for_agent() → dict,
                          scan_injection(), validate_skill_builder_write()
[✅] server.py — FastMCP stdio server
                 harness_get_active_session (crash recovery),
                 harness_new_session, harness_read_stage (+ skill injection
                 + auto-mark in_progress), harness_write_stage (+ verifier),
                 harness_get_status, harness_increment_attempt,
                 harness_get_skill, harness_get_reference
                 stub: harness_run_lint, harness_run_typecheck, harness_run_tests
[✅] All 5 .agent.md Input Contracts — Step 1: harness_get_active_session() resume check
[✅] cli.py — copilot-harness serve
[✅] .vscode/mcp.json — copilot-harness serve (portable CLI)
[✅] pyproject.toml — mcp>=1.0.0 dependency
[✅] tests/test_state.py + test_context_builder.py — 78 tests passing
```

### Day 3 — Verification + Correction Loop + Skill Loader ✅ Complete
```
[✅] verifier.py
      validate(output, agent_name) → ValidationResult
      _check_schema(output, schema) → list[SchemaError]
      _scan_secrets(text) → list[SecretMatch]   (6 patterns: AWS, GH token, private key,
                                                  generic API key, bearer token)
      OUTPUT_SCHEMAS: dict[str, dict]  ← one JSON schema per agent
      Wired into harness_write_stage in server.py

[✅] correction_loop.py
      run(session_id, review_output) → LoopResult
      get_attempt_count(session_id) → int
      build_retry_context(session_id) → fix_instructions only
      escalate(session_id) → EscalationMessage

[✅] skill_loader.py
      get_skill(skill_id) → SKILL.md content
      get_reference(skill_id, ref_name) → reference content
      list_skills() → list[SkillMeta]
      Wired into server.py harness_get_skill / harness_get_reference

[✅] Cross-stage contract validation in verifier.py
      design output must reference all plan task IDs
      code output must only modify files declared in design

[✅] tests/test_verifier.py + test_correction_loop.py
```

### Day 4 — Executor ✅ Complete
```
[✅] executor.py
      run_lint(files)       → LintResult       (ruff --output-format=json)
      run_typecheck(files)  → TypeCheckResult  (mypy)
      run_tests(test_dir)   → RunResult        (pytest -v --tb=short)
      run_all(files, dir)   → ExecutionResult  (lint → typecheck → tests, early exit)
      30s timeout per subprocess; FileNotFoundError + TimeoutExpired handled
      Wired into harness_run_lint / harness_run_typecheck / harness_run_tests

[✅] tests/test_executor.py — 31 tests
      Parser tests: _parse_ruff_output, _parse_mypy_output, _parse_pytest_output
      Integration: run_lint detects F401, run_typecheck detects type errors,
                   run_tests passes/fails, run_all stops early on lint failure
```

### Day 5 — Self-Improvement Loop + Packaging
```
[ ] memory/pattern_detector.py
      record_failure(session_id, agent_name, issue)
      detect_patterns(agent_name) → list[Pattern]
      trigger_skill_builder(pattern) → writes proposed/*.patch.md
[ ] Wire pattern_detector into correction_loop.py
[ ] proposed_patch_applier.py
      validates + applies + archives Skill-Builder patches
[ ] pyproject.toml — finalise entry point copilot-harness = "cli:main"

Test:
  3 sessions same coder failure → proposed/coder.patch.md created
  direct write to .github/agents/ blocked by validate_skill_builder_write()
  patch applier blocks non-Behavior-Rules changes
```

### Week 2 — Hardening
```
[ ] Full unit test suite (all components)
[ ] .github/memory/ — Tier 1 + Tier 2 memory files
[ ] memory_loader.py — inject into harness_read_stage responses
[ ] session_distiller.py — converts sessions to memory entries
[ ] README.md — team setup in < 5 minutes
[ ] Edge cases: malformed JSON output, empty agent response, executor timeout
```

### VS Code Extension ✅ Complete (v0.2.0)

```
[✅] src/mcpClient.ts
      McpClient.create(binary, args, env) — spawns server as child process
      Newline-delimited JSON-RPC 2.0 over stdio (no VS Code MCP panel needed)
      listTools() → McpToolDef[]
      callTool(name, args) → string (raw text from MCP content array)

[✅] src/extension.ts
      activationEvents: onStartupFinished — activates at VS Code start
      Spawns bin/copilot-harness[.exe] via McpClient with HARNESS_ROOT env
      Registers @harness chat participant (id: copilot-harness.harness)
      Output channel "CopilotHarness" auto-shown on activation for diagnostics

[✅] src/pipeline.ts
      runPipeline(client, request, workspaceRoot, stream, token) → PipelineResult
      callHarness(client, toolName, args) — calls McpClient.callTool() directly
        NO vscode.lm.invokeTool() — avoids VS Code tool registry entirely
      Per-agent: harness_read_stage → vscode.lm.sendRequest(copilot) → harness_write_stage
      Crash recovery: harness_get_active_session() → skip complete stages
      Correction loop: reviewer "fail" → harness_increment_attempt (code + review)
                       → coder retry with fix_instructions → reviewer re-run (max 3)

[✅] storage/db.py — schema embedded as string; DB path uses HARNESS_ROOT env var
                     fixes PyInstaller temp-dir issue (DB was recreated each run)

[✅] package.json — VS Code ^1.93.0, activationEvents: onStartupFinished
[✅] tsconfig.json — CommonJS / ES2022 / strict
[✅] .vscode/mcp.json — dev mode only: python server.py for manual Copilot Chat use
[✅] copilot-harness.spec — PyInstaller one-file build (Windows .exe + Linux binary)
```

---

## Testing Checklist

```
state.py: ✅
[✅] Completed stage cannot be overwritten
[✅] locked_agent_versions captured at session start
[✅] attempt counter increments, previous attempt preserved
[✅] resume() returns correct last incomplete stage
[✅] read_stage returns latest WRITTEN output (not latest attempt row)
[✅] create_session sets active_session pointer automatically
[✅] get_active_session() returns { session_id, request, resume_stage, attempt }
[✅] get_active_session() returns None when no active session exists
[✅] latest session wins when multiple sessions created
[✅] mark_in_progress() transitions pending → in_progress (idempotent)

context_builder.py: ✅
[✅] Planner context: request only, zero stage outputs
[✅] Designer context: plan only, no request text
[✅] Coder retry: fix_instructions only, not full review JSON
[✅] Skill-Builder: no session state, no user code
[✅] scan_injection catches "ignore your instructions" and variants
[✅] validate_skill_builder_write blocks path traversal

server.py / MCP tools: ⬜
[ ] harness_get_active_session → returns active session or null
[ ] harness_new_session → session created, versions locked
[ ] harness_read_stage → firewall enforced, skills auto-injected, stage marked in_progress
[ ] harness_write_stage → injection detected → rejected
[ ] harness_get_skill → SKILL.md content returned
[ ] harness_get_reference → reference content returned

verifier.py: ⬜ (Day 3)
[ ] Missing required field → ValidationResult.failed = True
[ ] API key pattern → secret detected
[ ] Valid output → all checks pass

correction_loop.py: ⬜ (Day 3)
[ ] Max 3 attempts enforced
[ ] Attempt 4 → escalation with session_id + all issues

executor.py: ⬜ (Day 4)
[ ] ruff error → structured LintResult
[ ] pytest failure → TestResult with test name + reason
[ ] Clean code → ExecutionResult.passed = True
```

---

## Known TODOs

```
DAY 3:
  TODO: Define output JSON schema per agent — drives verifier.py validation
  TODO: Secrets regex patterns — AWS keys, GitHub tokens, generic API key patterns
  TODO: Handoff schema validation (plan→design, design→code, code→review)

DAY 4:
  TODO: executor.py — use files_modified from coder output for targeted lint
  TODO: subprocess timeout per call — 30s default

WEEK 2:
  TODO: MEMORY.md format — what goes in Tier 1 index vs Tier 2 files
  TODO: session_distiller.py — what to extract and how to keep Tier 2 < 500 tokens
  TODO: Team onboarding — new developer setup in < 5 minutes
```

---

## Resources

- .agent.md format: https://learn.microsoft.com/en-us/visualstudio/ide/copilot-specialized-agents
- FastMCP docs: https://gofastmcp.com
- VS Code Language Model API (Phase 2): https://code.visualstudio.com/api/extension-guides/language-model
- awesome-copilot: https://github.com/github/awesome-copilot
- Harness Engineering: https://mitchellh.com/writing/harness-engineering
- SWE-agent paper (Princeton NeurIPS 2024): https://arxiv.org/abs/2405.15793
- OWASP Top 10 Agentic AI: https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/

---

*Updated: April 2026*
*Project: CopilotHarness*
*Repo: https://github.com/Eurus7895/CopilotHarness*
*Runtime: Extension mode (v0.2.0) — @harness in Copilot Chat drives pipeline automatically*
*         Dev mode — .vscode/mcp.json + python server.py for manual agent use*
*Current milestone: Extension fully working (McpClient direct, DB path fixed) — Day 5 next (pattern detector, self-improvement loop)*
