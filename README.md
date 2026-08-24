# Musubi

**Musubi** (結び — "knot / binding") is a model-agnostic coding-agent
harness. It coordinates agent work while a deterministic runtime controls
tool access, workspace boundaries, acceptance checks, retries, and audit
evidence.

Musubi exposes the runtime as an MCP server, provides a standalone `agent`
CLI, and includes a desktop Console for operating and inspecting sessions.
The runtime itself makes no LLM calls; model access goes through the
vendor-agnostic `LMRouter`.

## Why Musubi

An agent saying "done" does not prove that a task is complete. Musubi adds:

- fail-closed policy checks around agent and tool boundaries;
- deterministic verification before a pipeline stage may advance;
- bounded retries that preserve the original acceptance criteria;
- append-only records of tool calls, policy decisions, cost, and artifacts;
- workspace-scoped file and command execution;
- automatic, reversible input compression and context controls.

## Evidence-gated feedback loop

Each governed pipeline stage follows one loop:

```text
Freeze acceptance contract
          ↓
Agent executes the stage
          ↓
Runtime verifies deterministic evidence
          ↓
    Pass / Retry / Escalate
```

The model proposes a stage goal and measurable acceptance predicates. Musubi
validates and freezes them before execution. After the agent finishes,
zero-LLM checks inspect evidence such as changed files, DOM structure, lint,
tests, or allow-listed commands.

A failed check becomes focused feedback for a bounded retry, while the
contract and original stage baseline remain unchanged. The pipeline advances
only after the evidence passes; exhausted or non-retryable failures escalate.

For example, producing a weather page is insufficient when the contract also
requires five DOM rows, five distinct city names, and clean lint. Four rows
trigger a retry; passing every check allows the next stage to start.

**Feedback tells the agent what to improve. The evidence gate decides whether
execution may continue.**

## Core capabilities

- **Governed execution:** policy firewall, scoped workspace grants, restricted
  tool surfaces, command ceilings, and process-tree timeouts.
- **Deterministic pipelines:** composable recipes with frozen stage contracts,
  verification gates, retry limits, pause/resume, and escalation.
- **Auditability:** SQLite ledgers for model cycles, delegation, tool calls,
  policy denials, stage evidence, token usage, and artifacts.
- **Automatic context efficiency:** native JSON, Python, log, and text
  compression with exact retrieval, plus deterministic context packing.
- **Multiple LLM providers:** Anthropic, OpenAI, DeepSeek, Ollama, Azure OpenAI,
  and compatible on-prem endpoints.
- **Desktop Console:** run and resume sessions, inspect evidence, and build
  pipeline recipes through a Tauri operator interface.

## Quick start

Requirements: Python 3.11+.

```bash
python -m pip install -e "./musubi[all]"
musubi setup

agent "add a /health endpoint and tests"
agent "review this change" --pipeline code-review
```

`musubi setup` checks the environment, creates `.musubi/llm.json`, optionally
tests the endpoint, and can generate `.vscode/mcp.json` for MCP clients.

## LLM profiles

Provider, model, endpoint, and API-key environment variables live in named
profiles. Copy the example, edit it once, then select a profile at runtime:

```bash
cp .musubi/llm.json.example .musubi/llm.json
agent "<task>" --profile openai.cloud
agent "<task>" --profile azure.work
agent "<task>" --profile ollama.local
```

Corporate proxy, custom CA, mTLS, and on-prem configuration are documented in
the [usage guide](./docs/guide.md).

## Compression and context

Musubi automatically compresses large tool inputs before they reach the model
and stores the verbatim original by content hash. `musubi_retrieve` restores
the exact source; `musubi_compress` and `musubi_compression_stats` expose
on-demand compression and aggregate savings. Older context is packed
deterministically while system instructions, the task, recent turns, tool-call
pairing, and retrieval markers remain protected.

See [compression details and benchmarks](./docs/compression.md).

## Console

The Windows desktop Console launches the same standalone runtime and reads its
audit state. It provides session operation, pipeline editing, policy evidence,
runtime logs, model usage, skills, and settings without creating a second
execution path.

Use the prebuilt installer from the **Desktop build** GitHub Actions workflow,
or run the local developer build with `npm install` and `npm run tauri:dev`.
See the [Console guide](./docs/guide.md#6-console-gui--operator-view).

## Documentation

| Document | Purpose |
|---|---|
| [Usage guide](./docs/guide.md) | Installation, profiles, CLI, pipelines, and Console |
| [Hard invariants](./docs/hard-invariants.md) | Runtime guarantees and trust boundaries |
| [Roadmap](./docs/roadmap.md) | Product direction and implementation status |
| [Compression](./docs/compression.md) | Strategies, retrieval, metrics, and benchmarks |
| [Memory](./docs/memory.md) | Cross-session memory architecture |
| [CLAUDE.md](./CLAUDE.md) | Repository rules, conventions, and validation commands |

## Development

```bash
python scripts/check_musubi_tier.py
python -m pytest musubi/tests
npm test
```

Read [CLAUDE.md](./CLAUDE.md) before contributing. Update the roadmap when a
change affects product direction or a hard invariant.
