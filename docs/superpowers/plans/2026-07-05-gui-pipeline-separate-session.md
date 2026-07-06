# GUI: Pipeline studio runs its own session, separate from the Orchestrator

## Context

Today the whole console is one session. `AppState` holds a single `chat_id`
(`gui-orchestrator-<hash>`) and a single `ChatAgentRuntime`; `chat_log` is a
global table with no per-session column; and both the Orchestrator and the
Pipeline studio render the same `ChatBody` with the same `vals.chat`/`onSend`.
So the studio chat *is* the Orchestrator chat, and every run shows in the one
Orchestrator "Parent runs" list.

## Goal (chosen scope: separate chat + separate run list)

- The Pipeline studio drives its **own** agent session: its own `chat_id`
  (`gui-pipeline-<hash>`) and its own conversation history.
- The Orchestrator's "Parent runs" shows only Orchestrator-session runs; the
  studio shows only its own runs. Pipeline work never mixes into the
  Orchestrator list.
- The single agent **process slot is shared** — one run at a time across both
  surfaces (matches the console's single-agent model). Starting a run on one
  surface while the other is running is refused, as today.

## Design

**Surface = which session.** `chat_id` prefix encodes the surface:
`gui-orchestrator-*` vs `gui-pipeline-*`. Everything derives surface from the
prefix, so no id tables are threaded through.

### Backend — `musubi-data`
- `chat_log`: add `surface TEXT NOT NULL DEFAULT 'orchestrator'` (schema +
  `ALTER TABLE ADD COLUMN` migration for existing DBs).
- Read two histories: `chat` (surface `orchestrator`) and `pipeChat`
  (surface `pipeline`).
- `Agent`: add `chatId` (the owning session). subagent_audit has no chat_id,
  so build a `parent_session_id → chat_id` map from `agent_turns` and tag each
  agent; runs with no mapping default to the Orchestrator surface.
- `AgentTurn.chatId` already exists — expose nothing new.

### Backend — app `lib.rs`
- `AppState`: add `pipeline_chat_id` alongside `chat_id`
  (`scoped_chat_id(root, "pipeline")`).
- `insert_chat` gains a `surface` argument.
- `ChatAgentRuntime` tracks the active surface so the driver reply is written
  to the right history.
- `start_chat_agent` takes the `chat_id` to launch under.
- Actions: `send_chat` (orchestrator) unchanged; new `send_pipe_chat`
  (pipeline). `clear_driver_chat` gains a surface arg. `run` seeds both ids.

### Frontend
- `DOMAIN_KEYS`: add `pipeChat`.
- Orchestrator: `runs`/`agentTurns` filtered to non-pipeline `chatId`; uses
  `chat` + `send_chat`.
- Pipeline studio: its `ChatBody` uses `pipeChat` + `send_pipe_chat` + a
  `pipeDraft`; a compact run list scoped to `gui-pipeline-*`.
- `viewModel`: `groupRuns` takes a surface filter; expose `pipeRuns`,
  `pipeChat`, `onPipeSend`, `pipeDraft`.

## Verification
- musubi-data: chat/pipeChat split + agent chatId mapping unit tests; existing
  camelCase test extended.
- `viewModel.test.mjs`: run-scoping per surface.
- App crate builds; `npm run build` clean.

## Phasing
1. Backend session split (chat_log surface, two ids, scoped reads, agent chatId).
2. Frontend split chat + surface-scoped run lists.
3. Studio run list UI.
