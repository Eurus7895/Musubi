---
name: code-review
description: Review a branch or pull request — produces a ranked findings report
action: pipeline
pipeline: code-review
---

# /code-review

Runs the `code-review` pipeline: scope → findings → reviewer-aux fan-out
per file → synthesis. Produces a ranked report of issues with severity,
file/line citations, and fix suggestions.

## Usage

```
/code-review <branch-name>      # diff <branch>..origin/dev locally
/code-review #<PR-number>       # resolve via GitHub MCP, then run on the diff
```

Examples:

```
/code-review feat/login-revamp
/code-review #42
```

The branch form is the default and works on any local git repo.
The PR form requires the GitHub MCP server to resolve the PR's diff;
if unavailable, the runner asks you to use the branch form instead.

## Output

A markdown report ranked by severity. Each issue carries:

- severity (critical / high / medium / low)
- category (security, correctness, contract, …)
- file + line citation
- a concrete fix suggestion

Status:

- **pass** — no critical/high findings. Report still useful but advisory.
- **fail** — critical/high findings present. Surfaced inline in chat.
- **escalate** — synthesis couldn't reconcile sub-agent outputs. Rare.

## See also

- `.github/pipelines/code-review/pipeline.yaml` — pipeline definition
- `.github/pipelines/code-review/README.md` — stage + correction overview
- `.github/skills/pr-scope-detection/SKILL.md` — scoper's procedure
- `.github/skills/per-file-review/SKILL.md` — finder + reviewer-aux checklist
