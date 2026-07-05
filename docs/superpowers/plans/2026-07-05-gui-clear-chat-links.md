# GUI Clear Chat And Clickable Outputs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a clear control for the Orchestrator driver chat/session view and make generated output links reliably clickable.

**Architecture:** Keep audit history durable, but let the GUI clear the current operator view. Backend clears `chat_log` and idle runtime tails; frontend hides existing sub-agent session cards until new workers arrive, resets chat/process state, and renders Musubi markdown links through a tested parser.

**Tech Stack:** React/Vite frontend, Tauri v2 Rust command bridge, Node built-in test runner, Cargo tests.

## Global Constraints

- Do not delete append-only audit/sub-agent history when clearing the chat UI.
- Do not allow clear while the driver process is running; cancel remains the active-run control.
- Musubi internal links must use explicit click handlers, not plain navigation.
- Keep the UI dense and consistent with the existing Orchestrator chat styling.

---

### Task 1: Link Parser

**Files:**
- Create: `gui/src/components/chatLinks.js`
- Create: `gui/src/components/chatLinks.test.mjs`
- Modify: `gui/src/components/ChatBody.jsx`

**Interfaces:**
- Produces: `parseInlineSegments(text: string) -> Array<{type,text,href?,label?}>`
- Consumes: parser in `InlineText`

- [x] **Step 1: Write failing parser tests**

Run: `node --test gui/src/components/chatLinks.test.mjs`

Expected before implementation: FAIL because `chatLinks.js` does not exist.

- [x] **Step 2: Implement parser and wire ChatBody**

Render `musubi-log:` and `musubi-artifact:` links as `button type="button"` with explicit handlers and `preventDefault`.

- [x] **Step 3: Verify parser tests pass**

Run: `node --test gui/src/components/chatLinks.test.mjs`

Expected: PASS.

### Task 2: Backend Clear Action

**Files:**
- Modify: `gui/src-tauri/src/lib.rs`

**Interfaces:**
- Produces: `clear_driver_chat_log(conn: &Connection, rt: &mut ChatAgentRuntime) -> Result<(), String>`
- Consumes: Tauri `action("clear_driver_chat")`

- [x] **Step 1: Write failing Rust test**

Run: `cargo test --manifest-path gui/src-tauri/Cargo.toml clear_driver_chat`

Expected before implementation: FAIL because `clear_driver_chat_log` does not exist.

- [x] **Step 2: Implement clear helper and action**

Delete only `chat_log`; clear draft/runtime tails when no agent is running; return an error if an agent is still running.

- [x] **Step 3: Verify Rust test passes**

Run: `cargo test --manifest-path gui/src-tauri/Cargo.toml clear_driver_chat`

Expected: PASS.

### Task 3: Frontend Clear Button And Session Mask

**Files:**
- Modify: `gui/src/data/TauriSource.js`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/views/Orchestrator.jsx`
- Modify: `gui/src/components/ChatBody.jsx`

**Interfaces:**
- Produces: `clearDriverChat()` action
- Consumes: `vals.onClearDriverChat`, `vals.clearDriverDisabled`

- [x] **Step 1: Add local clear action**

Record the highest current sub-agent id as a visual cutoff, clear selected worker, close process/log windows, clear chat locally, and invoke backend `clear_driver_chat`.

- [x] **Step 2: Filter session cards after clear**

When merging backend state, hide sub-agents whose id is less than or equal to the local cutoff.

- [x] **Step 3: Add Chat header button**

Place a compact `Clear` button beside the `Chat · driver` title and disable it while the driver is running.

### Task 4: Verification

- [x] **Step 1: Run Node parser tests**

`node --test gui/src/components/chatLinks.test.mjs`

- [x] **Step 2: Run Rust clear tests**

`cargo test --manifest-path gui/src-tauri/Cargo.toml clear_driver_chat`

- [x] **Step 3: Build GUI**

`npm run build --workspace musubi-gui`

- [x] **Step 4: Check whitespace**

`git diff --check`
