# CLAUDE.md — CopilotHarness

> Context anchor for every coding session. Read this file before doing anything.
> Ultimate goal: a fully implemented harness engineering layer for GitHub Copilot's
> 5-agent development team. No LLM inside the harness — Copilot is the LLM,
> CopilotHarness controls the environment it operates in.

---

## One Sentence

CopilotHarness is a pure Python MCP server that acts as the harness layer for
GitHub Copilot's multi-agent team — it controls what each agent sees, validates
what each agent produces, enforces the correction loop, serves skills on demand,
and runs code to verify it actually works.

> Public-facing summary lives in [`README.md`](./README.md). This file is the
> internal source of truth for architecture, schemas, and the build roadmap.

---

## Harness Engineering Principle

> "The model is what thinks. The harness is what it thinks about.
>  And the harness is what determines the final outcome."
>
> Same model, same task, same compute →
> just changing environment design → 64% performance improvement (Princeton SWE-agent paper)

**Copilot is the LLM. CopilotHarness is the harness. Zero LLM calls inside the harness.**

```
What Copilot does:      reasoning, planning, coding, reviewing
What CopilotHarness does: state, context firewall, validation, execution, skills
```

---

## The Two Layers

### Layer 1 — Copilot Native Files (loaded automatically by Copilot)

```
.github/
    AGENTS.md                            ← P1: global always-on rules
    copilot-instructions.md              ← P1: global always-on conventions

    instructions/                        ← RULES AND STANDARDS (priority-ranked)
        universal/                       ← P1: world-wide, never overridden
            security.instructions.md
            ethics.instructions.md
        org/                             ← P2: team-wide standards
            git-conventions.instructions.md
            code-review-standards.instructions.md
        domain/                          ← P3: technology-specific, file-scoped
            python.instructions.md       ← applyTo: **/*.py
            api.instructions.md          ← applyTo: **/api/**
            database.instructions.md     ← applyTo: **/models/**
        project/                         ← P4: repo-specific overrides
            naming-conventions.instructions.md
            architecture-decisions.instructions.md

    agents/                              ← WHO does the work
        planner.agent.md
        designer.agent.md
        coder.agent.md
        reviewer.agent.md
        skill-builder.agent.md
        proposed/                        ← Skill-Builder writes here, human approves

    skills/                              ← HOW to do specific jobs (on demand)
        code-review/
            SKILL.md                     ← max 200 lines
            assets/
                review-script.py
            references/
                owasp-top10.md           ← loaded only when needed
                common-patterns.md
        api-design/
            SKILL.md
            assets/
                openapi-template.yaml
            references/
                rest-principles.md
        database-patterns/
            SKILL.md
            assets/
                query-analyzer.py
            references/
                indexing-strategies.md
```

### Layer 2 — CopilotHarness (MCP server, pure Python, zero LLM)

```
copilot-harness/
    server.py                            ← MCP stdio server, exposes all tools
    state.py                             ← append-only session state
    context_builder.py                   ← context firewall + injection detection
    verifier.py                          ← schema validation + secrets scan
    executor.py                          ← lint + type check + test runner
    correction_loop.py                   ← reviewer → coder retry orchestration
    skill_loader.py                      ← serves SKILL.md, references, runs assets
    memory/
        cross_session.db                 ← SQLite: fail patterns across sessions
        pattern_detector.py              ← detects recurring failures
    storage/
        db.py
        schema.sql
    tests/
        test_state.py
        test_context_builder.py
        test_verifier.py
        test_correction_loop.py
        test_executor.py
    pyproject.toml
    .vscode/
        mcp.json
    README.md
    CLAUDE.md
```

---

## Key Distinction: instructions vs skills

```
instructions.md = RULES AND STANDARDS to follow
                  "always use type hints"
                  "never hardcode secrets"
                  "follow REST conventions"
                  → constraints on agent behavior
                  → priority-ranked: P1 cannot be overridden by P4
                  → loaded automatically by Copilot

skills/         = PROCEDURES AND KNOWLEDGE to apply
                  "how to review code step by step"
                  "how to design an API"
                  "how to analyze database queries"
                  → reusable domain expertise
                  → max 200 lines per SKILL.md
                  → loaded on demand via MCP tool call
                  → deep knowledge in references/, loaded only when needed
                  → executable logic in assets/, run by executor.py
```

