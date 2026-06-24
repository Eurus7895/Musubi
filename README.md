# Musubi

**Musubi** (結び — "knot / binding / the connective force") is a
**governed-orchestration substrate** for agentic software-engineering
work. Its value is **deterministic, zero-LLM validation enforced at every
agent↔agent and agent↔tool boundary** — audit DB, skill catalog,
three-tier memory, fail-closed policy engine, deterministic verifiers,
reversible input compression, and workspace-scoped file & command tools —
exposed as an MCP server.

Any tool-using LLM drives it through one inject point. The **target**
host is the standalone `agent` CLI (`musubi/agent/run.py`) over the
vendor-agnostic `LMRouter` — model-agnostic, free of `vscode.lm` quota.
The legacy embedded GitHub Copilot host is being abandoned (roadmap
Step 7).

**The driver reasons. The substrate controls the environment.**
The substrate makes zero LLM calls (Hard Invariant #1); only the driver —
the agent loop — reaches a model, through the inject point.

> Same model + same task + changed environment = better outcomes.
> (Princeton SWE-agent paper: 64% improvement from harness design alone.)

### Substrate vs ephemeral (the project's discipline)

Every component carries a `musubi-tier` tag, enforced by CI:

- **Substrate** (invest, refactor) — the audit DB, the skill catalog,
  the three-tier memory, the policy engine, the MCP tool catalog
  (`musubi_*`), and Hard Invariants #1–#9. These are designed to outlive
  any specific model release.
- **Ephemeral** (label, schedule for removal) — the 4-stage pipeline
  shape (`planner → designer → coder → reviewer`), the sub-agent split,
  the correction loop, the cycle-loop guards. Each ephemeral file
  declares an `expires-when:` trigger; when models cross that
  threshold, the structure gets *deleted*, not refactored.

Full discipline + the PR-review sentence:
[`docs/roadmap.md`](./docs/roadmap.md).

### Surfaces

| Surface | When | What you get |
|---|---|---|
| `agent "<task>"` (standalone CLI) | **the target** — any task, any LLM | single-agent loop over `LMRouter` (Anthropic / OpenAI / extensible) against the Musubi substrate; model-agnostic, no Copilot quota |
| `@harness /feature-dev <task>` (VS Code) | legacy, **being removed** | the 4-stage governed pipeline; ephemeral and dissolving in roadmap Step 7 (the `musubi_*` MCP rename already breaks its hardcoded calls) |
| plain Copilot Chat | casual question | no Musubi overhead |

---

## Documentation

| File | For |
|---|---|
| `README.md` *(you are here)* | Install · build · run · contribute |
| [`docs/roadmap.md`](./docs/roadmap.md) | **Read first.** Direction, discipline, north-star pivot, numbered steps, dissolution candidates |
| [`CLAUDE.md`](./CLAUDE.md) | Rules · invariants · conventions · commands (Claude Code memory) |
| [`AGENTS.md`](./AGENTS.md) | Session-start orientation map for agents |
| [`musubi/server.py`](./musubi/server.py) · [`musubi/storage/schema.sql`](./musubi/storage/schema.sql) | MCP tool reference + DB schema (source of truth) |

Read `CLAUDE.md` before making code changes — it lists the hard invariants
(zero LLM in the substrate, evaluator firewall, fail-closed policy, etc.).

> **⚠️ The VS Code extension below is legacy.** The supported target is
> the standalone `agent` CLI (`musubi/agent/run.py`). The `harness_* →
> musubi_*` MCP rename intentionally breaks the extension's hardcoded
> calls; it is being removed in roadmap Step 7. The install/usage section
> documents it for now but a standalone-CLI-first rewrite lands with
> Step 4.

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- VS Code with the GitHub Copilot Chat extension
- **On Windows:** Git Bash (bundled with [Git for Windows](https://git-scm.com/download/win)) or PowerShell. **WSL is not supported** for the build / install scripts — the harness runs as a Windows binary inside Windows VS Code, and routing through WSL only adds a translation layer (path mangling, VS Code Server downloads) that breaks bringup.

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

Reload VS Code after install. The **Musubi** output channel confirms
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

The Musubi substrate is a Python MCP server (`musubi/server.py`). It
exposes ~56 `musubi_*` tools covering session lifecycle, skills, memory,
audit, file I/O, command execution, and reversible input compression.
**Zero LLM calls happen inside it**
(HI #1). Any tool-using LLM client can drive it; the VS Code extension is
the canonical one, but the bundled `agent` CLI is a peer.

**From the VS Code extension**: the extension spawns the server binary on
start (JSON-RPC over stdio). `@harness <input>` routes by pure string
check — zero LLM cost:

- `/<pipeline-name> <task>` → **pipeline mode** (governed agents,
  validation, audit trail) — *`musubi-tier: ephemeral`; on dissolution
  path as models improve, per the discipline*
- Anything else → **Agent mode** (persistent chat, model's native
  multi-turn shape; CLAUDE.md: "skills come first, the Agent grows")

**From any LLM API directly** (Anthropic, OpenAI, …):

```bash
pip install -e musubi/.[anthropic]   # or .[openai] or .[all]
ANTHROPIC_API_KEY=... agent "your task"
```

The Agent CLI spawns the same MCP server, lists the catalog, drives a
tool-use loop against your chosen LLM. No Copilot Chat required —
useful when Copilot quota is empty or you want to point a different
model at the substrate. New vendors are a single file under
`musubi/agent/vendors/`.

The MCP tool reference, schemas, hooks, and YAML formats are documented
at their source of truth: [`musubi/server.py`](./musubi/server.py) and
[`musubi/storage/schema.sql`](./musubi/storage/schema.sql).

---

## Slash Commands

Slash commands are `.github/commands/*.md` files with YAML frontmatter
declaring an action (`pipeline`, `step`, `continue`, `status`, `help`).
Add a command by dropping a new `.md` — no code change required.

| Command | Mode | What it does |
|---|---|---|
| `@harness <prompt>` | Agent | Persistent chat, spawns sub-agents on demand |
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
                                (musubi-tier: ephemeral)
    commands/                   slash command files (frontmatter-driven)
    agents/                     shared catalog (musubi-tier: ephemeral)
    instructions/               priority-ranked rules
    skills/                     domain skills — SKILL.md + assets/ +
                                references/ (musubi-tier: substrate)
    memory/                     3-tier memory: MEMORY.md +
                                project-profile.md + failure-patterns.md
                                (musubi-tier: substrate)

musubi/                Python MCP server — zero LLM (HI #1)
    storage/  memory/           audit DB + memory loaders (substrate)
    skills/   validation/       skill catalog + verifier + firewall (substrate)
    workspace/ tools/           profile detector + fs/command tools (substrate)
    agent/                     vendor-agnostic CLI (Anthropic/OpenAI/…)
                                — agent entry point (substrate)
    session/  execution/        pipeline-shape lifecycle + executors
                                (mix; see musubi-tier tags)

copilot-harness-extension/      VS Code extension (TypeScript)
                                — the Copilot Chat adapter; the runners/
                                subdir is musubi-tier: ephemeral
hooks.json + scripts/           SessionStart / PreToolUse / PostToolUse
                                + check_musubi_tier.py (CI lint for HI #9)
docs/roadmap.md                 direction + discipline + numbered steps (read first)
docs/memory.md                  memory architecture detail
musubi/server.py                MCP tool reference (source of truth)
```

The MCP tool reference is `musubi/server.py`; the DB schema is
[`musubi/storage/schema.sql`](./musubi/storage/schema.sql).

---

## Diagnostics

`Ctrl+Shift+U` → **Musubi** output channel. A healthy startup looks
like:

```
Musubi v<version> activating...
Checking: ...\bin\musubi.exe — found
Starting MCP server...
MCP server started. Listing tools...
Tools available (24): musubi_get_active_session, musubi_new_session, ...
Musubi ready. Use @harness in Copilot Chat.
```

Any line beginning `[server]` is the harness server's stderr piped through
the extension — Python tracebacks land there during activation.

---

## Troubleshooting

Symptom-first guide. Open the **Musubi** output channel before
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
| `[server] Traceback (most recent call last):` | The Python server crashed on startup. | Read the traceback. Common cause: an editable install picked up stale `.pyc` files or a missing dep — `pip install -e musubi/` from a fresh venv usually fixes it. |
| `ERROR starting server: ...` | The extension caught the failure cleanly. | The error message is the actual reason — file not executable, antivirus quarantine, wrong arch, bad shebang. |
| `MCP server started. Listing tools...` | Server is fine; the freeze is elsewhere. | Disable other Copilot Chat extensions one at a time and reload. |

### Slash commands don't autocomplete after `@harness /`

VS Code only shows autocomplete for slash commands declared in the
participant's `package.json` `chatParticipants[].commands` array. The
parser handles `/<command>` typed manually regardless, but discoverability
relies on the manifest. Tracked separately — see open issues.

### `npm run install:vsix` fails with `EPERM: operation not permitted, rename ...`

```
[node.js fs] rename failed after 1091 retries with error:
  Error: EPERM: operation not permitted, rename '...\copilot-harness-extension-0.4.0' -> '...vsctmp'
Error: Please restart VS Code before reinstalling Musubi.
```

Windows holds a file lock on the extension folder while VS Code is
running. Either:

1. **Close all VS Code windows** and re-run `npm run install:vsix`. The
   script now detects this case and prints a clear banner with both
   options.
2. **Skip the install entirely — use the Extension Development Host:**
   - Open `copilot-harness-extension/` as the workspace
   - Press **F5** to launch a second VS Code with the extension loaded
     directly from `dist/` (no `.vsix`, no install)
   - Edit code, run `npm run build`, then `Ctrl+R` in the dev host to reload

   This is the fastest iteration loop for harness work. Use the .vsix
   install only when you want to test the shipped extension.

### `npm run install:vsix` exits with `running under WSL`

The build / install scripts are not supported under WSL — see
**Prerequisites** above. The WSL `code` command targets the WSL distro,
not the Windows host VS Code, and silently breaks bringup.

Use **Git Bash** (right-click in the repo folder → "Git Bash Here") or
**PowerShell** instead, then re-run:

```powershell
npm run all
```

### `npm run setup` fails on Windows with `not a Python project`

```
ERROR: file:///C:/mnt/c/Workspace/.../musubi does not appear to be a Python project
```

The `/mnt/c/...` prefix in that error means you're running under WSL —
same root cause as the install error above. Re-run from Git Bash or
PowerShell.

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
3. Look at [`docs/roadmap.md`](./docs/roadmap.md) § Steps for the current
   backlog.
4. Run the checks listed in [`CLAUDE.md`](./CLAUDE.md) § Commands before
   opening a PR.
5. Don't add new pipelines until `feature-dev` is validated (see roadmap).

---

## License

See [`LICENSE`](./LICENSE).
