# Roadmap - Musubi

> Current direction and live work only. Historical detail lives in git log,
> closed PRs, ADRs, implementation plans, artifacts, and the audit DB.
> Repo rules -> [`/CLAUDE.md`](../CLAUDE.md).

---

## Discipline

Every PR must move Musubi toward either:

- **Thicker substrate:** queryable audit, sharper invariants, deterministic
  validation, better skill metadata, and stronger boundaries.
- **Thinner ephemeral structure:** less pipeline scaffolding, fewer prompt
  preambles, and fewer model-limit compensations.

Substrate is anything that still helps when a stronger model lands. Ephemeral
structure exists only to compensate for current model limits and should be
deleted when its expiry condition is met.

## North Star

Musubi is a governed orchestration substrate: the model owns interpretation
and decisions; deterministic code validates contracts, authority, evidence,
budgets, and side effects at agent-agent and agent-tool boundaries.

The standalone `agent` host is the driver surface. The desktop Console observes
and operates the same substrate through `audit.db`: policy, audit, skill
catalog, compression, memory, execution contracts, and boundary controls. The
VS Code extension is gone: one model inject point (`LMRouter`) and one prompt
catalog remain.

## Current Work

### Active

1. **Validate Work Package control before changing the default.** Goal
   Contract and Work Package execution are implemented behind
   `root_control_mode=work_package`; `legacy` remains the default. Run both
   modes on representative tasks and compare completion quality, token/turn
   cost, retry behavior, false-completion rate, and rollback outcomes. Define
   explicit promotion or rejection criteria before changing the default.

   ADR:
   [`0001-root-work-package-control.md`](./adr/0001-root-work-package-control.md)

   Implementation plan:
   [`2026-09-03-goal-contract-work-packages.md`](./superpowers/plans/2026-09-03-goal-contract-work-packages.md)

1. **Plan continuity across conversation turns.** A new turn can persist a
   plan but still rediscover and re-read the same workspace and planning
   artifacts. Restore the active goal and its accepted plan deterministically,
   then re-measure root planning spend before changing worker budget splits.
   Work Package ledger replay is available, but it does not yet make ordinary
   legacy planning continuous across turns.

   Design note:
   [`2026-08-05-plan-continuity-design.md`](./superpowers/specs/2026-08-05-plan-continuity-design.md)

1. **Close audit-integrity gaps.** `scripts/audit_report.py` reconstructs one
   run and the Work Package ledger exposes Goal -> Package -> Attempt ->
   Evidence. The remaining regulated-audit gaps are human identity, an explicit
   approval event for destructive grants, durable plan/decision provenance in
   the record, and tamper evidence for append-only rows. Specify these before
   adding more report presentation.

### Backlog

- **Installer runtime reduction.** Prefer a bundled or locally repairable
  Python core payload so first run does not depend on global `pip install` or
  manual `PATH` edits. Keep network installation as a development fallback.
- **Signing and release hardening.** Sign the Windows installer and document
  the expected Defender / SmartScreen path for non-developer installs.
- **Stage extension by user grant.** Route per-stage turn/cycle exhaustion
  through the existing bounded, audited budget-grant pause instead of aborting
  immediately. The grant must never waive the wall-clock rule. Write a design
  before implementation.
- **Lines-of-substrate vs lines-of-skill ratio.** Track whether capability
  growth is moving into durable substrate and reusable skills rather than
  one-off prompt scaffolding.
- **Relocate substrate out of `.github/`.** Move skills, memory, agents, and
  pipeline definitions to a platform-neutral root before a large catalog
  expansion creates avoidable move churn.

## Dissolution Candidates

The `musubi-tier` and `expires-when` headers in source are authoritative. This
table lists only live ephemeral components; completed removals do not remain in
the roadmap.

| Component | Expiry condition | Removal value |
|---|---|---|
| `agent/subagent.py` | Models gain reliable native multi-agent tool use | Remove the standalone spawn/run/complete adapter |
| `session/sub_sessions.py` | Native multi-agent tool use owns worker lifecycle and evidence linkage | Remove duplicate lifecycle and cascade-abandon machinery |
| `agent/pipeline_runner.py` | Models can orchestrate governed multi-step pipelines natively | Remove the driver-side stage sequencer |
| `memory/session_distiller.py` | The four-stage pipeline is dissolved | Remove memory logic tied to planner/designer/coder/reviewer |
| Automatic stage retry | Latest 500 eligible attempts reach at least 95% first-pass success, Wilson 95% lower bound at least 93%, with no P0/P1 incident prevented only by retry | Remove repeat workers, retry preflights, and cross-attempt recovery branches |
| `agent/stage_preflight.py` | Worker runtime can require model skill selection before work tools without a separate model call or harness default | Remove one model call per stage attempt |
| `.github/agents/**` role variants | A role variant no longer adds a capability or boundary beyond the canonical agent | Remove redundant prompt scaffolding per role |

## Postponed

- **Dissolve the four-stage pipeline shape.** The deterministic pipeline and
  adaptive Root Work Package flow remain separate supported modes. If pipeline
  dissolution is revisited, first re-home its reusable contract, gate,
  checkpoint, and evidence primitives at agent and tool-call boundaries.

## Reference Index

- Goal Contract / Work Package control:
  [`ADR 0001`](./adr/0001-root-work-package-control.md)
- Root planning continuity:
  [`2026-08-05 design`](./superpowers/specs/2026-08-05-plan-continuity-design.md)
- Model-authored stage goals:
  [`design`](./superpowers/specs/2026-08-01-stage-goals-and-loop-design.md),
  [`implementation plan`](./superpowers/plans/2026-08-01-stage-goals-and-loop.md)
- Audit evidence integrity:
  [`design`](./superpowers/specs/2026-08-04-harness-evidence-integrity-design.md),
  [`implementation plan`](./superpowers/plans/2026-08-04-harness-evidence-integrity.md)
