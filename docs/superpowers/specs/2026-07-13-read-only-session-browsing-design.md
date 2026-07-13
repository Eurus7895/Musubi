# Read-only session browsing during active runs

## Context

The Console currently treats the orchestrator session being viewed as the
session owned by the driver. Both the JavaScript data source and the Rust
`select_driver_session` boundary reject session selection while an agent is
running. This prevents users from reviewing prior work while a long-running
agent continues in the background.

## Goal

Allow users to browse the complete chat and worker flow of another
orchestrator session while an agent is running, without changing the running
agent's chat ID or routing any output to the viewed session.

## Design

The Console will distinguish the **active session**, which owns the running
driver and future messages, from the **viewed session**, which controls the
read-only content shown in the UI.

- The existing orchestrator chat ID remains the active session identifier.
- Selecting a session records a separate viewed-session identifier and loads
  that session's chat history without mutating the active identifier.
- Worker-flow selection continues to use the viewed session so chat and flow
  remain aligned.
- When no agent is running, selecting an existing session may continue to make
  it active so users can resume that conversation.
- When an agent is running, a viewed historical session is read-only. Sending,
  clearing, starting a new session, and other active-session mutations remain
  unavailable until the run finishes or the user returns to the active
  session.
- Finishing the background run does not automatically replace the user's
  viewed session. The session list and running indicator provide the route
  back to the completed or active run.

## Data flow and boundaries

The Rust backend will validate that a requested viewed session belongs to the
current project and orchestrator surface, then query its durable chat history.
For a busy runtime it must not update the active chat-ID slot, persisted nonce,
or `ChatAgentRuntime`. The frontend stores the returned historical messages in
view-only state. Normal polling may update live runtime state, but must not
overwrite the selected historical chat.

When the runtime is idle, the existing active-session switch remains the
resume path. This preserves current behavior for continuing an old
conversation while adding a separate safe path during active execution.

## Failure handling

- Unknown, cross-project, or cross-surface session IDs are rejected without
  changing active or viewed state.
- A failed history load keeps the current view and reports the backend action
  error through the existing action error channel.
- Empty historical sessions render the existing empty-chat state and remain
  read-only while another session runs.

## Tests and acceptance criteria

- A frontend test selects a historical session while `driverStatus.running`
  and confirms the selected session and read-only chat view change.
- Backend tests confirm busy selection loads the requested history while the
  active chat ID, nonce, and runtime remain unchanged.
- Existing idle selection still switches the active session and can be used
  for subsequent conversation turns.
- Invalid scope and missing-session tests continue to fail closed.
- Polling updates from the running session do not replace the historical chat
  currently being viewed.
- The running agent completes under its original chat ID, with no messages
  written to the viewed historical session.

## Non-goals

- Running more than one agent concurrently.
- Sending messages into a historical session while another run is active.
- Changing pipeline-studio session behavior in this patch.
