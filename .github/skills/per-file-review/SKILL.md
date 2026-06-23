---
name: per-file-review
description: Per-file review checklist for the /code-review pipeline. Use this when reviewing a single file's diff (reviewer-aux fan-out) or doing the cross-cutting pass at the finder stage.
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
---

## Purpose

The reviewer-aux fan-out applies this checklist to each high/medium
priority file independently. The finder uses the same checklist for
its cross-cutting pass over the whole diff. Single source of truth so
neither pass invents its own criteria.

This is a sibling of the `code-review` skill (used by feature-dev's
reviewer). That one is about reviewing code the harness just wrote;
this one is about reviewing arbitrary code from a PR/branch where the
intent is documented in the PR body or commit messages, not in a plan.

## Procedure

For each file, walk these checks in order. Stop after producing the
finding if any check fires — multiple findings per check are fine.

### 1. Correctness

- Does the change do what the diff context or commit message claims?
- Are edge cases handled: None/null/empty inputs, boundary values,
  off-by-one in loops?
- Are errors caught at the right level and propagated correctly?
- Are async/await pairs balanced; no fire-and-forget on results that
  should be awaited?

### 2. Security

- External input handled? Validated before use?
- Subprocess calls use `shell=False` (or equivalent)?
- SQL uses parameterised queries?
- No hardcoded secrets (real ones — test fixtures with `"test-key"`
  are fine, real-looking AWS access keys / API tokens are not)?
- File paths validated against allowed bases?
- New dependencies pulled from trusted registries?

### 3. Contracts

- Public API changes (function signatures, exported types, route
  shapes, schema columns) — are all callers in the diff updated?
- Backwards-compatibility considerations stated?
- Deprecation paths use a grace period rather than hard removal?

### 4. Tests

- New behaviour has tests?
- Happy path AND at least one failure path?
- Test names follow the project's convention (check existing tests
  in the file's neighbourhood)?
- No tests deleted without an explanation in the commit message?

### 5. Code quality

- Functions small and single-purpose?
- No dead code or commented-out code (a removed block is fine; a
  block commented `// TODO: re-enable` is rot)?
- Type annotations present where the project uses them?
- Magic numbers / strings extracted to constants when used > 1×?
- Imports organised per project convention?

### 6. Project-specific patterns

When reviewing a Musubi file specifically:
- `encoding="utf-8"` on every `open()` / `Path.read_text()` / etc.
- No new LLM-SDK imports inside the harness layer.
- New MCP tools have docstrings ending with the soft-fail posture
  ("returns defaults when …").
- Skill files have YAML frontmatter with `name` and `description`.

## Output rows

For each finding, emit:

```json
{
  "severity": "critical | high | medium | low",
  "category": "security | data-loss | performance | style | correctness | breaking-change | other",
  "line": 42,
  "description": "string — what is wrong",
  "fix_suggestion": "string — what to change",
  "checklist_section": "Correctness | Security | Contracts | Tests | Code quality | Project-specific"
}
```

Severity rubric (same as `code-review`):

| Severity   | Meaning                                                    |
|------------|------------------------------------------------------------|
| `critical` | Security defect, data loss, or guaranteed crash in prod    |
| `high`     | Correctness bug or contract violation — feature is wrong   |
| `medium`   | Standards/quality violation — works but isn't to spec      |
| `low`      | Preference or nit — "would be nicer if…"                   |

Only `critical` or `high` should drive the synthesizer's `status: fail`.

## Negative space

- Don't propose architectural rewrites in per-file review. If a file
  needs to be restructured, that's a finder cross-cutting concern.
- Don't flag style preferences as `medium` or above. Preferences are
  always `low`.
- An empty finding list is the right output for a clean file. Don't
  invent issues to look thorough.
