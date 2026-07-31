# Root-owned planning and model-owned dispatch

## Context

The direct agent currently splits one decision across three places:

1. Root decides whether it needs planning.
2. A separate `planner` worker reads the workspace and emits a plan plus
   manifest.
3. `assess_manifest` converts file counts and flags into a route and worker
   ceiling.

That split produced the observed weather-site failure:

- the creation request named no existing path, so the evidence gate refused a
  coder and required a read-only worker;
- Explorer and Investigator both searched for a target although creation did
  not require an existing target;
- Root called `recommend_skills` repeatedly because a recommendation had no
  durable identity;
- the default three-worker ceiling was spent on Explorer, Investigator, and
  Coder before recovery;
- the task could become `large` only after a Planner manifest, but Root never
  chose Planner, so the capacity needed to complete the task could never be
  granted.

The design assumption was that code could infer a useful route from evidence
and manifest arithmetic. It cannot: evidence can prove that a path exists and
a manifest can bound a declared radius, but neither can decide whether the
user's goal deserves Direct or Planning mode. That is model judgment.

## Goal

Make Root the single decision maker and planning owner for direct sessions.
The model chooses Direct or Planning and chooses every worker skill. Musubi
validates declarations and enforces boundaries without classifying user text.

This change affects model-routed direct sessions only. User-invoked pipelines
remain deterministic recipes; their `planner` stage is retained for backward
compatibility until the pipeline-dissolution track removes it separately.

## Decisions

### 1. One Root, two modes

Root starts `undecided` and makes exactly one explicit mode transition:

- `musubi_begin_direct(target_intent, target_path, worker_role)`
- `musubi_begin_plan(deliverable)`

`--plan` forces the Planning transition. Without it, the model decides.
Musubi does not scan the user's sentence for size, intent, or planning
keywords.

### 2. Direct mode carries a structured target declaration

`target_intent` is `create` or `modify`.

- `create`: `target_path` must resolve inside a granted workspace root; it may
  be absent because absence is the point of creation.
- `modify`: `target_path` must resolve inside a granted root and exist.

The declaration replaces the rule that every pathless request must pay for an
Explorer. The model infers the target; the harness checks only the path fact.
Explorer remains available when Root genuinely needs a broad workspace survey.

### 3. Root plans with bounded read-only tools

After `musubi_begin_plan`, Root receives Read/Grep/Glob tools. It reads only
what changes the plan, then commits both artifacts through:

`musubi_commit_plan(plan_markdown, change_manifest, change_size, worker_chain)`

The driver validates and persists:

- `.musubi/goals/<conversation-key>/plan.md`
- `.musubi/goals/<conversation-key>/manifest.json`

There is no direct-session Planner spawn and no Planner summary handoff. Root
owns the user goal, the reads, the assumptions, and the worker chain in one
context.

### 4. Model declares size and chain; harness bounds them

`change_size` is `small`, `medium`, or `large`. It is the model's decision.
`worker_chain` is an ordered list drawn from:

- `designer`
- `coder`
- `reviewer`
- `investigator`
- `explorer`
- `reviewer-aux`

The harness does not derive `large` from file counts. It validates role
membership, requires at least one implementation role for a work plan, and
enforces declared order.

Root worker capacity becomes:

`workers already used + declared chain length + one recovery slot`

bounded by a hard safety ceiling of eight. This removes the circular dependency
where a large task needed more workers before it could earn a large-worker
allowance.

### 5. Manifest governs radius, not routing

The manifest remains the machine-checkable declaration used for:

- file-radius overrun detection;
- sensitive-change audit;
- blocking decisions;
- validation expectations.

`assess_manifest` no longer decides the direct route or worker ceiling.
Manifest parsing accepts a compact core and supplies safe defaults:

- required: `files_expected`, `subsystems`;
- default `false`: the five sensitive flags;
- default `[]`: `blocking_decisions`;
- default `0`: `validation_commands`;
- unknown keys still fail closed.

This reduces formatting burden without letting unchecked fields enter the
governance channel.

### 6. Explorer and Investigator have non-overlapping meanings

