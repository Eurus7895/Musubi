# Harness Evidence Integrity Repair Implementation Plan

> **Status:** Implemented on 2026-08-04.

**Goal:** Close the latest branch review findings while preserving model-owned
decisions and substrate-owned evidence.

### Task 1: Reject stale prompt tool references

- Extend the tool-name cross-reference test to agent Markdown.
- Replace the deleted Direct-mode instructions with the current root planning
  sequence.

### Task 2: Persist and project policy identity

- Add nullable request and parent-session columns with additive migration.
- Pass launch identity into every policy write that has it.
- Load both fields in the Console data layer with legacy-schema fallbacks.
- Project policy evidence only through explicit identity or worker ownership.

### Task 3: Fail closed on missing stage attempts

- Add regressions for a missing database path and missing attempt row.
- Replace silent checkpoint returns with a required-row guard.

### Task 4: Verify and publish

- Run focused Python, JavaScript, Rust, build, and diff checks.
- Update the roadmap, commit with repository identity, rebase on `origin/dev`,
  and push `fix/console-run-evidence-scope`.
