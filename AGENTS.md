# AGENTS.md — CopilotHarness

## Project Context

CopilotHarness is a pure Python MCP server that acts as the harness layer for
GitHub Copilot's multi-agent team. It controls what each agent sees, validates
what each agent produces, enforces the correction loop, serves skills on demand,
and runs code to verify it works.

**Copilot is the LLM. CopilotHarness is the harness. Zero LLM calls inside the harness.**

## Global Rules (apply to all agents)

1. **Always use MCP tool calls** for reading and writing session state. Never access
   session state directly. Use `harness_read_stage` and `harness_write_stage`.

2. **Output valid JSON only** matching your agent's schema. No prose, no markdown
   wrappers, no explanations outside the JSON structure.

3. **Never hardcode secrets.** No API keys, tokens, passwords, or private keys in
   any output or code.

4. **Respect the context firewall.** Only read stages you are authorized to read.
   If `harness_read_stage` returns empty, that means you have no input for that stage.

5. **Check your confidence.** If confidence is `low`, explain why in `implementation_notes`
   or equivalent field.

6. **Halt on schema rejection.** If `harness_write_stage` returns a validation error,
   fix the output and retry. Do not proceed to the next stage.

7. **Never modify files outside your declared scope.** Only touch files listed in
   `session.plan.files_affected`.

8. **Security first.** P1 security and ethics instructions cannot be overridden by
   any instruction at P2, P3, or P4 level.

## Commands

| Command | What it does |
|---------|-------------|
| `@planner` | Invoke planner agent — creates or updates the session plan |
| `@designer` | Invoke designer agent — produces architecture and interface design |
| `@coder` | Invoke coder agent — implements code based on plan and design |
| `@reviewer` | Invoke reviewer agent — reviews code output, produces pass/fail |
| `@skill-builder` | Invoke skill-builder agent — proposes new skills based on failure patterns |

## Pipeline Order

```
@planner → @designer → @coder → @reviewer → (executor runs lint/tests)
                                    ↑              |
                                    └── retry ─────┘ (max 3 attempts)
```

## Session State Stages

| Stage | Written by | Read by |
|-------|-----------|---------|
| `plan` | Planner | Designer, Coder, Reviewer |
| `design` | Designer | Coder, Reviewer |
| `code` | Coder | Reviewer |
| `review` | Reviewer | Coder (fix_instructions only on retry) |

## Agent Files

All agent definitions live in `.github/agents/`. Do not modify `.agent.md` files
for agents with active sessions. Proposed changes go to `.github/agents/proposed/`.

## Skills

Skills live in `.github/skills/{skill-id}/`. Load them via MCP:
- `harness_get_skill(skill_id)` — loads SKILL.md
- `harness_get_reference(skill_id, ref_name)` — loads a reference file on demand
- `harness_run_asset(skill_id, asset_name, input)` — runs an asset script via executor

Never run asset scripts directly. Always use `harness_run_asset`.
