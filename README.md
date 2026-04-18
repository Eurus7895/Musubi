# CopilotHarness

A harness engineering layer for GitHub Copilot Chat in VS Code.
The harness controls what each agent sees, validates what each agent produces,
injects skills, enforces correction loops, and runs code verification.

**Copilot Chat reasons. CopilotHarness controls the environment it reasons about.**

---

## How It Works

CopilotHarness runs as a local MCP stdio server. There are two ways to drive it:

**Phase 1 — Manual (Copilot Chat):** VS Code spawns the server and Copilot Chat
agents call `harness_*` tools directly. The developer opens each `@agent` in turn.

**Phase 2 — Automated (VS Code Extension):** The extension drives all agents
automatically via `vscode.lm.sendRequest()`, calling the same MCP server on each
agent's behalf. No manual relay needed.

```
VS Code → .vscode/mcp.json → spawns python copilot-harness/server.py
                                       ↓
                    ┌──────────────────┴──────────────────┐
             Phase 1 (manual)                    Phase 2 (extension)
        Copilot Chat @agent                  copilotHarness.runPipeline
        calls harness_* tools                extension calls harness_* tools
        developer relays each step           all 5 agents run automatically
                    └──────────────────┬──────────────────┘
                                       ↓
              harness_get_active_session() → crash recovery check
              harness_new_session()        → session created, agent versions locked
              harness_read_stage()         → firewall enforced, skills auto-injected
              harness_write_stage()        → injection scan + schema validation + store
```

---

## Setup

**Requirements:** Python 3.11+, VS Code with GitHub Copilot Chat

```bash
cd copilot-harness
pip install -e .
```

VS Code reads `.vscode/mcp.json` and starts the MCP server automatically when
you open the workspace. No manual startup needed.

**Phase 2 extension** (automated pipeline):

```bash
cd copilot-harness-extension
npm install
npm run compile
```

Install the compiled extension in VS Code, then run
`CopilotHarness: Run Pipeline` from the Command Palette.

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
| `harness_run_lint(files)` | ruff check | Day 4 |
| `harness_run_typecheck(files)` | mypy | Day 4 |
| `harness_run_tests(test_dir)` | pytest | Day 4 |

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

copilot-harness-extension/  ← Phase 2: VS Code extension (TypeScript)
    src/
        extension.ts ← registers "CopilotHarness: Run Pipeline" command
        pipeline.ts  ← drives 5 agents via vscode.lm API + correction loop
        client.ts    ← MCP stdio client (spawns server, JSON-RPC)
    package.json     ← VS Code ^1.90.0 extension manifest

.vscode/mcp.json     ← connects VS Code to local MCP server
```

---

## Build Status

| Milestone | Deliverable | Status |
|---|---|---|
| Day 1 | Native Copilot files (agents, instructions, skills) | ✅ |
| Day 2 | State, context firewall, MCP server, crash recovery | ✅ |
| Day 3 | Verifier, correction loop, skill loader | ✅ |
| Phase 2 | VS Code extension (automated pipeline) | ✅ |
| Day 4 | Executor (lint, typecheck, tests) | ⬜ |
| Day 5 | Self-improvement loop, pattern detector | ⬜ |
| Week 2 | Hardening, memory architecture | ⬜ |

---

## Running Tests

```bash
cd copilot-harness
pytest tests/ -v
```

Tests cover state management, context firewall, injection detection, crash recovery,
schema validation, secrets scanning, and the correction loop.
