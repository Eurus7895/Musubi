# GUI vs CLI orchestrator token divergence: session isolation, two-layer validation, observability

## Context

The standalone `agent` orchestrator is reached from two surfaces:

- **CLI** — `agent "<task>"`. Stateless one-shot: `chat_id` defaults to `None`
  (`musubi/agent/run.py:183`), so no history is replayed. The model sees only
  the current task.
- **GUI** — Tauri `start_chat_agent` launches `agent "<task>" --chat-id <id>`
  (`gui/src-tauri/src/lib.rs:570-636`). Stateful: prior turns are replayed on
  every user message.

The same orchestrator, but **not the same input**. On a like-for-like artifact
task the GUI run spent ~2.7x the input tokens of the CLI run
(`in_tokens` 76,743 vs 28,333), and the GUI log showed
`chat_id=gui-orchestrator-… replay_messages=49` while the CLI log has no such
line.

### Root cause (confirmed in code)

Routing is **not** the difference. Both surfaces call `classify_task(task)`
(`run.py:210`), which reads only the current `task` — it is history-independent,
so GUI and CLI produce the **same** route and the **same** per-turn spawn caps
(`run.py:1424-1443`). The GUI does not spawn more workers than the CLI.

The divergence is **conversation replay**, and it compounds:

1. **The GUI session is immortal and equals the project.** `scoped_chat_id`
   hashes **only the project root** (`lib.rs:191-202`) and is minted once at
   startup (`lib.rs:1009`). There is no session nonce, no reset, no "new
   session" action anywhere in the GUI (verified by search). Every run for a
   project — across app restarts — reuses one `chat_id`, so
   `conversation_messages` (keyed by `chat_id`, `session/conversations.py:147`)
   accumulates without bound. That is why `replay_messages` reaches 49.

2. **The replayed history is the seed, and the seed is re-sent every cycle.**
   `_load_chat_history` (`run.py:326`, budget from `MUSUBI_CHAT_HISTORY_TOKENS`
   or the 50k default, `run.py:1061-1065` / `conversations.py:48`) builds
   `initial_messages` once (`run.py:329`). But the LM API is stateless: each
   cycle re-sends the **entire** `messages` array (`run.py:516,557`). A 50k
   seed therefore costs ~50k input tokens on **every** cycle. Cost ≈
   `seed_size × cycles`.

3. **Prior tool results re-enter verbatim.** `_messages_from_chat_history`
   re-injects `tool` rows as `[prior tool result]\n…` (`run.py:1082-1083`), so a
   large artifact produced last turn is re-ingested into this turn's seed.

### Two separate history stores (important)

The GUI display history and the agent replay history are **different tables in
different databases**:

| Store | Table / key | Purpose | Cleared by |
|---|---|---|---|
| Display | `chat_log`, keyed by `surface` (console DB) | what the GUI renders | `clear_driver_chat` → `DELETE FROM chat_log WHERE surface=?` (`lib.rs:102-114,956`) |
| Replay | `conversation_messages`, keyed by `chat_id` (agent compression DB) | what the agent replays as `initial_messages` | nothing today |

The existing "clear chat" action clears the **display** but leaves the
**replay** store intact — so clearing the visible chat does **not** reduce what
the agent replays. Any session-reset must reset the `chat_id`-keyed replay
store, not just `chat_log`.

## Goal

1. **Session isolation.** GUI history is scoped per session, not per project. A
   new session starts with fresh replay history; restarting the app continues
   the current session (chosen option **a**).
2. **Replay bound by session, not by a regex.** Stop any plan to gate replay on
   `is_simple_scope`/`classify_task`. The 50k budget becomes a within-session
   safety valve only.
3. **Two-layer validation with correct owners.** Separate *mechanical*
   validation (deterministic, goal-free, owned by worker+hook) from *acceptance*
   validation (goal-aware, owned by the root — the only holder of the goal).
4. **Observability.** Logs name the tools used and the replay token cost; the
   GUI surfaces these as first-class, not raw stderr.
