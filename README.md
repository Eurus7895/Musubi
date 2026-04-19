# CopilotHarness

A harness engineering layer for GitHub Copilot Chat in VS Code.
The harness controls what each agent sees, validates what each agent produces,
injects skills, enforces correction loops, and runs code verification.

**Copilot Chat reasons. CopilotHarness controls the environment it reasons about.**

---

## Quick Start

**Requirements:** Python 3.11+, VS Code with GitHub Copilot Chat extension

### 1. Install the MCP server

```bash
cd copilot-harness
python -m pip install -e .
```

### 2. Open the workspace in VS Code

VS Code reads `.vscode/mcp.json` and spawns the MCP server automatically.
No manual startup needed. Verify it's running: open Copilot Chat and check
that `harness_*` tools appear in the tool list.

### 3. Install and activate the extension

```bash
cd copilot-harness-extension
npm install
npm run package        # produces copilot-harness-extension-0.1.0.vsix
```

In VS Code: **Extensions** sidebar → `...` menu → **Install from VSIX** → select the file.

### 4. Run the pipeline from Copilot Chat

Open Copilot Chat and type:

```
@harness add a login endpoint that validates email + password
```

All five agents run automatically in sequence. Progress streams into the chat window:

```
✓ planner complete
✓ designer complete
✓ coder complete
✗ reviewer failed — retrying coder (attempt 2 of 3)
✓ coder complete
✓ reviewer passed
Pipeline complete. Session: abc123
```

If VS Code restarts mid-run the next `@harness` message resumes from where it stopped — no work lost.

---

## How It Works

CopilotHarness runs as a local MCP stdio server. There are two ways to drive it:

VS Code reads `.vscode/mcp.json` and starts `copilot-harness serve` — a local stdio
process. The `@harness` chat participant (VS Code extension) then drives the full
5-agent pipeline by calling `harness_*` tools via `vscode.lm.invokeTool()` on that
single server, and calling Copilot for each agent's reasoning via `vscode.lm.sendRequest()`.

```
VS Code reads .vscode/mcp.json → starts copilot-harness serve  (ONE server)
                                       ↓
User types: @harness <request> in Copilot Chat
                                       ↓
Extension (@harness participant) — for each agent:
    vscode.lm.invokeTool("harness_read_stage")   → firewall enforced, skills injected
    vscode.lm.sendRequest(copilot, agentPrompt)  → Copilot reasons, returns JSON
    vscode.lm.invokeTool("harness_write_stage")  → injection scan + validation + store
                                       ↓
    reviewer "fail" → correction loop → retry coder (max 3) → re-run reviewer
                                       ↓
    stream.markdown(progress) back to Copilot Chat
```

---

## Setup

**Requirements:** Python 3.11+, Node.js 18+, VS Code 1.93+ with GitHub Copilot Chat

```bash
# Install the MCP server (once, globally)
cd copilot-harness
python -m pip install -e .

# Build and install the extension
cd ../copilot-harness-extension
npm install && npm run package
# Then: VS Code → Extensions → ··· → Install from VSIX
```

Any project that needs the harness only requires two things:
- `.vscode/mcp.json` (copy from this repo — points to `copilot-harness serve`)
- `.github/agents/` + `.github/skills/` (agent definitions and skills)

---

## The 5-Agent Pipeline

| Agent | Invoke | Gets from harness | Writes to harness |
|---|---|---|---|
| `@planner` | Start of session | Request only | `plan` |
| `@designer` | After plan | Plan + api-design skill | `design` |
| `@coder` | After design | Plan + design + python skill | `code` |
| `@reviewer` | After code | All stages + code-review skill | `review` |
| `@skill-builder` | On recurring failures | Fail patterns only | `proposed/` patch |

Skills marked above are **auto-injected** by the harness — agents cannot skip them.

Every agent begins by calling `harness_get_active_session()`. If an interrupted
session is found, the agent resumes from the correct stage without data loss.

---

## MCP Tools

| Tool | Purpose | Status |
|---|---|---|
| `harness_get_active_session()` | Crash recovery — check for interrupted session | ✅ |
| `harness_new_session(request)` | Start pipeline, lock agent versions | ✅ |
| `harness_read_stage(session_id, stage, agent_name)` | Read with firewall + skill injection | ✅ |
| `harness_write_stage(session_id, stage, output, agent_name)` | Validate + store | ✅ |
| `harness_get_status(session_id)` | Pipeline summary | ✅ |
| `harness_get_skill(skill_id)` | Load SKILL.md on demand | ✅ |
| `harness_get_reference(skill_id, ref_name)` | Load reference document | ✅ |
| `harness_increment_attempt(session_id, stage)` | Increment attempt counter for retry | ✅ |
| `harness_run_lint(files)` | ruff check | ✅ |
| `harness_run_typecheck(files)` | mypy | ✅ |
| `harness_run_tests(test_dir)` | pytest | ✅ |

---

## Crash Recovery

If VS Code restarts or a Copilot Chat session is interrupted mid-pipeline,
no work is lost. Every agent starts with:

```
harness_get_active_session()
→ { session_id: null }                       → start fresh with harness_new_session()
→ { session_id, request, resume_stage, attempt } → skip completed stages, resume here
```

The harness tracks the active session in SQLite and marks each stage `in_progress`
the moment an agent calls `harness_read_stage` — so a crash between read and write
is always detectable on the next invocation.

---

## Project Structure

```
.github/
    agents/          ← 5 agent definitions (.agent.md) with resume contracts
    instructions/    ← priority-ranked rules (P1–P4)
    skills/          ← 6 skills with SKILL.md + assets + references
    agents/proposed/ ← Skill-Builder writes here (human approves)

copilot-harness/     ← Phase 1: Python MCP server (zero LLM)
    server.py        ← FastMCP stdio server — all harness_* tools
    state.py         ← append-only session state + crash recovery
    context_builder.py ← context firewall + injection detection
    verifier.py      ← schema validation + secrets scan
    correction_loop.py ← reviewer → coder retry logic (max 3)
    skill_loader.py  ← serves SKILL.md + reference files
    storage/         ← SQLite schema + CRUD (active_session table)
    tests/           ← state, context_builder, verifier, correction_loop tests

copilot-harness-extension/  ← VS Code extension (TypeScript)
    src/
        extension.ts ← registers @harness chat participant
        pipeline.ts  ← drives 5 agents via vscode.lm.invokeTool + sendRequest
    package.json     ← VS Code ^1.93.0 extension manifest

.vscode/mcp.json     ← connects VS Code to local MCP server
```

---

## Build Status

| Milestone | Deliverable | Status |
|---|---|---|
| Day 1 | Native Copilot files (agents, instructions, skills) | ✅ |
| Day 2 | State, context firewall, MCP server, crash recovery | ✅ |
| Day 3 | Verifier, correction loop, skill loader | ✅ |
| Day 4 | Executor (lint, typecheck, tests) | ✅ |
| Extension | @harness chat participant, vscode.lm.invokeTool pipeline | ✅ |
| Day 5 | Self-improvement loop, pattern detector | ⬜ |
| Week 2 | Hardening, memory architecture | ⬜ |

---

## Running Tests

```bash
cd copilot-harness
pytest tests/ -v
```

Tests cover state management, context firewall, injection detection, crash recovery,
schema validation, secrets scanning, correction loop, and executor (ruff/mypy/pytest).
163 tests, all passing.
