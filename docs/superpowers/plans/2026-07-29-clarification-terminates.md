# One clarification per stall, then the answer is acted on

## Context

Traced conversation `gui-orchestrator-969914c04aa96461-7bce98a4ecdc`, three
consecutive turns:

```
args=["create a website", …]
[agent] scope=unknown route=ask_scope requires=clarification reason="broad product request without deliverable constraints"
[agent] deterministic route=ask_scope; no model call
What should the website do, and should it be a static page or use a specific framework?

args=["i would like to create a weather checking website", …]
[agent] conversation usage: turns=2 tokens=16080 turns_without_a_file=2
[agent] scope=unknown route=ask_scope …
What should the website do, and should it be a static page or use a specific framework?

args=["i would like to create a weather checking website", …]
[agent] conversation usage: turns=3 tokens=16080 turns_without_a_file=3
[agent] conversation no-progress warning: 3 turns without a file
[agent] scope=unknown route=ask_scope …
What should the website do, and should it be a static page or use a specific framework?
```

The user answered the question and got the question back — twice, byte for
byte. Three turns, zero model calls, zero files.

**The design assumption that broke.** `2026-07-22-governed-scope-budget-recovery.md`
specified that a broad product request "stops at **one** clarification". Nothing
in the code counts to one. `classify_task` (`musubi/agent/scope.py:288`) is a
pure function of a single message, and `_deterministic_scope_answer`
(`musubi/agent/run.py:1962`) returns
`ChangeAssessment.clarifying_question` whenever the route is `ask_scope`. The
user's answer — "i would like to create a weather checking website" — matches
`_BROAD_PRODUCT_RE` (`create` … `website`) and matches neither `_STATIC_FILE_RE`
nor `_FRAMEWORK_RE` (`musubi/agent/change_assessment.py:78`), so it re-derives
the identical assessment and the identical sentence. Every reply that stays on
the topic the question is about lands in the same branch. It is not a
recoverable stall: it is a fixed point.

Cost is small per turn (0 tokens, ~0 ms) and unbounded in turns. The one
substrate signal that saw it — `conversation no-progress warning: 3 turns
without a file` — is rendered into `GoalState` for the *model* to read, and the
deterministic halt returns before any model call, so it steers nothing.

## Goal

- A conversation spends at most **one** deterministic clarification per stall.
- The next message is read as the answer: it is merged with the pending request
  and routed for real.
- The escape can only remove a halt, never add one, and never widen a route.

## Tech stack

Python 3.11. One additive SQLite column through the established
`_migrate_columns` path (same shape as `delivered_artifact`). No new component,
no new dependency, so no new `musubi-tier` tag.

## Implementation steps

- [x] **Step 1: remember that the question was asked.** `agent_turns` gains
  `clarification_request TEXT` — the request a turn HALTED on, NULL when the
  turn actually ran. `db.pending_clarification(chat_id)` reads it from the
  LATEST turn only, so any real turn clears it without a delete and no marker
  can leak into an unrelated later request.

- [x] **Step 2: spend it once.** `classify_task` gains
  `allow_clarification: bool = True`. When False, every `ask_scope` return
  becomes `planner_then_coder_check` via `_clarification_already_spent`, which
  also downgrades the carried `ChangeAssessment` (route rewritten,
  `clarifying_question` dropped, `clarification-answered` appended to evidence)
  so nothing downstream can resurrect the halt. An empty message is the single
  exception: with no merged text to plan from, the question is the only move.

- [x] **Step 3: act on the answer.** In `run_agent`, an `ask_scope`
  classification now probes `_pending_clarification`. On a hit the driver builds
  `effective_task = f"{pending}\n\n[clarification answer] {task}"`, reclassifies
  it with `allow_clarification=False`, and runs the turn from that merged
  intent (goal state, parent session, worker brief). The raw message alone is
  what goes into `conversation_messages`, because the pending request is
  already on record as its own row and replaying it twice would duplicate the
  model's seed.

## Why a planner, not a second question

The alternative — ask a *different* question — needs a model call to compose
one, which HI #1 forbids in the substrate, or a hand-written question ladder,
which is ephemeral scaffolding by any reading of the decision rules. A planner
is the component that already asks specific questions, and it asks them after
reading the workspace instead of from a regex. Worst case the merged request is
still under-specified and one planner run produces a plan with `unknowns`,
which `assess_manifest` turns into a *targeted* question. That path terminates:
its question is derived from the plan, not from the sentence, so answering it
changes the classification.

## Direction of the escape

Both conversation-derived flags now move one way only. `has_history` can only
route toward the cheaper advisory answer; `allow_clarification=False` can only
remove a halt. A stale, wrong, or unreadable marker therefore costs at most one
planner run — `_pending_clarification` returns None on any storage failure,
which lands on the old behavior of asking again.

## Verification

- `musubi/tests/test_agent_turns.py` — the marker survives one turn and clears
  on the next; scoped per chat; blank id never claims one.
- `musubi/tests/test_agent_scope.py` — the merged request routes to a planner
  with the halt stripped from its assessment; a vague follow-up is released
  while an empty message still halts; every other route classifies identically
  with and without the flag.
- `musubi/tests/test_agent_loop.py` — end-to-end over two `run_agent` calls on
  one `chat_id`: turn 1 returns the question with zero model calls, turn 2
  reaches the model, logs `clarification answered`, routes
  `planner_then_coder_check`, and leaves no pending marker.
