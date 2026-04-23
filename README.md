# CopilotHarness

CopilotHarness is the harness layer for GitHub Copilot Chat in VS Code. It
controls what each agent sees, validates what each agent produces, enforces
the correction loop, serves skills on demand, and runs code verification — all
through a local Python MCP server driven by a VS Code extension.

> **Status:** Week 4 complete + Harness Dashboard webview. Extension v0.3.0 ·
> 379 tests passing. The full internal design doc is [`CLAUDE.md`](./CLAUDE.md);
> for session-start orientation read [`AGENTS.md`](./AGENTS.md).

---

## Harness Engineering Principle

> "The model is what thinks. The harness is what it thinks about. And the
> harness is what determines the final outcome."

Same model, same task, same compute — just changing environment design
yielded a **64% performance improvement** in the Princeton SWE-agent paper
(NeurIPS 2024). CopilotHarness takes that principle seriously: Copilot is the
LLM, CopilotHarness is everything that shapes what Copilot sees and what
happens with what Copilot produces.

```
What Copilot does:        reasoning, planning, coding, reviewing
What CopilotHarness does: routing, state, context firewall, skill injection,
                          validation, execution, correction loop, policy
                          enforcement
```

## Zero-LLM Principle

There are **zero LLM calls inside the harness**. Every component is
deterministic Python or TypeScript.

| Component              | LLM? | Implementation                        |
| ---------------------- | ---- | ------------------------------------- |
| `server.py`            | no   | FastMCP stdio, routes tool calls      |
| `state.py`             | no   | append-only session state + SQLite    |
| `context_builder.py`   | no   | per-agent firewall + injection scan   |
| `verifier.py`          | no   | jsonschema + regex secrets scan       |
| `executor.py`          | no   | subprocess: ruff, mypy, pytest        |
| `correction_loop.py`   | no   | orchestration logic (TS + Python)     |
| `skill_loader.py`      | no   | file I/O                              |
| `memory/`              | no   | 3-tier memory, SQLite audit log       |
| `extension.ts`         | no   | routes slash commands vs direct mode  |
| `pipeline.ts`          | no   | agent driver, emits dashboard events  |
| `dashboard.ts`         | no   | webview owner, event-to-DOM renderer  |
| Copilot (`vscode.lm`)  | yes  | all agent reasoning happens here      |

---

## How You Use It

Two modes, routed deterministically inside `extension.ts`:

```
@harness <question>                     → direct mode (single vscode.lm call,
                                          no pipeline, no evaluator)
@harness <task> --pipeline              → pipeline mode
@harness /feature-dev <task>            → pipeline mode (slash command)
```

**Pipeline mode (governed):**

1. Extension spawns the bundled Python MCP server over stdio.
2. Harness creates a session, locks agent versions.
3. Four agents run in sequence — `planner` → `designer` → `coder` → `reviewer`.
   Each `harness_read_stage` applies the context firewall and injects the
   stage's skill + Tier 1 memory; each `harness_write_stage` runs injection
   detection + JSON schema validation before storing.
4. Reviewer runs in a separate `vscode.lm` session (evaluator firewall —
   sees only the code artifact, not the plan or design).
5. Reviewer fail → coder retries with `fix_instructions` (max 3 attempts,
   then the pipeline escalates).
6. Chat shows a one-line marker + **Show Harness Dashboard** button;
   the dashboard renders the live pipeline card (see below).

**Direct mode (fast path):**

- Single `vscode.lm.sendRequest` — no pipeline, no evaluator, no schema.
- One MCP round-trip up front fetches the direct-mode skill catalog and
  Tier 1 memory.
- The model may optionally emit `{"action":"pull_skill","skill_id":"…"}`
  on the first line of a response to pull a skill on demand (max 3 pulls).

---

## Harness Dashboard (v0.3.0)

The VS Code chat stream accepts only CommonMark plus a few primitives —
it cannot render colored status dots, flex layouts, or animations. So
pipeline runs surface in a dedicated **webview** panel that mirrors the
design mockup verbatim:

- Route pill (`/FEATURE-DEV`), pipeline level, retries counter, live elapsed timer.
- Per-stage status dots (pass / fail / running / retry / queued) with tags
  for **skill**, **memory**, **firewall**, **schema**, **policy**.
- Retry block showing the reviewer verdict and `fix_instructions` text.
- Footer actions — `/status`, **Cancel** (cancels the in-flight pipeline
  via a linked `CancellationTokenSource`), **View plan.md** (opens the
  materialised session artifact).

