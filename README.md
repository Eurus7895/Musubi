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
agent loop — reaches a model, through one inject point: the vendor-agnostic
`LMRouter` behind the standalone `agent` CLI (model-agnostic — anthropic /
openai / deepseek / azure-on-prem / ollama). The desktop Console (GUI)
observes and operates the same substrate through `audit.db`.

> Same model + same task + changed environment = better outcomes.
> (Princeton SWE-agent paper: 64% improvement from harness design alone.)

## Substrate vs ephemeral (the discipline)

Every component carries a CI-enforced `musubi-tier` tag. **Substrate**
(audit DB, skill catalog, memory, policy engine, the `musubi_*` catalog,
Hard Invariants) is invested in; **ephemeral** (the 4-stage pipeline
shape, the main-vs-sub split, correction loop) is labelled with an
`expires-when:` trigger and deleted — not refactored — when models cross
it. In the standalone host the main-vs-sub split has already dissolved
into the **worker model** (one `run_unit` path; only workers at a depth).
Full plan + the PR-review sentence: [`docs/roadmap.md`](./docs/roadmap.md).

## Surfaces

| Surface | When | What you get |
|---|---|---|
| `agent "<task>"` (standalone CLI) | any task, any LLM | agent loop over `LMRouter` with the **worker model** — parallel workers, depth-2 nesting, and summonable pipelines (incl. user-defined preset pipelines); any vendor (anthropic / openai / deepseek / azure-on-prem / ollama), model-agnostic. Configure with `musubi setup`. |
| `agent "<brief>" --pipeline <name>` | deterministic staged runs | the governed pipeline recipes (`feature-dev`, `code-review`, `dev-lite`, or your own presets) with the evaluator firewall and stage audit |
| Console (GUI) | observe & operate | the orchestrator cohort, policy stream, and append-only ledger, read straight from `audit.db` — zero LLM calls |

Every surface drives the **same** substrate (audit, firewall, policy,
compression).

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
  shows 266,851 payload chars compressed to 6,434 model-visible chars
  with exact round-trip retrieval. Opt out with **`MUSUBI_COMPRESS=0`**.
