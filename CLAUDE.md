# CLAUDE.md — CopilotHarness

> Rules, conventions, and commands for working in this repo.
> Architecture, roadmap, and status → [`docs/design.md`](./docs/design.md).
> Agent session-start map → [`AGENTS.md`](./AGENTS.md).

---

## One Sentence

CopilotHarness is a Python MCP server that acts as the harness layer for
GitHub Copilot Chat. It controls what each agent sees, validates what each
agent produces, enforces the correction loop, injects skills, runs code
verification — and contains zero LLM calls itself.

**Copilot Chat reasons. CopilotHarness controls the environment.**

---

## Hard Invariants

These cannot be broken without an explicit design discussion. If a change
would violate one, stop and ask.

1. **Zero LLM calls inside the harness.** Python harness + TS extension orchestrate; only `vscode.lm.sendRequest` calls the model. New code must not import an LLM SDK.
2. **Skills are pushed, not pulled.** In pipeline mode (and agent mode, when shipped) the harness injects skill content based on the agent's `inject_skills` frontmatter. Agents cannot opt out. Pull-on-demand exists only in **direct mode**.
3. **Evaluator firewall.** The reviewer runs in a fresh session and sees `code` only — no request, plan, design, or memory. Enforced in `validation/context_builder.py` (`_STAGE_PERMISSIONS["reviewer"] = {"code"}`) and mirrored in `pipeline.ts`.
4. **Zero-cost routing.** `/feature-dev` etc → pipeline. `/agent` → agent (Week 6 — planned). `--pipeline` flag → pipeline. Bare `@harness <prompt>` → direct. No LLM call to decide which mode.
5. **Fail-closed policy engine.** `scripts/policy_engine.py` `PIPELINE_POLICIES` denies unknown pipeline/agent combinations. Never relax to fail-open.
6. **Agents live in a flat shared catalog at `.github/agents/`.** Pipelines compose them by reference from `pipeline.yaml` (`agent: agents/planner.agent.md`). Canonical role files use the bare name (`planner.agent.md`); a pipeline-specific variant of a role would be filename-prefixed (`<pipeline>-<role>.agent.md`) — but only when 3+ specific failures of the canonical agent justify it. The pipeline directory itself contains only `pipeline.yaml` + `README.md`.
7. **Append-only stage store.** Stage outputs are written once; retries write `<stage>.attemptN.md`. Never overwrite a prior attempt.
8. **No silent sub agents.** Every spawn must emit a chat marker and an audit-log row. The harness primitives (`harness_spawn_subagent` / `_complete_subagent` / `_await_subagent` / `_list_subagents`) shipped in Phase A.1; the chat-marker + audit surface lands in Phase A.3.

---

## Decision Rules

**Agent complexity levels** (pick the lowest viable):

| Level | Shape | When |
|---|---|---|
| Direct | 1 LLM call, no harness | Simple questions, lookups |
| 0 | 1 agent + skill + plan JSON | Well-defined task, simple schema |
| 1 | 1 agent + separate evaluator + correction loop | Wrong output has real cost |
| 2 | Multi-agent + evaluator | ONLY when single-agent demonstrably fails |

**Promotion checklist** — before promoting a pipeline 0→1 or 1→2:

- [ ] Observed the specific failure 3+ times
- [ ] Failure is reproducible, not random
- [ ] A better skill file or schema can't fix it
- [ ] A specialized agent would demonstrably handle the subtask better
- [ ] Documented: subtask, single-agent output, why a separate agent does better

If any item is unchecked, fix the skill file first. **Do not invent agents speculatively.**

**Instructions vs skills:**
- `instructions/` = always-loaded, priority-ranked rules (P1 universal > P2 org > P3 domain > P4 project). P1 cannot be overridden.
- `skills/` = procedures and knowledge. Pushed by the harness in pipeline mode; pulled on demand in direct mode.

---

## Conventions

**File layout:**
- Pipelines: `.github/pipelines/<name>/{pipeline.yaml, README.md}` — composes shared agents by path
- Canonical agents: `.github/agents/<role>.agent.md`
- Pipeline-specific variants: `.github/agents/<pipeline>-<role>.agent.md`
- Shared cross-pipeline agents (skill-builder etc.): `.github/agents/<name>.agent.md`
- Slash commands: `.github/commands/<name>.md` (frontmatter-driven; loader is `slashCommands.ts`)
- Skills: `.github/skills/<name>/SKILL.md` (+ `assets/`, `references/`)
- Memory: `.github/memory/{MEMORY.md, architecture.md, failure-patterns.md}` (3-tier)
- Hook scripts: `scripts/<event>.py`; wired in `hooks.json`

