---
applyTo: "**"
priority: P4
---

# Architecture Decisions — Project (P4)

## Zero LLM Inside Harness

Every harness component (`state.py`, `context_builder.py`, `verifier.py`,
`executor.py`, `correction_loop.py`, `skill_loader.py`, `pattern_detector.py`)
is pure Python with zero LLM calls. Copilot is the only LLM. This is not
a performance decision — it is a correctness and reproducibility decision.
Deterministic harness + LLM agent = auditable system.

## Append-Only State

Session state is append-only. Completed stage outputs are never overwritten.
Retries create new attempt rows alongside previous ones. This enables:
- Crash recovery to last known good state
- Full audit trail of every attempt
- Pattern detection across sessions

## Context Firewall Is Structural

The context firewall in `context_builder.py` is not a suggestion to agents.
It is enforced by the harness before returning data. Agents cannot request
data they are not authorized to see — `harness_read_stage` simply will not
return it regardless of what the agent asks for.

## Subprocess for Execution

`executor.py` uses `subprocess` with `shell=False` and explicit timeouts.
The alternative (Docker sandbox) is deferred to Week 2. The interface is
the same either way — callers use `executor.run_lint(files)`, not subprocess
directly. This makes the switch to Docker a single-file change.

## Agent Prompts Assembled in One Place

All agent context is assembled by `context_builder.py`. No other file
constructs prompts or context dicts. This is enforced by code review and
is the mechanism that makes the context firewall work.

## Skill-Builder Cannot Auto-Apply Changes

Skill-Builder writes to `.github/agents/proposed/` only. A human must
review and apply the patch. This is a deliberate safety gate — the system
can propose improvements to itself but cannot silently change its own
behavior.

## MCP stdio Transport

The harness uses MCP stdio (not HTTP). VS Code spawns the process via
`.vscode/mcp.json`. This keeps the harness local, avoids network
configuration, and makes it easy to test with `echo | python server.py`.

## SQLite for State

Single-file SQLite database. No server to manage. No connection pooling
needed — SQLite handles concurrent readers with WAL mode. If the project
outgrows SQLite, `db.py` is the single abstraction layer to replace.
