---
name: orchestrator-routing
description: Routing rules for the Orchestrator — answer directly using the available read tools, defer multi-stage work to pipelines, never spawn sub-agents while their runners are not wired. Pushed by the harness via the orchestrator's inject_skills frontmatter.
---

## Purpose

Help the Orchestrator answer each user turn cheaply, using the
smallest amount of work that produces a real result. Today that means:
**answer directly from your context and the available read tools, or
suggest a pipeline. Do not try to spawn sub-agents — the runners are
not wired yet, and any spawn will hang for ~30 s and return nothing.**

## Current state — read this first

Phase B shipped the sub-agent primitives (spawn, await, list, audit
log) on the Python side. The extension-side runners that turn a
`running` sub_sessions row into an actual LM session for `explorer`,
`investigator`, and `reviewer-aux` have **not** shipped. Until they
do, the harness deliberately hides the sub-agent tools from the
catalog so you cannot call them by accident.

If you ever see `harness_spawn_subagent` in your tool catalog, that
means a runner has been wired and the rest of this skill (sub-agent
picker, sequencing, budget) becomes live again. Until then, treat
the sub-agent guidance lower in this file as reference, not as the
current playbook.

## How to answer turns today

### 1. Direct answer from context

Answer directly, no tools, in these cases:

- The user is asking about prior decisions you can answer from
  `memory_tier1` or `conversation_history`.
- The user wants a discussion, recommendation, or trade-off analysis —
  reasoning, not lookup.
- The user wants you to summarise or rephrase something already in the
  conversation.
- The user is correcting or redirecting your previous answer — adjust
  course in prose.

A turn that does NOT need a tool call should not get one. Calling a
read tool "just to be safe" is the most common waste.

### 2. Direct lookup with the read tools you have

For "where is X?" / "show me the structure" / "what does file F
contain?" use the read tools your catalog actually advertises —
typically Copilot's `copilot_readFile`, `copilot_searchWorkspace`,
`copilot_findFiles`, `copilot_listDirectory`, `copilot_getErrors`,
and similar. They run synchronously in the user's workbench and
return real results in a few hundred milliseconds.

Pick the cheapest one that answers the question:

- "Find file by name" → `copilot_findFiles` (fastest)
- "Find code by content" → `copilot_searchWorkspace` (workspace search)
- "Read file F" → `copilot_readFile` (only when you have the path)
- "Browse a directory" → `copilot_listDirectory`
- "Diagnose a build break" → `copilot_getErrors`

### 3. Light edits when explicitly asked

If the user says "change foo to bar in file X", use the edit tools in
the catalog (`copilot_replaceString`, `copilot_insertEdit`, or their
equivalents). One edit per request — for anything multi-step, suggest
the pipeline (next section).

### 4. Hand off to a pipeline for multi-stage work

You may not invoke pipelines directly. Suggest one when:

- The work needs plan + design + code + review with the evaluator
  firewall preserved across stages.
- Multiple back-and-forth correction loops are likely.
- The user explicitly wants the durable, append-only stage record.

Say: *"This looks like work for `/feature-dev` — try `/feature-dev
<one-line goal>`."* Do not try to recreate the pipeline by chaining
read + edit calls; the pipeline carries state, schemas, and the
correction loop that an ad-hoc orchestrator turn does not.

## Anti-patterns

- **Calling a tool whose result you already have** in the conversation.
  Re-reading the same file three times in one turn is pure waste.
- **Looking up scaffolding the user can already see.** They opened the
  file; they don't need you to confirm its line count.
- **Pasting a 50 KB tool result into the chat.** Quote a few lines for
  context, summarise the rest. The harness already truncates oversized
  rows at storage time, but the user still has to read the chat.
- **Summarising the conversation before answering.** They wrote it;
  they remember. Just answer.

## When sub-agent runners ship — reference

This section becomes live once `harness_spawn_subagent` shows up in
your catalog. Until then, ignore it.

| Kind of work | Spawn | Brief shape |
|---|---|---|
| "Where is X defined / referenced?" | `explorer` | One verifiable question |
| "What does this command output? / Why is the test failing?" | `investigator` | One diagnostic question + the command(s) allowed |
| "Does file F satisfy checklist C?" | `reviewer-aux` | File path + checklist items, per-file |
| "Scope this multi-task feature" | `planner` | The user's request, verbatim or tightened |
| "Implement change X in file F" | `coder` | Plan-shaped brief: tasks, files, acceptance criteria |
| "Evaluate this code-as-artifact" | `reviewer` | The code blob being judged (no plan/design/intent) |

Cap: 3 spawns of any one role per user turn. If you hit it, the brief
was probably wrong — stop spawning and ask. Sub-agents do not see each
other; pass facts forward in the next brief.
