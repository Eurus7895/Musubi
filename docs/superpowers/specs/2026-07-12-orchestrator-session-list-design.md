# Orchestrator Session List and Agent Flow Design

## Context

The desktop console already assigns each Orchestrator conversation an exact,
project-scoped `chat_id`. New session mints a new ID without deleting durable
rows. The current view model nevertheless filters all run history to the active
ID and groups the rail by `parent_session_id`. This makes prior sessions vanish
from the UI and produces the misleading `Session unavailable` label.

The root worker is recorded in `agent_turns`; summoned workers are recorded in
the append-only `subagent_audit`. The UI currently renders only the latter as
timeline nodes, so a driver-only turn appears to contain no agents.

## Goals

- Rename the Orchestrator rail from Parent runs to Sessions.
- Keep every non-empty Orchestrator session visible after New session.
- Add a new session to the rail only after its first message is persisted.
- Allow an idle operator to select an older session, restore its chat, and
  continue that conversation.
- Render the latest turn in the selected session as `root -> summoned workers`.
- Show what root and each summoned worker are doing or did.
- Remove `Session unavailable` and worker-empty copy for driver-only turns.

## Non-goals

- No per-session filesystem directory or worktree.
- No new session database table.
- No deletion or mutation of append-only orchestration audit rows.
- No cross-project session browsing.
- No nested turn picker; the workspace focuses the latest turn in a session.

## Data Model

`chat_log` is the session index because it records the first user message before
the process starts. The Rust data layer exposes an `orchestratorSessions` array
grouped by non-empty Orchestrator `chat_id`. Each item contains:

- `chatId`;
- `createdAt` and `updatedAt` from the first and last chat rows;
- `title` from the first user message;
- `lastRequest` from the latest user message;
- the number of root turns and summoned workers;
- an aggregate status derived from the live runtime overlay and audited turns.

An empty newly minted ID has no `chat_log` row and therefore is not listed.

`AgentTurn` gains its root `request`, read from the audit `sessions` row keyed by
`parent_session_id`. This is presentation metadata only; existing governance and
session ownership remain unchanged.

## Session Selection

Selecting a rail card while the project writer is idle changes the active
Orchestrator `chat_id` to that existing ID. The next snapshot loads that
session's chat through the existing exact-session query. Selecting is rejected
while any Orchestrator or Pipeline run owns the project writer lease.

New session continues to mint and persist a fresh ID. It clears only the current
chat view and retained process overlay. It does not delete prior `chat_log`,
`agent_turns`, `subagent_audit`, or session index entries.

## Agent Flow

The selected session chooses its latest root turn. The timeline begins with a
synthetic presentation node backed by that `AgentTurn`:

1. `root`, with the user request as its brief;
2. workers whose `parent_session_id` matches that turn;
3. worker order follows audit spawn order.

For an active turn that has not written its aggregate `agent_turns` row yet,
the runtime overlay supplies the root node, request, running status, and start
time. Audited workers are attached when their spawn rows appear.

A completed driver-only turn therefore renders one completed root node instead
of an empty timeline. The node text uses the request for "what it is doing";
status and token/cycle metadata come from `agent_turns` where available.

## UX Copy

- Rail heading: `Sessions`.
- Rail helper: `newest first · project conversations`.
- Empty rail: `No sessions yet. Send a message to start one.`.
- Workspace title: the selected session's short stable label.
- Workspace subtitle: latest root turn plus its worker count.
- Timeline ordering label: `agent flow`.
- The strings `Parent runs` and `Session unavailable` are removed from the
  Orchestrator view.

## Failure Handling

- Unknown, empty, Pipeline, or cross-project chat IDs are rejected by the Rust
  selector and do not replace the active session.
- Selection while the writer lease is busy returns an actionable error and
  leaves both the runtime owner and active session unchanged.
- Legacy audit rows without `chat_id` remain visible through the existing
  Orchestrator fallback grouping but cannot masquerade as a selectable modern
  session.

## Testing

- Rust data tests prove session summaries come from non-empty chat rows and that
  the root request joins from `sessions`.
- Rust shell tests prove selecting an existing session swaps the active ID,
  selecting an unknown ID fails, and New session preserves prior summaries.
- JavaScript source tests prove selecting a rail session invokes the backend
  switch and New session does not clear the retained session index.
- View-model tests prove sessions are grouped by `chat_id`, old sessions remain
  after the active ID changes, and every selected flow starts with root before
  workers.
- Component source tests lock the approved copy and remove the misleading copy.

