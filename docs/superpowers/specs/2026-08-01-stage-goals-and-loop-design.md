# Stage Goals and the Deterministic Stage Loop Design

**Status:** Proposed

**Date:** 2026-08-01

## Goal

Make a pipeline stage finish because its declared acceptance contract passed,
not merely because a worker returned text.

The model owns meaning: it selects the skill and translates the current task
into a structured acceptance contract. The harness owns enforcement: it
validates and freezes that declaration, runs deterministic checks, persists
each attempt, and stops or retries within a hard bound.

This design does not ask substrate code to interpret prose and does not add an
LLM call to the substrate. Model preflight and worker execution remain in the
standalone driver through `LMRouter`.

## Current Failure

The standalone pipeline runner currently walks the stage plan once. A stage is
treated as done when `run_unit` returns non-`None` text. The runner completes
the worker, appends its summary in memory, and advances. It does not act on a
reviewer's domain verdict, and it does not run the recipe's `correction:`
policy.

Four existing mechanisms are not sufficient to add a loop by themselves:

1. `stage_outputs` is seeded for the fixed legacy stages `plan`, `design`,
   `code`, and `review`; user-defined stages such as `scope`, `findings`, and
   `synthesis` cannot use it through `session/state.py`.
2. `(session_id, stage, chunk_id, attempt)` is an application convention, not
   a database uniqueness constraint, and attempt increment is not one atomic
   read-insert transition.
3. Driver-internal `_call_tool_text` calls the MCP session directly. Calling
   `musubi_run_command` through it would not automatically apply the model tool
   dispatcher policy and audit path.
4. `subagent_audit` records worker lifecycle status, not a stage gate verdict,
   and current audit failures can still be swallowed. A gate cannot claim
   durable attribution until Hard Invariant #8 is strengthened.

The shipped `correction:` blocks are therefore declarations without a current
standalone execution path. This design replaces their intended retry behavior;
it does not revive a second correction loop around the stage loop.

## Ownership

| Concern | Owner |
|---|---|
| Stage order, role ceiling, allowed helpers, allowed check vocabulary, iteration and credit caps | Pipeline recipe |
| Skill selection, stage goal, task-specific acceptance predicates, helper use | Model |
| Catalog listing, validation, skill injection, predicate execution, retry bound, persistence, audit | Harness |
| Pipeline launch and any named command definitions | Operator |

The ownership boundary is strict:

- The harness never selects, ranks, substitutes, defaults, or silently drops a
  skill.
- The model never widens a role allowlist, checker vocabulary, command
  allowlist, iteration cap, or tool surface.
- A retry may select a different permitted skill, but it cannot weaken or
  replace the frozen acceptance contract.
- A pipeline recipe may constrain choices; it does not make a runtime skill
  choice for the model.

## Pipeline Recipe Contract

The recipe declares ceilings and named deterministic commands. It does not
contain a request-specific goal.

```yaml
name: feature-dev
version: 2.0.0

checks:
  project-tests:
    type: command
    argv: [npm, test]
    timeout_seconds: 120

stages:
  - stage: plan
    agent: planner
    max_iterations: 1

  - stage: design
    agent: designer
    max_iterations: 1

  - stage: code
    agent: coder
    spawns: [explorer, investigator]
    allowed_checks:
      - file_exists
      - file_created_or_modified
      - dom_count
      - dom_distinct_text
      - dom_text_set
      - lint_clean
      - named_command
    allowed_commands: [project-tests]
    max_iterations: 3

  - stage: review
    agent: reviewer
    spawns: [reviewer-aux]
    max_iterations: 1
```

Rules:

- `max_iterations` defaults to `1` and is bounded to `1..3` in the first
  release.
- `max_iterations > 1` requires a non-empty `allowed_checks` list.
- `allowed_checks` is a ceiling. The model chooses task-specific predicates
  from that vocabulary during preflight.
- A command is operator-authored, named, and stored in the recipe. The model
  may reference its ID but may not supply a shell string or arbitrary argv.
- Unknown fields, checker types, command IDs, duplicate stage names, invalid
  roles, and out-of-range caps fail recipe validation.
- Legacy `generator:` / `evaluator:` recipes remain readable during migration,
  but Pipeline Studio must not rewrite a recipe until it can represent every
  governed stage field losslessly.

