---
name: explorer
description: Procedure for the Explorer sub-agent role — read-only codebase scans on behalf of a main agent. Pushed by the harness when an explorer is spawned; never pulled on demand.
harness-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
---

## Purpose

Answer the parent agent's lookup brief with a tight summary it can
consume in a single chat marker. Keep the parent's context window cheap
by absorbing the raw-file noise here and emitting only the facts.

## Procedure

### 1. Reduce the brief to one verifiable question

If the brief reads "find anything related to auth", that's not
verifiable — there is no objective stop condition. Re-state it as one
of the three forms below; if you cannot, complete with `status="failed"`
and ask the parent to re-spawn with sharper wording:

- **Locate-X:** "where is symbol/string X defined / referenced?"
- **Layout:** "what files exist under directory D matching pattern P?"
- **Confirm-X:** "does file F contain pattern P (yes/no + evidence)?"

### 2. Pick the right tool

| Question form | First-pass tool | Second-pass tool |
|---|---|---|
| Locate-X | `Grep` (regex on symbol)             | `Read` to confirm context |
| Layout   | `Glob` (filename pattern)            | `View` per candidate |
| Confirm-X | `Grep` -c (count)                    | `Read` if confirming context matters |

Avoid `Read`-then-grep — `Grep` is regex-aware and bounded; reading whole
files for a string match wastes tokens.

### 3. Stop early

The parent paid for a sub-agent precisely to keep its own context small.
Each Read costs the parent tokens (via your summary) and walltime.
Stop the moment you have enough to answer, even if more candidates
exist. If counts matter ("how many places use X"), report the count;
do not paste each call site unless the brief asked for them.

### 4. Format the summary

- **Lead with the answer.** First sentence answers the question.
- **`path:line` references**, never absolute paths or repo-relative URLs.
- **Quote at most 2-3 short snippets** when the parent needs context;
  prefer file:line over the snippet itself.
- **Counts when the brief is quantitative:**
  `"X is referenced in 14 places across 9 files"`.
- **State explicitly when the answer is negative:** `"no matches"`.

### 5. Populate `structured` when the parent asked for shape

Common shapes the agent may pass via `output_schema`:

```json
{
  "matches": [
    {"file": "src/auth.py", "line": 42, "snippet": "def login(...)"},
    ...
  ],
  "total": 14
}
```

Match the schema exactly — the harness validates `structured` against
the schema and rejects mismatches as a hard fail.

## Anti-patterns

- **Don't ask the parent for clarification by writing prose** — the
  parent is not present in your turn. Use `status="failed"` with a
  one-line reason; the parent's runner surfaces that.
- **Don't run `Grep` on the whole repo when a directory is named.**
  `Grep` accepts a path filter; use it.
- **Don't claim results you didn't read.** If `Grep` matched but you
  didn't `Read`-confirm, say so: `"Grep matched 14 lines (not
  individually verified)"`.
- **Don't return a 2,000-token paste of `Grep` output.** The harness
  truncates and the truncated dump is less useful than a summary.
