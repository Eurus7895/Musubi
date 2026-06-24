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
| `agent "<task>"` (standalone CLI) | any task, any LLM | single-agent loop over `LMRouter` against the substrate; model-agnostic, no Copilot quota |
| `@harness /feature-dev <task>` (VS Code) | inside Copilot Chat | the 4-stage governed pipeline + substrate features, driven by Copilot's model |

Both surfaces drive the **same** substrate (audit, firewall, policy,
compression). The extension's tool calls need updating from `harness_*`
to `musubi_*` after the rename — see `docs/roadmap.md`.

## Input compression (substrate, reversible)

Musubi shrinks the tokens the model reads at the substrate boundary —
deterministic, zero-LLM, and **reversible**:

- Content-type-routed compressors — JSON-minify, code comment/blank-strip,
  whitespace-collapse (`musubi/compression/`).
- The verbatim original is stored (content-hash keyed); the model pulls it
  back any time with the **`musubi_retrieve`** tool, and the audit trail
  always reads the original.
- Wired into `musubi_read_file` / `musubi_run_command` behind the
  **`MUSUBI_COMPRESS`** flag (default off). ~67% reduction on indented
  JSON with an exact round-trip.

```bash
MUSUBI_COMPRESS=1 agent "summarise the config files"
```

## Quick start (standalone CLI)

```bash
cd musubi
pip install -e ".[anthropic]"      # or ".[openai]" / ".[all]"
export ANTHROPIC_API_KEY=...        # or OPENAI_API_KEY
agent "add a /health endpoint and a test for it"
# agent "<task>" --vendor openai --model gpt-5-mini
# agent "<task>" --vendor ollama --model llama3.1    # local, no key
```

The CLI spawns the MCP substrate (`musubi/server.py`), lists its `musubi_*`
tools, and drives them with the model through `LMRouter` — zero LLM calls
in the substrate itself. Requirements: Python 3.11+.

### Vendors & on-prem endpoints

A new vendor is one `LMRouter` subclass; endpoints are configuration. Supported
out of the box: `anthropic`, `openai`, `ollama` (local), and `azure` /
on-prem OpenAI-compatible gateways. For named endpoints — including **Azure
OpenAI**, reached through `curl` so corporate proxy / custom CA / mTLS are
honoured — describe them once in `.musubi/llm.toml` (copy
`.musubi/llm.toml.example`) and select with `--profile`:

```bash
cp .musubi/llm.toml.example .musubi/llm.toml   # then edit; secrets via api_key_env
agent "<task>" --profile azure.work
```

Profiles are grouped by **LLM family** (`[azure]`, `[openai]`, …); the section
selects the wire/client and its keys are shared defaults inherited by each
`[<family>.<name>]` profile. Selection precedence: `--vendor` → `--profile` →
the file's `default` → env-key detection.

### Sub-agents (multi-step delegation)

The standalone agent can spawn governed sub-agents for delegated multi-step
work: when the model calls `musubi_spawn_subagent(role, brief)`, Musubi runs a
turn-capped child loop on a firewalled brief and restricted tool surface, then
feeds the verified summary back — every spawn is policy-checked and audited
(`musubi_query_subagent_events`).

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
