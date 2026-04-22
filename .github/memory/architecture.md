# Architecture Decisions

## SQLite WAL Mode

Chosen over PostgreSQL/Redis because:
- Works inside a PyInstaller one-file binary (no external process)
- WAL mode allows concurrent reads without blocking writes
- DB path controlled by `HARNESS_ROOT` env var so extension binary has a stable,
  writable location across runs (PyInstaller extracts to a temp dir on each start)
- Schema embedded as a Python string in `storage/db.py` — no `.sql` file needed at runtime

## MCP stdio (not HTTP)

Copilot agents call harness tools over JSON-RPC stdio. This means:
- No network port — works behind any corporate firewall
- Extension spawns server binary as a child process via `McpClient`
- Agents CANNOT skip the harness: it is the only path to read inputs or write outputs
- VS Code MCP panel is bypassed — extension calls `McpClient.callTool()` directly

## PyInstaller one-file binary

Extension bundles `bin/copilot-harness[.exe]` so users need no Python installation.
Constraints imposed by one-file mode:
- All file reads must use `Path(__file__).parent` or `HARNESS_ROOT` — NOT `os.getcwd()`
- Schema SQL must be an embedded string in `db.py` (temp dir changes on each run)
- Skill files (`.github/skills/`) are read from `HARNESS_ROOT`, not the PyInstaller temp dir

## Context Firewall

`read_stage_for_agent()` in `context_builder.py` enforces per-agent access:
- Planner: request only
- Designer: plan only
- Coder: plan + design (on retry: fix_instructions from review only, not full review JSON)
- Reviewer: plan + design + code
- Skill-Builder: no stage access — only sees fail patterns via separate mechanism

## Correction Loop

Max 3 attempts per code stage. After 3 failures → escalate with full context.
Pattern detector records every non-pass review issue; at threshold (3 across distinct
sessions) triggers Skill-Builder to write a proposed patch to `.github/agents/proposed/`.
Human reviews and applies via `proposed_patch_applier.py`.