- The model can also compress a payload on demand with **`musubi_compress`**
  and measure the feature's efficiency with **`musubi_compression_stats`**
  (aggregate ratio, bytes saved, per-kind breakdown over every stored blob).
  The deterministic eval gate is `python -m agent.compression_eval`; detailed
  benchmark artifacts live in [`docs/compression.md`](./docs/compression.md).

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
// compression capability artifact after Step 4 context packing
{
  "status": "ok",
  "total_blobs": 4,
  "total_original_chars": 339348,
  "total_compressed_chars": 10906,
  "bytes_saved": 328442,
  "overall_ratio": 0.032,
  "savings_pct": 96.8,
  "rows_without_metric": 0,
  "by_kind": [
    { "kind": "json", "count": 1, "compressed_chars": 2521 },
    { "kind": "code", "count": 1, "compressed_chars": 6229 },
    { "kind": "log", "count": 1, "compressed_chars": 736 },
    { "kind": "text", "count": 1, "compressed_chars": 1420 }
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
| **Effort routing** | Read-only workers start at a low cap and retry at the ceiling on truncation; workers with file-mutation tools start at the ceiling so whole-artifact writes are not predictably cut off. After one escalation, later cycles stay at the ceiling. | Read-only floor: `MUSUBI_EFFORT_TOKENS=<n>` (default 2048). Worker ceiling: `.agent.md` `maxOutputTokens` (default 16384). Profile `max_output_tokens` optionally clamps that ceiling. |
| **IntelligentContext** | When the conversation exceeds a budget, deterministically protects system/task/recent turns, compresses old tool results first, and only then trims the largest remaining blocks. Pairing and `musubi_retrieve` markers are preserved. | `MUSUBI_CONTEXT_BUDGET=<chars>` (default 40000; `0` disables) |

Output effort resolves once per worker loop: optional worker
`maxOutputTokens`, otherwise the shared `16384` ceiling, then an optional
profile `max_output_tokens` clamp. Mutation-capable workers open at the
resolved ceiling; read-only workers open at the smaller effort floor. The
value is a maximum response size, not reserved or pre-billed usage, and the
same resolution path applies to direct workers and deterministic pipeline
stages.

## Standalone host controls

The standalone `agent` host now carries the same operational controls that
were previously only visible in narrower paths:

- **Multi-turn CLI state:** pass `--chat-id <id>` to store user/assistant
  turns in `conversation_messages` and replay that bounded history on the
  next run.
- **Token caps:** each run is guarded by `TokenBudgetEnforcer` at the LM
  boundary. The default cap is `200000` total tokens, `--max-tokens <n>`
  overrides it, and `--max-tokens 0` or `MUSUBI_AGENT_MAX_TOKENS=0` disables
  the cap.
- **Usage telemetry:** every LM cycle logs estimated input/output tokens,
  elapsed LM milliseconds, and optional estimated credits. Persisted chat turns
  also write `agent_turns` rows. Token counts are the source of truth; credits
  are only a price-table-dependent estimate.
- **Tool boundary audit:** model-requested `musubi_*` tool calls pass a
  deterministic PreToolUse policy check before dispatch and append PostToolUse
  rows after success, denial, or error. Policy verdicts are stored in
  `policy_audit`; tool outcomes are stored in `tool_audit`. Federated external
  MCP tools remain outside Musubi governance and are routed by the driver only.

```bash
agent "continue the refactor" --chat-id musubi-refactor
agent "large migration" --max-tokens 120000
MUSUBI_AGENT_MAX_TOKENS=0 agent "one uncapped diagnostic pass"
```

Capability evidence is captured in
[`artifacts/agent/standalone_boundary_report.html`](./artifacts/agent/standalone_boundary_report.html)
with machine-readable status in
[`standalone_boundary_status.json`](./artifacts/agent/standalone_boundary_status.json).

## Quick start (standalone CLI)

```bash
python -m pip install -e "./musubi[all]"   # or "./musubi[anthropic]" / "./musubi[openai]"
musubi setup                       # guided: deps check, LLM endpoint, mcp.json, Windows installer guidance
export ANTHROPIC_API_KEY=...        # the env var the wizard recorded
agent "add a /health endpoint and a test for it"
# agent "<task>" --profile openai.cloud     # pick a profile from .musubi/llm.json
# agent "<task>" --profile deepseek.cloud   # DeepSeek API
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
`.musubi/llm.json` endpoint profile (cloud, DeepSeek, local Ollama, or on-prem
Azure), optionally tests the connection, generates `.vscode/mcp.json` for VS
Code MCP clients, and points Windows users to the prebuilt Musubi installer bootstrap.
On Windows, if you opt into local GUI development, it can also install npm
dependencies and verify that `cargo` and the MSVC linker are on `PATH`. macOS
and Linux setup skip GUI installation.

The CLI spawns the MCP substrate (`musubi/server.py`), lists its `musubi_*`
tools, and drives them with the model through `LMRouter` — zero LLM calls
in the substrate itself. Requirements: Python 3.11+.

### Vendors & on-prem endpoints

A new vendor is one `LMRouter` subclass; endpoints are configuration. Supported
out of the box: `anthropic`, `openai`, `deepseek`, `ollama` (local), `azure`, and the
**Gen AI Farm** on-prem gateway (Azure-style deployment-in-path URL with Bearer
auth). For named endpoints — including **Azure OpenAI** and the **Gen AI
Farm**, which can fall back to `curl` so corporate proxy (with proxy auth) /
custom CA / mTLS are honoured — describe them once in `.musubi/llm.json` (copy
`.musubi/llm.json.example`) and select with `--profile`:

```bash
cp .musubi/llm.json.example .musubi/llm.json   # then edit; secrets via api_key_env
agent "<task>" --profile azure.work
agent "<task>" --profile deepseek.cloud
```

DeepSeek uses the OpenAI-compatible transport with default base URL
`https://api.deepseek.com`, default model `deepseek-v4-flash`, and
`DEEPSEEK_API_KEY` by default:

```jsonc
{
  "default": "deepseek.cloud",
  "deepseek": {
    "cloud": {
      "model": "deepseek-v4-flash",
      "api_key_env": "DEEPSEEK_API_KEY"
    }
  }
}
```

Profiles are grouped by **LLM family** (`[azure]`, `[genai_farm]`, `[openai]`,
`[deepseek]`, ...); the section
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

### Workers (parallel delegation & pipelines)

There is no "main agent" vs "sub-agent" — only **workers** (one code path,
`agent/run.py::run_unit`); the top-level task is the depth-0 worker. The point
is **context-window offloading**: a worker does bounded work in its own
firewalled context and returns only a compact summary, keeping the orchestrator
lean.

- **Spawn & offload.** `musubi_spawn_subagent(role, brief)` runs a turn-capped
  child on a firewalled brief + restricted tool surface and feeds back the
  verified summary — every spawn policy-checked and audited
  (`musubi_query_subagent_events`).
- **Parallel / background.** Workers summoned in one turn run concurrently
  (`asyncio.gather` + threaded LM calls), results paired back in order, with a
  per-role width cap.
- **Nesting.** A role that declares a `spawn_allowlist:` may summon its own
  workers, up to a depth cap (default 2); leaf roles never gain the spawn tool.
- **Pipelines.** `musubi_spawn_pipeline(name, brief)` runs an ordered recipe of
  workers — each stage's summary feeds the next, the evaluator sees only the
  prior stage. Users define their own pipelines by composing **presets**
  (`.github/pipelines/presets/`), validated fail-closed at boot. See
  `.github/pipelines/presets/README.md`.

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
npm run tauri:dev                                      # desktop, auto-detect DB
MUSUBI_DB=/path/to/storage/audit.db npm run tauri:dev   # explicit DB override
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

Seven views (Orchestrator / Pipeline studio / Policy / Audit / Models / Skills /
Settings). The trust strip shows whether the app is reading an explicit,
root-derived, workspace, package, or no configured `audit.db`. Settings shows first-run
checks for Python, `musubi`, `agent`, `.musubi/llm.json`, and the selected audit
DB. Full walkthrough: [`docs/guide.md`](./docs/guide.md), Console section.
Static first-run artifact:
[`artifacts/gui/setup_first_run_report.html`](./artifacts/gui/setup_first_run_report.html).

## Documentation

| File | For |
|---|---|
| [`docs/roadmap.md`](./docs/roadmap.md) | **Read first** — direction, discipline, numbered steps, dissolution candidates |
| [`CLAUDE.md`](./CLAUDE.md) | Rules · Hard Invariants · conventions · commands |
| [`docs/guide.md`](./docs/guide.md) | **How to use Musubi** — install, CLI, profiles, compression, workers, pipelines, and the console (GUI), end to end |
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
