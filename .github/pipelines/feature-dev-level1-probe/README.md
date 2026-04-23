# feature-dev-level1-probe

**Status:** Week 4 Day 5 — one-off measurement. Not wired into the extension
(`pipeline.ts`'s `loadAgentPrompt` is still hard-coded to `feature-dev/`).
Running the probe is a manual, branch-local exercise.

## What this is

A Level-1 variant of `feature-dev` with planner + designer + coder collapsed
into one `composite` agent. The reviewer remains a separate evaluator with
the same firewall as production (`code` only, no plan/design/request).

`pipeline.yaml` references the production reviewer by relative path:
`../feature-dev/agents/reviewer.agent.md`. No reviewer file is duplicated.

## Why

Deferred from Week 3a: does the multi-agent generator in `feature-dev`
actually do better than a single composite generator, or is the extra
orchestration (and non-determinism) buying us nothing? Until we measure,
we keep Level 2. This probe makes the measurement possible.

## Measurement protocol

1. Pick 5 representative `/feature-dev` requests (small, medium, large,
   one ambiguous, one with a known retry pattern — record the exact list
   in this file before running the probe).
2. Run each through **both** pipelines using the same model, same workspace,
   same seed where configurable. Capture:
   - first-attempt reviewer status (pass / fail / escalate / wrong_plan)
   - number of correction rounds needed to reach pass (or cap at 3)
   - artifact sizes (plan + design + code chars)
   - wall-clock time
3. Compare first-attempt pass rates.

## Decision rule

| Outcome | Action |
|---|---|
| Level-1 ≥ 80% first-attempt pass **and** ≤ Level-2 retries | Collapse `feature-dev` to Level 1. Retire the probe pipeline. |
| Level-1 80%+ but needs **more** retries | Keep Level 2 — composite generator fails quietly on nuance. |
| Level-1 < 80% first-attempt pass | Keep Level 2. Document exact failure class in `.github/memory/failure-patterns.md`. |

Threshold is deliberately a **ratio**, not an absolute — a tiny sample
(n=5) cannot establish anything else with confidence. If we keep Level 2,
an entry in `failure-patterns.md` records which sub-task a single agent
couldn't handle. If we collapse to Level 1, the Level-2 agents move to
`.github/agents/deprecated/` for one release before deletion.

## Running the probe

The probe is run by pointing `pipeline.ts`'s hard-coded pipeline name at
`feature-dev-level1-probe` temporarily, then reverting. Do not ship that
change — the whole point of the probe is that production stays untouched.

```bash
# Revert before merging:
sed -i 's|feature-dev-level1-probe|feature-dev|g' copilot-harness-extension/src/pipeline.ts
```

## Decision log

(Empty until measurements are recorded. Append here — do not overwrite.)

---