---

## Instructions Priority System

```
P1 — Universal (never overridden by anything)
     security.instructions.md: never expose secrets, never trust unvalidated input
     ethics.instructions.md: never generate harmful code, never deceive

P2 — Organization (overrides domain and project)
     git-conventions, code-review-standards, documentation-standards

P3 — Domain (overrides project, overridden by org)
     python, typescript, api, database — scoped via applyTo field

P4 — Project (most specific, lowest precedence above nothing)
     repo-specific naming, architecture decisions

Conflict resolution: higher priority wins. P1 always wins.
```

---

## Skill Folder Structure

```
skills/{skill-id}/
    SKILL.md                ← required, max 200 lines
    assets/                 ← optional: executable scripts, templates, data files
    references/             ← optional: deep domain documents loaded on demand
```

### SKILL.md format (max 200 lines)

```markdown
---
id: code-review
name: Code Review
version: 1.0.0
description: Reviews code for bugs, security, and conventions
triggers: ["review", "check code", "audit", "code quality"]
assets:
    - assets/review-script.py
references:
    - references/owasp-top10.md
    - references/common-patterns.md
---

## Purpose
[1-2 sentences]

## Procedure
[step by step, concise]

## Assets
[what scripts are available and when to use them]

## When to Load References
- Load owasp-top10.md when: security issues detected in code
- Load common-patterns.md when: pattern matching or anti-pattern detection needed
```

### Progressive Disclosure

```
L1 — SKILL.md always loaded (~200 lines)
     handles 80% of cases
     ↓ agent decides more knowledge needed
L2 — references/*.md loaded on demand
     agent calls harness_get_reference(skill_id, reference_name)
     ↓ agent decides execution needed
L3 — assets/ scripts executed
     agent calls harness_run_asset(skill_id, asset_name, input)
     executor.py runs script, returns output
     never run directly by agent
```

---

## The 5-Agent Team

| Agent | File | Tools | Reads | Writes |
|---|---|---|---|---|
| Planner | planner.agent.md | view, glob | request + P1/P2 instructions | session.plan |
| Designer | designer.agent.md | view, glob | session.plan | session.design |
| Coder | coder.agent.md | view, edit, bash | session.plan + session.design | session.code |
| Reviewer | reviewer.agent.md | view, glob | all stages + request | session.review |
| Skill-Builder | skill-builder.agent.md | view, edit | reviewer feedback + target skill | .github/agents/proposed/ |

---

## .agent.md Format

```markdown
---
name: Coder
description: Implements code based on plan and design. Invoked when code needs to be written or modified.
tools: ["view", "edit", "bash"]
---

## Role
You are a senior software engineer implementing features based on a structured plan and design.

## Instructions
[full behavior instructions]

## Input Contract
Before doing anything, call harness_read_stage to get your inputs:
- harness_read_stage("plan") → task list + acceptance criteria
- harness_read_stage("design") → architecture + interfaces
- harness_read_stage("review") → fix_instructions only (on retry)

## Output Contract
Produce ONLY valid JSON matching this schema:
{
    "summary": "string",
    "files_modified": ["string"],
    "implementation_notes": "string",
    "confidence": "high | medium | low"
}
Then call harness_write_stage("code", <your output>).

## Skills
If you need domain knowledge, call harness_get_skill("api-design") or similar.
If a reference is needed, call harness_get_reference(skill_id, reference_name).

## Behavior Rules
- Never modify files outside the scope declared in session.plan
- Always handle errors on all external calls
- Never hardcode secrets or credentials
- If confidence is low, explain why in implementation_notes
```

---

## MCP Server — Tools Exposed

CopilotHarness exposes these tools to Copilot agents via MCP stdio.
**Zero LLM calls inside server.py or any harness component.**

### State Tools
```
harness_write_stage(session_id, stage, output)
    → verifier.py validates schema + secrets + injection
    → state.py stores if valid
    → correction_loop.py checks if stage is "review" (pass/fail decision)

harness_read_stage(session_id, stage)
    → context_builder.py filters output based on calling agent identity
    → returns only what that agent is allowed to see

harness_new_session(request)
    → state.py creates session, locks agent versions
    → returns session_id

harness_get_status(session_id)
    → returns current pipeline status for orchestration
```