- Explorer answers a bounded workspace-location or reference question using
  read/search tools.
- Investigator runs a named diagnostic command and reports its result.

Only Explorer and Finder outcomes establish target-location evidence.
Investigator success does not open the mutation gate because a passing `dir`,
test, or linter command does not prove which artifact the user intended.

### 7. Skill selection remains model-owned

`musubi_recommend_skills` ranks allowed candidates but does not choose one. It
returns a `recommendation_id` containing the candidate set.

Root passes both:

- `recommendation_id`
- its chosen `pushed_skill_id`, or no skill when no candidate fits

to `musubi_spawn_subagent`.

The spawn boundary validates that the ticket belongs to the worker role and
that the selected skill was one of its candidates. Once a ticket is open for
the next role, the driver withholds `recommend_skills` until that ticket is
consumed, preventing the same flow from calling the recommender repeatedly.

## State machine

| State | Allowed next action | Root tools |
|---|---|---|
| `undecided` | final answer, `begin_direct`, `begin_plan` | mode tools only |
| `direct` | recommend skill, spawn declared worker | recommender + spawn |
| `planning` | bounded reads, `commit_plan` | Read/Grep/Glob + commit |
| `planned` | recommend skill, execute declared chain | recommender + spawn |
| `done` | final answer | none |

A mode transition is one-way. A plan can be recommitted only after an explicit
manifest-overrun stop, replacing the prior artifacts atomically.

## Tool contracts

### `musubi_begin_direct`

Input:

```json
{
  "target_intent": "create",
  "target_path": "gui/weather.html",
  "worker_role": "coder"
}
```

Output records the normalized target and whether the path currently exists.

### `musubi_begin_plan`

Input:

```json
{"deliverable": "Weather website for Ho Chi Minh City"}
```

Output opens the bounded read-only planning surface.

### `musubi_commit_plan`

Input:

```json
{
  "plan_markdown": "# Deliverable\n...",
  "change_manifest": {
    "files_expected": 3,
    "subsystems": ["gui"]
  },
  "change_size": "large",
  "worker_chain": ["designer", "coder", "reviewer"]
}
```

Output records persisted artifact paths, normalized manifest values, the legal
next role, and the bounded worker ceiling.

## Acceptance criteria

1. A pathless creation request can declare a safe new target and spawn Coder
   without Explorer or Investigator.
2. A modify declaration for a missing path fails closed.
3. Direct Root cannot spawn Planner.
4. Planning Root can read, persist `plan.md` plus `manifest.json`, and then
   execute its declared chain.
5. `change_size=large` is accepted as a model declaration; no file-count rule
   is required to grant the declared chain capacity.
6. Investigator outcomes never satisfy target evidence.
7. Skill recommendation returns a ticket; spawn rejects a skill outside its
   ticket.
8. A pending recommendation hides the recommender until spawn consumes it.
9. Root worker capacity always includes one recovery slot and never exceeds
   eight.
10. Existing explicit pipelines continue to run unchanged.

## Validation

- Goal-state unit tests for every state transition and invalid transition.
- Manifest parser tests for defaults, unknown fields, and wrong types.
- Agent-loop tests for create-without-explorer, forced `/plan`, chain order,
  capacity, and recommendation reuse.
- Server/policy tests for the three new tools and skill-ticket validation.
- Existing Python, Rust console-core, and Console JS suites.

## Migration and removal

- Remove `planner` from the direct Root spawn allowlist and prompt.
- Keep `.github/agents/workers/planner.agent.md` and pipeline policy only for
  explicit pipelines.
- Stop recording the old free-text `[triage]` declaration for new direct turns;
  mode and target declarations are the auditable record. Preserve the database
  column for historical rows.
- Keep `assess_manifest` as a compatibility helper for stored tests/pipeline
  callers during this PR; direct orchestration stops calling it. Delete it when
  no caller remains.

## Out of scope

- Provider choice inside generated weather applications.
- Replacing deterministic user-invoked pipelines.
- Allowing Root to write implementation files.
- Letting code choose a skill, a mode, a target, or a change size.