The extension drives the webview via typed `postMessage` events
(`session_start` · `stage_start` · `stage_complete` · `correction_retry` ·
`pipeline_complete` · `tick` · `direct_*`). No new LLM calls — events
piggyback on the existing pipeline instrumentation points. A CSP +
per-render nonce lock down script execution in the webview.

Open it anytime from the command palette: **CopilotHarness: Show Dashboard**.

---

## The Two Layers

### Layer 1 — Copilot native files (auto-loaded by Copilot)

```
.github/
    AGENTS.md                    session map (session-start orientation)
    copilot-instructions.md      global conventions
    instructions/
        universal/               P1: ethics, security — never overridden
        org/                     P2: team-wide standards
        domain/                  P3: technology-specific (applyTo scoped)
        project/                 P4: repo-specific overrides
    pipelines/feature-dev/
        pipeline.yaml            level, baseline_checks, correction
        agents/
            planner.agent.md
            designer.agent.md
            coder.agent.md
            reviewer.agent.md
    agents/
        skill-builder.agent.md   meta-agent (proposes patches, writes to proposed/)
    commands/                    slash commands (frontmatter-driven *.md files)
    skills/
        code-review/, api-design/, python/, testing/,
        database-patterns/, documentation/
            SKILL.md             ≤ 200 lines
            references/          loaded only when needed
            assets/              executable scripts, run by executor.py
    memory/
        MEMORY.md                Tier 1 — ~200 tokens, always injected
        architecture.md          Tier 2 — on demand
        failure-patterns.md      Tier 2 — session-distilled, auto-compacted
```

### Layer 2 — CopilotHarness core (Python MCP server + VS Code extension)

```
copilot-harness/                 Python MCP server (FastMCP stdio)
    server.py                    harness_* tools
    state.py                     append-only session state
    context_builder.py           context firewall + injection detection
    verifier.py                  schema validation + secrets scan
    executor.py                  lint + typecheck + test runner
    correction_loop.py           Python-side correction orchestration
    skill_loader.py              serves SKILL.md / references
    memory/                      3-tier memory (loader, distiller, compactor)
    storage/                     SQLite audit + session store
    tests/                       379 tests

copilot-harness-extension/       VS Code extension (TypeScript, v0.3.0)
    src/
        extension.ts             chat participant + routing
        mcpClient.ts             JSON-RPC stdio client
        pipeline.ts              agent driver + correction loop + event emission
        slashCommands.ts         slash-command loader
        dashboard.ts             HarnessDashboard webview owner
    media/dashboard/
        index.html, style.css, app.js
                                 webview assets (mockup translated verbatim)
    bin/
        copilot-harness.exe      PyInstaller bundle of the Python server

hooks.json                       SessionStart / PreToolUse / PostToolUse
scripts/
    policy_engine.py             PIPELINE_POLICIES — fail-closed allowlist
    pre_tool_use.py              policy gate (exit 0 = allow, 1 = deny)
    post_tool_use.py             SQLite audit log
    session_start.py             runs pipeline.yaml baseline_checks
```

---

## Instructions vs Skills

```
instructions/   RULES AND STANDARDS — always loaded, priority-ranked
                "always use type hints"; "never hardcode secrets"
                P1 universal > P2 org > P3 domain > P4 project
                P1 can never be overridden

skills/         PROCEDURES AND KNOWLEDGE — injected by the harness
                per-stage (pipeline mode) or pulled on demand
                (direct mode)
                SKILL.md ≤ 200 lines; references/ on demand;
                assets/ executed by executor.py only
```

---

## The 4-Agent feature-dev Pipeline

| Stage    | Agent     | Reads                | Writes   | Skill pushed   | Firewall            |
| -------- | --------- | -------------------- | -------- | -------------- | ------------------- |
| 1 plan   | planner   | request + MEMORY.md  | plan     | (none)         | policy only         |
| 2 design | designer  | plan                 | design   | api-design     | schema: design.json |
| 3 code   | coder     | plan + design        | code     | python         | retry sees only fix_instructions |
| 4 review | reviewer  | code (firewall)      | review   | code-review    | code only — no plan, design, or memory |

**Correction loop.** Reviewer `status: "fail"` → coder retries with
`fix_instructions` only. Max 3 attempts, then the pipeline escalates.
`skill-builder` is a meta-agent outside this pipeline — it proposes patches
to agents after 3+ recurring failures of the same kind.

