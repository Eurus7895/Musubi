---
name: refactoring
description: Behaviour-preserving code restructuring — rename, extract, inline, dead-code removal — in small verifiable steps. Use when the user asks to refactor, clean up, simplify, deduplicate, restructure, or remove dead code without changing behaviour.
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
triggers:
  - refactor
  - rename
  - extract
  - dead code
  - clean up
  - simplify
  - deduplicate
  - restructure
tools:
  - musubi_grep
  - musubi_glob
---

## Purpose

Change the shape of the code without changing what it does, in steps
small enough that each one is provably behaviour-preserving. A refactor
that also "fixes a small thing on the way" is two changes wearing one
diff — reviewers can verify neither.

## Procedure

### 1. Pin behaviour before moving anything

- Identify the tests that cover the code you are about to move. Run
  them; record the passing baseline.
- No coverage? Write a characterization test first: call the current
  code, assert on its *actual* current output (even if odd). That test
  is the safety net, not a spec.

### 2. Map every usage

- `musubi_grep` for every reference to the symbol being changed —
  callers, string-based lookups, config keys, docs, tests. A rename
  that misses a dynamic reference is a runtime bug the type-checker
  will not catch.
- Note the public-vs-private line: an exported name needs a
  deprecation path or a coordinated update of all callers; a `_private`
  helper can just change.

### 3. One transformation per step

Apply exactly one named transformation, then re-run the pinned tests:

- **Rename** — new name states what the old comment used to explain.
- **Extract** — a function does two things → split at the seam;
  each piece gets one reason to change.
- **Inline** — an indirection with a single caller and no independent
  meaning goes away.
- **Deduplicate** — only after the third occurrence, and only when the
  copies change for the same reason. Two similar-looking blocks that
  evolve independently are not duplicates.
- **Delete dead code** — unreferenced per step 2's map. Delete it
  fully; do not comment it out (git history is the archive).

### 4. Keep the diff honest

- Behaviour change discovered mid-refactor (a real bug, a missing
  case)? Stop. Finish or revert the refactor, land the fix as its own
  change with its own test.
- Do not reformat untouched code in the same diff — it buries the real
  transformation.
- Preserve the module's public surface unless the task explicitly says
  otherwise; check `__init__` exports and re-export sites.

### 5. Verify

- Full pinned-test run + lint/typecheck at the end, not only per step.
- Re-grep the *old* names: zero hits outside the changelog/history is
  the done condition for a rename.

## Anti-patterns

- "While I'm here" edits — scope creep is the main way refactors break
  things.
- Extracting an abstraction for two call sites "for the future" — wait
  for the third; speculative generality is itself refactoring debt.
- Renaming across a boundary you don't own (DB columns, API fields,
  on-disk formats) as if it were a code-only change — those need a
  migration, not a refactor.
