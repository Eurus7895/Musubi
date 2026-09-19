# ADR 0001: Separate Root Work Packages from Pipeline Stages

- Status: accepted
- Date: 2026-09-03
- Baseline: `dev@4d04637`

## Context

Root previously controlled open-ended work through a mutable prose plan, a
fixed role chain, and compact worker summaries. Those summaries were useful
handoff data but could not serve as the source of truth for completion. The
Pipeline runner already has a different job: execute a known recipe in a fixed
order with deterministic stage gates and an evaluator firewall.

## Decision

Musubi keeps two independent controllers:

- `Root` adaptive execution uses immutable, versioned Goal Contracts and Work
  Packages. Criterion events are append-only; a Gap Report is folded from
  those events. Root selects the next package from that report.
- `PipelineRunner` continues to execute deterministic Stage recipes. It does
  not become a child of Root and Root cannot summon it from its agent surface.

Both modes share canonical JSON/SHA-256 primitives, deterministic acceptance
checks, append-only evidence, token enforcement, and audit storage. The shared
substrate makes zero model calls. Artifact reviewers remain firewalled from
the request, plan, and memory.

Direct Root execution has one control path: once Root declares Planning, it
must freeze a Goal Contract and execute through Work Packages. Conversational
or read-only answers may finish before Planning is declared. The deterministic
PipelineRunner remains a separate user-invoked controller.

## Consequences

- A worker outcome is evidence, never a completion verdict.
- Required criteria cannot pass without evidence and Root cannot report a
  complete work-package goal while a required criterion or regression is open.
- Root cannot escape an opened Planning state by returning implementation as
  chat text; the driver rejects that terminal transition and points it back to
  the required control tool.
- Retry requires the same hash and is bounded by attempt, turn, token, plateau,
  and repeated-failure rules.
- Automatic rollback is honest but narrow: only Musubi file mutations captured
  in a byte journal qualify. Shell/external effects require a manual or
  irreversible Work Package.
- Pipeline behavior and ownership are unchanged.
