# Conversation-scoped goal planning artifacts

## Context

The traced weather-site conversation exposed two coupled failures.

First, the planner returned prose plus an inline `<change_manifest>`, so the
human implementation contract and the machine governance declaration had no
independent identity. The next worker received a bounded summary rather than a
stable plan file.

Second, `assess_manifest()` used file count to decide whether an `unknown` was
defaultable. That is a semantic judgment the harness cannot prove. A multi-file
framework app therefore halted on reversible choices such as provider, locale,
and cache TTL even after the user explicitly delegated those choices.

The planner must remain read-only. Granting `Write` would widen the fail-closed
policy and let a planning worker mutate product source. The driver can instead
validate the planner response and persist only the two declared artifacts.

## Goal

- Require one non-empty Markdown plan and one valid bounded JSON manifest.
- Persist them as `.musubi/goals/<conversation-key>/plan.md` and
  `manifest.json`.
- Reuse the same directory across chat follow-ups instead of creating one plan
  per message.
- Keep planning files separate from user-deliverable progress accounting.
- Let the planner choose reversible assumptions with model reasoning.
- Reserve `blocking_decisions` for choices with no safe reversible default.
- Preserve deterministic blast-radius, critical-flag, and manifest-overrun
  enforcement.

## Non-goals

- Do not grant `Write`, `Edit`, or `Bash` to the planner.
- Do not add lexical rules for `ok`, `continue`, or similar messages.
- Do not implement `/goal new`, `/goal switch`, or persistent multi-goal
  lifecycle in this change. Those commands require a separate storage and
  operator-surface design.
- Do not count `plan.md` or `manifest.json` in `files_expected`.

## Technical design

### Planner response

The planner emits its existing terminal fields followed by:

```text
<plan>
# Deliverable
...
</plan>
<change_manifest>{...}</change_manifest>
```

The plan records assumptions, implementation steps, acceptance criteria, and
real blockers. The manifest remains bounded to 4 KiB and contains exactly nine
fields, with `blocking_decisions` replacing `unknowns`.

### Driver persistence

`agent/planning_artifacts.py`:

- validates exactly one literal `<plan>` pair;
- enforces a 64 KiB UTF-8 plan ceiling;
- delegates JSON validation to the existing manifest parser;
- derives a path-safe key from `chat_id`, falling back to root session id for
  standalone runs;
- writes both files through temporary siblings plus `os.replace()`.

`Orchestration.record_worker_outcome()` persists the pair only after a
successful planner result. Missing or malformed pairs keep the coder gate
closed. The resulting paths are rendered in `GoalState` so the root can pass
both files to the next worker.

Planning writes are driver-owned. They are not added to
`WorkerOutcome.touched_files`, so `delivered_artifact` remains false until an
implementation worker produces the requested artifact.

### Manifest assessment

The substrate no longer infers whether a decision is reversible from
`files_expected`. The planner makes that semantic decision:

- reversible default → record under plan assumptions;
- no safe reversible default → put in `blocking_decisions`.

`assess_manifest()` still halts on a non-empty `blocking_decisions` declaration,
but it no longer invents that declaration or changes its meaning based on file
count.

## Implementation steps

1. Add the planning artifact parser, stable key, and atomic writer.
2. Change the manifest schema from `unknowns` to `blocking_decisions`.
3. Remove the deterministic deferred-unknown/file-count heuristic.
4. Update planner and request-triage contracts.
5. Wire persistence into planner completion without widening planner tools.
6. Surface artifact paths through `GoalState`.
7. Ignore runtime goal artifacts in Git.
8. Update regression tests and roadmap.

## Validation

```text
python -m pytest tests/test_manifest.py tests/test_planning_artifacts.py \
  tests/test_goal_state.py tests/test_routes.py tests/test_agent_loop.py -q
ruff check agent/manifest.py agent/planning_artifacts.py \
  tests/test_manifest.py tests/test_planning_artifacts.py
```

The focused test suite must prove:

- malformed, duplicate, empty, and oversized plans fail closed;
- manifest JSON retains exact-schema and exact-type validation;
- conversation keys are stable across follow-up sessions;
- planner results create two separate files;
- planning files do not count as delivered implementation;
- missing `plan.md` keeps the coder gate closed;
- blocking decisions no longer depend on file count.

## Follow-up: session turns and operator token budget

The same conversation should not expose a second lifecycle called a
"request". The durable `request_id` remains an internal correlation key because
runtime log rows, folder-grant snapshots, and completed agent turns need an
append-only join key. The Console projects those records as:

```text
session → turns → workers
```

It labels root activity as `Turn 01`, `Turn 02`, and so on, while keeping the
internal key available for diagnostics and audit joins.

The total token budget is also a launch boundary, not a model decision. The
Console composer therefore accepts an optional budget for the next turn and
forwards it to the driver as `--max-tokens`. Blank uses the configured default,
0 disables the cap, and a positive integer overrides the default. Negative or
non-integer input fails before launch.

The root receives a stable explanation of these controls so it can answer
budget questions accurately. It deliberately receives no mutation tool for its
own enclosing budget; self-modifying governance would make the cap ineffective.