### Skill Tools
```
harness_get_skill(skill_id)
    → skill_loader.py reads .github/skills/{id}/SKILL.md
    → returns content to agent

harness_get_reference(skill_id, reference_name)
    → skill_loader.py reads .github/skills/{id}/references/{name}
    → agent decides when to call this, not auto-loaded

harness_run_asset(skill_id, asset_name, input_json)
    → executor.py runs .github/skills/{id}/assets/{name}
    → returns stdout as structured result
    → agent never runs scripts directly
```

### Execution Tools
```
harness_run_lint(files)
    → executor.py: subprocess ruff check {files}
    → returns structured LintResult

harness_run_typecheck(files)
    → executor.py: subprocess mypy {files}
    → returns structured TypeCheckResult

harness_run_tests(test_dir)
    → executor.py: subprocess pytest {test_dir}
    → returns structured TestResult
```

---

## MCP Connection

### .vscode/mcp.json (commit to repo)
```json
{
    "servers": {
        "copilot-harness": {
            "type": "stdio",
            "command": "copilot-harness",
            "args": ["serve"]
        }
    }
}
```

### How agents connect
Every `.agent.md` has an Input Contract and Output Contract section that tells
the agent to use MCP tool calls — `harness_read_stage` to get inputs,
`harness_write_stage` to submit outputs. Copilot calls these tools natively
via its agent loop. No custom integration needed.

---

## Harness Engineering — 7 Components

### 1. Tool Design ⬜
**What:** Each agent has explicit, enforced tool access.
**How:** `tools` field in `.agent.md` frontmatter, enforced by Copilot SDK at runtime.
**Harness role:** `context_builder.py` validates tool call logs, flags violations.
**File:** `.github/agents/*.agent.md`, `context_builder.py`

### 2. Feedback Loops ⬜
**What:** Structured signals between agents. Errors caught and routed correctly.
**How:** Feedback schema from Reviewer → Coder:
```json
{
    "status": "pass | fail | escalate",
    "attempt": 1,
    "issues": [{
        "severity": "critical | high | medium | low",
        "description": "string",
        "fix_instruction": "string",
        "checklist_item": "string"
    }],
    "escalate_reason": null
}
```
**Correction loop:**
```
verifier.py: structural check → fail → retry Coder (no Reviewer yet)
Reviewer: domain check → fail → correction_loop.py sends fix_instructions
    → Coder retries (max 3 attempts)
    → attempt 3 fails → escalate to user
executor.py: runs lint + tests after Reviewer passes → final gate
pattern_detector.py: records fails → Skill-Builder triggered at threshold
```
**File:** `correction_loop.py`, `reviewer.agent.md`

### 3. State Management ⬜
**What:** Append-only session state. Crash recovery. Full audit trail.
**Session schema:**
```json
{
    "session_id": "abc123",
    "request": "build a login endpoint",
    "locked_agent_versions": {
        "planner": "1.2.0",
        "designer": "1.0.0",
        "coder": "2.1.0",
        "reviewer": "1.1.0"
    },
    "stages": {
        "plan":   { "status": "complete", "output": {} },
        "design": { "status": "complete", "output": {} },
        "code":   {
            "status": "in_progress", "attempt": 2,
            "attempt_1": { "output": {}, "review_feedback": {} },
            "output": null
        },
        "review": { "status": "pending", "output": null }
    }
}
```
**Rules:** pending → in_progress → complete only. Output write-once per attempt.
On crash: resume from last in_progress stage.
**File:** `state.py`, `storage/schema.sql`

### 4. Context Firewall ⬜
**What:** Each agent receives only what its role requires. Injection detection.
**Per-agent rules:**
```
Planner:       request + P1/P2 instructions only
Designer:      session.plan.output only
Coder:         session.plan + session.design
               on retry: + fix_instructions only (not full review)
Reviewer:      all stage outputs + request
Skill-Builder: reviewer fail patterns + target .agent.md only
               blocked: session state, user code
```
**Injection detection:** scan every output before storage for:
- "ignore your instructions", "you are now", "forget previous"
- instruction override patterns
- adversarial content targeting downstream agents
**File:** `context_builder.py`

