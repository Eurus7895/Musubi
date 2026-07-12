# Orchestrator token-blowup fixes (post-run analysis of PR #135)

## Context

A single GUI prompt — "create a dashboard about carl juing" — spent the whole
200k token budget and halted incomplete, on a build that already has the
per-session history fix (`replay_tokens=8`, so replay was **not** the cause).
The trace exposed a cascade, and one link is a regression introduced by this
PR's C1 mechanical gate.

What the log showed, in order:

1. Three coders each tried to write the entire HTML in one `write_file`, hit the
   model's ~6k output cap, truncated, and dispatched nothing (~33k wasted).
2. A fourth coder succeeded via a generator script: wrote `build_jung.py`, ran
   it to produce `carl-jung-dashboard.html`, then **deleted** `build_jung.py`.
3. The C1 gate then linted `build_jung.py` — **already deleted** — so ruff
   returned non-zero, and the gate reported `[mechanical] exit=1
   artifact=build_jung.py`. The real deliverable (the HTML, written by a
   subprocess) was invisible to the gate (it only tracks write/append/edit).
4. Per the C2 prompt ("non-zero exit → route a fix"), the root chased the false
   signal: re-read the full HTML twice (~25k) and spawned an investigator that
   burned ~24k in a fix-then-verify loop whose verification kept crashing on the
   Windows console (`UnicodeEncodeError`) → budget halt.

Root cause ranking: the false gate signal (C1 bug) + the C2 prompt drove ~76k of
unnecessary work. Secondary: monolithic writes, root re-reads, debug
inefficiency, and an over-broad orientation `glob *` (403 files).

## Goal

Make the mechanical signal trustworthy and traceable, make the log
worker-attributable, and give the root a lightweight self-sizing step so it
picks a strategy (and escalation depth) before spawning — without re-introducing
the regex-forced planner that D retired.

## Fixes

### P0 — Mechanical gate bug (on this PR; the core regression)

- **G1 — existence filter.** In `_run_mechanical_gate`, drop touched files that
  no longer exist before linting. A scratch file written-then-deleted (the
  generator) is excluded, so a deleted file never produces a false failure.
- **G2 — result semantics.** Replace `validator_exit` with
  `result ∈ {pass, fail, error, skipped}`:
  - `pass` — ruff clean.
  - `fail` — ruff found real lint errors (the only state that should make the
    root route a fix).
  - `error` — the validator could not run (missing/unparseable file) — **not** a
    failure.
  - `skipped` — nothing lintable survived.
  Update the C2 root prompt: only a real `fail` means "not acceptable, route a
  fix"; `error`/`skipped` carry no verdict.

### P1 — Observability (on this PR)

- **O1 — detailed mechanical line.** Emit `result=…`, the concrete reason for
  `error`/`skipped`, the first 1–2 real lint errors for `fail`, and separate
  `artifact=` (deliverable) from `files=` (touched). Folded into G2.
- **O2 — name the dropped write.** The max-tokens-truncation log names the tool
  and target it discarded (`dropped write_file(<path>)`).
- **O3 — worker-tagged cycle lines.** Prefix each cycle log with the worker
  identity (`[root]`, `[coder#<short-handle>]`) so multiple "cycle 0" lines are
  distinguishable. `_run_loop` already carries `role`; thread a short worker
  label.

### P2 — Root self-sizing + anti-repeat (on this PR)

- **R1 — root decision ladder.** Add to the root system prompt a short ladder
  the root runs before spawning, biased to the shallowest path:
  1. trivial / answerable now → answer directly;
  2. one concrete low-risk change/artifact → one coder;
  3. ambiguous / multi-step / real risk → planner first, pass summary to coder;
  4. planner output spans modules / real architecture → insert a designer.
  The `[agent-routing-scope]` hint is input, not an order (root judges — keeps D
  intact). For any coder, state the execution approach in the brief: large
  artifacts use chunked `append_file` or a compact generator (never one
  oversized `write_file`); flag UTF-8 for non-ASCII; don't scan the whole tree
  to create a new file.
- **R2 — anti-repeat on truncation.** When a write is dropped for max-tokens,
  feed the next attempt an explicit "write in ordered `append_file` chunks"
  directive so a fresh coder does not repeat the monolithic write.

### P3 — Systemic efficiency (follow-up PR, not blocking)

- **E1** — coder contract defaults to chunked/generator writes for large
  artifacts.
- **E2** — stop the root re-ingesting the same file twice per turn (the read
  result is compressed out, then re-read); strengthen C3 beyond advisory.
- **E3** — investigator debug guidance: verify by exit code, not by printing raw
  bytes to a console that crashes; fix+verify in one command. Fix the real
  encoding bug at the source (generator must emit UTF-8).

## Order

```
PR #135:      P0 (G1,G2) → P1 (O1,O2,O3) → P2 (R1,R2)
Follow-up PR: P3 (E1,E2,E3)
```

G1 alone removes the false signal that caused ~76k of the blowup, so P0 lands
first.

## 2026-07-12 Artifact-run safety follow-up

A later Carl Jung dashboard trace exposed a second failure chain: a coder
successfully wrote an artifact and then issued an empty `write_file`, the root
spawned sequential replacement coders despite `max_workers=1`, and a truncated
append call ended its worker instead of teaching that same worker to retry in
chunks. The run consumed 191k of its 200k token budget before halting.

The corrective contract is:

- `write_file(path, "")` may create an empty file, but fails closed when it
  would replace an existing non-empty file.
- Root `ScopeHint.max_workers` is a cumulative run ceiling. Route and role
  selection remain advisory; the ceiling does not force a planner or coder.
- A max-token response containing tool calls is never dispatched. When cycles
  remain, its structured blocked result is fed back to the same worker so it
  can switch to ordered `append_file` chunks.
- Worker guidance requires platform-native commands and bounded validation
  output; Windows workers must not use `wc`/`tail` or print the whole artifact.

Regression coverage lives in `test_fs_tools.py`, `test_parallel_dispatch.py`,
`test_agent_loop.py`, and `test_context.py`.
