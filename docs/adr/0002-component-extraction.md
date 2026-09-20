# ADR 0002: simplify Musubi through responsibility extraction

Date: 2026-09-18
Status: accepted for incremental implementation

## Context

The agent host mixes context preparation, model invocation, response
interpretation, tool transport, run state and contract controls. Adding another
controller around it would preserve this coupling. The desired components are
Collector, Thinker, Decider, Runtime/Tools, Memory, Storage and separate HoH.

## Decision

Move existing implementations into narrowly owned modules and delete their old
definitions. Preserve compatibility aliases while migrating callers. Keep one
application and its existing execution path; no additional universal agent or
orchestrator is introduced.

Collector prepares bounded context. The Decider acts first on that context and
selects a legal typed operation. The active Decider provider is the existing
LLM gateway. It invokes the Thinker only when generation or deeper reasoning is
required, then decides again over the resulting candidates.
Runtime tool helpers handle transport and validation. State is data owned by
run state; deterministic controls enforce frozen contracts and completion
blockers. Model proposals cannot bypass these gates.

The first increment reuses already-accounted LLM responses when they contain a
valid bounded selection; separating Decider from Thinker does not by itself add
a model call. A dedicated LLM decision call is allowed only when a new choice
is required and must be charged and recorded separately. Jev integration is
deferred to research backlog and is not an Adaptive acceptance dependency.
Any future specialized provider requires a separate evidence-based decision.

Storage remains the evidence authority. Memory is derived knowledge. HoH will
have a separate manually invoked lifecycle and isolated candidate evaluation,
with no automatic promotion or recursive invocation.

## Consequences

The live loop becomes smaller without duplicating behavior. Imports into the
extracted modules cannot depend on the host or concrete model SDKs. Tests cover
these boundaries and existing contract, budget and tool behavior.

This decision does not complete the thin-runtime architecture. Lifecycle,
dispatch and CLI still require decomposition. Shared evidence records, terminal
Memory consolidation, typed provider dispatch and HoH remain scheduled work.