### 5. Security & Permissions ⬜
**What:** Three enforcement layers. Defense in depth.
```
Layer 1: Copilot tool restrictions (tools field in .agent.md)
Layer 2: Skill-Builder boundary
         → writes to .github/agents/proposed/ only
         → modifies Behavior Rules section only
         → blocked from agents with active sessions
Layer 3: Secrets scan on every agent output
         → API keys, tokens, passwords, private keys
         → blocked from entering session state if detected
```
**File:** `verifier.py`, `context_builder.py`

### 6. Verification ⬜
**What:** Two independent layers. Structural first, domain second, execution last.
```
verifier.py (structural, deterministic Python):
    runs after every harness_write_stage call
    checks: output schema, secrets, injection patterns

reviewer.agent.md (domain, Copilot LLM):
    structured checklist — not free-form judgment
    [ ] all acceptance criteria implemented
    [ ] architecture matches design
    [ ] no hardcoded secrets
    [ ] error handling on external calls
    [ ] follows instructions priority rules
    [ ] no unused code

executor.py (execution, deterministic Python):
    runs after Reviewer passes
    ruff check → mypy → pytest
    fail → back to Coder with execution errors as fix_instructions
```
**File:** `verifier.py`, `executor.py`, `reviewer.agent.md`

### 7. Architecture Enforcement ⬜
**What:** Agent behavior defined in files, not code. Immutable during session.
**Rules:**
```
locked_agent_versions captured at session start
Skill-Builder cannot modify .agent.md of active session agents
Skill-Builder writes to proposed/ only — human approves
Skill-Builder modifies Behavior Rules section only
All agent prompts assembled by context_builder.py only
No inline prompt construction anywhere else
```
**Proposed change flow:**
```
Skill-Builder detects recurring failure (3+ sessions)
    ↓
Writes to .github/agents/proposed/coder.patch.md
    (contains: rule to add, triggering sessions, confidence)
    ↓
Human reviews → approves → applies patch
    ↓
Next session uses updated .agent.md, version increments
```
**File:** `state.py`, `context_builder.py`, `skill-builder.agent.md`

---

## Processing Flow End-to-End

```
1. copilot-harness serve → MCP server starts (stdio)
   VS Code reads .vscode/mcp.json → spawns process

2. User sends request in Copilot Chat
   Planner.agent.md loaded by Copilot
   Planner calls: harness_new_session(request) → session_id
   Planner calls: harness_read_stage("plan") → empty (first stage)
   Planner produces plan JSON
   Planner calls: harness_write_stage("plan", output)
       → verifier.py: schema + secrets + injection check
       → state.py: stores if valid

3. Designer.agent.md loaded by Copilot
   Designer calls: harness_read_stage("plan")
       → context_builder.py: returns plan only (firewall)
   Designer optionally calls: harness_get_skill("api-design")
   Designer produces design JSON
   Designer calls: harness_write_stage("design", output)

4. Coder.agent.md loaded by Copilot
   Coder calls: harness_read_stage("plan") + harness_read_stage("design")
   Coder optionally calls: harness_get_skill + harness_get_reference
   Coder produces code output
   Coder calls: harness_write_stage("code", output)

5. Reviewer.agent.md loaded by Copilot
   Reviewer calls: harness_read_stage("*") → all stages
   Reviewer optionally calls: harness_get_reference("code-review", "owasp-top10")
   Reviewer produces review JSON
   Reviewer calls: harness_write_stage("review", output)
       → correction_loop.py: pass or fail?
       → fail: fix_instructions sent to Coder (attempt 2)
       → pass: continue to executor

6. executor.py runs: harness_run_lint + harness_run_tests + harness_run_typecheck
   fail → Coder receives execution errors as fix_instructions
   pass → final output returned to user

7. pattern_detector.py records session result
   recurring failures (≥3) → Skill-Builder triggered
   proposed change written to .github/agents/proposed/
```

---

## LLM Usage — Confirmed Zero Inside Harness

