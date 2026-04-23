# CopilotHarness

CopilotHarness is a pure Python MCP server that acts as the harness layer for
GitHub Copilot's multi-agent team — it controls what each agent sees, validates
what each agent produces, enforces the correction loop, serves skills on
demand, and runs code to verify it actually works.

> **Status:** Pre-build. Copilot native files and the Python package are both
> still being scaffolded. See [Status & Roadmap](#status--roadmap) below.
> Contributors should read [`CLAUDE.md`](./CLAUDE.md) for the full internal
> design.

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
What CopilotHarness does: state, context firewall, validation, execution, skills
```

## Zero-LLM Principle

There are **zero LLM calls inside the harness**. Every component is
deterministic Python.

| Component              | LLM? | Implementation                        |
| ---------------------- | ---- | ------------------------------------- |
| `server.py`            | no   | MCP stdio, routes tool calls          |
| `state.py`             | no   | Python dataclass + SQLite             |
| `context_builder.py`   | no   | dict filtering + regex                |
| `verifier.py`          | no   | jsonschema + regex secrets scan       |
| `executor.py`          | no   | subprocess: ruff, mypy, pytest        |
| `correction_loop.py`   | no   | orchestration logic                   |
| `skill_loader.py`      | no   | file I/O                              |
| `pattern_detector.py`  | no   | SQLite count threshold                |
| Copilot (VS Code)      | yes  | all agent reasoning happens here      |

---

## The Two Layers

### Layer 1 — Copilot native files (loaded automatically by Copilot)

```
.github/
    AGENTS.md                      P1: global always-on rules
    copilot-instructions.md        P1: global always-on conventions
    instructions/
        universal/                 P1: world-wide, never overridden
        org/                       P2: team-wide standards
        domain/                    P3: technology-specific (applyTo scoped)
        project/                   P4: repo-specific overrides
    agents/
        planner.agent.md
        designer.agent.md
        coder.agent.md
        reviewer.agent.md
        skill-builder.agent.md
        proposed/                  Skill-Builder writes here, human approves
    skills/
        code-review/
            SKILL.md               max 200 lines
            assets/                executable scripts, templates
            references/            loaded only when needed
        api-design/
        database-patterns/
```

### Layer 2 — CopilotHarness (MCP server, pure Python)

```
copilot-harness/
    server.py                MCP stdio server, exposes all tools
    state.py                 append-only session state
    context_builder.py       context firewall + injection detection
    verifier.py              schema validation + secrets scan
    executor.py              lint + type check + test runner
    correction_loop.py       reviewer → coder retry orchestration
    skill_loader.py          serves SKILL.md, references, runs assets
    memory/
        cross_session.db     SQLite: fail patterns across sessions
        pattern_detector.py  detects recurring failures
    storage/
        db.py
        schema.sql
```

---

## Instructions vs Skills

```
instructions.md   RULES AND STANDARDS to follow
                  "always use type hints"
                  "never hardcode secrets"
                  loaded automatically by Copilot, priority-ranked

skills/           PROCEDURES AND KNOWLEDGE to apply
                  "how to review code step by step"
                  loaded on demand via MCP tool call
                  SKILL.md ≤ 200 lines; deep docs in references/
                  executable logic in assets/, run by executor.py
```

### Instructions Priority System

```
P1 — Universal   never overridden (security, ethics)
P2 — Org         team-wide standards (git conventions, review standards)
P3 — Domain      technology-specific, applyTo scoped (python, api, database)
P4 — Project     repo-specific overrides (naming, architecture)
```

Conflict resolution: higher priority wins. P1 always wins.

### Skills: Progressive Disclosure

```
L1 — SKILL.md always loaded (~200 lines), handles 80% of cases
L2 — references/*.md loaded on demand via harness_get_reference
L3 — assets/ scripts executed via harness_run_asset
     (the agent never runs scripts directly)
```

---

## The 5-Agent Team

| Agent         | File                      | Tools              | Reads                                   | Writes                          |
| ------------- | ------------------------- | ------------------ | --------------------------------------- | ------------------------------- |
| Planner       | `planner.agent.md`        | view, glob         | request + P1/P2 instructions            | `session.plan`                  |
| Designer      | `designer.agent.md`       | view, glob         | `session.plan`                          | `session.design`                |
| Coder         | `coder.agent.md`          | view, edit, bash   | `session.plan` + `session.design`       | `session.code`                  |
| Reviewer      | `reviewer.agent.md`       | view, glob         | all stages + request                    | `session.review`                |
| Skill-Builder | `skill-builder.agent.md`  | view, edit         | reviewer feedback + target skill        | `.github/agents/proposed/`      |

Each agent declares its tools and contracts in frontmatter and in
Input/Output Contract sections. The harness enforces firewall rules per agent
identity at the MCP boundary.

---

## MCP Tools Exposed

The harness exposes the following tools over MCP stdio. Copilot agents call
them natively from their agent loop — no custom integration needed.

**State**
- `harness_new_session(request)` — creates a session, locks agent versions
- `harness_write_stage(session_id, stage, output)` — validates + stores
- `harness_read_stage(session_id, stage)` — firewall-filtered read
- `harness_get_status(session_id)` — pipeline status

**Skills**
- `harness_get_skill(skill_id)` — returns `SKILL.md` content
- `harness_get_reference(skill_id, reference_name)` — on-demand deep doc
- `harness_run_asset(skill_id, asset_name, input_json)` — runs asset in
  subprocess via `executor.py`

**Execution**
- `harness_run_lint(files)` — `ruff check`, structured `LintResult`
- `harness_run_typecheck(files)` — `mypy`, structured `TypeCheckResult`
- `harness_run_tests(test_dir)` — `pytest`, structured `TestResult`

### Connecting from VS Code

`.vscode/mcp.json`:

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

---

## Processing Flow

1. `copilot-harness serve` — MCP server starts (stdio). VS Code reads
   `.vscode/mcp.json` and spawns the process.
2. User sends a request in Copilot Chat. Planner loads, calls
   `harness_new_session`, produces a plan, calls `harness_write_stage("plan")`.
   `verifier.py` runs schema + secrets + injection checks before anything is
   stored.
3. Designer reads the plan through the firewall, optionally pulls a skill,
   writes `session.design`.
4. Coder reads plan + design, optionally pulls skills/references, writes
   `session.code`.
5. Reviewer reads all stages, produces structured feedback. On fail,
   `correction_loop.py` sends `fix_instructions` back to the Coder (max 3
   attempts, then escalates).
6. On Reviewer pass, `executor.py` runs lint + typecheck + tests. Any failure
   routes back to the Coder as `fix_instructions`.
7. `pattern_detector.py` records session results. After 3 recurring failures of
   the same kind, Skill-Builder is triggered and writes a proposal to
   `.github/agents/proposed/` for human approval.

---

## Harness Engineering — 7 Components

1. **Tool Design** — each agent has an explicit `tools` list in its
   `.agent.md`; `context_builder.py` validates tool-call logs.
2. **Feedback Loops** — structured Reviewer → Coder schema with severity,
   `fix_instruction`, and `checklist_item`; max 3 retries then escalate.
3. **State Management** — append-only session state, `pending → in_progress →
   complete`, write-once per attempt, resumable after crash.
4. **Context Firewall** — per-agent allowlists on what stage outputs are
   visible; injection detection scans every output before storage.
5. **Security & Permissions** — three layers: Copilot tool restrictions,
   Skill-Builder scoped to `proposed/` + Behavior Rules only, and a secrets
   scan on every agent output.
6. **Verification** — structural (`verifier.py`), domain (Reviewer with a
   fixed checklist), and execution (`executor.py`) — three independent gates.
7. **Architecture Enforcement** — agent versions locked at session start;
   Skill-Builder cannot mutate active-session agents; all prompts are
   assembled exclusively by `context_builder.py`.

---

## Status & Roadmap

**Current phase:** Pre-build. Day 1 has not started.

- **Day 1** — Copilot native files: `AGENTS.md`, `copilot-instructions.md`,
  `instructions/{universal,org,domain,project}/`, all 5 `.agent.md` files,
  first skill (`code-review/`).
- **Day 2** — `storage/schema.sql`, `state.py`, `context_builder.py` with
  per-agent firewall + injection detection passing tests.
- **Day 3** — `verifier.py` + `correction_loop.py`: max-3-attempt loop with
  clean escalation; secrets/injection scans.
- **Day 4** — `executor.py`, `skill_loader.py`, `server.py` + `.vscode/mcp.json`
  wiring so VS Code connects and tools round-trip.
- **Day 5** — `memory/pattern_detector.py` + `pipeline.py`: self-improvement
  loop that writes proposals to `.github/agents/proposed/`.
- **Week 2** — hardening: unit tests per component, crash recovery, README
  setup guide, edge cases, priority-enforcement docs.

See [`CLAUDE.md`](./CLAUDE.md) for detailed per-day checklists and the
testing checklist.

---

## Getting Started

The Python package is not yet published. Once Day 4 lands, the typical setup
will be:

```bash
pip install copilot-harness        # not yet available
copilot-harness serve              # starts the MCP stdio server
```

Then open the repo in VS Code with `.vscode/mcp.json` present. The 5 agents
will appear in the Copilot agent picker.

Contributors: read [`CLAUDE.md`](./CLAUDE.md) first. It is the source of
truth for architecture, schemas, and the build roadmap.

---

## Resources

- Copilot `.agent.md` format — https://learn.microsoft.com/en-us/visualstudio/ide/copilot-specialized-agents
- awesome-copilot — https://github.com/github/awesome-copilot
- Custom instructions — https://code.visualstudio.com/docs/copilot/customization/custom-instructions
- AGENTS.md spec — https://agents.md
- Copilot SDK custom agents — https://docs.github.com/en/copilot/how-tos/copilot-sdk/use-copilot-sdk/custom-agents
- Harness Engineering (Mitchell Hashimoto) — https://mitchellh.com/writing/harness-engineering
- SWE-agent paper (Princeton, NeurIPS 2024) — https://arxiv.org/abs/2405.15793
- OWASP Top 10 for Agentic AI — https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/

---

## License

MIT. See [`LICENSE`](./LICENSE).
