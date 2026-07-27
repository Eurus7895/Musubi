# Console Request Runtime History

## Context

The Orchestrator Console currently presents only the latest root turn inside a
conversation session. `groupOrchestratorSessions` selects `latestTurn` and then
filters workers to that turn's `parentSession`, so starting a second request
removes the first request's topology from the center workspace even though the
audit rows still exist. The workspace also splits runtime evidence into
top-level `Graph` and `Logs` tabs, while clicking a graph node switches to the
generic log table. Finally, collapsing the Sessions rail leaves a 58 px strip
instead of hiding it.

Process output has a separate durability problem. `ChatAgentRuntime` keeps only
64 KiB `stdout_tail` and `stderr_tail` buffers, clears both when the next request
starts, and does not capture Tauri host messages such as
`[musubi] launching agent ...`. That representation cannot power historical
request or worker transcripts.

This design replaces the latest-turn projection with full request history,
introduces request- and agent-scoped raw transcripts, and simplifies the
Conversation panel so runtime evidence has one owner.

## Goals

1. Hide the Sessions rail completely when the operator closes it.
2. Display every request in a selected conversation session, oldest to newest.
3. Render real request/agent topology rather than a chronological card list.
4. Replace the top-level `Graph / Logs` switch with graph-to-detail navigation.
5. Give request nodes a whole-request Overview and Request log.
6. Give agent nodes an agent-only Overview and Agent log.
7. Persist raw runtime output append-only so later requests never erase it.
8. Remove the redundant `Summary / Verbose` switch from Conversation.
9. Preserve Musubi's existing dark visual language with slightly larger text.

## Non-goals

- Changing pipeline composition, worker scheduling, policy, or model routing.
- Making new LLM calls from the substrate or Console.
- Reconstructing historical raw transcripts that predate the new ledger.
- Replacing structured audit tables; raw logs complement, not supersede, audit.
- Turning the Conversation panel into another runtime evidence surface.

## Interaction Model

The center workspace has one active screen at a time:

```text
Session graph -> Request detail -> Back to graph
              -> Agent detail   -> Back to graph
```

There is no split view and no persistent detail pane. `Back to graph` restores
the selected session graph and its scroll position.

### Session graph

Each root request is a first-class node. Requests are ordered by their start
time and connected by a dashed orange `next request` edge. That edge means
chronological continuation only.

Within a request subgraph, solid blue directed edges mean `summoned`. A request
root connects to workers spawned by the driver; nested workers connect to their
audited parent handle. Completed request graphs stay visible when a new request
starts. The active request and currently running node use the existing orange
selection treatment.

Clicking a request node opens Request detail. Clicking a worker node opens
Agent detail.

### Request detail

Request detail has two tabs:

- **Overview:** prompt, status, start/end time, total cycles, tokens, tool calls,
  worker count, artifacts/files, final answer, and the request's agent list.
- **Request log:** the complete chronological transcript for that request,
  merging Tauri host, root driver, and every worker event.

Request log supports search and source filters: `All`, `Host`, `Root`,
`Workers`, `stdout`, and `stderr`.

### Agent detail

Agent detail has two tabs:

- **Overview:** role, handle, brief, audited parent, model/profile, status,
  turns, token usage, tools, skills, policy denials, stop reason, and output
  summary.
- **Agent log:** only transcript events whose `agent_handle` is the selected
  agent.

Agent log supports search and category/stream filters: `All`, `Model`, `Tools`,
`Policy`, `stdout`, and `stderr`.

Overview does not embed log rows. It may contain an `Open log` action that
switches to the sibling log tab.

### Sessions rail

Closing Sessions removes the rail from layout instead of shrinking it to
58 px. A small `Show sessions` control remains attached to the left edge of the
center workspace so the action is reversible. Hiding the rail does not alter
the selected session.

### Conversation

The `Summary / Verbose` control and verbose audited-activity list are removed.
Conversation continues to own:

- successful skills summary;
- token economics summary;
- narrative/chat messages and artifacts;
- request input and send/cancel controls.

Runtime topology and raw evidence remain exclusively in Runtime Evidence.

## Durable Runtime Log Ledger

### Identity

The Console creates a stable `request_id` before launching each driver process.
It passes that identifier to the driver as launch metadata. The same identifier
is written to a new nullable `agent_turns.request_id` column, so request
topology, structured audit evidence, and raw transcript events join
deterministically. Association must never rely on nearest timestamps or parsing
a generated parent session ID from display text. The column is nullable only
for records created by older versions.

The existing `parent_session_id` remains the governance/audit parent for worker
relationships. `request_id` is the Console-facing identity of one submitted
request. `chat_id` remains the identity of the long-lived conversation session.

### Append-only schema

A new append-only `runtime_log_events` ledger is stored beside the Console's
existing audit-backed state:

```text
id             INTEGER PRIMARY KEY AUTOINCREMENT
request_id     TEXT NOT NULL
chat_id        TEXT NOT NULL
seq            INTEGER NOT NULL
ts             REAL NOT NULL
source         TEXT NOT NULL   # host | root | worker
stream         TEXT NOT NULL   # host | stdout | stderr
agent_handle   TEXT            # NULL for host/root, worker handle otherwise
role           TEXT            # root/planner/coder/...
category       TEXT            # lifecycle/model/tool/skill/policy/output
message        TEXT NOT NULL
```

