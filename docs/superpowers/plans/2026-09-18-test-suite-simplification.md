# Implementation plan: simplify Musubi tests without losing behavioral protection

Date: 2026-09-18
Status: planned; no test removals or runtime changes in this document
Branch: `refactor/agent-components-hoh`
Baseline implementation: `2a41d83`

## Objective

Reduce the maintenance cost of tests while preserving protection of observable
behavior, governance and historical failures. Support the component refactor by
moving tests toward stable boundaries, not by adding a test for every extracted
helper. There is no quota for test deletion or target total case count.

Scope: Python tests in `musubi/tests`, their shared fixtures, pytest configuration,
and the Python job in `.github/workflows/ci.yaml`. Rust and Console suites remain
required existing gates; reorganizing them is outside this work package.

Technology: existing pytest, Python AST for inventory if useful, fake LMRouter and
MCP dependencies, temporary SQLite databases and subprocesses. Do not introduce a
test framework, global plugin or mutation-testing dependency for this cleanup.

## Evidence and baseline limitations

- The last broad run reported 1,809 passed, 1 skipped, 4 deselected in 35.64s.
  This was a local Python 3.12 run, not a complete CI pass. CI uses Python 3.11.
- Three excluded executor tests need mypy: `test_run_typecheck_clean_file`,
  `test_run_typecheck_type_error`, and `test_run_all_clean_code`. Installation
  timed out. Missing tooling is an environment issue, not evidence for deletion.
- `test_run_tests_passing_suite` was also excluded. The current CI job already
  excludes this case with a comment about subprocess PATH. The test itself runs
  one passing test in a temporary directory; it does not intentionally recurse
  through the repository suite. Inspect the executor before accepting the
  comment's diagnosis or removing the exclusion.
- `test_agent_components.py` added 11 collected cases: four behavioral tests,
  one import-boundary test parameterized over six modules, and one compatibility
  alias test. Reducing six parameter instances to one case changes reporting,
  not necessarily maintenance cost or coverage.
- Module extraction required updating patch targets in `test_agent_loop.py`,
  `test_mechanical_gate.py`, `test_spawn_pipeline.py` and `test_stage_loop.py`.
  This identifies coupling to investigate; it does not prove those tests are
  redundant or invalid.

## Acceptance rules

A test can be removed only when its failure scenario and assertions are mapped
to a retained test, or the supported behavior has actually been retired.
Similar names, shared line coverage or matching setup are insufficient evidence.
Tests of the same rule at unit and integration levels may detect different bugs.

Preserve these behavior families explicitly:

| Family | Required observable protection |
| --- | --- |
| Policy and tool authority | Unauthorized calls produce no tool side effect; fail closed |
| Goal / Work Package | Frozen scope and identity; invalid contracts cannot escape into completion |
| Completion / evidence | Missing or failed required evidence cannot produce success |
| Budget | Retries are charged; parent/child charging is correct; exhaustion stops work |
| Evaluator firewall | Request, plan and Memory cannot leak into evaluator input |
| Skills | Required skills remain pushed into workers/stages |
| Storage and resume | Attempts are append-only; resume preserves identity and durable state |
| Spawn audit | Spawn and completion are persisted on all supported paths |
| Collector | Effective input bounds include tool schemas; preparation does not mutate source input |
| Tool execution | Arguments, error propagation and transport results retain semantics |

Architecture checks complement these tests; an AST import check cannot establish
that unauthorized tool calls never execute or that evaluator input is clean.

## WP-T0 — Establish a reproducible inventory

Scope: test collection, existing CI command, executor dependencies.

1. Capture revision, Python version, dependency versions and exact commands.
2. Collect actual pytest node IDs, including parameterized cases. Record both
   test-function count and collected-case count; do not compare unlike totals.
3. Run the existing suite with `--durations=20` and record skips, deselections,
   failures and reasons. Keep the CI job's current selection visible.
4. Run the excluded passing-subprocess case independently in a correctly
   provisioned environment. Inspect `execution/executor.py` interpreter and cwd
   handling. Reproduce the problem before changing code or CI.
5. Record slow groups and concentrations of private-function patch targets.
   Timing is diagnostic, not a hard performance claim from one machine/run.

Deliverable: a concise baseline section in the test review document, plus raw
JUnit/timing artifacts in CI when available. If mypy cannot be installed, report
that gate blocked and continue read-only inventory; never reinterpret it as pass.

Verifier: collection is reproducible; every excluded case has a concrete reason.
Rollback: inventory changes only.

## WP-T1 — Map behavior ownership and deletion candidates

Depends on T0. Start with the component refactor's touched tests; expand only
when an overlap is identified.

Create `docs/testing/test-review.md` with one row per candidate:
`behavior | current node IDs | setup boundary | distinct failure caught |
keep/merge/delete/move | retained node ID | rationale | validation evidence`.
This is a focused review record, not a manually maintained catalog of all tests.

Priority inspection:

- `test_agent_components.py` versus `test_context.py`, `test_agent_loop.py`
  and `test_agent_budget.py`: context bounds, markup and retry usage.
- `test_mechanical_gate.py`, `test_spawn_pipeline.py`, `test_stage_loop.py`:
  duplicated setup versus genuinely different engine/worker behavior.
- `test_work_package_contracts.py`, `test_goal_state.py`: maintain the distinction
  between pure validation and enforcement through the running engine.