**Level.** `level: 2` (multi-agent generator). A Level-1 single-generator
probe lives at `.github/pipelines/feature-dev-level1-probe/`; it's ready
to run but the measurement run is still outstanding.

---

## MCP Tools the Harness Exposes

**State**
- `harness_get_active_session()` — crash-recovery handshake
- `harness_new_session(request)` — locks agent versions, returns `session_id`
- `harness_read_stage(session_id, stage, agent_name)` — firewall + skill + memory
- `harness_write_stage(session_id, stage, output, agent_name)` — injection scan + schema check
- `harness_get_status(session_id)` · `harness_increment_attempt(session_id, stage)`

**Skills**
- `harness_get_skill(skill_id)` · `harness_get_reference(skill_id, name)`
- `harness_list_skills(agent_name)` — per-caller filtered catalog (Week 4 Day 3)

**Memory**
- `harness_get_memory_context()` — Tier 1 index + Tier 2 available
- `harness_get_memory_entry(name)` — Tier 2 on demand
- `harness_query_sessions(query, limit)` — cross-session substring search
- `harness_distill_session(session_id)` · `harness_compact_memory()`

**Execution**
- `harness_run_lint(files)` — `ruff check`, structured `LintResult`
- `harness_run_typecheck(files)` — `mypy`, structured `TypeCheckResult`
- `harness_run_tests(test_dir)` — `pytest`, structured `TestResult`
- `harness_run_hook(name)` — shells out to the matching script (Week 3c)

---

## Getting Started

**Prerequisites:** VS Code ≥ 1.93, GitHub Copilot + Copilot Chat
extensions installed and signed in.

**From source:**

```bash
git clone https://github.com/Eurus7895/CopilotHarness
cd CopilotHarness/copilot-harness-extension
npm install
npm run build        # builds the Python server binary + TS + copies assets
npm run package      # produces a .vsix
code --install-extension copilot-harness-*.vsix
```

Open any workspace containing `.github/pipelines/feature-dev/` (this repo
works). In Copilot Chat:

```
@harness /feature-dev add /jobs/:id/cancel — 401 if unauth, 403 if not owner, 404 if missing
```

The chat shows a one-line marker + **Show Harness Dashboard** button. Click
it (or run **CopilotHarness: Show Dashboard** from the palette) to see the
live pipeline card.

---

## Status & Roadmap

**Completed:**
- Week 1–2: harness core, 3-tier memory, crash recovery (379 tests).
- Week 3a: separate evaluator session (reviewer firewall).
- Week 3b: pipeline directory migration.
- Week 3c: direct mode + hooks.json + slash commands.
- Week 4: `/help`, plugin manifest, direct-mode skill pull, Tier 2 compaction +
  cross-session query, Level-1 probe infrastructure.
- **Dashboard (v0.3.0):** live pipeline card in a VS Code webview, driven
  from existing pipeline instrumentation — no new LLM calls.

**Deferred:** Run the Level-1 probe (5 representative requests through both
pipelines, record pass rates) before deciding whether to collapse
feature-dev to a single generator or keep it at Level 2.

**Planned:** Week 5 — sub agents for main-context preservation (explorer,
investigator, reviewer-aux). The dashboard's event bus already has the
shape to display spawn events.

See [`CLAUDE.md`](./CLAUDE.md) for the full day-by-day roadmap, invariants,
and architectural decisions.

---

## Resources

- Copilot `.agent.md` format — https://learn.microsoft.com/en-us/visualstudio/ide/copilot-specialized-agents
- awesome-copilot — https://github.com/github/awesome-copilot
- Custom instructions — https://code.visualstudio.com/docs/copilot/customization/custom-instructions
- AGENTS.md spec — https://agents.md
- Copilot SDK custom agents — https://docs.github.com/en/copilot/how-tos/copilot-sdk/use-copilot-sdk/custom-agents
- VS Code Language Model API — https://code.visualstudio.com/api/extension-guides/language-model
- FastMCP — https://gofastmcp.com
- Harness Engineering (Mitchell Hashimoto) — https://mitchellh.com/writing/harness-engineering
- SWE-agent paper (Princeton, NeurIPS 2024) — https://arxiv.org/abs/2405.15793
- OWASP Top 10 for Agentic AI — https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/

---

## License

MIT. See [`LICENSE`](./LICENSE).
