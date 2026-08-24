# Runtime Convergence Repair Design

**Status:** Design approved; awaiting written-spec review

**Date:** 2026-08-02

## Decision

Repair the failed pipeline/direct run as two sequential implementation tracks
on `fix/console-run-evidence-scope`:

1. **Pipeline runtime integrity (P0):** make stage input sizing predictable,
   make every spawned worker terminate exactly once, and make the substrate's
   terminal status authoritative.
2. **Root planning convergence (P1):** expose the planning contract as a typed
   model-visible schema, bound repeated contract failures, and show their
   results in Request Log.

The tracks share evidence from one incident but have separate change surfaces
and verification gates. They will therefore receive separate implementation
plans and commits. The P0 track lands first because it repairs Hard Invariant
#8 and prevents an open worker row after a pipeline abort.

## Incident Evidence

The observed pipeline request reached planner and designer, then failed before
the coder's first model call:

- planner output: 4,232 characters;
- designer output: 5,644 characters;
- coder role prompt + selected `web-ui` skill + stage brief: 18,041
  characters;
- eight coder tool schemas raised the complete serialized input to 26,615
  characters;
- `PIPELINE_CONTEXT_BUDGET` was 16,000 characters.

The failure was deterministic. `pipeline_runner.py::_stage_brief` placed the
request and every prior stage output into the middle-stage brief. The child
prompt builder then combined the full role, full selected skill, and that
brief into one first user message. `context.py::fit_model_input` correctly
protects that first message, so there was nothing eligible to compress before
it raised `ContextBudgetExceededError`.

The same failure exposed two lifecycle defects:

- coder handle `626af83cb29b` had a durable spawn event but no completion
  event;
- pipeline session `c297e869a003` finalized as `aborted`, while its code-stage
  attempt remained `worker_running`.

The preceding designer also finished at its four-turn cap. The completion
tool recorded `final_status=escalated`, but the runner ignored that response,
persisted the stage as passed, and continued. The assumption that the status
submitted by the driver was the status accepted by the substrate was false.

The separate direct request failed for a different reason. It spent 188,778
of 200,000 tokens (94.4%) over 16 cycles without spawning a worker or writing
a file. Nine `musubi_commit_plan` calls occurred: eight failed and the ninth
succeeded too late to leave useful execution budget. Seven failures used
invalid `change_manifest` shapes because the model saw only
`dict[str, Any]`; another used `planner` in `worker_chain`, even though the
accepted ordered-role set excludes it. The existing no-progress breaker did
not fire because it requires a failed or escalated worker, and this loop never
reached a worker.

## Goals

- Start the coder's first cycle for the reproduced feature pipeline without
  exceeding a stage input limit.
- Keep full prior outputs in the append-only store while forwarding only the
  immediate passed predecessor to the next stage.
- Make the per-stage hard input limit model-aware, token-oriented, and
  inclusive of system content, skill content, tool definitions, the task, and
  reserved output capacity.
- Guarantee that every accepted spawn produces exactly one terminal worker
  state, one durable completion audit obligation, and one terminal stage
  transition.
- Prevent a substrate-coerced `escalated` worker from becoming a passed stage.
- Let a read-only worker that finishes exactly at its turn cap complete when
  its non-empty structured/text result passes the declared verifier.
- Give the root model the exact plan and manifest contract before it calls the
  tool.
- Bound repeated pre-worker planning failures and expose their reasons to the
  operator.

## Non-goals

- Increasing pipeline stage count, worker depth, helper width, or total run
  token budget.
- Sending all historical stage output to each successor.
- Letting the harness select a skill. The model selects; the harness lists,
  validates, injects, and audits.
- Adding an LLM call to the substrate.
- Replacing the deterministic stage acceptance design.
- Automatically retrying paid external-model smoke runs.
- Refactoring unrelated ephemeral agent topology.

## Per-stage Hard Context Policy

### Best-practice unit and boundary

