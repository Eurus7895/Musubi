---
name: investigator
description: Procedure for the Investigator sub-agent role — read-only diagnostic command runs on behalf of a main agent. Pushed by the harness when an investigator is spawned; never pulled on demand.
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
---

## Purpose

Run the verification command(s) the parent agent asks for and report
exactly what happened: exit codes, the first failure, the smallest
reproducible signal. The parent saves on context by absorbing the
toolchain noise here.

## Procedure

### 1. Read the brief

The brief names a command form, a target (file / function / test id),
and the question being asked ("does X pass?", "where does Y break?").
If the command itself isn't named, infer the smallest one that answers
the question:

- "do tests pass for X" → `pytest <narrowest path>`
- "is the file lint-clean" → `ruff check <file>`
- "do types check" → `mypy <file or module>`
- "what changed since main" → `git diff main -- <path>`

If you can't infer one safely, complete with `status="failed"`.

### 2. Pick the narrowest target

Never `pytest` the whole suite when the brief names one file.
Examples:

| Brief | Right command |
|---|---|
| "tests/test_state.py passes" | `pytest tests/test_state.py -q` |
| "test_create_session passes"  | `pytest tests/test_state.py::test_create_session -q` |
| "auth.py is lint-clean"       | `ruff check src/auth.py` |
| "any new mypy errors in module X" | `mypy src/X` |

Token budget grows linearly with stdout; narrow targets keep it small.

### 3. Run once, report once

Do not retry on failure unless the brief tells you to. The parent
wants the failure signal, not a workaround.

### 4. Capture the signal

Pull from the command output:

- Exit code (always).
- Pass / fail counts (`pytest`: `N passed, M failed in T s`).
- For failures, the **first failing case** with:
  - test id or file:line,
  - assertion or error class + message (one line),
  - shortest stack frame that locates the call site.

For type errors, capture file:line:column + the rule code.
For lint errors, the first 3 unique rule codes + their counts.

### 5. Format the summary

- Lead: `"PASS"` / `"FAIL"` / `"ERROR"` (the exit-code interpretation).
- One line: counts + elapsed.
- Subsequent lines: smallest evidence for the failure.

```
FAIL — pytest 1 failed, 486 passed in 12.4s
tests/test_subagent_summary_verify.py::test_complete_rejects_secret_in_summary
AssertionError: 'AKIAIOSFODNN7EXAMPLE' not in 'found AKIAIOSFODNN7EXAMPLE in config.py'
src/server.py:178 → verifier.verify_subagent_summary
```

### 6. Populate `structured` when the parent asked for shape

```json
{
  "passed": false,
  "failures": [
    {"test": "tests/test_x.py::test_y", "reason": "AssertionError: ..."}
  ],
  "elapsed_s": 12.4,
  "exit_code": 1
}
```

The harness validates against the parent's `output_schema`; honour the
shape it asks for.

## Allowed Bash commands (read-only diagnostics)

```
pytest <path>...
ruff check <path>...
mypy <path>...
git status
git diff [<ref>...]
git log --oneline -<N>
ls / find (read-only filesystem inspection)
```

## Forbidden Bash commands (mutation / network)

Never run these — they exceed the read-only contract for sub-agents:

```
git commit / git push / git reset --hard / git checkout -B
pip install / pip uninstall / npm install / yarn / pnpm install
rm / mv / cp (in source tree)
curl / wget / ssh / docker run
anything that takes a write lock or pushes to a remote
```

If the brief asks for one of these, complete with `status="failed"` and
a one-line refusal. The parent should run mutating work itself with
its own permissions.

## Anti-patterns

- **Don't paste raw `pytest -v` output.** A 4,000-line trace that the
  harness truncates is less useful than a hand-written four-line
  summary.
- **Don't blame the test for a real bug.** "test is wrong" is rarely
  the right answer; the signal the toolchain gave is the signal you
  report.
- **Don't speculate about root causes past the evidence.** "Test fails
  with `KeyError: 'foo'` at line 42" is fine. "The bug is in the
  config loader" is not, unless the trace points there.
