# CopilotHarness

A harness engineering layer for GitHub Copilot Chat in VS Code. The harness
controls what each agent sees, validates what each agent produces, injects
skills, enforces a correction loop, and runs code verification.

**Copilot Chat reasons. CopilotHarness controls the environment.**

> Same model + same task + changed environment = better outcomes.
> (Princeton SWE-agent paper: 64% improvement from harness design alone.)

---

## Documentation

| File | For |
|---|---|
| `README.md` *(you are here)* | Install · build · run · contribute |
| [`CLAUDE.md`](./CLAUDE.md) | Rules · invariants · conventions · commands (Claude Code memory) |
| [`AGENTS.md`](./AGENTS.md) | Session-start orientation map for agents |
| [`docs/design.md`](./docs/design.md) | Full architecture · schemas |
| [`docs/roadmap.md`](./docs/roadmap.md) | Build roadmap · status · phase plans |

Read `CLAUDE.md` before making code changes — it lists the hard invariants
(zero LLM in harness, evaluator firewall, fail-closed policy, etc.).

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- VS Code with the GitHub Copilot Chat extension

`npm run setup` handles the Python venv and PyInstaller — no manual
Python setup required.

---

## Build & Install

```bash
cd copilot-harness-extension
npm install -g @vscode/vsce      # one-time, if not already installed
npm run setup                    # one-time: venv + server (editable) + PyInstaller + npm deps
npm run package                  # builds binary, compiles TS, produces .vsix
code --install-extension copilot-harness-extension-<version>.vsix
```

Reopen VS Code. The **CopilotHarness** output channel confirms the server
started. Then in Copilot Chat:

```
@harness /feature-dev add a login endpoint that validates email + password
@harness how does the correction loop work?
```

The first runs a full 4-agent governed pipeline. The second is a direct
single-call answer with no harness overhead.

---

## Dev Mode

For working on the harness itself without rebuilding the `.vsix` each
time:

```bash
cd copilot-harness-extension
npm run setup        # creates .venv, installs server in editable mode
code ..              # open the repo in VS Code
```

VS Code reads `.vscode/mcp.json` and spawns `python server.py` directly
from the venv. Harness tools then appear in Copilot Chat's tool picker;
agents call them manually. Edit Python sources, restart the MCP server
from the panel — no extension rebuild required.

---

## How It Works (one-paragraph summary)

The extension spawns the bundled server binary on VS Code start (JSON-RPC
over stdio). When you type `@harness <input>`, `extension.ts` routes by
pure string check — zero LLM cost:

- Starts with `/` → **pipeline mode** (governed agents, validation, audit trail)
- Contains `--pipeline` → **pipeline mode** (forced)
- Everything else → **direct mode** (single `vscode.lm.sendRequest`, no harness)

In pipeline mode, the harness pushes context per stage (firewall + skill
injection + memory), the agent reasons via Copilot, the harness validates
and stores the output, and the reviewer evaluates in a fresh session. A
fail triggers up to 3 retries before escalation.

Full pipeline diagram, MCP tool list, schemas, hooks, and YAML format live
in [`docs/design.md`](./docs/design.md).

---

## Slash Commands

Slash commands are `.github/commands/*.md` files with YAML frontmatter
declaring an action (`pipeline`, `step`, `continue`, `status`, `help`).
Add a command by dropping a new `.md` — no code change required.

| Command | Mode | What it does |
|---|---|---|
| `@harness <question>` | direct | Single Copilot call, no pipeline |
| `@harness /feature-dev <task>` | pipeline | 4-agent governed pipeline |
| `@harness /planner <task>` | pipeline | Planner only (new session) |
| `@harness /designer` `/coder` `/reviewer` | pipeline | Run a single stage on the active session |
| `@harness /continue` | pipeline | Run the next pending stage |
| `@harness /status` | pipeline | Show active session progress |
| `@harness /help` | — | List available commands |
| `@harness <task> --pipeline` | pipeline | Force pipeline for free-form input |

---

## Project Layout

```
.github/
    pipelines/feature-dev/      pipeline.yaml + agents/*.agent.md
    commands/                   slash command files (frontmatter-driven)
    agents/                     shared catalog: main agents (skill-builder)
                                + sub-agent roles (explorer, investigator,
                                reviewer-aux — Phase A.3)
    instructions/               priority-ranked rules
    skills/                     domain skills (SKILL.md + assets/ + references/)
    memory/                     3-tier memory (MEMORY.md + Tier 2)

copilot-harness/                Python MCP server (zero LLM)
copilot-harness-extension/      VS Code extension (TypeScript)
hooks.json + scripts/           SessionStart / PreToolUse / PostToolUse
docs/design.md                  full architecture + schemas
docs/roadmap.md                 build roadmap + status
```

Detailed file-by-file breakdown lives in
[`docs/design.md`](./docs/design.md) § File Structure.

---

## Diagnostics

`Ctrl+Shift+U` → **CopilotHarness** output channel:

```
CopilotHarness v<version> activating...
Checking: ...\bin\copilot-harness.exe — found
Starting MCP server...
Tools available (24): harness_get_active_session, harness_new_session, ...
                       harness_spawn_subagent, harness_complete_subagent, ...
CopilotHarness ready. Use @harness in Copilot Chat.
```

If the server fails to start, the channel shows the exact error and the
binary path it tried to spawn.

---

## Contributing

1. Read [`CLAUDE.md`](./CLAUDE.md) first — it lists the hard invariants
   that cannot be broken without an explicit design discussion.
2. Skim [`AGENTS.md`](./AGENTS.md) for the file-layout map.
3. Look at [`docs/roadmap.md`](./docs/roadmap.md) and
   [`docs/design.md`](./docs/design.md) § Known TODOs for the current backlog.
4. Run the checks listed in [`CLAUDE.md`](./CLAUDE.md) § Commands before
   opening a PR.
5. Don't add new pipelines until `feature-dev` is validated (see roadmap).

---

## License

See [`LICENSE`](./LICENSE).
