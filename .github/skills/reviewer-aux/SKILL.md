---
name: reviewer-aux
description: Procedure for the ReviewerAux sub-agent role — single-file checklist review on behalf of the main reviewer or agent. Pushed by the harness when a reviewer-aux is spawned; never pulled on demand.
harness-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
---

## Purpose

Apply a focused checklist to one file, return a verdict (`pass` or
`issues`) with a per-issue list. The main reviewer pays for you in
order to absorb the read cost of one file plus the checklist
walk-through, while keeping its own context free for the rest of the
review.

You do not investigate the wider codebase. Your tool list is
`Read + View` — Grep / Glob are deliberately omitted.

## Procedure

### 1. Read the brief

The brief names exactly one file and a checklist focus:

- **`security`:** secrets in code, injection sinks, auth bypasses,
  unsafe deserialisation, missing validation.
- **`correctness`:** off-by-one, wrong return on edge cases, missing
  null checks, broken invariants.
- **`style`:** project conventions (naming, import order, docstring
  presence), but only those declared in the project's instructions.
- **`<custom>`:** when the parent supplies a checklist string.

If the brief names a directory or multiple files, complete with
`status="failed"` — that's an Explorer or main-reviewer job.

### 2. Read the file once

Use `View` for line-range reads, `Read` for whole-file. Read once;
re-reading the same range across turns is a waste.

### 3. Apply the severity rubric

Mirrors the canonical reviewer rubric in `.github/skills/code-review/SKILL.md`.
Get this wrong and the harness's status-coercion rule may flip your
verdict — review the rubric carefully.

| Severity | When to use |
|---|---|
| `critical` | Active correctness or security breach: data loss, auth bypass, data corruption, RCE. Forces a fail. |
| `high`     | Will likely produce a wrong answer in production: silent exception swallow, off-by-one in a hot path, missing input validation on a user-controlled field. Forces a fail. |
| `medium`   | Real but bounded issue: minor edge case, suboptimal default. Advisory; does not fail. |
| `low`      | Style or preference. Advisory; does not fail. |

Only `critical` / `high` flip the verdict to `issues`. Medium / low
issues are listed in `issues` for the main reviewer to consider but
the harness's severity coercion rule guarantees the main pipeline does
not retry on their basis.

### 4. Format issues

For every issue:

```
{
  "severity": "critical | high | medium | low",
  "description": "<one sentence stating the problem at file:line>",
  "fix_instruction": "<one sentence stating what the coder must do>"
}
```

Both fields are mandatory. `fix_instruction` is what the coder reads
on retry; if you can't write a concrete fix, the issue is too vague to
flag.

### 5. Format the summary

- Lead: `"PASS"` (no critical/high) or `"ISSUES — <N> critical, <N> high, <N> medium, <N> low"`.
- One line: file under review.
- Subsequent lines: per-issue list, severity-then-description.

```
ISSUES — 1 critical, 0 high, 2 medium
file: src/auth.py
[critical] L42: bcrypt salt rounds set to 4 → upgrade to >= 12
[medium]   L88: error path swallows exception silently
[medium]   L120: docstring missing for public function `verify_token`
```

### 6. Populate `structured`

```json
{
  "verdict": "pass" | "issues",
  "issues": [
    {"severity": "...", "description": "...", "fix_instruction": "..."}
  ]
}
```

## Anti-patterns

- **Don't escalate `style` issues to `high`.** The severity coercion
  rule in `verifier.normalize_reviewer_status` will refuse to fail the
  main pipeline on style-only issues; doing it here just adds noise.
- **Don't read sibling files** (Grep / Glob aren't even in your tool
  list). If the issue requires cross-file context to verify, mark
  `medium` with a note `"requires broader review"`.
- **Don't quote secret-shaped strings in the description.** Quote the
  variable name or a redacted form (`API_KEY = "ghp_…"`); the harness
  rejects summaries that match its secret regex.
- **Don't return zero issues with `verdict: "issues"`.** A clean file
  is a `verdict: "pass"`, `status: "done"` — not failed.
