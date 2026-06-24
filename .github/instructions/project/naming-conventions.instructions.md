---
applyTo: "**"
priority: P4
description: Project-specific naming rules — Python identifiers, session/state string literals, MCP tool name pattern, file names, result type suffixes, and custom exception class suffixes.
---

# Naming Conventions — Project (P4)

## Python Identifiers

| Kind | Convention | Example |
|------|-----------|---------|
| Module | `snake_case` | `context_builder.py` |
| Class | `PascalCase` | `ValidationResult`, `SessionState` |
| Function/method | `snake_case` | `build_context`, `scan_injection` |
| Variable | `snake_case` | `session_id`, `agent_name` |
| Constant | `UPPER_SNAKE` | `MAX_RETRY_ATTEMPTS` |
| Private | leading `_` | `_validate_path` |

## Session and State Identifiers

- Session IDs: 8-character hex strings (e.g., `a3f9c1d2`)
- Stage names: lowercase literals — `plan`, `design`, `code`, `review`
- Agent names: lowercase literals — `planner`, `designer`, `coder`, `reviewer`, `skill-builder`
- Status values: lowercase — `pending`, `in_progress`, `complete`, `escalated`

## MCP Tool Names

Pattern: `musubi_{verb}_{noun}`

| Tool | Verb | Noun |
|------|------|------|
| `musubi_write_stage` | write | stage |
| `musubi_read_stage` | read | stage |
| `musubi_new_session` | new | session |
| `musubi_get_status` | get | status |
| `musubi_get_skill` | get | skill |
| `musubi_get_reference` | get | reference |
| `musubi_run_asset` | run | asset |
| `musubi_run_lint` | run | lint |
| `musubi_run_typecheck` | run | typecheck |
| `musubi_run_tests` | run | tests |

## File Names

- Skill directories: `kebab-case` — `code-review`, `api-design`, `database-patterns`
- Agent files: `{role}.agent.md` — `coder.agent.md`, `reviewer.agent.md`
- Instruction files: `{topic}.instructions.md` — `security.instructions.md`
- Test files: `test_{module}.py` — `test_state.py`, `test_verifier.py`

## Result Types

Suffix all result dataclasses with `Result`:
- `ValidationResult`, `LintResult`, `TypeCheckResult`, `TestResult`, `AssetResult`

## Error Classes

Suffix with `Error`:
- `SecretDetectedError`, `SchemaValidationError`, `InjectionDetectedError`
- `StageNotFoundError`, `SessionNotFoundError`, `SkillNotFoundError`