5. **Explicit routing.** Planning becomes opt-in via a `plan` command rather
   than a regex-forced planner stage; `classify_task` is demoted to advisory.

Net effect: a self-contained GUI artifact task runs in a fresh session with
`replay_messages=0`, so GUI cost ≈ CLI cost, while genuine multi-turn threads
keep the history the root needs to hold the goal.

## Architecture principles

**Session = goal thread.** The root is the only layer that holds the user's
goal (the worker gets a narrowed brief; the reviewer is firewalled to `code`
only, HI#3). History exists so the root can hold the goal across turns.
Therefore history scope = session scope = goal thread. A new goal → a new
session → fresh replay. This is the principled replacement for regex-based
"is this turn simple" budgeting.

**Two validation layers, two owners.**

| Layer | Question | Owner | Nature |
|---|---|---|---|
| Mechanical | does it compile / lint / test / exist? | worker + hook | deterministic, goal-free |
| Acceptance | does it satisfy the goal? | **root** | goal-aware, irreducible |

`verify_subagent_summary` (`validation/verifier.py:594-642`) runs **no**
mechanical checks today — only truncation, secret scan, injection scan, and
schema check (status enum, `verifier.py:92-99`). So a coder's `status="pass"`
is an **unverified self-report**. That is precisely why the root re-reads the
artifact: it has legitimate reason to re-establish facts the worker never
proved. The fix is not "trust the self-report" but "give the worker a
deterministic mechanical signal the root can accept for free, so the root spends
tokens only on acceptance — on a compact surface, not a full re-ingest."

## Design

### Workstream A — Session isolation (driver-side; substrate untouched)

`conversation_messages` already treats `chat_id` as opaque
(`conversations.py:24`) and filters by it, so this is a pure driver change at
the inject boundary (HI#1).

- **A1** — `scoped_chat_id` (`lib.rs:191`) gains a session nonce:
  `gui-{surface}-{project_hash}-{nonce}`. `nonce` is a monotonic/uuid value
  minted per session.
- **A2** — Persist the current nonce per project+surface (console DB or config).
  On startup, load it; if absent, mint one. Replaces the mint-once call at
  `lib.rs:1009`. Restart therefore continues the current session (option a).
- **A3** — "New session" action (both `orchestrator` and `pipeline` surfaces):
  mint a fresh nonce, update `AppState.chat_id`, and clear the `chat_log`
  display for that surface. Because replay is keyed by `chat_id`,
  `get_history` returns empty automatically — no deletion of
  `conversation_messages` needed. Old history is retained under the old
  `chat_id` (append-only) for future browsing. Reuse/extend the existing
  `clear_driver_chat` seam (`lib.rs:102-114,956`) so both the display store and
  the replay pointer reset together (fixing the two-store gap noted above).
- **A4** *(deferred)* — Session list / browse past sessions, aligned with the
  pipeline run-history split (#134). Not required for the token fix.
- **A5** — Remove any coupling of replay/budget to `is_simple_scope` /
  `classify_task`. Replay is bound by session scope (A1-A3). The 50k budget
  (`conversations.py:48`) remains only as a within-session safety valve.

### Workstream B — Observability (substrate; independent; do first)

- **B1** — `_model_action` (`run.py:1776`) reports detail: distinguish
  `spawn(<role>)` / `read` / `grep` / `write`, not just the coarse
  `tool_calls` bucket.
- **B2** — `_log_cycle` (`run.py:1799`) logs tools by name+count, e.g.
  `tools=[grep×3, read_file×2, retrieve×1]` instead of `tools=6`. This makes a
  verification-loop visible instead of inferred.
- **B3** — The replay line (`run.py:331`) adds `replay_tokens=` from
  `history["total_tokens"]` (already computed, `conversations.py:188`).
- **B4** — GUI parses `replay_messages`, `replay_tokens`, `in_tokens`,
  `token_budget`, `tools[]` into a first-class panel rather than leaving them in
  `stderr_tail` (`lib.rs:387-390`).

### Workstream C — Two-layer validation (substrate + driver)

- **C1 (mechanical gate)** — The worker (via hook / `baseline_checks`, see the
  SessionStart hook `scripts/session_start.py`) runs the project validator. The
  coder summary carries structured fields: `validator_exit`, `artifact_path`,
  `files_touched`. Extend the summary schema in `validation/verifier.py` and the
  coder contract in `.github/agents/coder.agent.md`.
- **C2 (status semantics)** — The coder's `status` means *mechanical-only*
  ("done + here is what I produced"), never "goal achieved". Acceptance-against-
  goal moves entirely to the root. This matches the firewall: workers are cut
  off from the goal by design, so the goal verdict must live at the root.
- **C3 (compact root acceptance)** — The root accepts on a compact surface
  (validator signal + diff/summary + `artifact_path`) instead of re-ingesting
  the full artifact. Also stop re-injecting large `tool` rows verbatim on replay
  (`run.py:1082`): drop or summarize them. Depends on C1.

### Workstream D — Explicit routing (driver)

- **D1** — Remove the forced planner-before-coder at cycle 0 for
  `MEDIUM_CHANGE` (`run.py:1427-1443`).
- **D2** — Add an explicit `plan` command/tool (opt-in planning). Demote
  `classify_task` (`scope.py:114`) to advisory-only prompt guidance; it no
  longer gates cost or spawns. Aligns with the retirement of routing as Hard
  Invariant #4.

### Crosswalk (earlier dependency table → workstream steps)

| Earlier row | Steps |
|---|---|
| 4a — mechanical gate (validator exit + `artifact_path` + `files_touched`) | C1 |
| 4b — `status` mechanical-only; acceptance to root | C2 |
| 3 — root acceptance on compact surface; no full re-ingest | C3 |
| 1 — tool-by-name log + `replay_tokens` + GUI display | B1-B4 |
| 2 — drop forced planner → `plan` command; `classify_task` advisory | D1-D2 |
| — replay seed by goal, not by regex "simple" | Workstream A (A1-A3 realize it; A5 removes the regex coupling) |

## Dependency order

```
E1 (this plan)  →  B (observability, measures the rest)
                →  A (session isolation, highest token impact)
                →  D (independent)
                →  C1 → C2 → C3   (C3 depends on C1)
                →  E2 (roadmap)   →  E3 (tests)   →  PR
```

B, A, and D are mutually independent and independent of C. C3 is blocked by C1.

## Testing

- **A** — unit test that a new nonce yields empty `get_history` while the old
  `chat_id` still returns its rows (append-only retained); test restart loads
  the persisted nonce (continues the session); test "New session" resets both
  `chat_log` and the replay pointer.
- **B** — log-format assertions (`tools=[…]`, `replay_tokens=`); a GUI parse
  test for the new fields.
- **C** — schema test for the new coder summary fields; a test that the root
  accepts a `validator_exit==0 + artifact_path` signal without re-reading the
  artifact; that `status` from a worker never asserts goal acceptance.
- **D** — `classify_task` no longer forces a planner spawn; `plan` command
  spawns a planner on demand.

## Tier tags (HI#9)

| Component | Tier | Note |
|---|---|---|
| Session nonce in `chat_id`, per-session replay | substrate | strengthens the conversation-store contract |
| Two-layer validation split (mechanical vs acceptance) | substrate | anchors "success" to a deterministic signal + firewall-aligned ownership |
| Tool-by-name + `replay_tokens` logging | substrate | observability |
| Forced planner-cycle-0 removal | ephemeral retired | expires-when: `plan` command lands; cost-lever: fewer forced planner turns |
| `classify_task` regex routing | ephemeral (demoted to advisory) | expires-when: explicit intent (`plan` command) is the norm |

## Out of scope / deferred

- Session browsing UI (A4) — separate follow-up, builds on #134 run-history.
- Reactive compaction tuning beyond the existing 80/90/99% path.
- Changes to the feature-frozen embedded extension pipeline.
