# Plan Continuity Across Turns — Design Note

> **Status:** Open. Written to hold a decision, not to make it. Two defects
> measured on the 2026-08-05 four-turn run are recorded here because fixing
> them touches the session lifecycle, which is a larger commitment than the
> repairs shipped alongside this note.

## What was measured

Four consecutive turns on one `chat-id`, after Direct mode was removed and the
worker budget was sliced:

| turn | input | cycles | tokens | files written |
|---|---|---|---:|---|
| 1 | "Create an application to check weather of cities in vietnam" | 7 | 67,933 | 0 |
| 2 | "ok carry on" | 16 | 179,581 | 0 |
| 3 | "carry on" (`--max-tokens 500000`) | 7 | 101,080 | 0 |
| 4 | "decide by yourself" | 5 | 135,027 | 0 |
| | | **35** | **483,621** | **0** |

Three defects in that run were repaired separately: a budget halt that
discarded paid-for writes, `blocking_decisions` inflation, and a second
`begin_plan` whose refusal named no way forward. The two below were not.

---

## D4 — every turn re-plans from nothing

Each of the four turns opens the same way: `musubi_begin_plan`, then
`glob **/*` (826–828 matches), then re-reading the same files. Turn 4 also ran
`grep 'weather|open-meteo|vietnam'` — 239 hits across 806 files — for a single
32,806-token cycle.

The files being re-read are Musubi's own:

```
.musubi/goals/8983840305a0b0f0d4de/plan.md        read in turns 1, 2, 3, 4
.musubi/goals/8983840305a0b0f0d4de/manifest.json  read in turns 1, 2, 3
.musubi/goals/d66ee46c32ebf931af45/plan.md        read in turns 2, 3, 4
```

Root committed a plan, the harness persisted it under
`.musubi/goals/<id>/`, and the next turn had no way to receive it. Root
rediscovered its own artifacts by globbing the workspace and guessing which
goal directory was the current one — in turn 2 it read two different goals,
having no way to tell which was live.

Cost: 18,000–33,000 tokens per turn re-establishing state that was already on
disk. Roughly 20% of the four-turn spend.

**The shape of a fix.** A committed plan is conversation-scoped state, like
`chat_turns` and `chat_barren_turns` which are already loaded at turn start
from `agent_turns`. The same load could carry the last committed plan's goal
id, manifest, and worker chain, so a resuming turn starts from "here is the
committed plan; the chain is at step 2" instead of from a workspace glob.

**What has to be decided first,** and why this is not a small change:

1. **When does a committed plan expire?** "ok carry on" clearly continues it.
   A new unrelated request must not inherit it. There is no deterministic test
   for "same task", and inferring one from the user's text is exactly what the
   LLM-owned-scope track removed.
2. **Who owns the transition?** If Root is handed a live plan it did not
   commit this turn, `mode` starts at `planned` rather than `undecided`, and
   the opening surface changes. That interacts with the mode being one-shot.
3. **What happens when the workspace moved underneath it?** A plan naming
   files that no longer exist has to fail closed, which means the resume path
   needs its own validation, not just a load.

## D3 — Root's planning spend leaves too little for the worker

Turn 2, the only turn that reached a worker:

```
root spends 86,399 / 200,000 (43%) before the first spawn
  coder #1  allowance 33,747   halted on budget
root spends 140,107 / 200,000 (70%) before the second spawn
  coder #2  allowance 29,946   halted on budget
```

The split itself is correct — 101,242 remaining ÷ 3 slots = 33,747, and
59,893 ÷ 2 = 29,946, both exactly as `root_worker_allowance` specifies. The
problem is what is left to split.

A coder's write cycle in that run cost 19,801 tokens. At a ~30,000-token
allowance it can afford roughly one such cycle plus its orientation reads.
Both coders died on budget.

This is a direct consequence of removing Direct mode: every turn now plans,
and Root's planning reads cost more than the worker's work. The fair-share
split is not the defect; the numerator is.

**Why it waits on D4.** Most of Root's spend is rediscovery — the same globs
and the same artifact reads, every turn. Fixing continuity removes the cause
rather than rebalancing around it. Deciding a different split now would be
tuning against a number that D4 is expected to move.

**If D4 lands and the numerator is still too small,** the levers, in order of
how little they assume:

- Reserve Root a fixed planning budget and split what remains among workers,
  instead of splitting whatever survives Root.
- Let a worker that halts on budget with surviving artifacts be an
  `AUTO_REPLACE` rather than a `HALT` (`decide_recovery` currently halts every
  `FailureKind.BUDGET` fail-closed; with paid-for writes now landing, a budget
  failure can carry real artifacts, which is the condition that already earns
  a turn-cap failure its continuation).
- Raise `DEFAULT_AGENT_MAX_TOKENS`, which is the least informative option and
  the one that hides the measurement.

## Not in scope for this note

The discovery tax itself — a worker receives `{brief, role, role_skill,
role_skill_id, allowed_tools}` and no workspace facts, so every worker globs
to orient itself. That is a separate question about the closed context set
(`validation/subagent_context.py::context_keys`) and is tracked with the
skill-catalog work.
