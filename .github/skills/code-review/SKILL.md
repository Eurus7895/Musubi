---
id: code-review
name: Code Review
version: 1.0.0
description: Reviews code for correctness, security, type safety, and convention compliance
triggers: ["review", "check code", "audit", "code quality", "inspect"]
assets:
    - assets/review-script.py
references:
    - references/owasp-top10.md
    - references/common-patterns.md
---

## Purpose

Provide a structured, repeatable code review procedure that catches bugs, security
issues, and convention violations before code is merged or executed.

## Procedure

1. **Read the plan first.** Acceptance criteria are the ground truth. Each criterion
   must map to at least one line of code or test.

2. **Check correctness.**
   - Does the code implement every acceptance criterion?
   - Are edge cases handled: None inputs, empty collections, boundary values?
   - Are errors caught at the right level and propagated correctly?

3. **Check security.** (Load `owasp-top10.md` if any of these apply.)
   - External input handled? Validated before use?
   - Subprocess calls use `shell=False`?
   - SQL uses parameterized queries?
   - No hardcoded secrets?
   - File paths validated against allowed base?

4. **Check types.**
   - All public functions annotated?
   - No bare `Any` without explanation?
   - No `# type: ignore` without comment?

5. **Check tests.**
   - New logic has tests?
   - Happy path and at least one failure path covered?
   - Test names follow `test_{what}_{condition}_{expected}` pattern?

6. **Check code quality.** (Load `common-patterns.md` if anti-patterns suspected.)
   - Functions small and single-purpose?
   - No dead code or commented-out code?
   - Names clear and consistent?

7. **Classify each issue** with severity: `critical`, `high`, `medium`, `low`.

8. **Write fix instructions** — specific enough that a developer can act without
   asking questions. Include file name and approximate line number if possible.

## Assets

`review-script.py` — run via `harness_run_asset("code-review", "review-script.py", input)`
- Input: `{"files": ["path/to/file.py"]}` 
- Output: structured list of static analysis findings to supplement manual review
- Use when: reviewing a large file (>200 lines) or when you want a second pass

## When to Load References

- Load `owasp-top10.md` when: any code handles external input, subprocess, SQL,
  file I/O, or authentication
- Load `common-patterns.md` when: code uses inheritance, caching, retry logic,
  or you suspect a known anti-pattern
