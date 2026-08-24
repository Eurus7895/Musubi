---
applyTo: "**"
priority: P2
description: Organization-wide git standards — branch naming, commit message format, merge strategy, pull request requirements, and release tagging. Applies to all agents that produce or review commits.
---

# Git Conventions — Organization Standard (P2)

## Branch Naming

```
feat/short-description      ← new feature
fix/short-description       ← bug fix
refactor/short-description  ← code change with no behavior change
test/short-description      ← tests only
docs/short-description      ← documentation only
chore/short-description     ← build, deps, tooling
```

All lowercase. Hyphens only. No underscores. No spaces.

## Commit Messages

Format: `type(scope): imperative description`

```
feat(state): add session resume on crash
fix(verifier): reject outputs with embedded newlines in secret scan
refactor(executor): extract timeout into named constant
test(stage-loop): cover max-attempt exhaustion path
docs(agents): clarify coder output contract
```

Rules:
- Imperative mood: "add", not "added" or "adding"
- Under 72 characters for the subject line
- No period at the end of the subject line
- Blank line between subject and body if body is present
- Body explains WHY, not WHAT

## Merge Strategy

- Feature branches: rebase onto `dev` before merging. No merge commits.
- `dev` to `main`: squash merge with a summary commit message.
- Never force-push to `main` or `dev`.

## Pull Requests

- Title: same format as commit message
- Description: what changed, why, how to test
- Must pass CI (lint + typecheck + tests) before merge
- At least one review approval required

## Tags

- Releases: `v{major}.{minor}.{patch}` (semantic versioning)
- No lightweight tags for releases — use annotated tags.