## Model Preflight Contract

Every pipeline stage attempt begins with a bounded driver-side model preflight.
The preflight sees:

- the stage role and brief;
- the role's permitted skill catalog entries as ID, title, and description;
- the recipe's allowed check types and named command IDs;
- the frozen contract and failure evidence when this is a retry.

It has no mutation tools. It returns strict JSON.

For a stage with no `allowed_checks`, including the final evaluator, preflight
returns only `skill_id`; `goal` and `exit_when` are absent. For an opt-in stage,
the first attempt returns all three fields shown below. This distinction keeps
the evaluator from receiving task-specific goal context under HI #3.

### First attempt

```json
{
  "skill_id": "web-ui",
  "goal": "Render exactly five distinct cities in the weather table",
  "exit_when": [
    {
      "type": "file_created_or_modified",
      "root": "musubi",
      "path": "index.html"
    },
    {
      "type": "dom_count",
      "root": "musubi",
      "path": "index.html",
      "selector": "[data-testid='weather-row']",
      "equals": 5
    },
    {
      "type": "dom_distinct_text",
      "root": "musubi",
      "path": "index.html",
      "selector": "[data-testid='city-name']",
      "equals": 5
    },
    {
      "type": "named_command",
      "command_id": "project-tests"
    }
  ]
}
```

### Retry

```json
{
  "skill_id": "debugging",
  "contract_hash": "sha256:4e6d44ad218ca80924f7c6f71d884df60b34f0332a8a01efc09e987a01a5ef29"
}
```

The first valid acceptance contract is canonical for that stage. The harness
serializes it canonically, stores it, and records its SHA-256 hash before the
worker may mutate files. A retry must echo the same hash. It may select another
permitted skill but cannot submit new predicates. A replacement skill is valid
only when its observable completion requirements are already satisfied by the
frozen contract; otherwise the model must retain or choose another skill.

Preflight validation is fail-closed:

- missing, unknown, or disallowed skill: reject and allow one bounded
  correction response;
- missing goal when predicates are declared: reject;
- `max_iterations > 1` with an empty `exit_when`: reject;
- predicate outside the recipe vocabulary: reject;
- command ID outside `allowed_commands`: reject;
- invalid root, escaping path, invalid selector, non-positive timeout, or
  malformed comparison: reject;
- a second invalid preflight: escalate before spawning a worker.

The preflight is a driver model call and is recorded as an agent cycle. It is
not a substrate LLM call. Its cost is charged to the pipeline credit and token
budgets.

## Worker Brief and Skill Enforcement

After preflight, the driver spawns the stage lead with:

- the role prompt;
- the exact selected skill body;
- the selected skill ID, catalog version, and content hash;
- the frozen goal and acceptance predicates;
- the accepted output of prior stages allowed by the context firewall;
- bounded failure evidence from the prior attempt, if any;
- the recipe- and policy-intersected tool and helper surface.

For an evaluator stage, the brief omits the task-specific goal, predicates,
request, plan, and design. It contains only the artifact being judged and the
fixed rubric carried by the selected review skill.

The harness validates the model's skill choice against the role allowlist and
catalog before spawn. Missing content, a changed catalog entry between
validation and load, or an invalid selection blocks spawn. There is no
`SUBAGENT_ROLE_SKILLS` runtime fallback and no invalid-choice-to-`None` path.

For nested helpers, the lead model chooses each helper's skill through the same
explicit `pushed_skill_id` contract. The server validates it; it never fills in
a role default.

Each spawn audit carries the selected skill ID, version, and content hash. A
completion can be accepted only after the skill was successfully loaded into
the worker prompt and its declared mechanical completion requirements, if any,
have been evaluated.

### Observable skill-use contract

Prompt injection proves receipt, not semantic obedience. The harness therefore
uses the word `enforced` only for observable requirements declared by the
selected skill. A skill that can be selected for a pipeline stage declares a
machine-readable completion block in its frontmatter:

```yaml
completion-contract:
  required-output-fields: [summary, files_modified]
  required-check-types: [file_created_or_modified]
```

The harness loads this block from the exact selected skill version and merges
its requirements into preflight validation before hashing the stage contract:

- every `required-output-field` must be present in the validated worker result;
- every `required-check-type` must appear in the frozen acceptance predicates
  and pass;
- requirements can narrow completion but cannot add tools, helpers, commands,
  roots, or predicates outside the recipe and policy ceilings;
- an impossible merge is a preflight configuration error, not a reason to
  weaken the skill contract;
- the audit records the requirement set and its verdict beside the skill hash.

Role and evaluator skills whose work is text-only use output fields or an
existing structured output schema rather than inventing a file check. Helper
skills use their worker completion verifier. A prose-only skill may still be
selected and injected during migration, but the Console labels it `received`,
not `mechanically enforced`, until it declares an observable completion
contract. Shipped pipeline-selectable skills must gain this metadata before the
stage loop is enabled for them.

## Deterministic Acceptance Contract

`goal` is prose for the model. The harness stores and displays it but never
parses, scores, matches, or interprets it.

`exit_when` is a conjunction: every predicate must pass. The harness executes
all applicable deterministic predicates to return complete failure evidence.
An infrastructure error is not a failed worker check and is never a pass; it
escalates as `gate_error`.

### Initial predicate vocabulary

| Type | Required fields | Pass condition |
|---|---|---|
| `file_exists` | `root`, `path` | The granted-root path exists, is a regular file, and is non-empty |
| `file_created_or_modified` | `root`, `path` | The file is non-empty and its fingerprint differs from the stage-start snapshot |
| `dom_count` | `root`, `path`, `selector`, `equals` | Static HTML contains exactly the requested number of matching elements |
| `dom_distinct_text` | `root`, `path`, `selector`, `equals` | Matching elements contain exactly the requested number of distinct non-empty text values |
| `dom_text_set` | `root`, `path`, `selector`, `equals` | Normalized matching text equals the declared set, without missing or extra values |
| `lint_clean` | `paths`, optional `allow_skipped` | The governed linter reports pass; skipped passes only when explicitly allowed |
| `named_command` | `command_id` | The operator-declared command exits zero within its timeout |

`paths: changed` means the cumulative artifact manifest for the entire stage,
not only files touched by the latest attempt. Files deleted during the stage
remain in the attempt record but are not linted as surviving artifacts.

Static DOM checks do not execute JavaScript. A dynamic application must use an
operator-declared test command, such as a Playwright suite, until a separate
governed browser-check vocabulary is designed.

### Named command execution

Named commands run through a dedicated gate dispatcher, not raw
`_call_tool_text`. The dispatcher must:

1. resolve `root` and `cwd` through the immutable request root registry;
2. evaluate the stage role's tool and argument policy;
3. apply the same blast-radius and command constraints as a model tool call;
4. execute the exact operator-authored argv without shell interpolation;
5. cap runtime and captured output;
6. write policy and tool audit rows with the stage and attempt identity;
7. record filesystem changes made by the command in the attempt manifest.

A policy denial, missing command, timeout, transport failure, or audit failure
is `gate_error`. It does not consume a worker retry as if the worker produced a
bad artifact.

## Stage State Machine

```text
pending
  -> preflight_running
  -> contract_frozen
  -> worker_running
  -> worker_complete
  -> gate_running
  -> passed
     | retryable_failed -> next attempt pending
     | gate_error       -> escalated
     | exhausted        -> escalated
```

Per attempt:

1. run and validate model preflight;
2. freeze or verify the acceptance contract;
3. spawn the lead worker with the selected skill;
4. allow model-chosen helpers within `spawns:` intersected with the firewall;
5. persist the worker output and cumulative artifact manifest once;
6. run the deterministic gate;
7. persist the gate result and append transition events;
8. advance to the next stage on pass;
9. create a new attempt with bounded failure evidence on a check failure;
10. escalate and stop the pipeline on infrastructure error or exhaustion.

The next stage sees only the latest passed output of its predecessor. Failed
attempts remain queryable but never flow downstream.

## Generic Attempt Storage

The existing stage store is generalized rather than duplicated.

### `stage_outputs`

Add the attempt checkpoint fields:

- `phase`
- `contract_json`
- `contract_hash`
- `selected_skill_id`
- `selected_skill_version`
- `selected_skill_hash`
- `worker_handle_id`
- `artifact_manifest_json`
- `gate_result_json`
- `gate_written_at`

