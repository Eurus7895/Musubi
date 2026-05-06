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

One-shot install:

```bash
cd copilot-harness-extension
npm install -g @vscode/vsce      # one-time, if not already installed
npm run all                      # setup + package + install:vsix
# Ctrl+Shift+P → "Developer: Reload Window"
```

`npm run all` runs setup (venv + PyInstaller + npm deps), packages the
`.vsix`, and installs it via `code --install-extension --force`. Each step
is also exposed individually if you want to skip one:

| Script | Does |
|---|---|
| `npm run setup` | One-time: create `.venv`, editable-install the server, install PyInstaller, install npm deps. |
| `npm run build` | `build:server` (PyInstaller binary) + `build:assets` (bundle `.github/`) + `compile` (TypeScript) — in parallel. |
| `npm run package` | `build` + `vsce package` (produces `copilot-harness-extension-<version>.vsix`). |
| `npm run install:vsix` | `code --install-extension --force` on the newest local `.vsix`. Requires `code` on PATH. |
| `npm run all` | `setup` + `package` + `install:vsix` — full bringup from a fresh checkout. |

Reload VS Code after install. The **CopilotHarness** output channel confirms
the server started. Then in Copilot Chat (in **Ask** mode — chat
participants don't work in Agent or Edit mode):

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

- `/<pipeline-name> <task>` → **pipeline mode** (governed agents, validation, audit trail)
- Anything else → **orchestrator mode** (persistent chat, spawns sub-agents
  on demand, Tier-1 memory injected, reactive compaction)

In pipeline mode, the harness pushes context per stage (firewall + skill
injection + memory), the agent reasons via Copilot, the harness validates
and stores the output, and the reviewer evaluates in a fresh session. A
fail triggers up to 3 retries before escalation.

In orchestrator mode, one main agent holds the chat. It can spawn
read-only sub-agents (`explorer`, `investigator`, `reviewer-aux`,
`summarizer`) or governed pipeline-stage agents via `harness_spawn_subagent`,
budgeted to 3 spawns per role per turn.

Full pipeline diagram, MCP tool list, schemas, hooks, and YAML format live
in [`docs/design.md`](./docs/design.md).

---

## Slash Commands

Slash commands are `.github/commands/*.md` files with YAML frontmatter
declaring an action (`pipeline`, `step`, `continue`, `status`, `help`).
Add a command by dropping a new `.md` — no code change required.

| Command | Mode | What it does |
|---|---|---|
| `@harness <prompt>` | orchestrator | Persistent chat, spawns sub-agents on demand |
| `@harness /feature-dev <task>` | pipeline | 4-agent governed pipeline |
| `@harness /planner <task>` | pipeline | Planner only (new session) |
| `@harness /designer` `/coder` `/reviewer` | pipeline | Run a single stage on the active session |
| `@harness /continue` | pipeline | Run the next pending stage |
| `@harness /status` | pipeline | Show active session progress |
| `@harness /help` | — | List available commands |

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

`Ctrl+Shift+U` → **CopilotHarness** output channel. A healthy startup looks
like:

```
CopilotHarness v<version> activating...
Checking: ...\bin\copilot-harness.exe — found
Starting MCP server...
MCP server started. Listing tools...
Tools available (24): harness_get_active_session, harness_new_session, ...
CopilotHarness ready. Use @harness in Copilot Chat.
```

Any line beginning `[server]` is the harness server's stderr piped through
the extension — Python tracebacks land there during activation.

---

## Troubleshooting

Symptom-first guide. Open the **CopilotHarness** output channel before
anything else; nine times out of ten it tells you exactly what failed.

### `@harness` doesn't respond / chat input frozen

1. **Check the chat mode.** Chat participants only work in Copilot Chat's
   **Ask** mode. The dropdown above the model picker must say **Ask**, not
   **Agent** or **Edit**. In Agent mode `@harness` mentions are silently
   dropped.
2. **Check the output channel.** Match the last line you see against the
   table below.

| Last line in the channel | Diagnosis | Fix |
|---|---|---|
| `Server binary not found...` | The `.vsix` was installed without its PyInstaller binary. | From `copilot-harness-extension/`: `npm run all`. Reload window. |
| `Starting MCP server...` then nothing | Server launched but never replied to the JSON-RPC `initialize` handshake. After 15 s the extension surfaces `MCP call initialize timed out after 15000 ms`. | Look for `[server] ...` lines just below — they carry the Python traceback. If there are none, the binary is writing JSON to stderr instead of stdout, or to neither (built with the wrong entrypoint). |
| `[server] Traceback (most recent call last):` | The Python server crashed on startup. | Read the traceback. Common cause: an editable install picked up stale `.pyc` files or a missing dep — `pip install -e copilot-harness/` from a fresh venv usually fixes it. |
| `ERROR starting server: ...` | The extension caught the failure cleanly. | The error message is the actual reason — file not executable, antivirus quarantine, wrong arch, bad shebang. |
| `MCP server started. Listing tools...` | Server is fine; the freeze is elsewhere. | Disable other Copilot Chat extensions one at a time and reload. |

### `npm run setup` fails on Windows with `not a Python project`

```
ERROR: file:///C:/mnt/c/Workspace/.../copilot-harness does not appear to be a Python project
```

This is Git Bash / WSL passing a Unix-style path (`/mnt/c/...`) to a
Windows Python (`.venv\Scripts\python.exe`). Fixed in this branch —
`setup.sh` now runs `wslpath -w` on the server dir before handing it to
pip.

If you still hit it, run the two pip steps directly from PowerShell where
paths are native:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .\copilot-harness
.\.venv\Scripts\python.exe -m pip install pyinstaller
```

Then `npm run package` from `copilot-harness-extension/`.

### Slash commands don't autocomplete after `@harness /`

VS Code only shows autocomplete for slash commands declared in the
participant's `package.json` `chatParticipants[].commands` array. The
parser handles `/<command>` typed manually regardless, but discoverability
relies on the manifest. Tracked separately — see open issues.

### `npm run install:vsix` tries to download the VS Code Server

```
Updating VS Code Server to version ...
ERROR: Failed to download https://update.code.visualstudio.com/...
```

You ran the install step from inside WSL. The WSL `code` command is a
wrapper that downloads the VS Code Server inside WSL (for Remote-WSL
development) — it doesn't install extensions into your Windows host VS
Code, and it can't even start without internet access from the WSL
distro.

The script now auto-detects WSL and prefers `code.exe` (the Windows VS
Code launcher). If `code.exe` isn't on your WSL PATH, run the install
step from PowerShell instead, or invoke `code.exe` directly:

```bash
/mnt/c/Users/<you>/AppData/Local/Programs/'Microsoft VS Code'/bin/code.cmd \
    --install-extension copilot-harness-extension-0.4.0.vsix --force
```

### `npm run package` fails at `build:server`

```
Error: PyInstaller not found in .venv
```

The venv exists but `pyinstaller` was never installed. Either rerun
`npm run setup` (or the manual fallback above), or install just
PyInstaller: `.venv/Scripts/python.exe -m pip install pyinstaller`.

### Asking the harness for help

Anything not covered above is fair game for `@harness` itself once the
server is running:

```
@harness why does my pipeline keep escalating?
@harness explain the evaluator firewall
```

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
