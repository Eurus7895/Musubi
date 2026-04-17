# CopilotHarness

A harness engineering layer for GitHub Copilot Chat in VS Code.
The harness controls what each agent sees, validates what each agent produces,
injects skills, enforces correction loops, and runs code verification.

**Copilot Chat reasons. CopilotHarness controls the environment it reasons about.**

---

## How It Works

CopilotHarness runs as a local MCP stdio server. VS Code spawns it automatically
and Copilot Chat agents call `harness_*` tools to read inputs and submit outputs.
The developer drives agents via Copilot Chat — the harness enforces all structure.

```
VS Code → .vscode/mcp.json → spawns python copilot-harness/server.py
                                       ↓
Copilot agent calls harness_new_session()   → session created, agent versions locked
Copilot agent calls harness_read_stage()    → firewall enforced, skills auto-injected
Copilot agent calls harness_write_stage()   → validated, injection-scanned, stored
```

---

## Setup

**Requirements:** Python 3.11+, VS Code with GitHub Copilot Chat

```bash
cd copilot-harness
pip install -e .
```

VS Code reads `.vscode/mcp.json` and starts the server automatically when you
open the workspace. No manual startup needed.

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

---

## MCP Tools

| Tool | Purpose | Status |
|---|---|---|
| `harness_new_session(request)` | Start pipeline, lock agent versions | ✅ |
| `harness_read_stage(session_id, stage, agent_name)` | Read with firewall + skill injection | ✅ |
| `harness_write_stage(session_id, stage, output, agent_name)` | Validate + store | ✅ |
| `harness_get_status(session_id)` | Pipeline summary | ✅ |
| `harness_get_skill(skill_id)` | Load SKILL.md on demand | ✅ |
| `harness_get_reference(skill_id, ref_name)` | Load reference document | ✅ |
| `harness_run_lint(files)` | ruff check | Day 4 |
| `harness_run_typecheck(files)` | mypy | Day 4 |
| `harness_run_tests(test_dir)` | pytest | Day 4 |

---

## Project Structure

```
.github/
    agents/          ← 5 agent definitions (.agent.md)
    instructions/    ← priority-ranked rules (P1–P4)
    skills/          ← 6 skills with SKILL.md + assets + references
    agents/proposed/ ← Skill-Builder writes here (human approves)

copilot-harness/
    server.py        ← FastMCP stdio server
    state.py         ← append-only session state
    context_builder.py ← context firewall + injection detection
    storage/         ← SQLite schema + CRUD
    tests/           ← 69 tests, all passing

.vscode/mcp.json     ← connects VS Code to local MCP server
```

---

## Build Status

| Day | Deliverable | Status |
|---|---|---|
| Day 1 | Native Copilot files (agents, instructions, skills) | ✅ |
| Day 2 | State, context firewall, MCP server | ✅ |
| Day 3 | Verifier, correction loop, skill loader | ⬜ |
| Day 4 | Executor (lint, typecheck, tests) | ⬜ |
| Day 5 | Self-improvement loop, pattern detector | ⬜ |
| Week 2 | Hardening, memory architecture, crash recovery | ⬜ |
| Phase 2 | VS Code extension (automated pipeline) | ⬜ |

---

## Running Tests

```bash
cd copilot-harness
pytest tests/ -v
```

69 tests — state management, context firewall, injection detection.
