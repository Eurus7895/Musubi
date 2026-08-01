# Stage goals and the stage loop — design

Status: proposed · Date: 2026-08-01

## Context

A pipeline stage today is **exactly one worker run**. `run_pipeline` walks the
plan once:

```python
for i, step in enumerate(plan):          # agent/pipeline_runner.py:341
    ... spawn worker → complete → next stage
```

There is no retry anywhere in that path, and nothing checks whether the stage
achieved anything. Three consequences, all verified against the code:

1. **A stage that misses its target still passes its output downstream.** The
   recorded status comes from `status = "done" if answer is not None else
   "escalated"` — that asks *did the worker produce text*, not *did the work
   land*.
2. **The `correction:` block is declared and never executed.** Both shipped
   recipes carry `max_retries: 3` and `escalate_on_critical: true`.
   `musubi_get_correction_rules` appears only in `boundary.py`'s allowed-tool
   list; nothing under `agent/` calls it. It is a leftover of the removed
   TypeScript runner — the same class of defect as the per-stage `skill:`
   field that the standalone runner ignored until 2026-08-01.
3. **The reviewer's verdict changes nothing.** It emits a structured
   `status` that `verifier.py` validates, and the runner never reads it.

So a pipeline is a conveyor belt: each stage runs once, hands whatever it
produced to the next, and the evaluator's opinion is recorded rather than
acted on.

## Goals

- A stage **declares what it must achieve**, and the substrate can tell
  whether it did.
- A stage that has not achieved it **runs again**, with the reason it failed,
  up to a declared bound.
- The bound is a **hard stop**, not advice.
- Every iteration is on the **append-only** record and attributable.
- Existing recipes behave **byte-identically** until they opt in.

## Non-goals

- The harness deciding which agent runs next inside a stage. That is routing,
  and routing is model-owned (see Decision 5).
- Agents running in parallel within a stage.
- Reviving `correction:` as a second, outer retry loop. This work replaces it
  (see Migration).
- Code evaluating a free-text goal. That is the defect this design exists to
  avoid (see Decision 1).

## Decisions

### 1. `goal:` is prose for the model; `exit_when:` is a predicate for the harness

Two fields, two readers, no overlap.

```yaml
goal: "index.html renders a weather table for 5 cities and tests are green"
exit_when:
  - type: lint_clean
    paths: [changed]
  - type: command
    run: npm test
  - type: reviewer
```

- **`goal:`** is copied verbatim into the worker's brief. The harness **never
  parses, matches, scores or interprets it.**
- **`exit_when:`** is what actually ends the loop.

This separation is the whole design. A loop needs an exit condition; if that
condition were the harness reading `goal:` and deciding whether it had been
met, this would re-introduce exactly the judgement the repository has spent
two tracks removing — `assess_request` and its nineteen regexes
(`agent/scope.py`), then the skill ranker and its confidence score
(2026-08-01). The rule those deletions established holds here without
exception: **the substrate may check a claim; it may not decide a meaning.**

### 2. `exit_when` has two tiers, and the order is load-bearing

| Tier | Runs | Answers | Cost |
|---|---|---|---|
| 1 — facts | harness | does the file exist, is lint clean, does the command exit 0 | ~0 tokens |
| 2 — judgement | reviewer (a worker) | does this actually meet the goal | one worker run |

Tier 1 runs first and short-circuits. There is no reason to spend a reviewer
call to learn that the test suite is red — the exit code already said so. The
reviewer is asked only about what a command cannot answer.

### 3. Delegating tier 2 to the reviewer does not violate HI #1

HI #1 says the substrate makes no LLM calls. A reviewer check does not: the
**runner spawns a worker** and **reads a field off its structured output**.
The reviewer is a participant in the pipeline, exactly as the coder is — not
part of the harness.

Two conditions keep it that way, and both are already enforced elsewhere:

- **The verdict is a field, never prose.** The runner reads
  `status ∈ {pass, fail, escalate}` and branches on it. It does not read the
  reviewer's summary and decide what the reviewer meant. This is the same
  *parse, never judge* pattern as `agent_turns.root_triage`, which records the
  root's declared shape without checking whether the shape was right.
