# PR 157 Governance Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PR 157 fully fail-closed and add durable evidence for its governed recovery claims.

**Architecture:** Validate planner evidence strictly at the parsing boundary, halt initial high-risk requests before model execution, make direct-worker caps role/server owned, propagate policy denial as typed terminal control flow, and exercise recovery through the real audit path.

**Tech Stack:** Python 3.11+, dataclasses, `StrEnum`, `contextvars`, standard-library JSON/SQLite, pytest, existing fake LM routers and real MCP test fixtures.

## Global Constraints

- Preserve HI #1: substrate code and every `musubi_*` tool make zero LLM calls.
- Preserve HI #5: pipeline membership and tool access remain fail-closed.
- Preserve HI #8: automatic continuation uses the normal spawn/completion path and writes `subagent_audit` rows.
- Role frontmatter or the server default is the only owner of a direct worker's turn cap.
- High ambiguity and initial critical-risk routes return before any parent session, model call, or worker spawn.
- Policy denial launches no sibling tool, cannot be converted to model-recoverable text, and never auto-replaces a worker.
- Large workflows remain explicitly user-invoked; no pipeline is auto-launched.
- Root worker/token ceilings and `PipelineWorkerSpec` remain unchanged.

---

### Task 1: Strict manifest validation and initial risk routing

**Files:**
- Modify: `musubi/agent/change_assessment.py`
- Modify: `musubi/agent/goal_state.py`
- Modify: `musubi/agent/run.py`
- Test: `musubi/tests/test_change_assessment.py`
- Test: `musubi/tests/test_agent_scope.py`
- Test: `musubi/tests/test_goal_state.py`
- Test: `musubi/tests/test_agent_loop.py`

**Interfaces:**
- Produces strict `parse_change_manifest(text: str) -> ChangeManifest | None`.
- Stores `ScopeHint.assessment` in `GoalState`.
- Produces a deterministic pipeline recommendation for initial and post-plan large routes.

- [ ] **Step 1: Add failing parser tests**

Cover exact nine keys, duplicate keys, exact JSON types, malformed duplicate
tags, non-finite counts, and UTF-8 byte limits. Each malformed case must return
`None`.

- [ ] **Step 2: Run parser tests and verify RED**

Run:
`python -m pytest musubi/tests/test_change_assessment.py -q`

Expected: new malformed-input cases fail against the coercive parser.

- [ ] **Step 3: Implement strict parser**

Count literal tags, parse the entire payload with duplicate-key and
non-finite-number rejection, validate exact keys/types, then normalize arrays.

- [ ] **Step 4: Add failing routing tests**

Parameterize authentication, permissions, payments, databases, migrations,
security, and public API phrases. Add an agent-loop test proving an initial
critical-risk request returns the pipeline recommendation with zero router
calls and zero worker rows.

- [ ] **Step 5: Run routing tests and verify RED**

Run:
`python -m pytest musubi/tests/test_agent_scope.py musubi/tests/test_goal_state.py musubi/tests/test_agent_loop.py -k "critical or initial_large" -q`

Expected: uncovered vocabulary routes medium/simple and the initial large
request still enters the root model.

- [ ] **Step 6: Implement critical vocabulary and initial halt**

Store the initial assessment in `GoalState`, reuse one pipeline recommendation
formatter, and return it from the deterministic pre-session route.

- [ ] **Step 7: Run Task 1 tests**

Run:
`python -m pytest musubi/tests/test_change_assessment.py musubi/tests/test_agent_scope.py musubi/tests/test_goal_state.py musubi/tests/test_agent_loop.py -q`

Expected: PASS.

### Task 2: Role/server-owned default turn cap

**Files:**
- Modify: `musubi/agent/subagent.py`
- Test: `musubi/tests/test_subagent_orchestrator.py`

**Interfaces:**
- Declared `maxTurns` is forwarded unchanged.
- Without a valid declaration, model `max_turns` is omitted and the server default is used.

- [ ] **Step 1: Add failing undeclared-role test**

Use an agent prompt without `maxTurns`, request `max_turns=1`, and assert the
spawn call omits the field while runtime uses the server-returned default.

- [ ] **Step 2: Run the test and verify RED**

Run:
`python -m pytest musubi/tests/test_subagent_orchestrator.py -k "without_maxturns" -q`

Expected: the current code forwards `max_turns=1`.

