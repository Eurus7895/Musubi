# Conversation-aware routing, progress accounting, and deferred unknowns

## Context

Follow-up to
[`2026-07-25-advisory-route-and-manifest-precedence.md`](./2026-07-25-advisory-route-and-manifest-precedence.md),
which added the advisory route and stopped a one-file plan being escalated by
subsystem count. That fixed the turns carrying an explicit consultative
signal. The same traced conversation
(`gui-orchestrator-969914c04aa96461-3d15f0b6d6e5`) exposed four further
defects, addressed here in the order they cost the most.

**1. The classifier sees one message.** `classify_task` receives the raw CLI
argument; conversation history is loaded for the *model* only. A bare
follow-up — "Okta", "skill?" — carries no verb, no path, and no advisory
keyword, so it falls to the mutation catch-all and spawns a planner. Turn 3
spent 96.16s / 27,455 tokens doing that, and ended in a wall of questions.

**2. Cost accounting is process-scoped.** Every chat message is a fresh agent
process with a fresh 200k allowance (`_build_token_budget`); the no-progress
breaker only sees the current turn. The conversation burned ~408s and 109,494
tokens across six turns and delivered no file, and no breaker could observe
it — each turn started at 0/200000.

**3. Every planner unknown blocks.** `assess_manifest` turned the whole
`unknowns` array into one halt. Turn 6 asked the user to decide branding,
palette, contrast targets, breakpoints, grid data model, hero copy, and
typography scale before a simple page could be written — and discarded the
plan the planner had just spent 30–61s producing.

**4. The pipeline recommendation was unreachable.** `_pipeline_recommendation`
emits a CLI line. The GUI already had a picker, but `classifyChatCommand`
exact-matched `run pipeline`, so the "ok then run pipeline" the user actually
typed missed it and went to the agent — another planner, another wall.

## Goal

- A bare follow-up is answered, not planned.
- A conversation's cost and its run of turns without a file are visible to the
  root that decides what to do next.
- Unknowns that are cheap to get wrong are defaulted by the worker, not
  escalated to the user.
- The pipeline recommendation names an action that works on the surface the
  user is on, without weakening the human gate.

## Tech stack

Python 3.11 plus the existing GUI JS (`gui/src/data/`). One additive SQLite
column via the established `_migrate_columns` path. No new dependency, no new
component, so no new `musubi-tier` tag.

## Implementation steps

- [x] **Step 1 (#7): conversation-aware classification.**
  `db.count_conversation_messages` + `conversations.has_history` give a cheap
  boolean; `run.py::_chat_has_history` probes it before `classify_task`.
  `classify_task(task, *, has_history=False)` gains a bare-follow-up branch:
  under `_FOLLOW_UP_MAX_WORDS` and carrying no mutation verb, inspect verb,
  diagnostic signal, or path target → `advisory`.

  The flag says only *that* prior turns exist, never what they were about,
  and it is used in exactly one direction — toward the cheaper answer. A
  stale or wrong flag therefore cannot open a mutation path.

- [x] **Step 2 (#6): conversation-scoped progress accounting.**
  `agent_turns` gains `delivered_artifact` (additive, defaults 0).
  `Orchestration.delivered_artifact` is true when some worker finished `done`
  with files touched; `_record_agent_turn` persists it.
  `db.chat_turn_usage` aggregates turns, tokens, and the TRAILING run of
  barren turns. `GoalState` carries the three numbers and renders them, plus
  a `conversation_warning` at `NO_PROGRESS_TURN_THRESHOLD` (3) telling the
  root to stop planning and either deliver or ask one question.

  Deliberately steering, not halting: a hard stop mid-conversation would be
  hostile, and the failure mode here was wasted planning, not runaway spend.

- [x] **Step 3 (#4): deferred unknowns on cheap changes.**
  `ChangeAssessment` gains `deferred_unknowns`. In `assess_manifest`, unknowns
  block as before EXCEPT when the change carries no critical flag and at most
  `MAX_SIMPLE_FILES` file — then they ride along for the next worker to settle
  with sensible defaults, surfaced to the root as `choose_sensible_defaults=`.
  A wrong palette costs one turn to redirect; halting costs the whole plan.
  Critical and multi-file changes keep the fail-closed halt unchanged.

- [x] **Step 4 (#5): a pipeline action that works in chat.**
  `chatCommands.js` strips leading conversational filler ("ok", "then",
  "yes", …) before matching, and accepts the phrasings users type ("run the
  pipeline", "start pipeline"). `pipelineNameFromCommand` replaces the inline
  regex in `TauriSource.js` so both entry points share the tolerance.
  `_pipeline_recommendation` now names the in-chat phrase first and keeps the
  shell command as the alternative.

  The human gate is untouched: the picker still requires the user to pick and
  send, so the orchestrator still never launches a pipeline (locked
  decision #4). Only the affordance changed. Work orders that merely mention
  a pipeline ("add a pipeline stage to the runner") are still routed to the
  agent, pinned by test.

- [x] **Step 5: verify GREEN.**

```bash
python3 -m pytest musubi/tests/ -q          # 1592 passed, 1 skipped
node --test gui/src/data/chatCommands.test.mjs   # 4 pass
```

## Result on the traced conversation

| Turn | Was | Now |
|---|---|---|
| "explain each" | planner, 95s / 18.7k | advisory, no spawn |
| "choose the best for me" | planner-routed | advisory, no spawn |
| "Okta" | planner, 96s / 27.5k → wall | advisory, no spawn |
| "these are complicated" | planner-routed | advisory, no spawn |
| "a simple front end page…" | escalated large → dead end | coder gate opens, unknowns defaulted |
| "ok then run pipeline" | planner → second wall | GUI opens the picker, agent not called |
| "skill?" | planner-routed | advisory, no spawn |

## Out of scope

- **Hard conversation budget.** Spend is now visible and warned on; no cap is
  enforced across turns. Enforcing one needs a policy decision about what
  happens to a long, legitimate conversation.
- **Pre-existing `agent_turns` rows** read as `delivered_artifact = 0`. That
  is deliberate: the flag only ever makes the root more conservative about
  planning, so defaulting it low is safe.
