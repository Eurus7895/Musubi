# MEMORY.md — Tier 1 Index

> Always loaded by the harness (~200 tokens). Pointers only — load
> Tier 2 entries on demand via `harness_get_memory_entry(<name>)`.

CopilotHarness — a Python MCP stdio server. Harness layer for GitHub
Copilot Chat. Zero LLM calls inside the harness; Copilot Chat is the
LLM, the harness is the environment. Two modes:
`/<pipeline-name>` → pipeline; everything else → agent.

## Active Tier 2 Files

- `architecture.md` — SQLite/WAL choice, MCP stdio rationale,
  PyInstaller constraints, embedded-schema decision, vscode.lm
  interface trade-offs.
- `failure-patterns.md` — recurring coder/reviewer failures, distilled
  from prior sessions and live triggers (reviewer-fail, frustration
  regex).
- `project-profile.md` — language / framework / doc tool / conventions
  auto-detected at SessionStart by `copilot-harness/workspace/detector.py`.
  Consumed by the skill router (MVP item 6 / Track D.3) to filter the
  catalog so the model only sees skills whose `applies-to:` matches
  this workspace's stack.

