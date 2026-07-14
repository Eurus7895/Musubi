# Resumable Historical Session Design

## Context

The Orchestrator lets an operator inspect an older session while another
session owns the single driver process. That historical view is correctly
read-only while the process is running. The defect appears after the process
finishes: the backend retains the older ID in `viewed_orchestrator_chat_id`
without promoting it to the active `chat_id`, while the frontend continues to
disable chat whenever those IDs differ. The session therefore remains
read-only until it is selected a second time.

Worker-card hover is a separate visual issue. `Box` changes only the border on
mouse enter; it does not call the selection action. Its orange hover border is
too similar to the selected style and makes the card look auto-selected.

## Goal

An operator may browse a historical session while another run is active and
continue that exact session as soon as the shared driver becomes idle. The
first follow-up must be recorded and launched with the historical session's
`chat_id`; it must never be routed to the previously active session.

## Behavior

- While any driver process is running, a different historical Orchestrator
  session remains read-only.
- Once the driver is idle, the viewed historical session's input and send
  button become available without requiring another selection click.
- Sending a follow-up passes the viewed session ID in the existing `send_chat`
  command. The backend validates project/surface ownership, promotes that ID
  to the active Orchestrator `chat_id`, persists its nonce, records the user
  message, and launches the agent for that same ID.
- If a process wins the runtime between display and send, the backend refuses
  the resume instead of routing the message to another session.
- Worker hover uses a neutral border distinct from selected/current orange.

## Architecture and Data Flow

### View model

`viewingHistoricalSession` remains an identity fact: the locally selected
session differs from the backend active Orchestrator chat ID. A new blocked
condition combines that fact with `driverStatus.running`. Only the blocked
condition disables input. Pipeline ownership continues to block Orchestrator
chat independently.

### Frontend action

`TauriSource.sendChat` captures `selectedSession` before clearing local
selection and sends it as an optional second `send_chat` argument. New-session
and already-active-session sends pass an empty value and preserve existing
behavior. A single backend command is used; the frontend does not race
`select_session` against `send_chat`.

### Rust command boundary

The `send_chat` action resolves an optional requested session before reading
the launch ID. Resolution reuses the existing project/surface and durable-chat
validation from `select_driver_session`, but rejects promotion whenever the
runtime is busy. The resolved ID is then used for both `insert_chat` and
`start_chat_agent`.

This keeps the existing single-process runtime lease and does not relax
cross-project session validation.

## Error Handling

- Unknown or cross-project IDs return the existing deterministic session
  validation error.
- A busy-runtime race returns a clear refusal and does not fall back to the
  current active ID.
- Empty messages remain no-ops.
- Backend action failures remain visible through the existing action error
  logging; no optimistic message is inserted into another session.

## Tests

- View-model regression: an idle historical session is resumable and chat is
  enabled.
- View-model regression: the same session remains read-only while another run
  owns the driver.
- Frontend action regression: `sendChat` forwards the selected session ID
  before clearing local selection.
- Rust regression: an idle known session is promoted and returned as the send
  target.
- Rust regression: a busy runtime refuses historical promotion and leaves both
  active and viewed IDs unchanged.
- Existing session switching, project-scope rejection, driver ownership, and
  GUI build tests remain green.

## Non-goals

- Multiple concurrent driver processes.
- Mutating a historical session while another run is active.
- Changing pipeline-session behavior.
- Automatically changing the active session merely because a run completed;
  promotion happens only when the operator sends a follow-up.
