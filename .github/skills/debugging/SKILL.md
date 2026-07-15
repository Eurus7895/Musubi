---
name: debugging
description: Systematic root-cause diagnosis for failing code — reproduce, isolate, instrument, then fix once with evidence. Use when the user reports a bug, crash, traceback, regression, or flaky test, or asks why something fails.
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
triggers:
  - bug
  - traceback
  - stack trace
  - root cause
  - regression
  - crash
  - flaky
  - reproduce
  - why does it fail
tools:
  - musubi_grep
  - musubi_glob
  - musubi_spawn_subagent
---

## Purpose

Find the *cause* of a failure before touching the fix. A patch written
against a symptom re-breaks; a patch written against a reproduced,
evidenced cause stays fixed. The deliverable of the diagnosis phase is
one sentence: "X fails because Y, proven by Z."

## Procedure

### 1. Reproduce first

- Get the exact failing command and its full output (exit code,
  traceback, assertion text). "It doesn't work" is not a reproduction.
- Shrink to the narrowest reproduction: one test id over a suite, one
  input over a batch. `pytest path/to/test.py::test_name -x` beats
  `pytest tests/`.
- If you cannot reproduce, stop and say so — do not fix blind. Ask for
  the environment delta (version, OS, config) instead.

### 2. Read the error before reading the code

- The **deepest frame in your own code** is the starting point, not the
  top frame (usually library plumbing).
- Exception *type* narrows the cause class: `KeyError`/`AttributeError`
  → shape mismatch; `TypeError` → contract drift at a call site;
  `AssertionError` in a test → expected-vs-actual, read both values.

### 3. Isolate by halving

- Bisect the *input*: does half the payload still fail?
- Bisect the *history*: `git bisect` (or diff against the last known-good
  ref) when the failure is a regression — the introducing commit usually
  names the cause for you.
- Bisect the *path*: comment out or stub half the pipeline; the failure
  follows the guilty half.

### 4. Instrument, don't stare

- Add one temporary print/log at the boundary where your belief about
  the state and the actual state might diverge. Verify or kill the
  hypothesis, then move the probe. Remove all probes before committing.
- In this harness, delegate command runs to an **investigator**
  sub-agent (exit codes, first failure, smallest signal) and codebase
  lookups to an **explorer** — keep the raw noise out of your context.
- Use `musubi_grep` / `musubi_glob` to find every caller of the failing
  symbol before concluding the failure is local.

### 5. State the cause, then fix once

- Write the one-sentence cause with its evidence (file:line, the
  observed value, the introducing commit). If you can't write it, you
  aren't done diagnosing.
- The fix addresses the cause, not the symptom: prefer fixing the
  producer of a bad value over hardening every consumer.
- Add the regression test *first*, watch it fail for the diagnosed
  reason, then apply the fix and watch it pass.

### 6. Verify the blast radius

- Re-run the narrowest reproduction, then the surrounding suite.
- Grep for siblings of the bug (same copied pattern elsewhere) — a
  root cause usually has more than one instance.

## Anti-patterns

- Fixing where the exception *surfaced* instead of where the bad state
  was *created*.
- Retrying a flaky test until green — flakiness is a bug with a
  timing/ordering cause; diagnose it the same way.
- Stacking multiple speculative fixes in one change; you learn nothing
  from whichever one "worked".