Worker output, contract, artifact manifest, and gate result are individually
write-once within an attempt. `phase` advances through compare-and-swap
transitions and cannot move backward.

Create uniqueness enforcement for non-chunked and chunked rows. Because SQLite
`NULL` values do not collide in an ordinary unique index, use separate partial
unique indexes:

```sql
CREATE UNIQUE INDEX uq_stage_outputs_without_chunk
ON stage_outputs(session_id, stage, attempt)
WHERE chunk_id IS NULL;

CREATE UNIQUE INDEX uq_stage_outputs_with_chunk
ON stage_outputs(session_id, stage, chunk_id, attempt)
WHERE chunk_id IS NOT NULL;
```

Session creation seeds the stages returned by the validated composer plan, not
the legacy `STAGES` constant. Reads of historical sessions remain compatible.
Attempt increment becomes one immediate transaction that reads the current
attempt, inserts the next row, and fails on a stale concurrent writer.

### `stage_attempt_events`

Add an append-only event ledger:

```sql
CREATE TABLE stage_attempt_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL NOT NULL,
    session_id      TEXT NOT NULL,
    stage           TEXT NOT NULL,
    chunk_id        TEXT,
    attempt         INTEGER NOT NULL,
    event           TEXT NOT NULL,
    worker_handle_id TEXT,
    contract_hash   TEXT,
    detail_json     TEXT NOT NULL
);
```

Events include preflight acceptance/rejection, contract freeze, worker start
and completion, individual check results, gate verdict, retry creation, and
escalation. State transition and its event append occur in one `musubi.db`
transaction.

The audit relay track remains responsible for making spawn and completion
evidence durable in `audit.db`. The Console can reconstruct stage-loop history
from `musubi.db` even while an audit relay is pending, but a new worker may not
start until its spawn audit obligation is durable.

## Crash and Resume Semantics

Resume reads the latest attempt phase:

| Phase | Resume action |
|---|---|
| `pending` | Run preflight |
| `preflight_running` | Mark interrupted preflight and rerun it within the same attempt |
| `contract_frozen` | Spawn the worker |
| `worker_running` | Reconcile the handle; await it if live, otherwise record interruption and retry within the bound |
| `worker_complete` | Run the gate without rerunning the worker |
| `gate_running` | Rerun idempotent checks and append a resumed-gate event |
| `retryable_failed` | Atomically create the next attempt |
| `passed` | Advance to the next incomplete stage |
| `gate_error`, `exhausted`, `escalated` | Keep the pipeline stopped |

Gate predicates are idempotent observations except named commands. A named
command event records a stable execution ID. Resume never executes the same
command ID twice for one attempt after a durable result exists.

## Reviewer Semantics in V1

There is no `reviewer` predicate in the initial `exit_when` vocabulary.

The final reviewer remains a normal evaluator stage under Hard Invariant #3.
It sees only the artifact it judges and the fixed review rubric in its skill;
it does not receive the original request, plan, design, model-authored goal, or
acceptance provenance. Deterministic predicates establish whether the artifact
meets the declared mechanical contract.

The runner strictly parses and validates the reviewer's structured status:

- `pass`: the pipeline may finalize successfully;
- `fail`, `wrong_plan`, or `escalate`: stop and escalate with the reviewer
  evidence;
- malformed or missing status: fail closed and escalate.

V1 does not automatically route a reviewer failure back to a coder. Such a
route would be a separate model-owned correction design; rerunning the reviewer
cannot fix code, while a harness-selected prior stage would reintroduce routing
judgement.

If a future feature needs an LLM to decide whether a task-specific goal was
met, it requires an explicit Hard Invariant #3 discussion. The viable narrow
change would expose an immutable acceptance contract plus the artifact, never
the request, plan, design, or memory. That change is outside this design.

## Failure Feedback

Check failures are fed to the next preflight and worker as data, not raw prompt
instructions. The payload contains checker type, expected value, observed
value, and a bounded diagnostic preview.

Rules:

- maximum 8 KiB total feedback per attempt;
- command stdout/stderr is truncated and marked untrusted;
- no raw terminal control sequences;
- no command output may alter the frozen goal, contract, tool surface, or
  system prompt;
