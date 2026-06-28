# Musubi

**Musubi** (結び — "knot / binding / the connective force") is a
**governed-orchestration substrate** for agentic software-engineering
work. Its value is **deterministic, zero-LLM validation enforced at every
agent↔agent and agent↔tool boundary** — audit DB, skill catalog,
three-tier memory, fail-closed policy engine, deterministic verifiers,
**reversible input compression**, and workspace-scoped file & command
tools — exposed as an MCP server.

**The driver reasons. The substrate controls the environment.** The
substrate makes zero LLM calls (Hard Invariant #1); only the driver — the
agent loop — reaches a model, through one inject point. The target host is
the standalone `agent` CLI over the vendor-agnostic `LMRouter`
(model-agnostic, no `vscode.lm` quota). The VS Code extension is a second
supported surface — it brings the substrate (governance **and** input
compression) to GitHub Copilot Chat.

> Same model + same task + changed environment = better outcomes.
> (Princeton SWE-agent paper: 64% improvement from harness design alone.)

## Substrate vs ephemeral (the discipline)

Every component carries a CI-enforced `musubi-tier` tag. **Substrate**
(audit DB, skill catalog, memory, policy engine, the `musubi_*` catalog,
Hard Invariants) is invested in; **ephemeral** (the 4-stage pipeline
shape, sub-agent split, correction loop) is labelled with an
`expires-when:` trigger and deleted — not refactored — when models cross
it. Full plan + the PR-review sentence: [`docs/roadmap.md`](./docs/roadmap.md).

## Surfaces

| Surface | When | What you get |
|---|---|---|
| `agent "<task>"` (standalone CLI) | any task, any LLM | agent loop + on-demand sub-agents over `LMRouter`; any vendor (anthropic / openai / azure-on-prem / ollama), model-agnostic, no Copilot quota. Configure with `musubi setup`. |
| `@harness /feature-dev <task>` (VS Code) | inside Copilot Chat | the 4-stage governed pipeline + substrate features, driven by Copilot's model |

Both surfaces drive the **same** substrate (audit, firewall, policy,
compression). The extension's tool calls need updating from `harness_*`
to `musubi_*` after the rename — see `docs/roadmap.md`.

## Input compression (substrate, reversible)

Musubi shrinks the tokens the model reads at the substrate boundary —
deterministic, zero-LLM, and **reversible**:

- Content-type-routed native compressors — JSON smart-crush, Python AST
  structure summaries, log pattern grouping, and heading-aware text outlines
  (`musubi/compression/`).
- The verbatim original is stored (content-hash keyed); the model pulls it
  back any time with the **`musubi_retrieve`** tool, and the audit trail
  always reads the original.
- Wired into `musubi_read_file` / `musubi_run_command` and **on by
  default** — reversible, so it's safe. The latest capability artifact
  shows 339,930 chars compressed to 6,639 model-visible chars with exact
  round-trip retrieval. Opt out with **`MUSUBI_COMPRESS=0`**.
- The model can also compress a payload on demand with **`musubi_compress`**
  and measure the feature's efficiency with **`musubi_compression_stats`**
  (aggregate ratio, bytes saved, per-kind breakdown over every stored blob).
  Detailed benchmark artifacts live in [`docs/compression.md`](./docs/compression.md).

```bash
agent "summarise the config files"     # compression on by default
MUSUBI_COMPRESS=0 agent "..."          # disable it for this run
```

### The `MUSUBI_COMPRESS` switch

Compression is controlled by one environment variable, read inside the
Musubi server. The standalone `agent` forwards every `MUSUBI_*` var to the
server it spawns, so setting it in your shell takes effect.

| `MUSUBI_COMPRESS` | Effect |
|---|---|
| *unset* (default) | **on** |
| `1` / `true` / `on` / `yes` | on |
| `0` / `false` / `off` / `no` | off |

Scope is per process — set it for one run, or make it stick:

```bash
# Windows (persists for future shells; open a new one after):
setx MUSUBI_COMPRESS 0
# macOS/Linux — add to your shell profile:
export MUSUBI_COMPRESS=0
```

The original is never lost (it's stored and reachable via
`musubi_retrieve`), so leaving it on is safe; turn it off only when you
want the model to read raw, uncompressed tool output.

### Compression tools

Three MCP tools expose the feature directly — the model (or you, via the
agent) can compress, recover, and measure without touching the file/command
tools:

| Tool | Purpose |
|---|---|
| **`musubi_compress(text, hint=None)`** | Compress a payload on demand and store the original. `hint` (a filename, extension, or `"json"`/`"code"`/`"log"`/`"text"`) steers the compressor; without it the kind is detected from content. Returns `kind`, `ref_id`, `original_chars`, `compressed_chars`, `ratio`, and the `compressed` text. Inputs under ~800 chars, or any case where compression wouldn't shrink the text, come back unchanged with `ref_id: null` and `ratio: 1.0`. |
| **`musubi_retrieve(ref_id)`** | Return the verbatim original for a `ref_id` — the reverse of any compression (implicit or via `musubi_compress`). |
| **`musubi_compression_stats()`** | Aggregate efficiency over every stored blob: `total_blobs`, `total_original_chars`, `total_compressed_chars`, `bytes_saved`, `overall_ratio`, `savings_pct`, `rows_without_metric`, and a per-`kind` breakdown. |

`ref_id` is a content hash, so compressing identical text twice dedups to a
single stored row. The recorded sizes are the compressor output (excluding
the ~80-char retrieval marker), so `musubi_compression_stats` reports the
true compression win rather than marker overhead.

```jsonc
// compression capability artifact after Step 3 native compressors
{
  "status": "ok",
  "total_blobs": 4,
  "total_original_chars": 339930,
  "total_compressed_chars": 6087,
  "bytes_saved": 333843,
  "overall_ratio": 0.018,
  "savings_pct": 98.2,
  "rows_without_metric": 0,
  "by_kind": [
    { "kind": "json", "count": 1, "compressed_chars": 2521 },
    { "kind": "code", "count": 1, "compressed_chars": 1408 },
    { "kind": "log", "count": 1, "compressed_chars": 736 },
    { "kind": "text", "count": 1, "compressed_chars": 1422 }
  ]
}
```

> Want a number for "how well is compression working?" — call
> `musubi_compression_stats` at the end of a session; `savings_pct` is the
> headline figure and `by_kind` shows where the wins come from.

## Context controls (driver-side, deterministic)

Alongside input compression, the standalone agent applies four
deterministic, zero-LLM token controls at the LM-call boundary (the
Musubi counterparts of Headroom's verbosity steering, prefix caching,
effort routing, and IntelligentContext — implemented without any learned
model, to keep the substrate LLM-free):

| Control | What it does | Knob |
|---|---|---|
| **Verbosity steering** | The system prompt tells the model to be concise and not restate context — cuts output tokens. | always on |
| **CacheAligner** | Marks the static prefix (system prompt + tool catalog) with Anthropic `cache_control`; OpenAI-compatible vendors use provider-native automatic prompt caching when available. Cache reads/writes show in the cycle log through shared keys. | `MUSUBI_PROMPT_CACHE=0` to disable Anthropic `cache_control` |
| **Effort routing** | Starts each cycle at a low output-token cap and escalates to the ceiling only if a call truncates — bounds runaway turns without cutting real answers. | `MUSUBI_EFFORT_TOKENS=<n>` (default 2048) |
| **IntelligentContext** | When the conversation exceeds a budget, deterministically elides the oldest/largest tool results (pairing preserved, `musubi_retrieve` markers kept) instead of dropping turns. | `MUSUBI_CONTEXT_BUDGET=<chars>` (default 40000; `0` disables) |

## Quick start (standalone CLI)

```bash
python -m pip install -e "./musubi[all]"   # or "./musubi[anthropic]" / "./musubi[openai]"
musubi setup                       # guided: deps check, LLM endpoint, mcp.json, Windows installer guidance
export ANTHROPIC_API_KEY=...        # the env var the wizard recorded
agent "add a /health endpoint and a test for it"
# agent "<task>" --profile openai.cloud     # pick a profile from .musubi/llm.json
# agent "<task>" --profile ollama.local     # local, no key
```

`--profile` is the only endpoint switch — vendor, model, endpoint, and
api-key all live in the chosen `.musubi/llm.json` profile. To use a
different vendor or model, edit (or add) a profile, don't pass a flag.

If `musubi` is not recognized after installation, add Python's user Scripts
directory to `PATH` and open a new terminal:

```powershell
$scripts = python -c "import pathlib, site; print(pathlib.Path(site.USER_BASE) / 'Scripts')"
[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $scripts, 'User')
$env:Path += ';' + $scripts
musubi setup
```

`musubi setup` is the fastest path: it runs an environment doctor, builds a
`.musubi/llm.json` endpoint profile (cloud, local Ollama, or on-prem Azure),
optionally tests the connection, generates `.vscode/mcp.json` for the
extension, and points Windows users to the prebuilt Musubi installer bootstrap.
On Windows, if you opt into local GUI development, it can also install npm
dependencies and verify that `cargo` and the MSVC linker are on `PATH`. macOS
and Linux setup skip GUI installation.

The CLI spawns the MCP substrate (`musubi/server.py`), lists its `musubi_*`
tools, and drives them with the model through `LMRouter` — zero LLM calls
in the substrate itself. Requirements: Python 3.11+.

### Vendors & on-prem endpoints

A new vendor is one `LMRouter` subclass; endpoints are configuration. Supported
out of the box: `anthropic`, `openai`, `ollama` (local), `azure`, and the
**Gen AI Farm** on-prem gateway (Azure-style deployment-in-path URL with Bearer
auth). For named endpoints — including **Azure OpenAI** and the **Gen AI
Farm**, which can fall back to `curl` so corporate proxy (with proxy auth) /
custom CA / mTLS are honoured — describe them once in `.musubi/llm.json` (copy
`.musubi/llm.json.example`) and select with `--profile`:

```bash
cp .musubi/llm.json.example .musubi/llm.json   # then edit; secrets via api_key_env
agent "<task>" --profile azure.work
```

Profiles are grouped by **LLM family** (`[azure]`, `[genai_farm]`, `[openai]`, …); the section
selects the wire/client and its keys are shared defaults inherited by each
`[<family>.<name>]` profile. Selection precedence: `--profile` → the file's
`default` → env-key detection (when no config file exists). `--profile` is the
only CLI selector; vendor and model are properties of the profile.

#### Behind a corporate proxy (`407 Proxy Authentication Required`)

If a `curl`-transport endpoint fails with `curl: (56) CONNECT tunnel failed,
response 407`, the proxy needs authentication. Set **`proxy_auth`** on the
profile to the scheme your proxy advertises:

| `proxy_auth` | Use when | Credentials |
|---|---|---|
| `negotiate` | Windows/Kerberos proxy (most corporate setups) | none — your OS login via SSPI |
| `ntlm` | older Windows proxy | none — your OS login |
| `basic` / `digest` | proxy with a username/password | `proxy_user_env` (or inline `proxy_user`) = `user:password` |

```jsonc
// .musubi/llm.json — integrated Windows auth, no password stored
"integrated-proxy": { "transport": "curl", "deployment": "…", "proxy_auth": "negotiate" }
```

For `negotiate`/`ntlm` Musubi hands curl an empty `:` user automatically, so
it authenticates as your logged-in account with nothing stored. curl reuses
the proxy URL from `$HTTPS_PROXY` if you omit `proxy`. Not sure which scheme?
`curl.exe -I --proxy-negotiate -U : "<your endpoint url>"` — whichever flag
gets you past the `407` is your `proxy_auth`.

### Sub-agents (multi-step delegation)

The standalone agent can spawn governed sub-agents for delegated multi-step
work: when the model calls `musubi_spawn_subagent(role, brief)`, Musubi runs a
turn-capped child loop on a firewalled brief and restricted tool surface, then
feeds the verified summary back — every spawn is policy-checked and audited
(`musubi_query_subagent_events`).

## Console (GUI — operator view)

A dark, governance-focused desktop console reads `audit.db` directly and
shows the substrate at work — the sub-agent cohort, fail-closed policy
stream, and the append-only audit ledger. **Zero LLM calls**, no localhost
server, no Copilot; the agent reasons, the console only observes and
operates the governance layer.

Primary path on Windows: use the prebuilt Musubi installer bootstrap from the
**Desktop build** GitHub Actions workflow. It builds the Windows `.msi` /
`.exe` installer in CI, so local machines do not need Rust or MSVC build
dependencies to install the desktop surface. The bootstrap expects the Python
core CLIs (`musubi` and `agent`) to be installed or repaired through
`musubi setup`. macOS and Linux GUI installers are intentionally not built.

Local Windows developer path:

```bash
npm install
MUSUBI_DB=/path/to/storage/audit.db npm run tauri:dev   # desktop, real DB
```

`npm run tauri:dev` requires Rust's `cargo` binary and the MSVC linker. If
Tauri reports `failed to run 'cargo metadata'` / `program not found`, install
Rustup first. If Rust reports `link.exe not found`, install Visual Studio Build
Tools with the C++ workload. Then open a new terminal:

```powershell
winget install --id Rustlang.Rustup -e
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
cargo --version
where.exe link
```

Run local console npm commands from the repository root. The root
`package.json` delegates to the GUI workspace in `gui/`.

Six views (Orchestrator · Pipeline studio · Policy · Audit · Models ·
Skills). The trust strip shows whether the app is reading a real `audit.db` or
fallback demo data. Without `MUSUBI_DB` it seeds a demo. Full
walkthrough: [`docs/guide.md`](./docs/guide.md) § Console.

## VS Code extension (Copilot surface)

`copilot-harness-extension/` is the `@harness` Copilot-Chat surface — it
spawns the substrate and lets Copilot's model drive the `musubi_*` tools.
It is a **supported surface** kept alongside the standalone CLI. Build
scripts live in that directory. **Pending fix:** its hardcoded tool calls
must be updated from `harness_*` to `musubi_*` (broken by the rename);
tracked in `docs/roadmap.md`.

## Documentation

| File | For |
|---|---|
| [`docs/roadmap.md`](./docs/roadmap.md) | **Read first** — direction, discipline, numbered steps, dissolution candidates |
| [`CLAUDE.md`](./CLAUDE.md) | Rules · Hard Invariants · conventions · commands |
| [`docs/guide.md`](./docs/guide.md) | **How to use Musubi** — install, CLI, profiles, compression, sub-agents, the console (GUI), and the VS Code extension, end to end |
| [`docs/compression.md`](./docs/compression.md) | Compression capability — native compressor strategies, artifact links, and latest benchmark numbers |
| [`AGENTS.md`](./AGENTS.md) | Session-start orientation map |
| [`musubi/server.py`](./musubi/server.py) · [`musubi/storage/schema.sql`](./musubi/storage/schema.sql) | MCP tool reference + DB schema (source of truth) |
| [`docs/memory.md`](./docs/memory.md) | Memory architecture detail |

## Contributing

Read `CLAUDE.md` first — it lists the Hard Invariants (zero LLM in the
substrate, evaluator firewall, fail-closed policy, append-only audit) and
the git conventions. Before opening a PR: run
`python scripts/check_musubi_tier.py` and `cd musubi && python -m pytest`,
and update [`docs/roadmap.md`](./docs/roadmap.md) to reflect any direction
change.
