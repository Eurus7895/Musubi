# PR 157 Governance Remediation Design

## Goal

Close the review gaps in governed request assessment, manifest parsing,
role-owned worker budgets, typed policy failure, and recovery audit evidence
without changing worker/token ceilings or auto-launching a pipeline.

## Design

### Strict change-manifest boundary

`parse_change_manifest()` accepts exactly one literal
`<change_manifest>...</change_manifest>` pair. The stripped JSON payload is
limited to 4,096 UTF-8 bytes and must be an object with exactly the documented
nine keys. Duplicate JSON keys, extra or missing fields, non-finite numbers,
wrong scalar types, non-array collections, non-string array members, and blank
array members fail closed to `None`.

Counts use `type(value) is int`, deliberately rejecting booleans, floats, and
numeric strings. Critical flags use `type(value) is bool`. `subsystems` and
`unknowns` are normalized only after validation by stripping, exact
deduplication, sorting, and tuple conversion.

### Initial critical-risk routing

The request assessment recognizes the safety categories already represented by
the manifest: authentication/access control, payment/billing side effects,
database/migration risk, security, and explicit public API contracts. Singular
and plural forms route identically.

The initial assessment is stored in `GoalState`. An initial
`plan_design_workflow` route returns the deterministic user-invoked
`feature-dev` pipeline recommendation before a parent session, model call, or
worker spawn. The same recommendation remains in force after planner-manifest
reclassification. No pipeline is auto-launched.

### Role-owned direct-worker cap

When role frontmatter declares `maxTurns`, that value remains authoritative.
When it does not declare a valid cap, `run_subagent()` removes any
model-supplied `max_turns` before calling the server, so the server default is
the only owner. Pipeline `PipelineWorkerSpec` remains unchanged.

### Typed policy failure

Policy denial is a terminal control-flow event, not model-visible ordinary
text. A typed `PolicyDeniedError` carries role, tool, and reason.

`_dispatch` preflights every Musubi tool in a batch before launching sibling
coroutines. If any call is denied, the denial and tool audit rows are written,
no sibling is launched, and `PolicyDeniedError` propagates. `_dispatch_one`
keeps its gate as defense in depth without double-auditing.

At the root boundary, the exception becomes one deterministic `[incomplete]`
answer and ends the run without another model call. At a direct-worker
boundary, the handle is completed as `escalated` and the parent receives a
`WorkerOutcome` with `FailureKind.POLICY`; recovery therefore halts without an
automatic replacement. Pipeline stages propagate the same terminal failure to
their existing fail-closed stage boundary.

Spawn/firewall denials returned by the substrate carry a machine-readable
`error_kind: "policy_denied"` so the driver never classifies policy by parsing
prose.

### Recovery audit evidence

An end-to-end test runs the real Musubi MCP server and real `_dispatch` path:
the primary coder writes an artifact and reaches its role cap; the driver
automatically spawns one same-role continuation; the replacement completes;
the root concludes.

The test queries SQLite and proves:

- exactly two coder handles;
- spawn/completion rows for both handles;
- primary `escalated`, replacement `done`;
- replacement brief includes the structured handoff;
- no third worker;
- root has only its actual initial and final LM cycles;
- total `agent_cycles` rows correspond to real router calls, so the
  deterministic transition creates no synthetic LM charge.

## Invariants

- HI #1: all decisions remain deterministic and zero-LLM.
- HI #5: policy is final authority and denial is fail-closed.
- HI #8: recovery uses normal spawn/completion and durable audit paths.
- Worker and token ceilings are unchanged.
- Large workflows remain explicitly user-invoked.
- Pipeline worker-cap ownership is unchanged.

## Test Strategy

Every behavior is implemented RED-GREEN:

1. strict manifest schema, tag counting, byte cap, and critical vocabulary;
2. initial high-risk deterministic halt;
3. absent-frontmatter server-default cap;
4. typed policy denial at batch, root, worker, and recovery boundaries;
5. real recovery audit/economics integration.

Affected suites, tier check, `git diff --check`, and the full Python suite are
run before completion. Environment-only full-suite failures must be reported,
not hidden.
