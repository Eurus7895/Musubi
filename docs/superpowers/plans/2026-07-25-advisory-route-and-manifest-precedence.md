# Advisory route and single-file manifest precedence

## Context

A seven-turn GUI orchestrator conversation (`chat_id`
`gui-orchestrator-969914c04aa96461-3d15f0b6d6e5`) spent ~408s of model time
and ~109k tokens across six turns and delivered **no file**, two walls of
blocking questions, and one pipeline recommendation the chat surface cannot
execute. The user was consulting about an auth model and then asked for a
simple front-end page.

Two deterministic defects produced that outcome.

**1. No consultative intent class.** `classify_task` recognises greetings,
destructive file operations, exact-match vague phrases, path inspection, and
mutation. A request to be *advised* — "explain each", "choose the best for
me" — matches none of them, so it falls through `assess_request`'s
`insufficient-deterministic-evidence` catch-all
(`change_assessment.py`) into `classify_task`'s own catch-all
(`scope.py`), landing on `medium_change` /
`planner_then_coder_check`. `GoalState.create` then pins
`next_role="planner"` on the weakest evidence bucket in the system. Turn 1
spent 95s and 18.7k tokens spawning a read-only planner to answer a question
that named no file. The turns that went *well* (2 and 4) were the ones where
the model ignored the route and answered directly.

**2. Subsystem count alone escalates a single-file change.**
`assess_manifest` treats `len(subsystems) > MAX_MEDIUM_SUBSYSTEMS` as a
large-blast-radius trigger independent of `files_expected`, and that check
precedes the single-file check. Turn 5's manifest was
`files_expected:1, subsystems:3` with **no critical flag set** (the emitted
evidence tuple carried no `critical:*` entry), yet routed to
`plan_design_workflow`. Because the orchestrator may not launch a pipeline
(locked decision #4, `scripts/policy_engine.py`), the turn ended in a
recommendation instead of a file — and the `coder`, the only role that can
write files *and* the only role allowlisted for the `web-ui` skill
(`validation/context_builder.py`), never ran.

## Goal

- A consultative request is answered by the root in one model call with no
  worker spawn and no tool round trip.
- A single-file change is never escalated to the large workflow by subsystem
  count alone; critical flags keep their absolute precedence.

## Tech stack

Python 3.11, existing substrate modules only. No new dependency, no new
component (so no new `musubi-tier` tag is required — `agent/scope.py`,
`agent/goal_state.py`, and `agent/change_assessment.py` are all already
tagged `substrate`).

## Implementation steps

- [x] **Step 1: `ScopeKind.ADVISORY` + `advisory` route.** Add `_ADVISORY_RE`
  (explain / compare / recommend / choose / should I / which is better /
  pros and cons / trade-offs). Add the branch to `classify_task` *before*
  `assess_request`, so a pure question about a critical-risk topic ("which
  auth provider should I choose?") is not forced into a plan/design/review
  workflow by the risk gate. Gate the branch on three exclusions: no mutation
  verb, no diagnostic signal, and no concrete path/filesystem target — the
  last keeps "explain run.py" routed to a worker that actually reads the
  file rather than answered from the root's memory.

- [x] **Step 2: withhold the tool catalog on the advisory route.**
  `root_decision_tools` returns `[]` for `ADVISORY_ROUTE`, checked before
  every other phase. This reuses the existing forced-conclusion mechanism
  (the `spawn_exhausted` branch) and also removes the
  `musubi_recommend_skills` round trip, which cost ~103s across the traced
  conversation while being structurally unable to deliver a skill to the
  planner it was selecting for.

  The advisory route is deliberately **not** handled in
  `_deterministic_scope_answer`. Routing it to the existing `direct_answer`
  would return the canned `"Hi! How can I help?"` with zero model calls and
  destroy exactly the two turns that worked. Advisory falls through to the
  normal LM loop; the model still reasons, it just has no tools.

- [x] **Step 3: single-file precedence in `assess_manifest`.** The subsystem
  ceiling applies only when `files_expected > MAX_SIMPLE_FILES`. Critical
  flags and the `MAX_MEDIUM_FILES` ceiling are untouched, so a one-file
  security or migration change stays `plan_design_workflow`.

- [x] **Step 4: tests.**
  - `test_agent_scope.py` — advisory classification across seven phrasings;
    advisory beats the critical-risk gate for a pure question; advisory never
    swallows a mutation, a diagnostic, or a path question.
  - `test_goal_state.py` — advisory root surface offers no tools, including
    under a recovery phase.
  - `test_change_assessment.py` — single-file/many-subsystem is not large;
    still large when a critical flag is set; multi-file/many-subsystem is
    still large (regression guard).
  - `test_agent_loop.py` — an advisory request makes exactly one model call,
    is offered an empty tool list, and is *not* served by
    `_deterministic_scope_answer`.

- [x] **Step 5: verify GREEN.**

```bash
python3 -m pytest musubi/tests/ -q
# 1582 passed, 1 skipped
```

## Out of scope

Deliberately not addressed here; each needs its own change:

- **Bare-noun follow-ups.** "Okta" and "skill?" still classify as
  `medium_change`, because `classify_task` sees only the current message —
  conversation history is loaded for the model, not for the classifier.
  Fixing this means giving the classifier turn context.
- **The `unknowns` wall.** `assess_manifest` still converts every entry of
  the planner's `unknowns` array into one blocking question. Tiering it
  (coder picks defaults for low-risk items) changes halt semantics and needs
  a separate decision.
- **Pipeline recommendation on the GUI surface.** `_pipeline_recommendation`
  still emits a CLI string the chat surface cannot run. Keeping compliance
  human-gated while making the proposal actionable is a GUI change.
- **Conversation-scoped budget.** Each GUI turn is a fresh process with a
  fresh 200k budget, so no breaker can observe a multi-turn no-progress loop.