- complete output remains retrievable by audit reference when compression
  stores one;
- secrets scanning runs before persistence and prompt injection.

## Budget and Cost

One stage attempt costs:

- one bounded model preflight;
- one lead worker;
- zero or more helpers within the existing spawn ceiling;
- zero model calls for deterministic checks.

An invalid first preflight consumes one additional bounded correction call.
Credit reservation includes that permitted correction path.

A three-attempt code stage therefore costs at least six model calls, not three.
The worst case additionally includes every helper the lead may summon. Pipeline
credit validation must reserve the worst permitted stage cost before opt-in;
an insufficient recipe budget fails validation rather than halting midway by
construction.

The existing per-worker turn cap still bounds each worker. The root
no-progress breaker does not bound the stage loop because every retry is a new
worker, so `max_iterations` and the pipeline credit budget are the load-bearing
cross-attempt limits.

## Pipeline Studio

Pipeline Studio must model and round-trip:

- `max_iterations`;
- `allowed_checks`;
- `allowed_commands`;
- top-level named check definitions;
- existing legacy skill declarations during migration, without executing them
  as runtime choices.

The run-evidence view adds:

- stage goal and contract hash;
- attempt number and phase;
- selected skill per attempt;
- lead and helper handles;
- per-check result;
- retry reason;
- final pass, exhaustion, gate error, or reviewer escalation.

Request Log contains the merged stage and gate history. Selecting a worker
continues to show only that worker's log; gate events belong to the request and
stage, not to an individual agent log.

## Compatibility and Migration

1. Extend the Rust and JavaScript recipe models before allowing Studio to save
   the new fields.
2. Generalize stage seeding and add attempt uniqueness/checkpoint columns with
   idempotent migrations.
3. Add the append-only stage attempt event ledger.
4. Add model preflight and explicit skill selection for pipeline stages.
5. Add deterministic checkers and the governed named-command dispatcher.
6. Add the loop and crash-resume transitions.
7. Make the final reviewer verdict terminal and actionable.
8. Project attempt and gate evidence into Console.
9. Keep legacy `skill:` and `correction:` fields readable and losslessly
   round-trippable for one compatibility release, but do not execute them as
   runtime selection or a second retry loop.
10. Remove the legacy fields after the compatibility release and after every
    shipped recipe has migrated.

A recipe with no opt-in stage-loop fields still has `max_iterations = 1` and
no deterministic retry. Model-selected pipeline skill preflight is a separate
behavioral correction required by Hard Invariant #2 and therefore is not
claimed to be byte-identical with the current recipe-selected/default path.

## Error Handling

- Invalid recipe: reject at load/save; do not list it as runnable.
- Invalid preflight: allow one bounded correction, then escalate without a
  worker spawn.
- Missing or changed skill content: deny spawn.
- Check failure: retry only when attempts remain.
- Checker infrastructure error: escalate as `gate_error`; do not blame or
  rerun the worker automatically.
- Policy denial or audit failure: fail closed.
- Reviewer malformed/fail/escalate verdict: stop the pipeline.
- Credit reservation failure: reject before the first opt-in attempt.
- Crash: resume from the persisted phase without overwriting output or
  duplicating a completed named command.

## Test Strategy

### Recipe and Studio

- parse, validate, render, and round-trip every new field;
- reject unknown checks, command IDs, roles, helpers, and invalid iteration
  caps;
- preserve legacy recipe fields during migration;
- refuse a save that would drop a governed declaration.

### Model preflight and skills

- model-selected permitted skill is injected and audited;
- missing, unknown, disallowed, or stale skill fails closed;
- harness never supplies a role default;
- invalid pipeline skill is never silently converted to `None`;
- selected skill completion requirements are merged into the frozen contract;
- missing required output fields or failed required checks reject completion;
- prose-only migration skills are labeled `received`, never falsely reported
  as mechanically enforced;
- retry may change skill while the contract hash remains fixed;
- two invalid preflights escalate without spawning a worker.

### Deterministic gate