A model consumes tokens, not characters. The durable contract is therefore an
input-token ceiling at the LM-call boundary. Serialized characters remain a
compatibility estimator until a router exposes an exact tokenizer; the safety
margin, rather than the estimator itself, covers estimation error.

The hard ceiling includes everything sent on the wire:

- role/system instructions;
- the exact model-selected skill body;
- host and output-contract scaffolding;
- request and permitted predecessor handoff;
- accumulated conversation/tool results;
- tool names, descriptions, and JSON schemas.

Tool schemas are not free metadata. They consumed about 8,574 characters in
the failed coder call and must be reserved before message fitting.

### Two ceilings, not one magic number

Each stage call uses the smaller of:

1. an **operational stage ceiling**, initially 8,000 estimated input tokens;
2. the **model-safe input ceiling**, derived from the selected profile:

```text
model_safe_input =
    context_window_tokens
    - resolved_max_output_tokens
    - transport_margin_tokens

hard_stage_input =
    min(8_000, floor(model_safe_input * 0.80))
```

`transport_margin_tokens` defaults to 1,024. The additional 20% headroom
absorbs estimator error and provider-added protocol framing. A profile that
declares no context window uses the 8,000-token compatibility default and logs
that the model-safe ceiling could not be independently verified.
With the current four-characters-per-token estimator this is a 32,000-character
fallback, replacing the undersized 16,000-character constant.

The run-level token allowance remains separate. It limits cumulative spend
across calls; the stage input ceiling limits the size of one call. Neither is
derived from the other.

### Fixed-floor preflight

Before a stage worker starts, the driver calculates and records:

```text
fixed_floor = role + selected_skill + host_scaffold + output_contract + tools
protected_payload = request_or_firewalled_artifact + predecessor_output
initial_input = fixed_floor + protected_payload
```

- If `fixed_floor > hard_stage_input`, the configuration is impossible. Fail
  before spawn with a component-size breakdown; do not truncate the selected
  skill or role contract.
- If `initial_input > hard_stage_input`, fail before spawn with a handoff
  overflow. The producer's output ceiling or the tool surface must be fixed;
  the consumer must not receive silent truncation.
- On later cycles, reversible compression and tool-result stubbing may reclaim
  working-history space, but the role, selected skill, task, and accepted
  predecessor handoff remain protected.

### Bounded handoff

- Stage 0 receives the request.
- A middle stage receives the request plus only the latest passed output of
  its immediate predecessor.
- The final evaluator receives only the prior artifact/output allowed by the
  evaluator firewall.
- Full outputs and failed attempts remain in the append-only store and Request
  Log. Context omission does not delete evidence.
- Planner and designer receive a 2,048 output-token ceiling and their accepted
  handoff text must serialize to at most 8,000 characters. An oversized result
  is an explicit stage-output failure, not a downstream context failure.

This policy is deliberately conservative. A larger model context does not make
an orchestration stage more useful when the additional input is mostly copied
history. The default can be raised only from measured truncation/overflow data,
not merely because a provider advertises a larger window.

## Track 1: Pipeline Runtime Integrity

### Context flow

`_stage_brief` stops joining every summary. It receives an explicit
`predecessor_output` so its contract cannot accidentally regress to cumulative
history. The append-only stage store remains the source for historical output;
the prompt is a bounded projection of that store.

The resolved `PipelineWorkerSpec` owns the operational input cap. Profile
metadata can reduce the effective cap for a smaller model but cannot silently
widen it beyond the stage ceiling.

### Worker lifecycle

After `musubi_spawn_subagent` returns a handle, the runner enters an owned
lifecycle region. Every exit from that region invokes one idempotent terminal
operation with:

- handle ID, request, pipeline stage, and attempt identity;
- driver-reported status and turns;
- verified summary or sanitized failure reason;
- failure kind when applicable.

The terminal operation performs or schedules three linked facts:

1. transition the sub-session to one terminal state;
2. persist a `worker_complete` audit obligation in the existing durable
   outbox, then deliver it to `audit.db`;
3. transition the stage attempt from `worker_running` to a terminal phase.

Completion delivery becomes the same durable-obligation pattern already used
for spawn evidence. An audit database outage may leave a pending outbox item,
but it may not erase the obligation or allow the runner to claim an unaudited
success. Relay accepts both spawn and completion kinds. The audit database
enforces one completion event per handle, so duplicate cleanup or relay calls
are idempotent and cannot create a second completion row.

### Authoritative completion result

The JSON returned by `musubi_complete_subagent` is authoritative:

- `final_status=done` permits the runner to continue to its deterministic
  stage gate;
- `failed`, `escalated`, or `abandoned` transitions the attempt accordingly
  and stops or retries according to the existing frozen contract;
- `status=error`, malformed JSON, or missing `final_status` fails closed.

The runner never derives stage success from its submitted status after the
completion call. This removes the observed `worker=escalated / stage=passed`
split-brain state.

### Exact-turn-cap semantics

Mutation workers retain the current rule: a `done` result at the cap requires
the substrate to verify non-empty surviving files.

A read-only worker cannot provide a file manifest by design. At exactly the
turn cap it may be accepted only when all of the following are true:

- the driver submitted `done`;
- the result is non-empty;
- any declared output schema validates;
- summary verification passes;
- the worker used no mutation tool and the role policy is read-only.

The completion audit records `accepted_at_turn_cap=true` and the verifier used.
Wall-clock expiry is never waived. Empty, malformed, unverified, or mutating
results still coerce to `escalated`.

### Failure behavior

- Context floor or handoff overflow: no model call; if no handle exists, abort
  the attempt before spawn; if a handle already exists, complete it failed.
- Vendor, budget, policy, or unexpected runtime error after spawn: terminalize
  the worker and attempt exactly once before finalizing/pausing the pipeline.
- Completion audit delivery failure: retain a pending outbox obligation and
  block successful pipeline finalization until it is durable.
- Stage checkpoint failure after worker completion: keep the completion
  evidence and mark the pipeline incomplete; never reopen the worker.

## Track 2: Root Planning Convergence

### Typed model-visible contract

`musubi_commit_plan` exposes a closed JSON schema instead of
`change_manifest: dict[str, Any]`. The schema is generated from the same field
definitions used by `parse_change_manifest_object`, including required
`files_expected` and `subsystems`, optional risk/validation fields, bounds,
and `additionalProperties: false`.

`worker_chain` is a non-empty array whose items are an enum of the ordered
worker roles. `planner` is absent because Root owns planning. The schema also
requires at least one mutation role.

Runtime validation remains fail-closed even when a provider does not honor
tool schemas. Schema generation and parser validation must share constants so
the model-visible and enforcement contracts cannot drift.

### Correction protocol and breaker

An invalid plan call returns a machine-readable result:

```json
{
  "status": "error",
  "error_kind": "invalid_change_manifest",
  "message": "files_expected is required",
  "expected_schema": {
    "required": ["files_expected", "subsystems"],
    "additionalProperties": false
  },
  "allowed_roles": ["designer", "coder", "reviewer"]
}
```

The root goal state counts consecutive planning-contract failures:

- after the first failure, return the typed correction and keep the normal
  planning surface;
- after the second, expose only the correction/commit operation needed to
  submit a valid plan;
- after the third, stop with an incomplete result that reports the final
  validation reason and consumed budget.

A valid plan resets the consecutive counter. Unrelated tool errors do not
count as planning-contract failures.

The no-progress breaker also observes a pre-worker root control loop. High
budget use plus repeated failed control operations is sufficient to stop even
when no worker has existed. Productive file delivery still prevents the
breaker from misclassifying progress.

### Request Log

Control-tool results are appended as sanitized request-scoped runtime events.
The operator sees tool name, success/error status, `error_kind`, and a bounded
reason. Full schemas and raw model arguments are not repeated in every log
line. These events belong to Request Log; Agent Log remains filtered to the
selected worker handle.