**Adding things:**
- New slash command → drop a `.md` file in `.github/commands/`. No code change.
- New skill → add directory under `.github/skills/`. Wire injection in `pipeline.yaml`.
- New pipeline → not yet — feature-dev must be validated first (see `docs/design.md` § Build Roadmap).

**Editing:**
- Prefer editing existing files. Don't create new top-level docs.
- Don't add status/version/week-number footers — they rot. Status lives in `docs/design.md`.
- Don't add scaffolding comments or backwards-compat shims.

**Text I/O — always pass `encoding="utf-8"` explicitly.**
- `Path.read_text()` / `Path.write_text()` / `open()` without an `encoding=`
  argument falls back to the platform default — `cp1252`/`charmap` on Windows
  — and crashes on the em dashes, arrows, and other non-ASCII characters in
  agent `.md` files, skill content, and stage outputs. The harness has hit
  this once already (`harness_new_session` failing with
  `'charmap' codec can't decode byte 0x90`); never reintroduce it.
- Same rule for `json.load`/`json.dump` when the file handle is opened by
  this codebase — open with `encoding="utf-8"` first.

---

## Commands

```bash
# Python harness
cd copilot-harness
pip install -e .
pytest tests/ -v                 # 487 tests

# Per-component checks
ruff check copilot-harness/
mypy copilot-harness/

# VS Code extension
cd copilot-harness-extension
npm install
npm run package                  # builds copilot-harness-extension-<v>.vsix

# Install built extension
code --install-extension copilot-harness-extension-<v>.vsix
```

---

## MCP Tools

Names + one-line purpose. Full schemas and behavior in `docs/design.md` § MCP Tools.

| Tool | Purpose |
|---|---|
| `harness_get_active_session` | Crash recovery — returns interrupted session or null |
| `harness_new_session` | Start pipeline, lock agent versions |
| `harness_read_stage` | Read with firewall + skill + memory injection |
| `harness_write_stage` | Validate output + injection scan + append-only store |
| `harness_get_status` | Pipeline stage summary |
| `harness_increment_attempt` | Bump attempt counter for retry |
| `harness_get_skill` | Load `SKILL.md` on demand |
| `harness_list_skills` | Per-caller filtered skill catalog |
| `harness_get_reference` | Load reference document |
| `harness_get_memory_context` | Tier-1 index + Tier-2 available |
| `harness_get_memory_entry` | Load Tier-2 entry on demand |
| `harness_query_sessions` | Cross-session substring search |
| `harness_distill_session` | Append session failures to Tier-2 memory |
| `harness_compact_memory` | Prune `failure-patterns.md` when > 5 KB |
| `harness_run_lint` | ruff |
| `harness_run_typecheck` | mypy |
| `harness_run_tests` | pytest |
| `harness_run_hook` | Execute `hooks.json` lifecycle hook |
| `harness_spawn_subagent` | Validate spawn (policy ∩ caller tools) + insert sub-session row, return handle (Phase A.1) |
| `harness_complete_subagent` | Record terminal result; verify_subagent_summary cap + secrets / injection / schema check; auto-escalate on max_turns / wall-clock breach (Phase A.1 + A.2) |
| `harness_await_subagent` | Poll until terminal or wall-clock kill; return summary + structured + tools_used + turns + escalated (Phase A.1) |
| `harness_list_subagents` | Return spawn allow-list for the calling main agent (Phase A.1) |
| `harness_get_subagent_context` | Return firewalled `{brief, role, role_skill, allowed_tools}` for a handle (Phase A.2) |

---

## Hooks

| Hook | Script | Behavior |
|---|---|---|
| `SessionStart` | `scripts/session_start.py` | Run `baseline_checks` from `pipeline.yaml` |
| `PreToolUse` | `scripts/pre_tool_use.py` | Policy gate — exit 0 allow, 1 deny |
| `PostToolUse` | `scripts/post_tool_use.py` | SQLite audit log to `storage/audit.db` |

`on-eval-fail` and `on-escalate` are reserved — not wired yet.

**Rule:** "Never send an LLM to do a linter's job." Deterministic checks belong in hooks.
