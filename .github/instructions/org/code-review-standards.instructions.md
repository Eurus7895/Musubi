---
applyTo: "**"
priority: P2
description: Organization-wide code review standards — review checklist (correctness, security, types, tests, quality, architecture), severity classification, reviewer output schema, and what reviewers must not do.
---

# Code Review Standards — Organization Standard (P2)

## What Every Review Must Check

### Correctness
- [ ] Does the code implement all acceptance criteria from the plan?
- [ ] Are edge cases handled (empty inputs, None values, boundary conditions)?
- [ ] Are errors caught at the right level and surfaced correctly?

### Security
- [ ] No hardcoded secrets (apply P1 security rules)
- [ ] All external input validated before use
- [ ] No shell injection, SQL injection, or path traversal vectors
- [ ] Subprocess calls use `shell=False`

### Type Safety
- [ ] All public functions have type annotations
- [ ] No bare `Any` types without justification
- [ ] No `# type: ignore` without explanation

### Tests
- [ ] New logic has corresponding tests
- [ ] Tests cover the happy path and at least one failure path
- [ ] Test names clearly describe the scenario

### Code Quality
- [ ] Functions are small and do one thing
- [ ] No dead code or commented-out code
- [ ] Names are descriptive and consistent with the codebase

### Architecture
- [ ] Code belongs in the file/module where it is placed
- [ ] No coupling introduced across layers that shouldn't be coupled
- [ ] No inline prompt construction (must go through `context_builder.py`)

## Severity Levels

| Severity | Meaning | Action |
|----------|---------|--------|
| `critical` | Security vulnerability, data loss risk | Block merge, fix immediately |
| `high` | Correctness bug, broken acceptance criteria | Block merge |
| `medium` | Code quality, missing tests, unclear error handling | Fix before merge |
| `low` | Style, naming, minor improvements | Fix if easy, else track as follow-up |

## Review Output Schema

Reviewer agent must produce JSON matching this schema:

```json
{
    "status": "pass | fail | escalate",
    "attempt": 1,
    "issues": [
        {
            "severity": "critical | high | medium | low",
            "description": "string",
            "fix_instruction": "string",
            "checklist_item": "string"
        }
    ],
    "escalate_reason": null
}
```

- `status: pass` — no critical or high issues remain
- `status: fail` — one or more critical/high issues present; Coder must retry
- `status: escalate` — attempt limit reached or issue is unresolvable by Coder alone

## What Reviewers Must Not Do

- Never rewrite code in the review output — provide `fix_instruction` only
- Never approve code with critical or high issues
- Never add new requirements not in the original plan