- [ ] **Step 3: Remove model ownership when the declaration is absent**

Copy `spawn_args`, remove `max_turns`, and call the server unchanged otherwise.

- [ ] **Step 4: Run direct and pipeline cap suites**

Run:
`python -m pytest musubi/tests/test_subagent_orchestrator.py musubi/tests/test_sub_sessions.py musubi/tests/test_pipeline_yaml.py musubi/tests/test_spawn_pipeline.py -q`

Expected: PASS.

### Task 3: Typed fail-closed policy denial

**Files:**
- Modify: `musubi/agent/run.py`
- Modify: `musubi/agent/subagent.py`
- Modify: `musubi/server.py`
- Test: `musubi/tests/test_agent_loop.py`
- Test: `musubi/tests/test_subagent_orchestrator.py`
- Test: `musubi/tests/test_spawn_pipeline.py`

**Interfaces:**
- Produces `PolicyDeniedError(role: str, tool: str, reason: str)`.
- Substrate policy rejections expose `error_kind: "policy_denied"`.
- Root denial returns deterministic incomplete output; worker denial records `FailureKind.POLICY`.

- [ ] **Step 1: Add failing policy boundary tests**

Cover denied mixed batches launching no siblings, root denial consuming one LM
call, disallowed worker tools producing `POLICY`, no automatic replacement, and
pipeline-stage denial aborting later work.

- [ ] **Step 2: Run policy tests and verify RED**

Run:
`python -m pytest musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_spawn_pipeline.py -k "policy or denied" -q`

Expected: denials are returned as ordinary strings and follow-up responses are consumed.

- [ ] **Step 3: Implement typed policy propagation**

Add batch preflight before coroutine launch, preserve one policy/tool audit
write, raise through broad exception boundaries, convert only at root/stage
ownership boundaries, and complete direct worker handles as escalated POLICY.

- [ ] **Step 4: Run policy and recovery suites**

Run:
`python -m pytest musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_goal_state.py musubi/tests/test_agent_budget.py musubi/tests/test_spawn_pipeline.py -q`

Expected: PASS.

### Task 4: Real recovery audit and economics regression

**Files:**
- Test: `musubi/tests/test_subagent_orchestrator.py`
- Test: `musubi/tests/test_subagent_audit.py`

**Interfaces:**
- Uses real `run_agent`, real MCP spawn/completion, and SQLite audit queries.
- Proves two audited coder handles and no synthetic recovery LM cycle.

- [ ] **Step 1: Add the real-MCP recovery integration test**

Drive primary coder turn-cap, automatic replacement, replacement completion,
and root conclusion through canned router responses without mocking
`_dispatch`.

- [ ] **Step 2: Run the test and verify RED**

Run:
`python -m pytest musubi/tests/test_subagent_orchestrator.py musubi/tests/test_subagent_audit.py -k "automatic_recovery_audit" -q`

Expected: the test initially exposes any fixture/query gap and proves it is not
the old mocked test.

- [ ] **Step 3: Complete only the missing production/audit wiring**

If the real path already persists the required evidence, no production change
is allowed. If a row or field is missing, make the smallest wiring change at
the existing audit boundary.

- [ ] **Step 4: Run affected suites**

Run:
`python -m pytest musubi/tests/test_agent_scope.py musubi/tests/test_change_assessment.py musubi/tests/test_goal_state.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_subagent_audit.py musubi/tests/test_agent_budget.py -q`

Expected: PASS.

### Task 5: Final verification and documentation state

**Files:**
- Modify only if evidence requires correction: `docs/roadmap.md`
- Modify: `docs/superpowers/plans/2026-07-23-pr157-remediation.md`

- [ ] **Step 1: Run deterministic checks**

Run:
`python scripts/check_musubi_tier.py`

Run:
`git diff --check`

Expected: both exit 0.

- [ ] **Step 2: Run the full Python suite**

Run from `musubi/`:
`python -m pytest -q`

Expected: PASS, or report environment-only failures with exact evidence.

- [ ] **Step 3: Review roadmap completion claim**

Keep the track under Completed only if every locked behavior is now implemented
and covered; otherwise move it back to Active.

- [ ] **Step 4: Run whole-branch sub-agent review**

Review `origin/dev...HEAD` for spec compliance, correctness, test quality, and
unintended scope. Fix all Critical/Important findings and re-review.
