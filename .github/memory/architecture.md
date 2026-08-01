# Architecture Decisions

## SQLite WAL Mode

Chosen over PostgreSQL/Redis because:
- Works inside a PyInstaller one-file binary (no external process)
- WAL mode allows concurrent reads without blocking writes
- DB path controlled by `MUSUBI_ROOT` env var so extension binary has a stable,
  writable location across runs (PyInstaller extracts to a temp dir on each start)
- Schema embedded as a Python string in `storage/db.py` — no `.sql` file needed at runtime

## MCP stdio (not HTTP)

Copilot agents call harness tools over JSON-RPC stdio. This means:
- No network port — works behind any corporate firewall
- Extension spawns server binary as a child process via `McpClient`
- Agents CANNOT skip the harness: it is the only path to read inputs or write outputs
- VS Code MCP panel is bypassed — extension calls `McpClient.callTool()` directly

## PyInstaller one-file binary

Extension bundles `bin/musubi[.exe]` so users need no Python installation.
Constraints imposed by one-file mode:
- All file reads must use `Path(__file__).parent` or `MUSUBI_ROOT` — NOT `os.getcwd()`
- Schema SQL must be an embedded string in `db.py` (temp dir changes on each run)
- Skill files (`.github/skills/`) are read from `MUSUBI_ROOT`, not the PyInstaller temp dir

## Context Firewall

`read_stage_for_agent()` in `context_builder.py` enforces per-agent access:
- Planner: request only
- Designer: plan only
- Coder: plan + design (on retry: fix_instructions from review only, not full review JSON)
- Reviewer: **code only** (Week 3a evaluator firewall — no request, plan, design,
  Tier 1 memory, or dynamic `required_skills` injection; only the static
  `code-review` skill is auto-injected)
- No meta-agent receives stage access; fail patterns remain evidence for humans.

## Stage Loop

Each stage has an explicit attempt ceiling and deterministic acceptance gate.
Failed attempts are append-only evidence; exhaustion escalates with full context.
No meta-agent silently authors corrective prompts or patches.

## Pipeline Directory Layout (Week 3b)

Each pipeline lives at `.github/pipelines/<name>/` with its own
`pipeline.yaml`, `agents/`, and `README.md`. `feature-dev` ships at
`level: 2` (4-agent generator + separate evaluator). The shared
`.github/agents/` path remains available for worker definitions;
`state.AGENTS_DIRS` globs the pipeline directory first and falls back to it.

## Routing & Hooks (Phase D)

The extension routes at string-match cost (no LLM):
- Input starts with `/<pipeline-name>` → pipeline mode (full guardrails
  + evaluator firewall). Other slash commands dispatch via
  `.github/commands/*.md` frontmatter.
- Otherwise → agent: persistent chat per chat_id, spawns
  sub-agents on demand, Tier-1 memory injected, reactive compaction.

`hooks.json` + `scripts/` wire deterministic Python scripts to
`SessionStart` (baseline checks), `PreToolUse` (policy gate,
fail-closed), and `PostToolUse` (SQLite audit log). The harness
exposes `musubi_run_hook(event, payload)` as the entry point.