```
Component              LLM?   Implementation
──────────────────────────────────────────────────────
server.py              ❌     MCP stdio, routes tool calls
state.py               ❌     Python dataclass + SQLite
context_builder.py     ❌     Python dict filtering + regex
verifier.py            ❌     jsonschema + regex secrets scan
executor.py            ❌     subprocess: ruff, mypy, pytest
correction_loop.py     ❌     Python orchestration logic
skill_loader.py        ❌     file I/O
pattern_detector.py    ❌     SQLite count threshold

Copilot (VS Code)      ✅     ALL agent reasoning happens here
Skill-Builder agent    ✅     runs inside Copilot, writes to proposed/
```

---

## Build Roadmap

### Day 1 — Native Copilot Files (no Python)
```
[ ] AGENTS.md — global rules, project context, commands
[ ] .github/copilot-instructions.md — team conventions
[ ] .github/instructions/universal/security.instructions.md
[ ] .github/instructions/universal/ethics.instructions.md
[ ] .github/instructions/org/ — team standards
[ ] .github/instructions/domain/ — python, api, database (with applyTo)
[ ] All 5 .agent.md files:
      frontmatter: name, description, tools
      Role, Instructions, Input Contract, Output Contract, Behavior Rules
      MCP tool calls in Input/Output Contract sections
[ ] .github/skills/code-review/ — SKILL.md + assets/ + references/
[ ] .github/agents/proposed/ — empty directory

Goal: agent definitions complete, skills folder populated
Test: open VS Code, agents appear in Copilot agent picker
      copilot-instructions.md referenced in chat responses
```

### Day 2 — State + Context Firewall
```
[ ] storage/schema.sql — sessions, stage_outputs, agent_versions, fail_patterns
[ ] storage/db.py — SQLite CRUD operations
[ ] state.py
      create_session(request) → session_id
      lock_agent_versions() → reads .agent.md frontmatter versions
      write_stage(session_id, stage, output) → append-only
      read_stage(session_id, stage) → output or None
      increment_attempt(session_id, stage) → updates attempt counter
      resume(session_id) → last incomplete stage
[ ] context_builder.py
      build_context(session_id, agent_name) → filtered dict
      scan_injection(text) → bool
      validate_skill_builder_write(path) → bool (proposed/ only)

Goal: firewall is structural, not agent self-enforced
Test:
      context for "planner" contains zero stage outputs
      context for "coder" contains no raw request text
      context for "skill-builder" contains no session state
      scan_injection("ignore your previous instructions") → True
```

### Day 3 — Verification + Correction Loop
```
[ ] verifier.py
      validate_schema(output, agent_name) → ValidationResult
      scan_secrets(text) → list[SecretMatch]
      scan_injection(text) → bool
      verify(session_id, agent_name, output) → VerificationResult
[ ] correction_loop.py
      run(session_id) → orchestrates reviewer → coder loop
      get_attempt_count(session_id) → int
      build_retry_context(session_id) → fix_instructions only
      escalate(session_id) → EscalationMessage

Goal: loop runs max 3 attempts, escalates cleanly
Test:
      inject bad Coder output → verify 3 retries, then escalation
      verify attempt_N outputs preserved in state
      verify retry context contains fix_instructions only
      verify secrets scan blocks hardcoded API keys
```

### Day 4 — MCP Server + Execution
```
[ ] executor.py
      run_lint(files) → LintResult
      run_typecheck(files) → TypeCheckResult
      run_tests(test_dir) → TestResult
      run_asset(asset_path, input_json) → AssetResult
      run_all(session_id) → ExecutionResult
[ ] skill_loader.py
      get_skill(skill_id) → SKILL.md content
      get_reference(skill_id, ref_name) → reference file content
      run_asset(skill_id, asset_name, input) → delegates to executor.py
[ ] server.py — MCP stdio server
      registers all harness_* tools
      routes tool calls to correct component
[ ] .vscode/mcp.json
[ ] cli.py: copilot-harness serve

Goal: VS Code connects, agents can call all MCP tools
Test:
      VS Code connects to MCP server
      harness_write_stage triggers verifier
      harness_read_stage respects context firewall
      harness_run_tests catches a known failing test
```

