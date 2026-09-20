# Musubi System

This is the only system-level document in the repository. It summarizes the
current product and architecture. Source code, database schemas, configuration,
and tests remain authoritative for exact interfaces and behavior.

## Purpose

Musubi is a model-agnostic harness for governed software-engineering agents. A
model reasons and proposes actions; deterministic code controls tool access,
workspace scope, budgets, verification, persistence, and audit evidence.

Musubi has three operator surfaces:

- `agent` is the standalone model driver. It selects a configured provider,
  runs direct work or a named pipeline, and connects to Musubi over MCP.
- `musubi serve` exposes governance and workspace tools as an MCP server.
- The Tauri Console launches the same standalone driver and reads persisted
  state. Pipeline Studio edits recipes; it does not own a separate executor.

`musubi setup` checks the environment, creates `.musubi/llm.json`, and can
configure an MCP client. Provider, model, endpoint, and credential environment
variable are selected by a named profile.

## Current execution paths

### Direct agent work

`musubi/agent/run.py` owns the shipped reason-act-observe loop. The driver calls
models through `LMRouter`, exposes only its selected MCP tool surface, records
usage and events, and returns the final result or a bounded failure.

The driver has extracted components for context collection, model thinking,
typed decisions, run state, and tool transport. These components are being
wired into a smaller Adaptive controller, but the live CLI has not completed
that cutover. Their presence in the tree does not by itself make Adaptive a
separate supported CLI mode.

### Pipelines

`agent "<task>" --pipeline <name>` loads a recipe from
`.github/pipelines/<name>/pipeline.yaml`. The pipeline runner executes the
declared stages in order, applies stage budgets and verification gates, and
persists attempts and checkpoints. A stage may spawn helper workers only when
its recipe permits them.

The root agent tool surface does not expose pipeline creation as an autonomous
action. Users start pipeline runs explicitly through the CLI or Console.

### Workers and skills

Workers receive a bounded brief, an allowed tool set, and a selected skill from
`.github/skills/`. The runtime validates the skill and role before execution.
Worker spawn, completion, tool activity, and reported skill identity are
recorded for inspection.

## Main components

| Area | Current owner |
|---|---|
| CLI and model loop | `musubi/agent/run.py` |
| Provider abstraction | `musubi/agent/vendors/` |
| Pipelines | `musubi/agent/pipeline_runner.py`, `.github/pipelines/` |
| Adaptive contracts and control | `musubi/agent/decision_contracts.py`, `adaptive_controller.py`, `work_package_controller.py` |
| MCP tools | `musubi/server.py` |
| Published tool surfaces | `musubi/tool_surface.py` |
| Policy and hooks | `scripts/policy_engine.py`, `hooks.json`, `scripts/` |
| Workspace and file authority | `musubi/workspace/`, `musubi/tools/` |
| Verification | `musubi/validation/`, `musubi/execution/` |
| Persistent state | `musubi/storage/`, `musubi/session/` |
| Context compression | `musubi/compression/`, `musubi/agent/context.py` |
| Memory | `musubi/memory/` |
| Desktop Console | `gui/`, especially `gui/src-tauri/` for native state access |

## Control and evidence

Tool calls pass through the published tool surface, role policy, and workspace
checks before execution. Unknown roles and tools are denied by the current
policy implementation. Verification is separate from worker self-report: test,
lint, typecheck, command, and artifact evidence can be recorded against a stage
or work-package contract.

SQLite stores sessions, stage outputs and attempts, conversations, pipeline
runs, agent cycles, folder grants, goal and work-package versions,
verification evidence, budgets, rollback records, and Adaptive decision events.
The table definitions in `musubi/storage/schema.sql` are the schema source of
truth. The Console reads the audit database through its Rust backend rather
than maintaining a second execution database.

Large tool results can be compressed before entering model context. The
verbatim source is retained by content identity and can be retrieved later.
Memory is derived from stored run evidence and does not replace original
records.

## Architecture status

The shipped product supports direct agent runs, deterministic pipelines, MCP
governance tools, profiles, compression, memory, audit inspection, and the
desktop Console.

The active refactor is narrowing the direct agent path around an Adaptive
Controller, Collector, Thinker, Decider, bounded Runtime/Tools, and a public
Storage boundary. Several components and typed contracts exist, but the
end-to-end Adaptive cutover and its acceptance scenario are not yet complete.
Pipeline migration to shared Runtime/Storage, broader Memory lifecycle work,
and Harness-of-Harnesses candidate evaluation are later work, not accepted
current behavior.

When this status changes, update this section in the same change as the code
and tests. Do not add a second architecture plan or specification under
`docs/`.

## Entry points and verification

Common commands:

```bash
python -m pip install -e "./musubi[all]"
musubi setup
agent "add a /health endpoint and tests"
agent "review this change" --pipeline code-review
python -m pytest musubi/tests
npm test
```

For exact CLI flags, use `agent --help` and `musubi setup --help`. For exact MCP
tools, read `musubi/server.py` and `musubi/tool_surface.py`. For current
behavior, prefer executable tests over historical descriptions.