- every predicate passes and fails on constructed artifacts;
- empty or pre-existing artifacts cannot satisfy created/modified predicates;
- `lint_clean` does not pass on `skipped` unless opted in;
- `paths: changed` uses the cumulative stage manifest;
- named commands use exact argv, policy, timeout, audit, and root isolation;
- unknown, denied, timed-out, or unaudited commands become `gate_error`;
- all check failures are returned while infrastructure errors remain distinct.

### Storage and resume

- arbitrary composer stage names receive attempt rows;
- duplicate attempts fail at the database boundary;
- concurrent increments create at most one next attempt;
- output, contract, manifest, and gate result are write-once;
- every state transition appends an event in the same transaction;
- crashes at every phase resume at the documented action;
- a durable named-command result is never executed twice;
- failed attempts never flow downstream.

### Runner and budget

- first-attempt pass advances once;
- repeated check failures create exactly `max_iterations` attempts and then
  escalate;
- final reviewer pass succeeds while fail/malformed/escalate stops;
- preflight, lead, and helper calls charge the shared budgets;
- insufficient worst-case credit reservation rejects before execution;
- recipes without loop opt-in run one worker attempt per stage.

### Audit and Console

- contract, skill, worker, check, retry, and terminal events are attributable
  to request, stage, and attempt;
- Request Log contains gate events while Agent Log remains worker-scoped;
- pending audit relay state is visible;
- an audit write failure cannot produce a silently running worker.

## Hard Invariants

- **HI #1 — preserved.** Preflight and workers are driver model calls through
  `LMRouter`; substrate validation and checkers make zero model calls.
- **HI #2 — strengthened.** The model explicitly selects the skill. The
  harness validates, injects, audits, and enforces its observable completion
  contract; it never selects, defaults, substitutes, or drops the choice.
- **HI #3 — preserved.** The final evaluator sees only the artifact and fixed
  review rubric. Task-specific acceptance judgement is deterministic in V1.
- **HI #5 — preserved.** Roles, helpers, tools, checks, and named commands are
  deny-by-default and recipe declarations cannot widen policy.
- **HI #7 — strengthened.** Every attempt has unique database identity,
  write-once evidence, and append-only transition events.
- **HI #8 — prerequisite and strengthened.** A worker may not start without a
  durable spawn audit obligation; no gate or worker lifecycle event is silently
  lost.
- **HI #9 — explicit lifecycle.** Deterministic acceptance contracts, checkers,
  and the model-selection enforcement boundary are durable. Automatic retry
  and the extra preflight-call adapter are separately removable ephemera.

## Lifecycle

Use three lifecycle entries rather than tagging the whole feature as
ephemeral:

### `stage-acceptance-gate`

- tier: substrate
- expires when: never
- reason: deterministic verification, frozen claims, and attributable evidence
  remain valuable as models improve

### `automatic-stage-retry`

- tier: ephemeral
- expires when: over the latest 500 eligible production stage attempts, at
  least 95% pass their frozen deterministic acceptance contract on attempt 1,
  the Wilson 95% lower confidence bound is at least 93%, and no P0/P1 incident
  in that window was prevented only by an automatic retry
- cost lever: removes retry preflight calls, repeat workers, cross-attempt
  feedback scaffolding, and resume branches while retaining one deterministic
  acceptance gate

### `pipeline-stage-preflight-adapter`

- tier: ephemeral
- expires when: the worker runtime can require the model to select and load one
  permitted skill before any work tool is enabled, without a separate model
  call and without allowing the harness to choose or default the skill
- cost lever: removes one model call per stage attempt while retaining the same
  model-owned choice, allowlist validation, injected skill content, and audit
  evidence

The lifecycle registry and its evidence query are implemented by the separate
Hard Invariant #9 enforcement track. Until that registry lands, the source
metadata and roadmap entry must carry the same measurable trigger.

## Out of Scope

- LLM reviewer predicates over a task-specific goal.
- Automatically routing a failed reviewer back to a prior stage.
- Parallel lead workers within one stage attempt.
- Arbitrary model-authored shell commands or executable check code.
- Increasing worker depth, width, turn, token, wall-clock, or credit ceilings.
- Durable audit outbox implementation details beyond the contract this feature
  requires.
- The lifecycle cleanup deleted the dead legacy `session/correction_loop.py`,
  its tests/support path, and the two obsolete meta-agent prompts.
