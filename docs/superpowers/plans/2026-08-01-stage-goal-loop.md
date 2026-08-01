# Stage goals and the stage loop

Date: 2026-08-01 · Design:
[`2026-08-01-stage-goal-loop-design.md`](../specs/2026-08-01-stage-goal-loop-design.md)

## Context

A pipeline stage runs exactly once. `run_pipeline` walks the plan with a
single `for i, step in enumerate(plan)` (`agent/pipeline_runner.py:341`) and
never re-enters a stage. Nothing checks whether a stage achieved anything: its
recorded status is `"done" if answer is not None else "escalated"`, which asks
whether the worker produced text. The `correction:` block both shipped recipes
declare is dead — `musubi_get_correction_rules` appears only in `boundary.py`'s
allowed-tool list and nothing under `agent/` calls it — so the reviewer's
verdict is recorded and acted on by nobody.

## Goal

A stage declares what it must achieve; the substrate checks whether it did;
a stage that has not achieved it runs again with the reason, up to a declared
hard bound, and escalates when that bound is spent. Existing recipes are
unaffected until they opt in.

## Tech stack

Python: `musubi/composer.py` (recipe parse), `musubi/agent/pipeline_runner.py`
(the loop), a new `musubi/agent/stage_gate.py` (deterministic checks),
`musubi/validation/verifier.py` (reviewer verdict shape).
Rust: `gui/src-tauri/musubi-data` (recipe model + renderer + validator).
YAML: `.github/pipelines/*/pipeline.yaml`.

## Steps

Ordered by dependency — each needs the one before it.

1. **Recipe model carries the fields.** Add `skill`, `goal`, `exit_when`,
   `max_iterations` to `PipelineStageRecipe` and the renderer; extend the
   validator; drop the save refusal added on 2026-08-01 once a round-trip is
   provably lossless. Until this lands nothing can be authored.
2. **Composer reads them.** `stage_contract(pipeline_name, role)` returning
   goal / checks / bound, parallel to the existing
   `declared_stage_skill`. Absent fields resolve to the single-attempt
   default.
3. **Deterministic gate.** `agent/stage_gate.py` with `file_exists`,
   `lint_clean`, `command`. Pure, zero LLM, fail-closed on an unknown check
   type. `lint_clean` delegates to the existing `_run_mechanical_gate`;
   `command` routes through `musubi_run_command` so it stays inside the
   granted roots and lands in `tool_audit`.
4. **The loop.** Restructure the stage body of `run_pipeline` into a bounded
   attempt loop: run lead worker → write `stage_outputs(stage, attempt)` →
   run the gate → pass, or retry with the failure detail appended to the
   brief, or escalate at the bound. `musubi_increment_attempt` already exists.
5. **Reviewer tier.** `type: reviewer` spawns the declared role and branches
   on its structured `status`; runs only after every deterministic check has
   passed. A malformed verdict escalates rather than reading as a pass.
6. **Adopt in one recipe, measure, then decide the budget.** Put the loop on
   the single stage that most needs it, run it, and take the credit-budget
   question to the numbers rather than to opinion.

## Verification

Full suites at each step (`pytest`, `node --test`, `cargo test`), plus:

- Every new test confirmed to fail against the pre-change code.
- A regression test pinning that a recipe with no `goal:`/`exit_when:`
  produces the same worker count and the same audit rows as today.
- Round-trip test: a declared recipe survives open-then-save with `skill:`,
  `goal:`, `exit_when:` and stage names intact.

## Out of scope

Harness-driven turn order inside a stage (the lead agent decides — it already
has `spawns:` for this), parallel agents within a stage, and reviving
`correction:` as an outer loop. `correction:` stays parsed and ignored for one
release, then is removed.

## Open decisions

- `max_credits` after the loop lands — deferred to step 6 deliberately, so it
  is answered with a measured run.
- Whether the check vocabulary grows beyond the initial four types; each
  addition must be answerable without a model, or it belongs in the reviewer
  tier.
