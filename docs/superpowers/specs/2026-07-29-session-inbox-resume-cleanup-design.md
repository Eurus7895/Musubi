# Session Inbox, Resume Actions, and Cleanup Design

**Date:** 2026-07-29

## Goal

Make the Console session rail truthful and actionable:

- `Active` contains the currently running session.
- `Needs you` contains sessions with activity the operator has not viewed.
- `Earlier` contains viewed, non-running sessions.
- A genuinely paused pipeline exposes reason-specific decisions and continues
  automatically after the operator chooses one.
- The operator can delete the selected session or clean all inactive sessions
  without deleting append-only governance evidence.

This replaces the current behavior where failed, escalated, and budget-halted
workers are placed in `Needs you` even when no operator decision is pending.

## Observed Failure

The supplied AgentShield log ended after two investigator workers reached their
six-turn cap. The root reported an incomplete bounded recovery and exited
successfully. No pipeline pause was recorded. That session therefore represents
an incomplete, unread result, not an approval gate.

The same log also showed a separate Windows bug: file tools succeeded against
the attached `agentshield` root, while shell commands failed with a
`\\?\C:\...` working directory. The command runner must convert an extended
Windows path to a normal drive path before passing it to the shell. That fix is
independent of the session state machine and will be implemented and tested as
a separate commit.

The stale `example` MCP entry and mojibake visible in the log are outside this
design. They require separate configuration and encoding investigations.

## Session Rail Semantics

Session grouping is based on operator state, not worker outcome:

1. A running session is always in `Active`.
2. A non-running session is in `Needs you` when its latest durable activity is
   newer than its persisted viewed cursor.
3. A viewed, non-running session is in `Earlier`.

`failed`, `escalated`, `budget_halted`, and `incomplete` remain status labels.
They do not determine the rail bucket.

Selecting a session immediately advances its viewed cursor to the latest
durable activity known at the selection boundary. The session moves from
`Needs you` to `Earlier`, but its status and any unresolved pause remain
visible. Activity arriving after that cursor makes the session unread again.
The session currently displayed while new activity arrives is treated as
viewed; switching away persists its latest visible activity cursor.

## Durable View and Lifecycle State

Add a Console-owned table in the Console database:

```sql
CREATE TABLE orchestrator_session_state (
    chat_id TEXT PRIMARY KEY,
    viewed_through REAL NOT NULL DEFAULT 0,
    deleted_at REAL,
    updated_at REAL NOT NULL
);
```

`viewed_through` stores an activity timestamp/cursor, not a Boolean, so later
events can make a session unread again. `deleted_at` is a UI lifecycle
tombstone. This table is mutable operator state and is not part of the
append-only audit ledger.

The backend computes each session's latest activity from its durable chat,
root-turn, worker, and runtime-event timestamps. Deleted sessions are excluded
from the session index but their audit rows remain queryable by governance
surfaces.

## Pause Projection

Extend the projected pipeline run with:

- `pausedAtStage`
- `pausedAtChunk`
- `pauseReason`
- `pendingAction`

Only `stage_review` and `budget_exhausted` are accepted pause reasons. Unknown
values fail closed and expose an error instead of rendering guessed controls.

Pause state does not affect unread grouping. A paused session that has been
viewed remains in `Earlier`, with a visible `Waiting for decision` panel in the
selected session.

## Operator Decisions

The selected paused session shows actions determined by its reason:

### Stage review

- `Approve`
- `Retry` with an optional operator hint
- `Approve remaining`
- `Abort`

### Budget exhausted

- `Grant +3`
- `Continue without more workers`
- `Abort`

The Tauri command receives the pipeline session ID and decision, then:

1. opens the state database through a short-lived writable connection;
2. starts an immediate transaction;
3. re-reads the session pause row;
4. verifies that the reason still permits the requested decision;
5. clears the pause fields and writes the single pending action;
6. commits once;
7. relaunches continuation for that exact pipeline session.

