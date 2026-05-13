# /code-review pipeline

Review a branch or pull request and produce a ranked findings report.

## Stages

| Stage       | Agent        | Output                                        |
|-------------|--------------|-----------------------------------------------|
| scope       | scoper       | `{files: [{path, size, kind, priority}], …}`  |
| findings    | finder       | `{raw_findings: [{file, line, …}], …}`        |
| synthesis   | synthesizer  | `{report: { issues: […], summary: "…" }, …}`  |

The runner fans out one `reviewer-aux` sub-agent per high/medium-priority
file from `scope` before the synthesizer runs, so per-file checklists
are gathered in parallel. The synthesizer then aggregates the per-file
outputs into a single ranked report.

## Usage

```
/code-review feat/some-branch      # diff feat/some-branch..origin/dev locally
/code-review #42                   # resolve PR 42 via GitHub MCP, then run on the diff
```

The branch-comparison form is the default and works on any git repo.
The PR form requires the GitHub MCP server; on failure it surfaces a
clear "use branch syntax" message rather than silently falling back.

## Why these stages

- **scope** picks files that matter. A 50-file PR with 49 lockfile updates
  doesn't need 49 reviews.
- **findings** does the cross-cutting pass (architecture, intent, contracts).
- **synthesis** turns per-file output into a single ranked report ordered
  by severity, deduplicated across reviewer-aux outputs.

## Why no `design` stage

There is no design to do — the artifact under review already exists.
Trying to map code-review onto feature-dev's plan → design → code → review
chain would force `design` to be either empty or a redundant restatement of
the diff. PR 2a's pipeline-aware composer skipped the artificial mapping.

## Acceptance criteria (H.1)

- [ ] `/code-review feat/<branch>` runs end-to-end with audit trail captured.
- [ ] Output schema documented (issues with severity, file/line citations,
  fix suggestions).
- [ ] Level 2 promotion checklist completed.
