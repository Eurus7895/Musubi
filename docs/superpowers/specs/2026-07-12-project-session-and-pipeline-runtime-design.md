# Project-Scoped Sessions and Bounded Pipeline Runtime Design

## Context

Pipeline Studio now owns an exact chat session and can launch a registered
pipeline directly. Two follow-up observations refine that design:

1. Multiple sessions commonly collaborate on the same project, so a session
   must not imply a copied directory, worktree, virtualenv, or container.
2. A captured `feature-dev` run spent 105,540 tokens in planning and reached
   189,231 tokens during design, then halted before coder or reviewer ran.

The session boundary and the pipeline resource boundary are related through
runtime ownership, but they are separate delivery tracks.

## Chosen Boundaries

### Project boundary

The canonical project root is the only workspace/filesystem boundary. Every
session for that project uses the same current working directory, source tree,
dependencies, `.musubi` configuration, `musubi.db`, and `audit.db`.

File changes made by one session are intentionally visible to every other
session in the project. Musubi does not create a per-session directory,
worktree, repository clone, virtualenv, or container.

### Session boundary

A session isolates execution and conversation state only:

- exact `chat_id` and conversation replay;
- outer parent session and child pipeline IDs;
- selected model profile and pipeline name;
- token/credit accounting;
- process owner, task, cancellation state, retained log, and terminal status;
- pipeline stages, artifacts, and audit ancestry.

The active process record carries its owning exact `chat_id`, not only a
surface name. A retained process log is visible only while that exact session
is current. Starting a new session changes the chat ID and therefore cannot
inherit the prior session's process status or log.

### Writer concurrency

Musubi keeps one active child-process/writer lease per project. Orchestrator
and Pipeline Studio sessions remain independent, but a second mutating run is
refused while the project lease is held. This preserves shared-workspace
semantics without hidden merges or file races.

Parallel project writes, worktree allocation, and container isolation are
explicit non-goals for this increment.

## Standalone Pipeline Worker Contract

The standalone host treats pipelines as recipes of ordinary workers. It must
not accidentally inherit the VS Code pipeline-stage protocol, which assumes
harness-injected workspace trees and stage-store tools that the standalone
worker does not receive.

The runner will resolve an explicit `PipelineWorkerSpec` from the canonical
worker prompt plus recipe metadata:

```text
PipelineWorkerSpec
  role
  prompt
  max_cycles
  context_budget_chars
```

The spec makes the current standalone contract explicit while preserving the
existing worker catalog. Missing prompts, invalid `maxTurns`, or an empty tool
surface fail closed before the model call.

The same `max_cycles` value is sent to `musubi_spawn_pipeline_stage`, used by
`run_unit`, and recorded in audit. The current split where audit records eight
turns but the runner permits twelve is removed.

## Context and Token Budgeting

The pipeline context limit becomes a hard serialized-input cap covering both
messages and tool definitions. Compression may trim old tool results and large
arguments, but after fitting:

```text
serialized(messages) + serialized(tools) <= context_budget_chars
```

If the protected system prompt, task, and minimum tool schema cannot fit, the
stage terminates as an explicit context-budget failure. It does not make an
oversized model call.

The run-level token budget remains session-owned, while each stage receives a
bounded child allowance. Allowances reserve capacity for every later stage;
planner and designer cannot consume coder/reviewer capacity. Charges flow to
both the stage allowance and the parent run budget so totals remain accurate.

No task is silently rerouted to another pipeline. A user selecting
`feature-dev` still gets `feature-dev`; the resource controls make that choice
bounded and observable.

## Data Flow

```text
project root
  -> exact GUI chat_id
  -> one project writer lease
  -> agent --chat-id ... --pipeline ...
  -> outer parent session (chat_id persisted immediately)
  -> child pipeline envelope
  -> PipelineWorkerSpec per stage
  -> hard context fit + stage token allowance
  -> completion/finalization audit
```

The shared databases remain keyed by real session IDs. No session database or
session directory is introduced.

## Error Handling

- A process/log whose `chat_id` differs from the current surface session is
  not rendered on that surface.
- A busy project writer lease returns an actionable error naming the owning
  surface/session; it does not start another process.
- Missing or malformed worker metadata fails before stage spawn.
- A context cap failure completes the stage non-successfully and finalizes the
  pipeline as `aborted`.
- A stage token allowance halt completes the stage as `escalated` and leaves
  reserved capacity untouched; strict direct runs return non-zero.
- Pipeline envelope and stage spawn/completion events remain append-only.

## Testing Strategy

### Session/runtime tests

- Two session IDs for one project produce identical launch working directories.
- A launch records its exact owning `chat_id` in runtime state.
- A retained log is unavailable after New session changes the exact chat ID.
- Orchestrator and Studio contend for one project writer lease.
- No launch path creates or selects a per-session filesystem directory.

### Pipeline tests

- Worker specs use standalone worker prompts and validated `maxTurns`.
- Spawn, runtime, and audit receive the same stage turn cap.
- Serialized messages plus tools never exceed the hard context cap.
- Planner/designer exhaustion cannot spend the reserve assigned to later stages.
- Context and stage-budget failures finalize once with the expected status.
- The evaluator firewall continues to expose only the prior stage output.

## Documentation Impact

- The Pipeline Studio design must define isolation as logical session state,
  not filesystem isolation.
- The implementation roadmap must link separate session-runtime and bounded-
  pipeline plans.
- `AGENTS.md` and `CLAUDE.md` invariants remain unchanged; no new hard
  invariant or supported surface is introduced.

## Non-Goals

- Per-session folders, worktrees, clones, virtualenvs, or containers.
- Concurrent mutating processes in one project.
- Durable process-log history or a session-history browser.
- Model-driven pipeline selection.
- Replacing the standalone worker model with the VS Code stage protocol.

