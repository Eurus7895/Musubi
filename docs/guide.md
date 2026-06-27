# Using Musubi — the complete guide

> One place that walks you through **actually using Musubi** end to end:
> install, run your first task, pick a model, control tokens, delegate to
> sub-agents, watch it all in the console, and drive it from VS Code.
>
> This is the *how-to-use* guide. For **why** (direction, the substrate/ephemeral
> discipline) read [`docs/roadmap.md`](./roadmap.md); for the **rules &
> invariants** read [`CLAUDE.md`](../CLAUDE.md); the **MCP tool reference + DB
> schema** is the source of truth in [`musubi/server.py`](../musubi/server.py) ·
> [`musubi/storage/schema.sql`](../musubi/storage/schema.sql).

---

## 0. Mental model (30 seconds)

**The driver reasons. The substrate controls the environment.** Musubi is an
MCP server that makes **zero LLM calls** — firewall, audit, fail-closed policy,
validator, reversible compression, skill injection. Only the *driver* (the agent
loop) reaches a model, through one inject point. You use Musubi through one of
two driver surfaces, both driving the same substrate:

| Surface | Use when | Section |
|---|---|---|
| **Standalone `agent` CLI** | any task, any LLM, no Copilot quota | [§2](#2-your-first-task-cli) |
| **VS Code extension** (`@harness`) | inside GitHub Copilot Chat | [§7](#7-vs-code-extension-copilot-surface) |
| **Console (GUI)** | *observe & operate* a session — not a driver | [§6](#6-console-gui--operator-view) |

---

## 1. Install & setup

Requirements: **Python 3.11+**. For the console GUI also Node 20+ (and, for a
native build, the Rust toolchain — [§6](#6-console-gui--operator-view)).

```bash
cd musubi
pip install -e ".[all]"            # or ".[anthropic]" / ".[openai]"
musubi setup                       # guided wizard (recommended)
```

`musubi setup` is the fastest path — it runs an environment doctor, builds a
`.musubi/llm.json` endpoint profile (cloud / local Ollama / on-prem Azure),
optionally tests the connection, generates `.vscode/mcp.json` for the
extension, and installs console GUI dependencies with `npm install` when
`app/package.json` is present. For desktop Tauri runs it also checks that
`cargo` is on `PATH`; on Windows install it with
`winget install --id Rustlang.Rustup -e`, then open a new terminal. Prefer
manual? The steps below still work.

```bash
export ANTHROPIC_API_KEY=...       # the env var the wizard recorded
```

---

## 2. Your first task (CLI)

```bash
agent "add a /health endpoint and a test for it"
```

The CLI spawns the MCP substrate (`musubi/server.py`), lists its `musubi_*`
tools, and drives them with the model through `LMRouter` — zero LLM calls in the
substrate itself. The agent reads files, runs commands, and edits code through
governed tools; every file/command result flows through the substrate (where
compression and audit happen).

---

## 3. Choosing a model / vendor

A new vendor is one `LMRouter` subclass; **endpoints are configuration**.
Supported out of the box: `anthropic`, `openai`, `ollama` (local), `azure`, and
the **Gen AI Farm** on-prem gateway. Vendor, model, endpoint, and api-key all
live in a `.musubi/llm.json` **profile** — `--profile` is the only switch:

```bash
agent "<task>" --profile openai.cloud     # pick a profile
agent "<task>" --profile ollama.local     # local, no key
```

```bash
cp .musubi/llm.json.example .musubi/llm.json   # then edit; secrets via api_key_env
agent "<task>" --profile azure.work
```

Profiles are grouped by **LLM family** (`[azure]`, `[genai_farm]`, `[openai]`,
…); the section selects the wire/client and its keys are shared defaults
inherited by each `[<family>.<name>]` profile. Selection precedence:
`--profile` → the file's `default` → env-key detection (when no config file
exists). To use a different vendor or model, **edit a profile — don't pass a
flag.**

### Behind a corporate proxy (`407 Proxy Authentication Required`)

If a `curl`-transport endpoint fails with `curl: (56) CONNECT tunnel failed,
response 407`, the proxy needs auth. Set **`proxy_auth`** on the profile:

| `proxy_auth` | Use when | Credentials |
|---|---|---|
| `negotiate` | Windows/Kerberos proxy (most corporate setups) | none — OS login via SSPI |
| `ntlm` | older Windows proxy | none — OS login |
| `basic` / `digest` | proxy with a username/password | `proxy_user_env` = `user:password` |

```jsonc
// .musubi/llm.json — integrated Windows auth, no password stored
"integrated-proxy": { "transport": "curl", "deployment": "…", "proxy_auth": "negotiate" }
```

Not sure which scheme? `curl.exe -I --proxy-negotiate -U : "<endpoint url>"` —
whichever flag gets you past the `407` is your `proxy_auth`.

---

## 4. Token controls — compression & context

Musubi shrinks what the model reads at the substrate boundary —
deterministic, zero-LLM, and **reversible**. The verbatim original is always
stored (content-hash keyed) and reachable with `musubi_retrieve`; the audit
trail always reads the original.

Current native compressors are content-type routed: JSON smart-crush, Python
AST structure summaries, log pattern grouping, and heading-aware text outlines.
The latest capability artifact shows 339,930 chars compressed to 6,639
model-visible chars with 4 / 4 retrieve checks passing. See
[`docs/compression.md`](./compression.md) for the full artifact and numbers.

```bash
agent "summarise the config files"     # compression on by default
MUSUBI_COMPRESS=0 agent "..."          # disable it for this run
```

### The `MUSUBI_COMPRESS` switch

| `MUSUBI_COMPRESS` | Effect |
|---|---|
| *unset* (default) | **on** |
| `1` / `true` / `on` / `yes` | on |
| `0` / `false` / `off` / `no` | off |

It's per process; the standalone `agent` forwards every `MUSUBI_*` var to the
server it spawns. Make it stick: `export MUSUBI_COMPRESS=0` (macOS/Linux) or
`setx MUSUBI_COMPRESS 0` (Windows). Leaving it on is safe — the original is
never lost.

### Compression tools (call via the agent)

| Tool | Purpose |
|---|---|
| **`musubi_compress(text, hint=None)`** | Compress on demand and store the original. Returns `kind`, `ref_id`, sizes, `ratio`. Tiny/un-shrinkable inputs come back unchanged with `ref_id: null`. |
| **`musubi_retrieve(ref_id)`** | Return the verbatim original — the reverse of any compression. |
| **`musubi_compression_stats()`** | Aggregate efficiency over every stored blob: `bytes_saved`, `overall_ratio`, `savings_pct`, per-`kind` breakdown. `savings_pct` is the headline "how well is it working?" number. |

### Context controls (driver-side, deterministic)

Four zero-LLM token controls apply at the LM-call boundary:

| Control | What it does | Knob |
|---|---|---|
| **Verbosity steering** | System prompt steers the model to be concise. | always on |
| **CacheAligner** | Marks the static prefix (system + tools) with Anthropic `cache_control`; OpenAI-compatible vendors use provider-native caching. | `MUSUBI_PROMPT_CACHE=0` |
| **Effort routing** | Starts each cycle at a low output-token cap, escalates only on truncation. | `MUSUBI_EFFORT_TOKENS=<n>` (default 2048) |
| **IntelligentContext** | Over budget, elides oldest/largest tool results (pairing + retrieve markers kept) instead of dropping turns. | `MUSUBI_CONTEXT_BUDGET=<chars>` (default 40000; `0` disables) |

---

## 5. Sub-agents (delegated multi-step work)

When the model calls `musubi_spawn_subagent(role, brief)`, Musubi runs a
turn-capped child loop on a **firewalled brief** and **restricted tool surface**,
verifies the summary on completion, then feeds it back — every spawn is
policy-checked and audited. Inspect spawns with `musubi_query_subagent_events`,
or watch them live in the console ([§6](#6-console-gui--operator-view)).

---

## 6. Console (GUI) — operator view

A dark, governance-focused console that reads `audit.db` directly and shows the
substrate at work — the sub-agent cohort, fail-closed policy stream, and the
append-only audit ledger. **Zero LLM calls**, no localhost server, no Copilot —
the agent reasons, the console only *observes and operates* the governance
layer. It lives in [`app/`](../app); deep architecture + the backend contract
are in [`app/README.md`](../app/README.md) · [`app/src-tauri/SCHEMA.md`](../app/src-tauri/SCHEMA.md).

### Run it

```bash
cd app
npm install
npm run dev                                  # browser + live simulation (no toolchain)
npm run tauri:dev                            # desktop app, seeded demo data
MUSUBI_DB=/path/to/storage/audit.db npm run tauri:dev   # desktop, your real DB
```

- **Browser** (`npm run dev`, → http://localhost:5173) — an in-browser
  simulation, no Rust/DB needed. Fastest way to look around.
- **Desktop** (Tauri) — reads a real `audit.db`. Without `MUSUBI_DB` it seeds an
  in-memory demo so it runs standalone. Needs the Rust toolchain + webview libs
  (Linux: `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev`).
  On Windows, install Rustup with `winget install --id Rustlang.Rustup -e`,
  open a new terminal, and confirm `cargo --version` before running
  `npm run tauri:dev`.
- **Prebuilt installer** (no local toolchain) — the `Desktop build` GitHub
  Actions workflow ([`.github/workflows/desktop.yml`](../.github/workflows/desktop.yml))
  compiles `.dmg` / `.msi` / `.AppImage` / `.deb`. Run it manually
  (Actions ▸ *Desktop build* ▸ *Run workflow* → download from artifacts) or push
  a tag (`git tag v0.1.0 && git push --tags`) for a draft Release.

### The six views

A persistent **trust strip** surfaces the Hard Invariants (zero-LLM substrate,
fail-closed policy, append-only audit, evaluator firewall) and the active model.

| View | Shows | Backed by |
|---|---|---|
| **Orchestrator** | The driver "knot" spawning governed sub-agents over a woven net — each card's model, spawn order, turn cap, wall-clock budget; click for the firewalled brief + restricted tools. | `subagent_audit` per handle |
| **Pipeline studio** | Author a chain (or load `feature-dev` / `bugfix` / `explore`), reorder, then **Run** with a policy gate at each handoff. | authoring surface |
| **Policy** | Fail-closed PreToolUse allow/deny stream + tool-surface-by-role; the evaluator-firewall invariant (HI #3) is called out. | `policy_audit` |
| **Audit** | The append-only ledger (spawned / completed), filterable. | `subagent_audit` |
| **Models** | LMRouter vendor profiles with a live config snippet; selecting one sets the active model. | `meta.active_profile` |
| **Skills** | The pushed / pulled skill catalog + the "default to skill, not agent" rule. | static catalog |

### Live data & URL options

A background poller refreshes the desktop UI ~1×/second as `audit.db` grows — run
a pipeline or spawn a sub-agent through the MCP server and the cohort, policy
stream, and ledger update in place. A fresh DB yields empty surfaces (the reader
is tolerant). Source selection is automatic (`SimulationSource` in the browser,
`TauriSource` in the desktop shell), overridable with `?source=sim|tauri`. The
prototype's editor props are URL params too: `?startView=…`, `?simSpeed=Calm|Normal|Brisk`,
`?live=false`.

### Console troubleshooting

| Symptom | Fix |
|---|---|
| Fonts look generic | IBM Plex loads from Google Fonts; offline it falls back to system fonts. Layout/colours unaffected. |
| `tauri:dev` fails on Linux with a webkit error | Install the webview libs listed above. |
| App shows demo data | `MUSUBI_DB` is unset/empty — set it to your `audit.db` absolute path. |

---

## 7. VS Code extension (Copilot surface)

`copilot-harness-extension/` is the `@harness` Copilot-Chat surface — it spawns
the substrate and lets Copilot's model drive the `musubi_*` tools, running the
4-stage governed pipeline (`@harness /feature-dev <task>`). A **supported
surface** kept alongside the CLI. Build scripts live in that directory.
**Pending fix:** its hardcoded tool calls must be updated from `harness_*` to
`musubi_*` (broken by the rename) — tracked in [`docs/roadmap.md`](./roadmap.md).

---

## 8. Where to go next

| File | For |
|---|---|
| [`docs/roadmap.md`](./roadmap.md) | **Read first for direction** — discipline, numbered steps, dissolution candidates |
| [`docs/compression.md`](./compression.md) | Compression capability — native compressor strategies, artifact links, and latest benchmark numbers |
| [`CLAUDE.md`](../CLAUDE.md) | Rules · Hard Invariants · git conventions |
| [`AGENTS.md`](../AGENTS.md) | Session-start orientation map |
| [`musubi/server.py`](../musubi/server.py) · [`musubi/storage/schema.sql`](../musubi/storage/schema.sql) | MCP tool reference + DB schema (source of truth) |
| [`app/README.md`](../app/README.md) | Console architecture + backend contract |
| [`docs/memory.md`](./memory.md) | Memory architecture detail |

**Before opening a PR:** run `python scripts/check_musubi_tier.py` and
`cd musubi && python -m pytest`, and update `docs/roadmap.md` for any direction
change.
