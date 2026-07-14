# Final Review Fix Report

**Status:** DONE

**Implementation commit:** `43d7156 fix(gui): make historical resume atomic`

## Root-cause analysis

The reviewed implementation treated historical-session promotion as a UI
selection update followed by an ordinary launch. That assumption broke because
selection, durable state, and the shared writer lease are one ownership change,
not three independent operations.

Before the fix, the relevant flow was:

1. `snapshot` acquired `state.db`, loaded state and chat rows, and only near the
   end acquired `state.chat_agent`. The lock order was DB -> runtime.
2. `send_chat`, `select_session`, and `new_session` acquired runtime -> DB.
   A poll holding DB and a send holding runtime could therefore wait forever on
   each other.
3. Historical `send_chat` checked `rt.running`, promoted active/viewed session
   state, released runtime, inserted the user row, and then called
   `start_chat_agent`, which reacquired runtime and attempted the actual claim.
   A Pipeline Studio action or second send could claim the single project lease
   in that gap. The losing send retained a durable user row and promoted
   ownership even though no process launched for it.
4. `store_session_nonce` discarded the SQLite result. Idle promotion changed
   active/viewed ownership before attempting that ignored write, so an
   unpersistable session could become the in-memory owner and launch.
5. `TauriSource.sendChat` captured the viewed ID for normal sends, but cleared
   local selection before classifying picker commands and invoked
   `pipeline_hint` with only text. The backend consequently used the active
   `state.chat_id`; all three supported spellings could write into a different
   session than the one on screen.

The concrete root-cause hypothesis was: **the deadlock, TOCTOU row leak, and
nonce failure share one cause—runtime ownership was not the serialization
boundary for every durable mutation that authorizes a launch, while snapshot
acquired the same locks in reverse order.** The focused regressions confirmed
that hypothesis.

## Fix

- `gui/src-tauri/src/lib.rs:443` adds `prepare_orchestrator_send`, an
  already-borrowed helper called while the action owns the one runtime mutex.
  It validates the exact project/surface chat ID, writes the promotion nonce
  and user message in one SQLite transaction, commits, claims the runtime, and
  only then changes active/viewed in-memory ownership.
- `gui/src-tauri/src/lib.rs:478` gives Pipeline Studio the corresponding
  claim-before-launch boundary, so a pipeline cannot persist its user brief
  after losing the shared runtime race either.
- `gui/src-tauri/src/lib.rs:1204` explicitly drops snapshot DB guards before
  reading runtime. Snapshot therefore never nests DB -> runtime, while mutation
  paths serialize through runtime -> DB.
- `gui/src-tauri/src/lib.rs:307` makes nonce storage return `Result`. New-session
  and selection promotion persist before ownership mutation; startup fails
  closed if the initial nonce cannot be stored.
- `gui/src/data/TauriSource.js:198` preserves historical selection for picker
  commands and passes the exact requested ID. Backend
  `resolve_orchestrator_history_target` validates it and never falls back to the
  active session.
- `docs/guide.md:406` and `gui/README.md:96` now state that deterministic stage
  workers appear in Pipeline Studio and Audit. The documentation cleanup plan
  records the final-review correction.

The launch code remains in the driver-side Tauri process and continues through
the existing standalone `agent` CLI/LMRouter path. No LLM SDK or call was added
to the substrate. The single process/project lease and exact durable chat-ID
checks remain intact.

## TDD evidence

### RED

- Frontend picker regression, before production changes:
  `node --test gui/src/data/TauriSource.test.mjs` -> **15 passed, 3 failed**.
  `pipeline`, `/pipeline`, and `run pipeline` each expected the historical
  selection but observed `null`, proving the selection-clear/misroute defect.
- Rust regressions, before production changes:
  `cargo test --manifest-path gui/src-tauri/Cargo.toml
  atomic_send_boundary_claims_runtime_and_persists_to_exact_history -- --exact`
  failed compilation with `E0425` for missing `prepare_orchestrator_send` and
  `resolve_orchestrator_history_target`. These tests defined the atomic claim,
  race refusal, nonce failure, and exact picker-target behavior before the
  implementation existed.

### GREEN

- `node --test gui/src/data/TauriSource.test.mjs` -> **18 passed, 0 failed**;
  all three picker spellings preserve and forward the viewed session.
- Focused Rust atomic boundary -> **1 passed, 0 failed**.
- Focused real-thread competing claim -> **1 passed, 0 failed**. The competitor
  claims the Pipeline Studio runtime first; resumed send writes zero rows and
  leaves active/viewed and runtime ownership unchanged.
- Full Rust suite includes and passes
  `nonce_write_failure_leaves_send_ownership_unchanged` and
  `pipeline_hint_resolves_all_spellings_to_exact_viewed_history`.

## Full verification

- Frontend Task 5 selection: **61 passed, 0 failed**.
- `cargo test --manifest-path gui/src-tauri/Cargo.toml`: **31 passed, 0 failed**,
  plus 0 main/doc-test failures.
- `npm run build --prefix gui`: exit 0, Vite transformed **58 modules**.
- Repository stale-documentation query: no matches.
- Live relative Markdown-link check: **35 Markdown files**, zero missing links.
- `git diff --check`: exit 0.
- Scope review: only the six intended implementation/test/documentation files
  entered commit `43d7156`; `.superpowers/sdd/final-review-findings.md` and
  `artifacts/hanoi-dashboard.html` remained untracked and untouched.

## Self-review and concerns

- The SQLite transaction commits before the in-memory claim, but the runtime
  mutex remains held and `rt.running` was already verified false. Therefore no
  competing action can claim between commit and `claim_runtime_owner`; the
  latter has no remaining fallible external operation.
- A launch-spec or process-spawn failure releases only the matching runtime
  claim and appends the existing deny message to the same exact chat ID.
- Picker hints remain persistent by design, but now target only the validated
  viewed session; an invalid or missing requested historical ID returns an
  error instead of falling back.
- No known merge-blocking concerns remain.
