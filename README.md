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

**Requirements:** Python 3.11+, Node.js 18+, VS Code with GitHub Copilot Chat, PyInstaller

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
# → copilot-harness-extension-0.3.1.vsix

# 3. Install into VS Code
code --install-extension copilot-harness-extension-0.3.1.vsix
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
    Chat streams a header line: "🎛 /feature-dev — level 2 · session s/abc123".
    ↓
    harness_get_active_session()         → resume interrupted session or start fresh
    harness_new_session(request)         → create session, lock agent versions
    ↓
    For each agent (planner → designer → coder → reviewer):
        Chat ← ### ⏳ <agent>  +  tag line (skill/memory/firewall/schema/policy)
        harness_read_stage(...)          → context firewall enforced, skills injected
        vscode.lm.sendRequest(copilot)   → Copilot reasons, returns JSON output
        harness_write_stage(...)         → injection scan + schema validation + store
        Chat ← ✓ <agent> — 3.1s — 5-step plan, schema ✓
    ↓
    Reviewer "fail" → Chat ← ⚠️ reviewer → fail · 2 issues  +  fix_instructions
                    → correction loop (max 3 retries) → escalate
    ↓
    Chat ← ✅ Pipeline complete. Session: s/abc123   [View plan.md →]
```

The routing is a pure string check — **zero LLM cost** to decide direct vs pipeline.

---

## In-Chat Rendering (v0.3.1)

The pipeline renders inline in Copilot Chat — the same surface as Copilot
Chat or Claude Chat — so you don't switch panels to see what the harness
is doing. Each agent stage streams a markdown section as it runs:

```
### ⏳ planner
◆ memory: `MEMORY.md` · ◇ policy: `Read·Grep·Glob`

✓ **planner** — 3.1s — 5-step plan, schema ✓

### ⏳ designer
◈ skill: `api-design` · { } schema: `design.json`

✓ **designer** — 4.8s — 3 modules, schema ✓

### ↻ coder  *(attempt 2/3)*
◈ skill: `python` · ⟡ firewall: `fix_instructions only`

> ⚠️ **reviewer → fail** · 2 issues
>
> Fix: Reorder auth → ownership → fetch. Add test_cancel_403_when_not_owner.

✓ **coder** — 9.2s — 3 files, schema ✓
✓ **reviewer** — 2.1s — review: pass

*total: 18.4s*

✅ **Pipeline complete.** Session: `s/9f3a2c`
[View plan.md →]
```

The governance tags (◆ memory, ◈ skill, { } schema, ⟡ firewall, ◇ policy)
mirror the push-not-pull injection the harness enforces — you see what was
pushed to each stage without leaving the chat. Reviewer failures render as a
blockquote with the first `fix_instruction`, so you see *why* the retry is
happening before it starts. At pipeline end a **View plan.md** anchor opens
the materialised session artifact at `.harness/sessions/<sid>/plan.md`.

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
| `harness_list_skills(agent_name)` | Per-caller filtered skill catalog (Week 4 Day 3) |
| `harness_get_reference(skill_id, ref_name, agent_name)` | Load reference document |
| `harness_increment_attempt(session_id, stage)` | Increment attempt counter for retry |
| `harness_get_memory_context()` | Tier 1 index + Tier 2 available list |
| `harness_get_memory_entry(name)` | Load Tier 2 memory entry (e.g. `architecture.md`) |
| `harness_query_sessions(query, limit)` | Cross-session substring search (Week 4 Day 4) |
| `harness_distill_session(session_id)` | Distill session failures into Tier 2 memory |
| `harness_compact_memory()` | Tier 2 compaction (fires when failure-patterns.md > 5 KB) |
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
        feature-dev-level1-probe/ ← Level-1 probe infrastructure (Week 4 Day 5)
    commands/                    ← slash command contracts (Week 3c)
        feature-dev.md, continue.md, status.md, help.md,
        planner.md, designer.md, coder.md, reviewer.md
    agents/                      ← cross-pipeline home (un-deprecated for Week 5)
        skill-builder.agent.md   ← meta-agent
        proposed/                ← Skill-Builder writes here (human approves)
    instructions/                ← priority-ranked rules (P1–P4)
    skills/                      ← 6 domain skills, each: SKILL.md + refs/ + assets/
    memory/                      ← 3-tier memory architecture
        MEMORY.md                ← Tier 1: always-injected index (~200 tokens)
        architecture.md          ← Tier 2: key decisions
        failure-patterns.md      ← Tier 2: distilled failures (auto-compacted, Week 4 Day 4)

copilot-harness/                 ← Python MCP server (zero LLM)
    server.py                    ← FastMCP stdio — harness_* tools
                                   (includes harness_run_hook, Week 3c)
    state.py                     ← append-only session state + crash recovery
    context_builder.py           ← context firewall + injection detection
    verifier.py                  ← schema validation + secrets scan
    correction_loop.py           ← reviewer → coder retry (max 3)
    skill_loader.py              ← serves SKILL.md + reference files
    memory_loader.py             ← Tier 1/2 memory injection + cross-session query
    session_distiller.py         ← distills failures to Tier 2 memory + compaction
    executor.py                  ← ruff + mypy + pytest runner
    storage/db.py                ← SQLite CRUD; schema embedded
    tests/                       ← 379 tests covering all components

copilot-harness-extension/       ← VS Code extension (TypeScript, v0.3.1)
    src/
        mcpClient.ts             ← JSON-RPC stdio client
        extension.ts             ← @harness + direct-mode routing
        pipeline.ts              ← 4-agent orchestration + correction loop
                                   + rich in-chat stage rendering (v0.3.1)
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
CopilotHarness v0.3.1 activating...
Extension path: C:\...\extensions\copilot-harness-0.3.1\
Checking: ...\bin\copilot-harness.exe — found
Starting MCP server...
MCP server started. Listing tools...
Tools available (18): harness_get_active_session, harness_new_session, ...
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
| Week 4 Day 1 — `/help` slash command (dynamic, data-driven) | ✅ |
| Week 4 Day 2 — `.claude-plugin/plugin.json` manifest | ✅ |
| Week 4 Day 3 — Direct-mode skill catalog + pull-on-demand | ✅ |
| Week 4 Day 4 — Tier 2 compaction + `harness_query_sessions` | ✅ |
| Week 4 Day 5 — feature-dev Level-1 probe infrastructure (run pending) | ✅ |
| Extension v0.3.1 — rich in-chat pipeline rendering (status emoji, governance tags, retry blocks, plan.md anchor) | ✅ |

---

## Running Tests

```bash
cd copilot-harness
pytest tests/ -v
# 379 tests
```
