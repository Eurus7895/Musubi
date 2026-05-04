---
name: orchestrator-routing
description: Routing rules for the Orchestrator — which sub-agent to spawn for which kind of turn, when to spawn nothing at all, and how to size the brief. Pushed by the harness via the orchestrator's inject_skills frontmatter.
---

## Purpose

Help the Orchestrator pick the smallest sub-agent that can handle each
user turn, without invoking pipelines and without doing the work
itself. Routing wrong wastes spawn budget and pollutes the
conversation; routing right keeps the parent context small and the
answer fast.

## When NOT to spawn

Answer directly, no sub-agent, in these cases:

- The user is asking about prior decisions you can answer from
  `memory_tier1` or `conversation_history`.
- The user wants a discussion, recommendation, or trade-off analysis —
  reasoning, not lookup.
- The user wants you to summarise or rephrase something already in the
  conversation.
- The user is correcting or redirecting your previous answer — adjust
  course in prose, not by re-spawning.

A turn that does NOT need a sub-agent should not get one. Spawning
"just to be safe" is the most common routing mistake.

## Sub-agent picker

| Kind of work | Spawn | Brief shape |
|---|---|---|
| "Where is X defined / referenced?" | `explorer` | `Locate-X` / `Layout` / `Confirm-X` (one verifiable question) |
| "What does this command output? / Why is the test failing?" | `investigator` | One diagnostic question + the command(s) allowed |
| "Does file F satisfy checklist C?" | `reviewer-aux` | File path + checklist items, per-file |
| "Scope this multi-task feature" | `planner` | The user's request, verbatim or tightened |
| "Implement change X in file F" | `coder` | Plan-shaped brief: tasks, files, acceptance criteria |
| "Evaluate this code-as-artifact" | `reviewer` | The code blob being judged (no plan/design/intent) |

Rules of thumb:

- Prefer `explorer` over `investigator` when no shell command is
  needed. Investigator's extra Bash scope is cost; spend it only when
  earned.
- Prefer `reviewer-aux` over `reviewer` for checklist-style
  per-file checks; reserve `reviewer` for whole-artifact evaluation
  with the full code-review skill.
- Prefer asking the user over spawning `planner` when the request is
  small or already well-defined. Planner is for genuinely
  multi-task work.
- Spawn `coder` only with a brief tight enough that Coder doesn't have
  to guess. If you find yourself writing "and also …", stop — split
  into separate briefs or punt to a pipeline.

## Sequencing

Sub-agents do not see each other. If two depend on each other:

1. Spawn the upstream one (e.g. `explorer` to find the file).
2. Read its summary into your own working memory.
3. Spawn the downstream one (e.g. `coder` to edit the file) with a
   brief that *includes* the relevant facts from step 2.

Never assume a sub-agent will infer context from the chat — they
cannot see it.

## Budget

The harness caps you at **3 spawns of any one role per user turn**.
The cap is per-role, not total: 3 explorers + 1 coder is fine; 4
explorers is denied.

If you hit the cap, the brief was probably wrong. Stop spawning, tell
the user what you tried and what came back, and ask how to proceed.
Do not work around the cap by spawning a different role to do the
same job.

## When to recommend a pipeline

You may NOT spawn a pipeline. But you should suggest one when:

- The user's request needs plan + design + code + review with the
  evaluator firewall preserved across stages.
- Multiple back-and-forth correction loops are likely.
- The user explicitly wants the durable, append-only stage record.

In those cases, reply: "This looks like work for `/feature-dev` — try
`/feature-dev <one-line goal>`." Do not try to recreate the pipeline
by hand-spawning planner → coder → reviewer; the pipeline carries
state, schemas, and the correction loop that ad-hoc spawns do not.

## Anti-patterns

- **Spawning `explorer` to read a single file you already know the
  path of.** Use your own `Read` tool — that's why you have it.
- **Pasting the sub-agent's full transcript back to the user.** The
  chat marker already shows it; quote at most one or two lines for
  context.
- **Spawning multiple sub-agents in parallel hoping one works.** Each
  spawn costs the parent budget and the user's wall-clock. Pick the
  right one.
- **Summarising the conversation back to the user before answering.**
  They wrote it; they remember. Just answer.
- **Looping on a failed sub-agent.** A `failed` or `escalated` result
  means the brief was wrong or the work doesn't fit the role. Re-brief
  or ask the user — don't retry the same call.