## Verification Gates

### Track 1

- Reproduce the 26,615-character coder input against the old 16,000-character
  cap and prove the regression fixture fails before the repair.
- Prove the same fixture starts coder cycle 0 under the token-oriented bounded
  handoff without deleting stored plan/design output.
- Prove planner/designer output over the handoff cap fails at the producer.
- Inject context, vendor, budget, policy, completion-audit, and checkpoint
  failures after spawn; assert one terminal sub-session, one completion audit
  obligation, and one terminal attempt transition per handle.
- Prove a completion coerced to `escalated` cannot become a passed stage.
- Prove exact-turn-cap read-only verified output completes, while empty,
  malformed, mutating, and wall-clock-expired variants escalate.

### Track 2

- Assert the published tool schema contains every accepted manifest field,
  rejects unknown fields, and exposes only permitted worker roles.
- Reproduce the observed invalid manifest and `planner` worker-chain calls;
  verify their typed correction responses.
- Reproduce eight invalid plan attempts and prove the root stops after three,
  before exhausting the 200,000-token budget.
- Prove a valid second/third attempt resets the failure counter and permits the
  declared worker chain.
- Prove pre-worker no-progress detection does not fire after artifact delivery.
- Prove Request Log contains bounded control results and Agent Log does not
  misattribute them to a worker.

### Repository gate

Run the focused tests first, then the full Python suite, Ruff, and
`git diff --check`. A paid DeepSeek smoke run is optional and requires explicit
operator authorization after deterministic verification passes.

## Rollout and Compatibility

1. Land Track 1 and migrate the character constant to a token-oriented stage
   contract with a 32,000-character compatibility fallback.
2. Verify existing profiles without `context_window_tokens` retain deterministic
   behavior through the conservative fallback.
3. Land Track 2 without changing the accepted persisted manifest format.
4. Expose new failure/status fields as additive log and audit data.
5. Run a paid external-model smoke test only when explicitly requested.

Historical stage outputs, audit rows, and plan artifacts remain readable.
No migration overwrites prior attempts or completion evidence.

## Hard Invariants

- **HI #1 preserved.** Budget calculation, schema validation, lifecycle, and
  logging remain deterministic substrate operations. Only the driver calls a
  model through `LMRouter`.
- **HI #2 preserved.** The model selects a skill; the harness only validates,
  injects, and proves receipt/use requirements.
- **HI #3 preserved.** The evaluator still receives only its permitted
  artifact and rubric.
- **HI #5 strengthened.** Impossible context, invalid plan contracts, unknown
  roles, and audit failures stop closed.
- **HI #7 preserved.** Full prior outputs and every attempt remain append-only;
  bounded handoff is a prompt projection, not data deletion.
- **HI #8 strengthened.** Both spawn and completion use durable audit
  obligations; every accepted spawn has exactly one terminal lifecycle.
- **HI #9 preserved.** Prompt and pipeline scaffolding remain tagged
  ephemeral. The context-budget boundary, typed contract validation, and
  audit outbox are substrate controls and do not expire with a model upgrade.

## Lifecycle

The immediate-predecessor handoff and per-role prompt/output scaffolding remain
part of the existing ephemeral pipeline runtime. They expire with
`agent/pipeline_runner.py` when models orchestrate multi-step pipelines
natively.

The following controls are substrate and do not expire:

- LM-boundary input/output reservation;
- closed model-visible tool schemas plus runtime validation;
- durable worker completion obligations;
- request-scoped error evidence.

The three-failure correction breaker is ephemeral. It expires when production
evidence over the latest 500 root planning requests shows fewer than 1% require
more than one correction and no request requires three, with a Wilson 95%
upper confidence bound below 2%. Removing it deletes the forced-tool phase and
counter branches while retaining the typed schema and one fail-closed error.
