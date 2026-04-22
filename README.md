# CopilotHarness

A harness engineering layer for GitHub Copilot Chat in VS Code.
The harness controls what each agent sees, validates what each agent produces,
injects skills, enforces correction loops, and runs code verification.

**Copilot Chat reasons. CopilotHarness controls the environment it reasons about.**

```
Same model + same task + changed environment = better outcomes
(Princeton SWE-agent paper: 64% improvement from harness design alone)
```

---

## Quick Start

**Requirements:** Python 3.12+, Node.js 18+, VS Code with GitHub Copilot Chat, PyInstaller

```powershell
# 1. Python setup
python -m venv .venv
.venv\Scripts\activate
pip install -e copilot-harness/
pip install pyinstaller

# 2. Build the extension (bundles server binary + skills + agents)
cd copilot-harness-extension
npm install
npm install -g @vscode/vsce
npm run package
# → copilot-harness-extension-0.2.0.vsix

# 3. Install into VS Code
code --install-extension copilot-harness-extension-0.2.0.vsix
```

Close and reopen VS Code. The **CopilotHarness** output channel appears automatically,
confirming the server started. Then:

```
@harness add a login endpoint that validates email + password
```

---

## How It Works

The extension spawns the bundled server binary as a child process on VS Code start.
No MCP panel, no manual server start, no tool enabling required.

```
VS Code opens → extension activates (onStartupFinished)
    ↓
extension.ts spawns bin/copilot-harness.exe via McpClient (JSON-RPC stdio)
    ↓
User types: @harness <request> in Copilot Chat
    ↓
pipeline.ts drives 5 agents via McpClient.callTool() + vscode.lm.sendRequest():

    harness_get_active_session()         → resume interrupted session or start fresh
    harness_new_session(request)         → create session, lock agent versions
    ↓
    For each agent (planner → designer → coder → reviewer):
        harness_read_stage(...)          → context firewall enforced, skills injected
        vscode.lm.sendRequest(copilot)   → Copilot reasons, returns JSON output
        harness_write_stage(...)         → injection scan + schema validation + store
    ↓
    Reviewer "fail" → correction loop (max 3 retries) → escalate
    ↓
    Pipeline complete. Session: abc123
```

---

## The 5-Agent Pipeline

| Agent | Gets from harness | Writes | Auto-injected skill |
|---|---|---|---|
| Planner | request only | `plan` | none |
| Designer | plan | `design` | api-design |
| Coder | plan + design | `code` | python |
| Reviewer | plan + design + code | `review` | code-review (always) |
| Skill-Builder | fail patterns only | `proposed/` patch | none |

Skills are **pushed by the harness** — agents cannot skip them.

---

## Crash Recovery

If VS Code closes mid-pipeline, no work is lost. On next `@harness` call:

```
harness_get_active_session()
→ { session_id: null }                        → start fresh
→ { session_id, request, resume_stage, ... }  → skip completed stages, resume here
```

The harness marks each stage `in_progress` the moment `harness_read_stage` is called —
a crash between read and write is always detectable.

---

## MCP Tools

| Tool | Purpose |
|---|---|
| `harness_get_active_session()` | Crash recovery — check for interrupted session |
| `harness_new_session(request)` | Start pipeline, lock agent versions |
| `harness_read_stage(session_id, stage, agent_name)` | Read with firewall + skill + memory injection |
| `harness_write_stage(session_id, stage, output, agent_name)` | Validate + store |
| `harness_get_status(session_id)` | Pipeline stage summary |
| `harness_get_skill(skill_id)` | Load SKILL.md on demand |
| `harness_get_reference(skill_id, ref_name)` | Load reference document |
| `harness_increment_attempt(session_id, stage)` | Increment attempt counter for retry |
| `harness_get_memory_entry(name)` | Load Tier 2 memory entry (e.g. `architecture.md`) |
| `harness_distill_session(session_id)` | Distill session failures into Tier 2 memory |
| `harness_run_lint(files)` | ruff check |
| `harness_run_typecheck(files)` | mypy |
| `harness_run_tests(test_dir)` | pytest |

---

## Project Structure

```
.github/
    agents/          ← 5 agent definitions (.agent.md) with resume contracts
    instructions/    ← priority-ranked rules (P1–P4)
    skills/          ← 6 skills with SKILL.md + assets + references
    agents/proposed/ ← Skill-Builder writes here (human approves)

copilot-harness/     ← Python MCP server (zero LLM)
    server.py        ← FastMCP stdio server — all harness_* tools
    state.py         ← append-only session state + crash recovery
    context_builder.py ← context firewall + injection detection
    verifier.py      ← schema validation + secrets scan
    correction_loop.py ← reviewer → coder retry logic (max 3)
    skill_loader.py  ← serves SKILL.md + reference files
    memory_loader.py ← Tier 1/2 memory injection into harness_read_stage
    session_distiller.py ← distills session review failures to Tier 2 memory
    executor.py      ← ruff + mypy + pytest runner
    storage/db.py    ← SQLite CRUD; schema embedded; DB in HARNESS_ROOT/data/
    tests/           ← 260 tests covering all components

.github/memory/      ← 3-tier memory architecture
    MEMORY.md        ← Tier 1: always-injected index (~200 tokens)
    architecture.md  ← Tier 2: key decisions (SQLite, MCP, PyInstaller)
    failure-patterns.md ← Tier 2: distilled recurring failures (auto-updated)

copilot-harness-extension/   ← VS Code extension (TypeScript, v0.2.0)
    src/
        mcpClient.ts ← JSON-RPC stdio client — spawns server binary directly
        extension.ts ← @harness chat participant, activates on VS Code start
        pipeline.ts  ← 5-agent orchestration via McpClient + vscode.lm
    bin/
        copilot-harness.exe  ← PyInstaller binary (Windows)
        copilot-harness      ← PyInstaller binary (Linux/Mac)

.vscode/mcp.json     ← dev mode only: python server.py for manual agent use
```

---

## Dev Mode (manual agent use)

For working on the harness itself without the extension:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e copilot-harness/
code .   # launch VS Code with venv active
```

VS Code reads `.vscode/mcp.json` → spawns `python server.py` → harness tools appear
in Copilot Chat's tool picker. Agents call tools manually via Copilot Chat.

---

## Diagnostics

**Output panel** (`Ctrl+Shift+U`) → **CopilotHarness**:
```
CopilotHarness v0.2.0 activating...
Extension path: C:\...\extensions\copilot-harness-0.2.0\
Checking: ...\bin\copilot-harness.exe — found
Starting MCP server...
MCP server started. Listing tools...
Tools available (11): harness_get_active_session, harness_new_session, ...
CopilotHarness ready. Use @harness in Copilot Chat.
```

---

## Build Status

| Milestone | Status |
|---|---|
| Day 1 — Native Copilot files (agents, instructions, skills) | ✅ |
| Day 2 — State, context firewall, MCP server, crash recovery | ✅ |
| Day 3 — Verifier, correction loop, skill loader | ✅ |
| Day 4 — Executor (lint, typecheck, tests) | ✅ |
| Extension v0.2.0 — McpClient direct, DB path fixed, auto-activation | ✅ |
| Day 5 — Self-improvement loop, pattern detector | ✅ |
| Week 2 — Memory architecture (3-tier), session distiller, edge case hardening | ✅ |

---

## Running Tests

```bash
cd copilot-harness
pytest tests/ -v
```