- `test_executor.py`: preserve independent subprocess success/error coverage.

Verifier: each proposed removal names the retained failure detector. If the
mapping is uncertain, keep the test and mark the question; do not delete by guess.
Rollback: documentation only.

## WP-T2 — Simplify the newly added component tests first

Depends on T1. Keep changes small and reviewable.

| Current test | Proposed treatment | Condition |
| --- | --- | --- |
| Context identity | Keep distinct Collector contract coverage | Correct name: currently checks tool-schema changes, not policy authority; test message changes too only if identity is retained as a contract |
| Oversized tool schema | Compare with context and loop tests | Delete only if another test exercises the Collector wiring and the same rejected input |
| Decider markup/proposal | Keep or merge with existing response regression | Preserve markup removal and tool proposal preservation; remove weak `hasattr(verified)` assertion |
| Thinker retry usage | Compare effort and budget regressions | Keep one focused aggregation detector plus integration evidence that budget is actually charged |
| Six import cases | Keep one parameterized architecture rule | Preserve per-module diagnostics; no cosmetic case-count reduction |
| Compatibility aliases | Retain only during migration | Remove together with retired supported aliases, after consumers are migrated |

The assertion that a response lacks a `verified` field is not proof that a goal
cannot be completed illegally. That guarantee belongs to existing contract and
execution tests. Do not replace it with another shape-only assertion.

Verifier: affected tests pass and each retained test fails when its protected
behavior is intentionally broken in an isolated local check. Use only a few
representative faults where deletion would otherwise be uncertain; do not add
broad mutation infrastructure. Restore every temporary fault before committing.
Rollback: revert the bounded test-cleanup commit; production behavior unchanged.

## WP-T3 — Reduce fixture and mocking coupling

Depends on T2. Work on one test family at a time.

1. Identify repeated fake LMRouter/MCP/session setup with equivalent semantics.
2. Reuse a small fixture within that family; move to `conftest.py` only when
   multiple modules need exactly the same contract. No global autouse mocks.
3. Prefer injecting a fake external dependency through existing parameters over
   patching `agent.run` private aliases. Patch where the dependency is looked up
   if injection is not available; do not claim all monkeypatching is bad.
4. Assert outcomes: dispatched action, files, records, budget and terminal state.
   Assert exact call order/count only when it protects protocol, cost or safety.
5. Parameterize equivalent input partitions without combining distinct behavioral
   scenarios into one long test. Preserve readable case IDs and local fixtures.
6. Do not add production abstractions merely to reduce patch lines. If a missing
   seam reveals a real design issue, schedule it in the component refactor with
   its own behavior verification.

Verifier: each family passes independently and with the suite, without network
or live model credentials. Retained success and failure cases still exercise the
real boundary under test; fake only dependencies outside that boundary.
Rollback: family-level commits avoid an all-or-nothing fixture migration.

## WP-T4 — Resolve test environment and CI gaps

Depends on T0 and T3.

- Ensure pytest, ruff and mypy are provisioned as declared by CI. Do not hide
  missing tools with blanket skips or convert integration checks to fake success.
- Re-enable the passing subprocess test if its environment issue is fixed and
  verified. If an actual executor defect is found, use a separate fix commit.
- Keep the complete existing Python PR gate. Targeted local runs improve feedback;
  they do not replace full-suite acceptance after deletions.
- Record durations and selection in CI output/artifacts. Do not split jobs or
  create smoke/nightly tiers until measured runtime demonstrates a need.
- Do not modify unrelated Rust/Console gates or turn blocking checks non-blocking.

Verifier: full Python CI passes with all required dependencies; intentional skips
are enumerated. Existing repository gates remain unchanged and pass on CI.
Rollback: restore previous CI configuration independently of test cleanup.

## WP-T5 — Review result and close migration work

Depends on T2–T4.

Publish before/after evidence in `docs/testing/test-review.md`:

- functions and collected cases, with explanation of parameterization changes;
- scenarios removed and their retained detectors;
- shared fixture changes and private patch sites removed in touched families;
- full-suite results, exclusions and elapsed time on comparable environments;
- open risks, temporary alias tests and explicit removal triggers.

Completion criteria:

1. Every deleted test has an approved-by-review rationale and retained coverage
   mapping, or documented retirement of the behavior.
2. No new broad skip/deselection or weaker assertion conceals a regression.
3. Critical behavior families above retain meaningful tests at their boundaries.
4. Touched families require less duplicated setup or fewer patches to internal
   implementation. If cleanup produces no benefit, retain the original tests.
5. Complete Python CI passes; repository-required Rust/Console checks remain green.
6. Roadmap and component-refactor plan record what changed and what remains.

No minimum deletion percentage or promised speedup. A smaller suite that misses
contract violations is a failed outcome.

## Delivery sequence

Use separate reviewable commits on the ongoing refactor branch:

1. `docs(testing): inventory test behavior and overlap` — T0/T1 evidence.
2. `test(agent): simplify component regression coverage` — T2.
3. `test(agent): share boundary fixtures in touched suites` — bounded T3 slices.
4. `ci(test): restore verified executor integration coverage` — T4 if warranted.
5. `docs(testing): record suite simplification results` — T5.

This plan authorizes no arbitrary test deletion. Implementation decisions follow
from the evidence collected in T0/T1. No live Jev calls or HoH implementation is
required for this test cleanup.