- **The reviewer stays firewalled (HI #3).** It sees the artifact and nothing
  else — not the request, not the plan, not the goal's provenance. Already
  enforced by `_stage_brief` and unchanged here.

The audit consequence matters as much as the architectural one: a gate
decided by a model must be *attributable*. Every reviewer verdict is a
`subagent_audit` row with a handle, and the gate outcome is written per
attempt, so "why did stage `code` stop after 2 iterations" is answerable from
the DB alone.

### 4. `max_iterations` defaults to 1

An absent `max_iterations` means one attempt — today's behaviour precisely.
A recipe that declares no `goal:` and no `exit_when:` runs the same number of
workers, in the same order, producing the same rows. **Nothing changes until a
recipe opts in**, which is what makes this landable without re-validating
every existing run.

### 5. The lead agent decides who runs next inside the stage

A stage with several agents does not get a harness-driven turn order. The
stage's declared role is the **lead**; it summons helpers from `spawns:` when
it judges they are needed.

This is not new machinery — it already works. `pipeline_runner.py:446` grants
a stage worker the spawn tool when the recipe declares `spawns:` and depth
budget allows, which is how `code-review`'s synthesizer fans out
`reviewer-aux` per file today.

The alternative — the recipe listing a fixed order the harness walks — was
rejected: on iteration 1 there is no failure yet, so running the
`investigator` before the `coder` has written anything spends a worker to
diagnose nothing. Which helper is needed, and when, is a judgement about the
work in front of you. That belongs to the lead.

**Consequence for this design: "multiple agents per stage" needs no new
feature.** What is missing is the goal, the check, and the loop.

### 6. Exhausting `max_iterations` escalates and stops the pipeline

The stage is marked `escalated`, the pipeline finalises as escalated, and the
run reports which checks were still failing.

The alternative — continue to the next stage with a warning — was rejected:
a stage exists to produce an input for the next one, so continuing with an
input known to be unfit corrupts every stage after it and spends the rest of
the credit budget producing work on a bad foundation. Failing here is cheaper
and more honest.

### 7. Each iteration is a new attempt row, never an overwrite

`stage_outputs` is already keyed `(session_id, stage, chunk_id, attempt)` and
`musubi_increment_attempt` is already a tool. **The storage for a loop already
exists** — it was built for the retry path that was never wired up. HI #7
holds by construction: iteration 2 writes a new row, iteration 1 stays
readable.

## Contracts

### Stage declaration

```yaml
- name: coder
  stage: code
  agent: agents/workers/coder.agent.md
  skill: skills/web-ui/SKILL.md      # existing
  spawns: [explorer, investigator]   # existing — the lead's helpers
  goal: "…"                          # NEW — prose, for the model
  exit_when: [ … ]                   # NEW — predicate, for the harness
  max_iterations: 3                  # NEW — hard bound, default 1
```

### Check types (initial vocabulary)

| `type` | Fields | Passes when |
|---|---|---|
| `file_exists` | `path` | the path resolves inside a granted root and exists |
| `lint_clean` | `paths: [changed]` | the mechanical gate returns `pass` or `skipped` over the files this stage wrote |
| `command` | `run` | the command exits 0 |
| `reviewer` | `role` (default `reviewer`), `accept` (default `[pass]`) | the spawned reviewer's `status` is in `accept` |

`file_exists` and `lint_clean` reuse machinery that already exists —
`baseline_checks`' `file_read` type (`scripts/session_start.py:64`) and
`_run_mechanical_gate` (`agent/subagent.py:494`) respectively. The vocabulary
starts deliberately small; every entry must be answerable without a model
except `reviewer`, which is explicitly the model tier.

**`command` runs through `musubi_run_command`, not the shell directly**, so it
resolves inside the request's granted roots and lands in `tool_audit` like any
other tool call. A recipe is checked-in data authored by the operator, and it
already grants `Bash` to the coder role; routing exit checks through the same
governed tool keeps the blast radius identical rather than widening it.

### Gate result

```json
{ "passed": false,
  "checks": [ {"type": "lint_clean", "passed": true,  "detail": "…"},
              {"type": "command", "passed": false, "detail": "exit 1: 2 failing"} ],
  "failed": ["command"] }
```

Written per attempt and fed into the next iteration's brief, so the worker is
told *what* failed rather than being asked to guess why it is running again.

## State machine, per stage

```
attempt ← 1
loop:
    run lead worker (may summon from `spawns:`)     ─┐
    write stage_outputs(stage, attempt)              │ HI #7: append-only
    gate ← run exit_when, tier 1 then tier 2         │
    if gate.passed          → stage done, next stage │
    if attempt = max_iters  → escalate, stop         │
    attempt ← attempt + 1; brief += gate.failed     ─┘
```

With no `exit_when`, the gate is vacuously passed and the loop runs once —
today's behaviour.

## Cost

Worst case per stage multiplies by `max_iterations`:

| | worker runs, worst case |
|---|---|
| feature-dev today | 4 |
| feature-dev, all stages `max_iterations: 3` | 12 |

`max_credits: 50` was sized for the single-pass shape. Either the recipes
adopt the loop on the one or two stages that need it, or the budget is
revisited — a decision to make with the first real run's numbers, not before.
`TokenBudgetEnforcer` and the no-progress breaker still bound the run
independently of this loop.

## Blocker

The Pipeline Studio's editable model (`PipelineStageRecipe`, `musubi-data`)
carries four fields — `preset`, `agent`, `stage`, `spawns` — and the renderer
emits exactly those. It cannot represent `skill:` today, which is why saving a
declared recipe is currently **refused** rather than allowed to truncate it
(`2026-08-01`). `goal:`, `exit_when:` and `max_iterations:` land in the same
hole. **Extending that model is step 1**; nothing else can be authored until
it exists.

## Test strategy

- **Composer** — parses the three new fields from both recipe shapes; absent
  fields yield the single-attempt default.
- **Gate, deterministic** — each check type passes and fails on constructed
  inputs; a check the vocabulary does not know is a hard error, never a pass
  (fail-closed).
- **Gate, reviewer tier** — a `fail` verdict loops, a `pass` verdict exits, a
  malformed verdict escalates rather than being read as pass; tier 2 is not
  reached when tier 1 fails (asserted by the reviewer never being spawned).
- **Runner** — one attempt when the gate passes first time; N attempts then
  escalation when it never passes; each attempt writes its own
  `stage_outputs` row; the failure detail reaches the next brief.
- **Regression** — a recipe with no `goal:`/`exit_when:` produces the same
  worker count and the same rows as before.

## Invariants

- **HI #1** — preserved. The gate's model tier is a spawned worker; the
  harness reads a field.
- **HI #3** — unchanged. The reviewer sees only the artifact.
- **HI #7** — relied upon. Each iteration is a new attempt row.
- **HI #9** — the loop is `musubi-tier: ephemeral`; it exists because models
  do not yet reliably self-verify, and its `expires-when` is that.

## Migration

`correction:` is superseded. It declares a retry policy that nothing executes;
this design implements the same intent at stage granularity with a checkable
condition. It stays parsed and ignored for one release so no recipe breaks,
then is removed with its `expires-when` fired.