Double-clicks and stale UI fail because the second transaction no longer finds
the expected pause. The UI disables all decision controls while the command is
in flight and surfaces any backend error without optimistic state mutation.

## Resumable Relaunch

Approval must continue the original run, not create a new logical pipeline.
The state store therefore persists the minimum non-secret resume checkpoint:

- pipeline session ID
- parent chat ID
- original request ID
- pipeline name
- profile name
- task text

Folder access is not copied into a mutable checkpoint. Relaunch loads the
immutable folder-grant snapshot already stored for the original request.

The standalone agent gains an internal continuation argument scoped to Console
launches. The deterministic pipeline runner reconstructs completed stage
summaries from the append-only stage store, consumes the pending action once,
and resumes at the correct stage/chunk:

- `approve` continues with the next stage;
- `retry` creates the next attempt for the paused stage;
- `auto_approve_rest` continues and suppresses later review pauses;
- `grant` retries the paused stage with three additional worker slots;
- `force` retries without further worker spawning;
- `abort` finalizes the existing pipeline as aborted without launching a
  worker.

The continuation keeps the same pipeline session ID and chat ID but receives a
new runtime request ID for its new host process. Audit ancestry therefore
remains append-only and each launch is independently observable.

## Delete Selected Session

Only the selected session exposes `Delete session`.

Deletion is rejected when that session is running. For an inactive session, one
transaction:

- records its lifecycle tombstone;
- removes its Console chat messages;
- removes editable session folder grants;
- preserves immutable request folder snapshots;
- preserves agent turns, worker audit, tool audit, runtime events, and pipeline
  state evidence.

If the selected session is the current active conversation, Console mints and
selects a fresh empty session after deletion. Historical evidence remains
available through Audit and policy views, not through the operational Sessions
rail.

## Clean All Sessions

`Clean all sessions` is placed in the Sessions rail and always requires an
explicit confirmation dialog describing what is retained.

The operation fails closed if any agent is running. Otherwise a single
transaction tombstones all visible sessions, removes their chat messages and
editable folder grants, and then mints one fresh empty active session. Immutable
request snapshots and all governance evidence remain intact.

## Error Handling

- Missing or read-only state DB: pause decisions are disabled with an explicit
  reason; unread grouping and deletion remain available through the Console DB.
- Stale pause state: reject the decision and refresh.
- Relaunch failure after a committed decision: preserve the pending action,
  show `Resume failed`, and allow a safe retry without duplicating the action.
- Running-session deletion or clean-all: reject before any database mutation.
- Unknown session ID or deleted session: reject and refresh.
- Unknown pause reason/action pair: reject fail-closed.

## Testing

### Rust data and Tauri tests

- unread cursor grouping inputs and session tombstone filtering;
- selecting a session advances only that session's cursor;
- later activity makes a viewed session unread again;
- pause projection from the state DB;
- valid and invalid decision matrices;
- stale/double decision rejection;
- delete-selected preservation of evidence and request snapshots;
- clean-all atomicity and running-session rejection;
- continuation launch spec preserves session, request, profile, and grants.

### JavaScript view-model and UI tests

- rail buckets depend on running/unread state, not worker status;
- selecting a `Needs you` session calls mark-viewed;
- paused action sets match the pause reason;
- viewed paused sessions retain the decision panel in `Earlier`;
- only the selected session shows `Delete session`;
- clean-all confirmation and disabled states;
- backend errors remain visible.

### Python runner tests

- each pending action resumes at the correct stage/chunk;
- pending actions are consumed exactly once;
- completed stages are not rerun after approval;
- retry and grant create a new attempt;
- immutable request folder grants are restored;
- abort performs no model call;
- Windows command cwd strips the verbatim prefix before shell launch.

## Out of Scope

- Deleting append-only audit or policy evidence.
- Treating failure, escalation, or budget halt as unread by itself.
- Automatically approving a session merely because it was viewed.
- Repairing arbitrary user MCP server configuration.
- General log encoding cleanup beyond separately diagnosing the mojibake.
