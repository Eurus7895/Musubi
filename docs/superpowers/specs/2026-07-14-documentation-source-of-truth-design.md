# Documentation Source-of-Truth Cleanup Design

> Superseded for Console workspace ownership by
> [`2026-07-14-console-workspace-separation-design.md`](./2026-07-14-console-workspace-separation-design.md).
> This document remains as the rationale for the earlier documentation cleanup.

## Context

Musubi currently has 38 Markdown files. The 28 files under
`docs/superpowers/` alone contain 8,371 lines, including several completed
implementation plans that describe behavior later replaced by newer work.
The duplication has produced contradictions: current Console code can launch
the standalone driver, browse a historical Orchestrator session while another
run owns the process, and resume that viewed session atomically when idle,
while older plans and canonical user-facing documents still describe the
previous behavior.

Repository policy already states that the roadmap contains current direction
and that historical detail belongs in Git history, closed pull requests,
artifacts, and the audit database. Documentation should follow the same
current-versus-historical boundary.

## Decision

Use an aggressive current-source cleanup rather than retaining superseded
files in place or moving them into an archive directory.

- Git commits and closed pull requests remain the historical record.
- Canonical documents describe only current behavior.
- Completed implementation plans remain only when the roadmap still depends
  on their detailed constraints or acceptance criteria.
- A later design or plan replaces an earlier document when it owns the same
  behavior and includes every still-valid invariant.
- Do not add an archive tree; that would preserve the same search ambiguity
  under a different path.

## Alternatives Rejected

### Mark every old file as superseded

This retains useful chronology but leaves stale code snippets and obsolete
instructions in repository search results. Readers still need to determine
which file wins, and maintenance cost continues to grow.

### Move old files under `docs/archive/`

An archive makes age visible but duplicates Git's existing historical role.
It also creates another documentation surface with its own link and retention
rules.

## Files Removed

Delete these superseded documents after their still-valid invariants have
been represented in canonical documentation:

- `docs/superpowers/plans/2026-07-01-gui-on-demand-task-launcher.md`
- `docs/superpowers/plans/2026-07-05-gui-pipeline-separate-session.md`
- `docs/superpowers/plans/2026-07-09-gui-cli-orchestrator-tokens.md`
- `docs/superpowers/plans/2026-07-13-read-only-session-browsing.md`
- `docs/superpowers/specs/2026-07-13-read-only-session-browsing-design.md`

The first three are replaced by the current project-scoped Console runtime,
Pipeline Studio, and orchestrator token-economics documentation. The last two
are replaced by the resumable historical-session design and implementation
plan dated 2026-07-14.

## Canonical Document Responsibilities

### `AGENTS.md`

Remain a short session-start map. State that the Console is a native operator
surface which launches the standalone `agent` process only after an explicit
user submission. The GUI shell and substrate make no model calls; the launched
driver reaches the model through `LMRouter`.

### `README.md`

Give only the product overview and link to the complete guide. Describe the
Console as an operator surface capable of starting and observing governed
runs, without duplicating the detailed session state machine.

### `docs/guide.md`

Own user workflows: starting a Console chat, browsing another session during
an active run, resuming the viewed session once idle, cancellation, and direct
Pipeline Studio execution.

### `gui/README.md`

Own contributor architecture: Tauri launches the standalone driver process,
active and viewed Orchestrator session identities remain distinct, and the
frontend sends the viewed ID to the Rust boundary for atomic idle promotion.

### `gui/src-tauri/SCHEMA.md`

Own the durable and serialized contract. Document `orchestratorChatId`,
`viewedOrchestratorChatId`, exact runtime ownership, busy read-only browsing,
idle send-time promotion, GUI-side writes, and real action handlers.

### `docs/roadmap.md`

Keep summary-only current direction. Remove links to deleted historical plans,
retain only current canonical plans, and do not describe implementation steps.

### Session design and plan dated 2026-07-14

The design owns current session decisions and invariants. The implementation
plan retains checked acceptance evidence for the active GUI branch. The old
read-only browsing pair contributes no separate source of truth after its
valid active-versus-viewed invariants are included here and in the canonical
Console contract.

## Behavioral Contract to Preserve

- Opening the Console does not start a process.
- Explicit chat or Pipeline Studio submission may launch one governed
  standalone driver process.
- The GUI shell and substrate do not import an LM SDK or call a model.
- While a process owns the project writer lease, another historical
  Orchestrator session is viewable but read-only.
- When the driver is idle, sending from the viewed historical session
  atomically validates and promotes that exact chat ID before persistence and
  launch.
- Unknown, cross-project, cross-surface, or busy-race promotion remains
  fail-closed.
- Pipeline Studio invokes the selected registered deterministic recipe rather
  than relying on root to hallucinate or summon a pipeline from chat.

## Validation

- Search all Markdown files for deleted filenames and fail if live references
  remain.
- Search canonical docs for obsolete claims such as action handlers being
  `todo`, Console work being CLI-only, or idle history requiring a second
  selection.
- Validate all relative Markdown links outside fenced example blocks.
- Run `git diff --check`.
- Run the existing frontend session tests, Rust Console tests, and GUI build
  because documentation must describe the verified implementation.

## Non-goals

- Rewriting historical `CHANGELOG.md` entries.
- Deleting every completed implementation plan in one change.
- Changing Console behavior, database schema, or process ownership.
- Creating a permanent documentation archive.
