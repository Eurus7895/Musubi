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
@harness /feature-dev add a login endpoint that validates email + password
```

Or, for a quick question, skip the pipeline:

```
@harness how does the correction loop work?
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
User types @harness <input> in Copilot Chat → extension.ts routes it:

    starts with "/"      → slash command → pipeline / step / status / continue
    contains --pipeline  → force pipeline mode
    everything else      → DIRECT MODE: single vscode.lm.sendRequest, no harness

PIPELINE MODE (for slash commands like /feature-dev <task>):
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

The routing is a pure string check — **zero LLM cost** to decide direct vs pipeline.

---

## Commands

Slash commands live in `.github/commands/*.md` — each file declares an action
(`pipeline`, `step`, `continue`, `status`) in YAML frontmatter.

| Command | Mode | Action |
|---|---|---|
| `@harness <question>` | direct | Single Copilot call, no pipeline |
| `@harness /feature-dev <task>` | pipeline | Full 4-agent governed pipeline |
| `@harness /planner <task>` | pipeline | Planner only (new session) |
| `@harness /designer` | pipeline | Designer on active session |
| `@harness /coder` | pipeline | Coder on active session |
| `@harness /reviewer` | pipeline | Reviewer on active session |
| `@harness /continue` | pipeline | Run next pending agent |
| `@harness /status` | pipeline | Show active session progress |
| `@harness <task> --pipeline` | pipeline | Force pipeline for free-form input |

Legacy bare keywords (`continue`, `status`, `full`, `planner`, …) still work for
one release cycle — prefer the slash form.

---

## The feature-dev Pipeline

| Agent | Gets from harness | Writes | Auto-injected skill |
|---|---|---|---|
| Planner | request only | `plan` | none |
| Designer | plan | `design` | api-design |
| Coder | plan + design (+ review on retry) | `code` | python |
| Reviewer | **code only** (evaluator firewall) | `review` | code-review (always) |

Skills are **pushed by the harness** — agents cannot skip them.

The Reviewer runs under the Week 3a evaluator firewall: it cannot see the
request, plan, design, or memory. It judges the code artifact against the
`code-review` checklist and returns `pass | fail | escalate | wrong_plan`.

Skill-Builder is a meta-agent that lives outside the feature-dev pipeline at
`.github/agents/skill-builder.agent.md`. It writes proposed patches to
`.github/agents/proposed/` for human review.

---

## Hooks

`hooks.json` at the repo root wires deterministic scripts to lifecycle events:

| Hook | Script | Behavior |
|---|---|---|
| `SessionStart` | `scripts/session_start.py` | Runs `baseline_checks` from `pipeline.yaml` |
| `PreToolUse` | `scripts/pre_tool_use.py` | Policy-engine gate — exit 0 allow / 1 deny |
| `PostToolUse` | `scripts/post_tool_use.py` | SQLite audit log to `storage/audit.db` |

`scripts/policy_engine.py` holds `PIPELINE_POLICIES` — a fail-closed allowlist
mapping `(pipeline, agent) → [tool, …]`. Unknown pipeline or agent → deny.
Invoke any hook via the `harness_run_hook(event, payload)` MCP tool.

**Key rule:** "Never send an LLM to do a linter's job."

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
| `harness_get_skill(skill_id, agent_name)` | Load SKILL.md on demand |
| `harness_get_reference(skill_id, ref_name, agent_name)` | Load reference document |
| `harness_increment_attempt(session_id, stage)` | Increment attempt counter for retry |
| `harness_get_memory_entry(name)` | Load Tier 2 memory entry (e.g. `architecture.md`) |
| `harness_distill_session(session_id)` | Distill session failures into Tier 2 memory |
| `harness_run_lint(files)` | ruff check |
| `harness_run_typecheck(files)` | mypy |
| `harness_run_tests(test_dir)` | pytest |
| `harness_run_hook(event, payload)` | Execute hooks.json lifecycle hooks |

---

## Project Structure

```
.github/
    pipelines/
        feature-dev/             ← self-contained pipeline (Week 3b)
            pipeline.yaml        ← level: 2, baseline_checks, correction
            agents/              ← planner/designer/coder/reviewer .agent.md
            README.md
    commands/                    ← slash command contracts (Week 3c)
        feature-dev.md, continue.md, status.md,
        planner.md, designer.md, coder.md, reviewer.md
    agents/                      ← DEPRECATED (removed Week 5)
        skill-builder.agent.md   ← meta-agent stays here
        proposed/                ← Skill-Builder writes here (human approves)
    instructions/                ← priority-ranked rules (P1–P4)
    skills/                      ← 6 domain skills, each: SKILL.md + refs/ + assets/
    memory/                      ← 3-tier memory architecture
        MEMORY.md                ← Tier 1: always-injected index (~200 tokens)
        architecture.md          ← Tier 2: key decisions
        failure-patterns.md      ← Tier 2: distilled failures (auto-updated)

copilot-harness/                 ← Python MCP server (zero LLM)
    server.py                    ← FastMCP stdio — harness_* tools
                                   (includes harness_run_hook, Week 3c)
    state.py                     ← append-only session state + crash recovery
    context_builder.py           ← context firewall + injection detection
    verifier.py                  ← schema validation + secrets scan
    correction_loop.py           ← reviewer → coder retry (max 3)
    skill_loader.py              ← serves SKILL.md + reference files
    memory_loader.py             ← Tier 1/2 memory injection
    session_distiller.py         ← distills failures to Tier 2 memory
    executor.py                  ← ruff + mypy + pytest runner
    storage/db.py                ← SQLite CRUD; schema embedded
    tests/                       ← 334 tests covering all components

copilot-harness-extension/       ← VS Code extension (TypeScript, v0.2.0)
    src/
        mcpClient.ts             ← JSON-RPC stdio client
        extension.ts             ← @harness + direct-mode routing
        pipeline.ts              ← 4-agent orchestration + correction loop
        slashCommands.ts         ← frontmatter-driven slash loader (Week 3c)
    bin/
        copilot-harness.exe      ← PyInstaller binary (Windows)
        copilot-harness          ← PyInstaller binary (Linux/Mac)

hooks.json                       ← SessionStart / PreToolUse / PostToolUse (Week 3c)
scripts/                         ← deterministic hook implementations
    policy_engine.py             ← PIPELINE_POLICIES (fail-closed)
    pre_tool_use.py              ← policy gate (exit 0=allow, 1=deny)
    post_tool_use.py             ← SQLite audit log
    session_start.py             ← pipeline.yaml baseline_checks runner

.vscode/mcp.json                 ← dev mode only: python server.py for manual use
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
Tools available (14): harness_get_active_session, harness_new_session, ...
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
| Day 5 — Self-improvement loop, pattern detector | ✅ |
| Extension v0.2.0 — McpClient direct, DB path fixed, auto-activation | ✅ |
| Week 2 — Memory architecture (3-tier), session distiller, edge case hardening | ✅ |
| Week 3a — Separate evaluator session (reviewer firewall) | ✅ |
| Week 3b — Pipeline directory migration (`.github/pipelines/feature-dev/`) | ✅ |
| Week 3c — Direct mode + `hooks.json` + slash commands | ✅ |

---

## Running Tests

```bash
cd copilot-harness
pytest tests/ -v
# 334 tests
```