`(request_id, seq)` is unique. Indexes cover `(chat_id, ts)`,
`(request_id, seq)`, and `(request_id, agent_handle, seq)`. Rows are inserted
only; a later request cannot update or delete an earlier transcript.

Request log queries all rows for `request_id`. Agent log adds
`agent_handle = selected_handle`. Host and driver-wide messages belong to the
request/root scope and therefore do not appear in a worker's Agent log.

### Capture protocol

Tauri writes its own lifecycle messages directly to the ledger, including audit
DB resolution, command launch, cancellation, exit status, and spawn failures.
The driver emits line-framed runtime records with request identity, role, worker
handle, category, stream, and display text. Tauri preserves the human-readable
text for the terminal while persisting the structured envelope.

Worker attribution is supplied at emission time from the worker execution
context. The Console must not infer ownership afterward from role names or
free-form message text. Unattributed child output is retained at request/root
scope rather than guessed onto a worker.

The in-memory 64 KiB tails may remain as a short live-status cache, but they are
not the source of historical log truth. The durable ledger has no per-request
64 KiB truncation. UI rendering uses pagination or virtualization so large
transcripts do not block the application.

### Compatibility

Old databases receive the new table and nullable request identity through the
existing additive migration path. Sessions created before this change still
show structured topology and audit evidence. Their raw-log tabs show an
explicit `Raw transcript was not captured for this request` empty state rather
than synthesizing incomplete output.

A request that exits through deterministic scope clarification before opening
a parent audit session still has a request root and raw transcript because the
Console-created `request_id` exists before process launch. Its worker subgraph
is simply empty.

## Presentation Model

`groupOrchestratorSessions` must return all root turns for each chat instead of
only `latestTurn`. The selected session projection exposes:

- ordered request roots;
- workers grouped by `parent_session_id`;
- parent/child edges inside each request;
- chronological edges between request roots;
- request summaries and request-scoped log events;
- agent summaries and agent-scoped log events.

Pipeline stages launched from an Orchestrator request join only the request that
owns their pipeline run. An older pipeline cannot attach to a newer direct
request. Ambiguous audit rows stay request-scoped and visible; they are never
assigned to a worker without an audited handle.

The React view owns only navigation state:

```text
{ screen: "graph" }
{ screen: "request", requestId, tab: "overview" | "log" }
{ screen: "agent", requestId, agentHandle, tab: "overview" | "log" }
```

Changing the selected session resets the center screen to `graph`.

## Visual Design

Use the existing Console tokens and the approved dense log mockup:

- `IBM Plex Mono` for detail titles, tabs, filters, badges, timestamps, source
  labels, and transcript content;
- background `#0d1117`;
- log surface `#0c131c`;
- panels between `#101721` and `#141b27`;
- borders `rgba(255,255,255,.08)`;
- active/selected orange `#ff9b3d`;
- primary text `#e9edf4`;
- muted metadata `#7d8999`;
- cyan/blue for host/root identity;
- teal/purple role colors for workers;
- green success and red failure/deny.

Typography is larger than the original compact prototype without becoming
spacious:

- detail title: 20 px;
- graph node title and transcript content: 13 px;
- tabs, filters, badges, breadcrumb, and metadata: 12 px;
- sequence/timestamps and graph metrics: 11 px;
- transcript line-height: 1.65.

Borders stay thin, spacing stays compact, and active glow remains restrained.

## Error Handling and Safety

- A failure to persist a log event cannot crash or cancel the driver; it emits a
  bounded diagnostic and continues.
- SQLite busy handling uses the project's existing timeout/WAL conventions.
- Invalid or unknown request identities fail closed to request-level display;
  they are never attached to a different request or worker.
- Search and filtering operate on already-loaded/paged display fields and never
  interpolate raw text into SQL.
- Existing path and argument output remains visible because this is explicitly
  a raw transcript surface. Secrets must continue to be excluded at the
  producer boundary; the UI does not attempt unreliable after-the-fact
  redaction.

## Testing Strategy

### Rust data and capture tests

- Schema migration creates `runtime_log_events` without damaging old DBs.
- Host, root, and worker records retain request identity and sequence.
- Request queries merge all sources in order.
- Agent queries return only the selected `agent_handle`.
- A second request appends rows without changing the first request.
- Tails can roll over 64 KiB while durable rows remain complete.
- Legacy requests return the explicit unavailable state.

### View-model tests

- One chat with two root turns projects two request roots and both worker sets.
- Request continuation and summon edges use different relation types.
- Older pipeline stages remain attached to their owning request only.
- Request log contains host, root, and all worker events.
- Agent log excludes host, root, and sibling worker events.
- Changing sessions resets detail navigation to graph.

### React source/component tests

- Sessions hide removes the rail and exposes `Show sessions`.
- No top-level `Graph / Logs` toggle remains.
- Request and agent nodes open their respective detail screens.
- Both detail screens expose Overview, scoped log, and `Back to graph`.
- Conversation has no `Summary / Verbose` switch or verbose evidence list.
- Approved typography and palette tokens are applied.

### Verification

Run focused Node and Rust tests, the full GUI test suite, the Tauri data-core
tests, and the production GUI build. Manually verify the three navigation paths
with a session containing at least two requests and nested workers.
