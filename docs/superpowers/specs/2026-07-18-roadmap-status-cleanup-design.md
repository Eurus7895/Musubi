# Roadmap Status Cleanup Design

## Context

`docs/roadmap.md` currently leaves two bounded, merged tracks under `Active`
while the ongoing skill-catalog work remains in `Backlog`. This makes current
focus and completed work harder to distinguish.

## Decision

Classify roadmap entries by the scope of the track, not by whether any work has
landed inside it:

- Move **Bounded standalone pipeline runtime** and **Root goal-state controller
  and token economics** to `Completed Tracks`. Both have bounded goals, merged
  implementations, and focused regression coverage.
- Move **Skill catalog growth** to `Active`. It is a continuing investment area;
  the landed first batch and worker-reachability work are evidence of progress,
  not a reason to close the track.
- Leave unfinished, design-gated, postponed, and release-hardening work in its
  existing section.
- Preserve the runtime-limit ownership rule near `Current Work` because it is an
  architectural constraint, not a completion-status entry.

## Editing Rules

- Move existing prose instead of rewriting implementation claims.
- Do not duplicate a track across status sections.
- Keep historical detail concise and retain links to the governing plans and
  design documents.
- Do not change product code, tests, or runtime behavior.

## Verification

- Confirm `Active` contains the ongoing skill-catalog track and no completed
  bounded track.
- Confirm both completed bounded tracks appear exactly once under
  `Completed Tracks`.
- Confirm every other Backlog and Postponed entry is unchanged.
- Run `git diff --check` and inspect the roadmap diff for accidental content
  loss or duplication.
