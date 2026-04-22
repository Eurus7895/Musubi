# MEMORY.md — Tier 1 Index

> Always loaded by the harness (~200 tokens). Pointers to where knowledge lives.
> Load Tier 2 entries on demand via `harness_get_reference("memory", "<name>.md")`.

---

## What This Project Is

CopilotHarness: Python MCP stdio server. Harness layer for GitHub Copilot Chat.
Controls what each agent sees, validates output, enforces correction loops, injects skills.
**Zero LLM calls inside the harness.** Copilot Chat is the LLM; harness is the environment.

---

## Key Architecture Decisions

| Decision | Choice | Reason |
|---|---|---|
| Persistence | SQLite WAL | Zero infra, works in PyInstaller one-file binary |
| Agent communication | MCP stdio | Copilot agents cannot bypass the harness |
| LLM interface | vscode.lm.sendRequest | No `api.githubcopilot.com` — works behind corporate firewall |
| Distribution | PyInstaller one-file | No Python on user machine required |
| Schema | Embedded string in db.py | No file dependency in one-file binary |

Full rationale → load `architecture.md` from this folder.

---

## Known Failure Patterns

Load `failure-patterns.md` from this folder for coder/reviewer failure history.

---

## Pipeline Stages

```
plan → design → code → review
```

Write-once per attempt. Reviewer "fail" → correction loop (max 3) → escalate.

---

## Active Tier 2 Files

- `architecture.md` — SQLite choice, MCP rationale, PyInstaller constraints
- `failure-patterns.md` — recurring coder/reviewer failures, distilled from sessions