### Day 5 — Self-Improvement Loop
```
[ ] memory/cross_session.db schema
[ ] memory/pattern_detector.py
      record_failure(session_id, agent, issue)
      detect_patterns(agent) → list of recurring issues
      trigger_skill_builder(pattern) → writes proposed/*.patch.md
[ ] Wire pattern_detector into correction_loop.py
[ ] pipeline.py — top-level: runs full agent pipeline end-to-end

Goal: system proposes its own improvements after recurring failures
Test:
      simulate same reviewer failure 3 sessions in a row
      proposed/*.patch.md created with correct rule text
      direct write to .github/agents/ is blocked
```

### Week 2 — Hardening
```
[ ] Unit tests for all components (test_*.py)
[ ] Crash recovery: pipeline.py resumes from last complete stage
[ ] README.md — setup, how to add agents, how to write skills
[ ] Edge cases: empty planner output, coder refuses task
[ ] Instruction priority enforcement documentation
```

---

## Testing Checklist

```
state.py:
[ ] Completed stage cannot be overwritten
[ ] locked_agent_versions captured at session start
[ ] attempt counter increments, previous attempt preserved
[ ] resume() returns correct last incomplete stage

context_builder.py:
[ ] Planner: no stage outputs in context
[ ] Designer: plan output only, no request text
[ ] Coder on retry: fix_instructions only, not full review
[ ] Skill-Builder: no session state, no user code
[ ] scan_injection() catches override attempts

verifier.py:
[ ] Schema validation catches missing required fields
[ ] Secrets scanner catches API keys and private key patterns
[ ] Injection scanner catches adversarial patterns in output

correction_loop.py:
[ ] Max 3 attempts enforced
[ ] Escalation message contains session_id, attempt count, all issues
[ ] Retry context is fix_instructions only

executor.py:
[ ] Lint errors returned as structured feedback
[ ] Type errors returned with file + line
[ ] Test failures returned with test name + reason
[ ] Asset scripts run in subprocess with timeout

pattern_detector.py:
[ ] Failure recorded per session per agent per issue
[ ] Pattern triggered after 3 occurrences
[ ] proposed/*.patch.md created, not .github/agents/ directly

MCP server:
[ ] All harness_* tools registered and callable from VS Code
[ ] harness_write_stage triggers verifier before storing
[ ] harness_read_stage respects context firewall per calling agent
```

---

## Known TODOs

```
BEFORE DAY 1:
  TODO: Define output schema for each agent — drives verifier.py validation
  TODO: Write actual team conventions for copilot-instructions.md
  TODO: Decide injection detection patterns — regex list or semantic check?
  TODO: Decide .agent.md versioning — git commit hash or semantic version?

DAY 2:
  TODO: How does context_builder.py know which agent is calling harness_read_stage?
        Agent must pass agent_name in every MCP tool call.
        Add agent_name as required param to harness_read_stage.

DAY 4:
  TODO: executor.py — subprocess with timeout or Docker sandbox?
        Subprocess is simpler. Docker is safer for untrusted code.
        Start with subprocess, add Docker option in Week 2.
  TODO: Which test runner to support? pytest only, or also jest for JS?

WEEK 2:
  TODO: Instructions priority enforcement — how does harness detect P1 violations?
        Option: verifier.py loads P1 instructions, checks agent output against them.
  TODO: Cross-session memory — local SQLite per developer or shared team database?
```

---

## Resources

- Copilot .agent.md format: https://learn.microsoft.com/en-us/visualstudio/ide/copilot-specialized-agents
- awesome-copilot examples: https://github.com/github/awesome-copilot
- Custom instructions: https://code.visualstudio.com/docs/copilot/customization/custom-instructions
- AGENTS.md spec: https://agents.md
- Copilot SDK custom agents: https://docs.github.com/en/copilot/how-tos/copilot-sdk/use-copilot-sdk/custom-agents
- Harness Engineering: https://mitchellh.com/writing/harness-engineering
- SWE-agent paper (Princeton NeurIPS 2024): https://arxiv.org/abs/2405.15793
- OWASP Top 10 for Agentic AI: https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/

---

*Updated: April 2026*
*Project: CopilotHarness*
*Phase: Pre-build*
*Ultimate goal: 7-component harness engineering — all enforced structurally, zero LLM inside harness*
*Next action: Day 1 — write all .agent.md files and AGENTS.md before touching any Python*
*Next milestone: Day 2 — context_builder.py passes all firewall tests*
