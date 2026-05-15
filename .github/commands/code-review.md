---
name: code-review
description: Review uncommitted changes or a branch — produces a ranked findings report
action: pipeline
pipeline: code-review
---

# /code-review

Runs the `code-review` pipeline: scope → findings → reviewer-aux fan-out
per file → synthesis. Produces a ranked report of issues with severity,
file/line citations, and fix suggestions.

## Usage

```
/code-review                    # review uncommitted + staged changes vs HEAD
/code-review <branch-name>      # diff <branch>..origin/dev locally
/code-review #<PR-number>       # resolve via GitHub MCP, then run on the diff
```

Examples:

```
/code-review                       # "what am I about to commit?"
/code-review feat/login-revamp     # "review this whole feature branch"
/code-review #42                   # "review PR #42" (when GitHub MCP wired)
```

The no-args form is the most useful default: it runs `git diff HEAD`,
which captures both staged and unstaged changes against the last commit.
Best for interactive review of work in progress.

If the working tree is clean (no uncommitted changes), the no-args form
falls through to **tree mode**: it synthesises a diff that treats every
tracked file as new content and runs the same pipeline. Capped at 200
files / 10,000 total lines / 500 lines per file so a large repo doesn't
blow the LM context. Files larger than the per-file cap appear as
header-only stubs; reviewer-aux can read them directly via its `view`
tool.

The branch form diffs the named branch against `origin/dev` (with
`origin/main` and `HEAD~1` as fallbacks). Best for reviewing a finished
feature branch before merging.

The PR form requires the GitHub MCP server to resolve the PR's diff;
if unavailable, the runner asks you to use the branch form instead.

Natural-language input like `/code-review review this codebase` is
treated as a request for a codebase scan (equivalent to no-args). The
runner surfaces a note in the chat about how the input was interpreted
so you can correct if you meant a typo'd branch name.

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
