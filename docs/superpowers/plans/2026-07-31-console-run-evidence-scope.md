# Console run evidence: scope, attribution, and what a token figure means

Date: 2026-07-31

## Context

Three operator reports from one Console session (`a96e979d`, 4 root turns, 18
audited cycles, 107,660 tokens):

1. *"the total token is not correct for the list of task and token in/out in
   the conversation"* — the turn rows in the timeline and the "This session"
   ledger describe the same spend with different arithmetic.
2. *"Still can't access to external folder"* — a folder was attached to the
   session (`bamf-updater`), and the driver answered *"I can't access that path
   from here; it's outside the current workspace (outside_workspace)"* and told
   the user to run `Remove-Item` by hand in PowerShell.
3. *"There's no skills were used, also for the policy"* — the conversation
   panel read **"No successful skill calls recorded"** and the per-agent log
   filtered to Skills (and to Policy) read **"No matching log lines for this
   scope"**, on a session that summoned a Coder for 8 turns.

Each is a separate defect. None is the model failing; all three are the design
guaranteeing the outcome.

## Goal

Every number and every badge the Console shows about a run is (a) derived from
one ledger, (b) attributable to the exact turn and worker that produced it, and
(c) scoped to the conversation rather than to its most recent turn.

## Tech stack

Python substrate (`musubi/agent`, `musubi/server.py`, `musubi/validation`,
`musubi/storage`), Rust reader (`gui/src-tauri/musubi-data`), React view model
(`gui/src/model/viewModel.js`).

## Root causes and steps

### 1. External folder — the prompt contradicted itself, loudest first

`agent/run.py` builds the root system prompt from three blocks in order: the
routing hint, `evidence.prompt_block()`, then `registry.prompt_block()`.

`agent/evidence.py::_classify_paths` measured containment against
`tools.fs._workspace_root()` — the `musubi` root alone. It knew nothing about
`workspace/grants.py`, so every path under an attached folder resolved as an
escape and rendered as:

    outside_workspace=C:\…\bamf-updater (no worker can reach these; say so and stop)

…immediately above a roots listing that named the same folder as available.
Two further consequences followed from the same miss:
`goal_state.target_named` was set from `names_workspace_path`, so
`GoalState.evidence_gap` also refused to let a mutation worker be summoned at
the folder; and the refusal's only implied next step was "do it yourself",
which hides the Add folder control the product already has.

**Step:** `collect()` takes the request's full grant list; containment is
tested against every granted root, primary first. A path under an attached
folder is named `<alias>/<rest>` and the new `named_root_aliases` field says
which first segments are aliases, so the prompt can state that the alias is the
tool's `root=` argument rather than part of the filename. Relative tokens still
resolve against the primary root. The escape line is renamed
`outside_granted_roots=` and names the remedy.

### 2. Tokens — one figure contains the other, and they were printed as peers

`agent/subagent.py` passes the parent's `AgentRunStats` into every worker it
runs (`stats=stats`), so `agent_turns.tokens_*_estimate` is a **subtree** total.
The Console printed that on the turn row and each worker's own cycle total on
the row beneath it, with no stated relationship: `T02 90,025 tok` above
`Coder 75,696 tok` invites 165,721, a quantity that does not exist.

A second, harder defect sat under it: `musubi-data` loaded turns with
`ORDER BY id ASC LIMIT 120` — the **oldest** 120 rows in the whole database.
Past 120 turns the Console stops seeing new ones entirely: recent sessions
render as "no agent activity yet", their timelines lose rows, and their token
ledger silently under-reports while the cycle-derived economics keeps counting.

**Steps:** the turn window becomes `ORDER BY id DESC LIMIT 120`, reversed back
into ascending order (the shape `agent_cycles` already used). Turn tokens are
derived from `agent_cycles` scoped to the turn's parent session, with the
`agent_turns` estimate as fallback, so *turn total = its own cycles + its
workers'* holds by construction and session total = Σ turn totals with nothing
counted twice. The turn node gains `ownTokens` and `tokensAreInclusive`, and
the overview labels the two figures `Tokens · turn total` and
`Tokens · root only`.

### 3. Skills and policy — three independent silences

**a. A role-default push was never recorded.** HI #2 makes the push
non-opt-out-able: `build_subagent_context` resolves
`pushed_skill_id or SUBAGENT_ROLE_SKILLS[role]`. But
`musubi_spawn_subagent` audited only `skill_choice`, the root's *override* —
which requires a `musubi_recommend_skills` ticket and is therefore rare. Every
default push landed in `subagent_audit.pushed_skill_id` as NULL, and the
Console has no other source for it. `musubi_get_subagent_context` also returned
only `role_skill` (prose), so no layer downstream could even name the skill.

**b. A pushed skill emits nothing at runtime.** It is baked into the worker's
system prompt by `build_subagent_system_prompt`; there is no tool call, so the
runtime ledger never sees it. The only `category="skills"` line ever written
came from a successful `musubi_get_skill` — a *pull*, which the push exists to
make unnecessary.

**c. ALLOW verdicts were recorded but never emitted.** Only denials reached the
ledger, so a Policy-filtered log read empty on every clean run — the gate
proving itself is exactly what that filter is opened for. `policy_audit` cannot
substitute: it carries no session or request column and the Console reads only
its most recent rows.

**d. The Console threw away every non-ledger source.** `viewModel.js` replaced
`runtimeLogs` with the ledger projection outright when a session had request
identity, discarding the tool ledger, the policy verdicts, the per-cycle model
rows, and the pushed skills. And `skillsByWorker` was built from
`activeSessionAgents` — the agents of the **latest** root turn — so a session
whose last turn spawned nothing reported zero workers and zero skills however
much the earlier turns did.

**Steps:** `SubagentContext` gains `role_skill_id`; the spawn tool audits the
effective push; `agent/subagent.py` emits a `category="skills"` record inside
the worker's runtime scope; the dispatcher emits the ALLOW verdict; the view
model merges derived rows into the request projection (attributed by handle,
then by parent session, then to the newest turn) and widens skill/evidence
scope from the latest turn to the whole conversation.

## Hard Invariants touched

- **HI #2** (skills are pushed): unchanged in behaviour; the push is now
  nameable and auditable rather than silent.
- **HI #3** (evaluator firewall): `role_skill_id` is public catalog metadata
  about a payload the worker already holds, never parent state. The two
  closed-set pins (`context_keys`, `test_subagent_firewall_g1`) were updated
  deliberately, with the reason recorded in each.
- **HI #8** (no silent sub-agents): strengthened — a spawn's pushed skill was
  a silent part of the spawn.

## Follow-up: a wrong argument must not be a terminal policy failure

A later run died like this:

    [agent]   policy denied musubi_spawn_subagent: Skill '7eb54567802b6738d489'
              is not permitted for worker role 'coder'.
    [agent] usage cycles=4 … in_tokens=7604 out_tokens=4779
    [incomplete] policy denied for role 'agent' while calling
              'musubi_spawn_subagent': …

`7eb54567802b6738d489` is twenty hex characters — the shape of a
`recommendation_id` (`sha256(...)[:20]`, `server.py::musubi_recommend_skills`).
The root put the ticket id in the skill field. The instruction it was following
made that a reasonable misreading: *"pass its `recommendation_id` and your
chosen candidate as `pushed_skill_id`"* places the two ids adjacent with the
choice buried between them.

**The design assumption that broke.** `PolicyDeniedError` is documented as
*"Terminal policy control flow"* and it is right for what it was built for: a
role reaching for a capability it does not have. Retrying cannot make the
caller a different role, so the run ends. `evaluate_argument_policy` then
reused that same terminal channel for **argument validation** — and
`pushed_skill_id` is an *optional* field on a call whose role, target role, and
tool intersection were all authorised. One wrong string in a slot the model
could have left empty ended the turn 4 cycles and 12,383 tokens in.

The asymmetry proving the point: the same rule reached through the MCP server
(`server.py:1628`) returns `{"status":"error","error_kind":"policy_denied",
"allowed_skills":[…]}` — recoverable, with the legal values attached. The
driver's preflight raised instead. Same rule, two verdicts, harsher one first.

Two further defects surfaced while fixing it:

- `refused_reason` was honoured only *inside* the spawn-with-orchestration
  branch of `_dispatch_one`. That held while the only refusals were the width
  and role-order caps, which cannot fire with orchestration off. Argument
  refusals fire in any configuration, and on that path a refused spawn fell
  through to the MCP server — the call the harness had just declined to make
  was made anyway.
- `Orchestration.open_recommendation_skills` has held the exact shortlist the
  ranker offered since recommendations shipped, and nothing ever read it.

**Steps:** `PolicyDecision` gains `recoverable`; the two argument-shaped checks
(`allowed_tools` intersection, `pushed_skill_id`) set it, while
`check_subagent_allowed` stays terminal. `_preflight_policy_batch` returns
`{tool_use_id: reason}` for recoverable denials into the existing per-call
refusal channel — so a sound sibling in the same batch still runs — and raises
only for authorization. Both are still recorded to `policy_audit` as denials:
the split governs what the caller may do next, never what the ledger says.
The refusal message names the recommendation-id swap explicitly, lists the
role's permitted skills, adds this turn's actual candidates from
`open_recommendation_skills`, and points out that omitting the field is legal.
`refused_reason` moves ahead of every branch in `_dispatch_one`. The
instruction in `agent/context.py` now states that the two ids are different
arguments and must never carry the same value.

## Follow-up: one name for the depth-0 driver

The same actor answered to three names, and one call site used two of them for
itself in adjacent lines (`agent/run.py`, launching the driver):

```python
role="agent",            # → policy_audit.role, every authorization key
audit_worker_id="root",  # → agent_cycles.worker_id
audit_stage="agent",
```

`agent` owned authorization (`policy_engine`, `AGENT_SKILL_ALLOWLIST`,
`_ROOT_AGENT_TOOLS`), `root` owned runtime scope (`runtime_log.py`,
`agent_cycles.worker_id`), and `driver` owned console prose. Nothing was
broken by it, but two presentation layers already paid a translation tax in
hard-coded string lists — `lib.rs` (`role == "agent" || role == "driver"`) and
`viewModel.js` (`['agent', 'driver'].includes(...)`) — neither covered by a
test that would fail if a fourth spelling appeared. The catalog file said it
best: `.github/agents/root/agent.agent.md`.

**`root` is canonical now.** The rename touches the fail-closed authorization
key (HI #5), so it is done through one definition and one normalizer rather
than a search-and-replace:

- `policy_engine.ROOT_ROLE` / `ROOT_ROLE_ALIASES` / `normalize_role()` are the
  single source of truth; `boundary.py`, `context_builder.py` and `server.py`
  import them rather than re-deriving.
- Every membership, capability and skill-allowlist lookup folds through
  `normalize_role`, so an `agent` string read back out of an append-only
  ledger still resolves to the one rekeyed entry. **Nothing rewrites history**
  — old rows keep saying `agent` and still join.
- **`driver` is deliberately not an alias.** It never carried the root's
  membership, so aliasing it would grant it the whole spawn firewall — a
  fail-open change. `normalize_role("driver") == "driver"`, and it is denied.
- `.github/agents/root/agent.agent.md` → `root.agent.md`, with the legacy
  filename kept as a fallback candidate: `spawn_allowlist:` frontmatter is
  authoritative when present, so missing it would silently drop a user's own
  declared firewall back to the constant.
- `call_role` is canonicalised before it is written, because `policy_audit`
  folded through `evaluate_tool_call` and `tool_audit` did not — the two
  ledgers disagreed about the same call.
- Both readers now share one predicate (`is_root_actor`) covering all three
  spellings, so a pre-rename verdict still joins to the root node.

The renaming sweep caught its own regression, which is the argument for doing
it this way: `_normalize_root_spawn_tool_uses` guards on `role != "agent"` —
the negated form escaped the first pass, the root's `allowed_tools` strip
stopped firing, and a coder spawn started intersecting to the empty set. A
test failure, not a production one.

Out of scope, and left alone deliberately: `--tool-surface agent` names a
*tool-surface preset* that contrasts with `operator` and `pipeline`, not an
actor, and `musubi_append_failure_pattern(source="agent")` is a free-text
provenance label that no role table ever reads.

## Follow-up: the ranker scored the conversation, not the request

Reported: *"why does it choose web-ui for the changing language of
application"*. The log agrees — `skill pushed=web-ui agent=coder` on a turn
whose request was to change the display language.

`recommend_skills` concatenated the request, the `context_summary` and the
tools into ONE bag of text and scored it as a unit
(`skills/recommender.py:38`). Nothing distinguished "what is being asked now"
from "what this conversation has been about". Reproduced exactly:

| input | web-ui score | result |
|---|---|---|
| request alone | 0 | *no skill matches* |
| request + 272-char context | **200** (html, css, dashboard, chart, responsive) | `web-ui` @ **0.99** |

The root asked which skill fitted, was told `web-ui` with near-certainty, and
pushed it into a coder that was there to change some strings. It behaved
correctly on the answer it was given.

Two compounding faults. First, weight: on turn 3 the context was ~50× the
request, so history outvoted the ask, and the longer the conversation ran the
more confidently wrong the ranker became. Second, `confidence = min(0.99,
score/100)` **saturated** — five context hits reached the cap, so the number
carried no information about whether the request matched at all.

**Step:** the request elects, the conversation only breaks ties. A skill needs
a signal from the request (or from the tools this turn actually used) to be a
candidate; context is then worth a quarter weight as a tiebreaker and its
matches are labelled `(from conversation context)` in `reasons`; `confidence`
is computed from the request score alone. After: "change the language" → no
recommendation, "make the dashboard responsive" → `web-ui` @ 0.80, "add a
chart" → `web-ui` @ 0.40, "fix the failing pytest traceback" → `debugging`,
`testing`. No test had ever exercised `context_summary` — which is how this
shipped.

## Follow-up: the ranker is deleted; the model chooses

Weighting the request over the context made the ranker less wrong. It did not
make it entitled. Scoring text to decide what a request is ABOUT is a
judgement, and this repository already took the position that code does not
make it — `agent/scope.py`'s own docstring records the deletion of
`assess_request` and nineteen regexes for exactly that reason, and the
`request-triage` skill states it outright: *"The harness makes no judgment
about how large or how risky a change is. It cannot."* The recommender was the
same mechanism wearing a confidence score, and its `expires-when: never —
skill selection is catalog routing, not model logic` was falsified by the
first trace that hit it.

**Deleted:** `musubi/skills/recommender.py`, `musubi_recommend_skills`, the
`_SKILL_RECOMMENDATION_TICKETS` store, the `recommendation_id` argument, the
`Orchestration.open_recommendation_*` state, the driver-side ticket
enforcement, and the `recommendation_pending` tool-withholding gate.
**831 lines removed against 226 added.**

**Replaced by:** `musubi_list_skills(agent_name, for_role=…)`, which returns
each permitted skill's `skill_id`, `title` and the one-line `description` its
SKILL.md already declared and the catalog never exposed. The harness lists
facts; the model chooses. The root instruction now says to judge the listing
against what the user is asking for *now*, and states plainly that pushing
nothing is the right answer when nothing fits.

**The ticket went with it.** It required `pushed_skill_id` to appear in a
ranked ticket, which constrained WHERE the root got a name and never WHICH
names are legal — the allowlist and catalog checks answer that independently
and still do, fail-closed. It was also the direct cause of a lost turn: two
adjacent string arguments, one of which is a 20-hex ticket id, and the model
put the wrong one in the wrong slot.

**No confidence anywhere.** The listing carries `skill_id`, `title`,
`description` — no score, no ranking, no ordering that implies one. Selection
is the model's alone, so there is nothing for a number to express: a score is
the harness stating an opinion about a request it is not entitled to have one
about. (Unrelated and untouched: the `confidence: high|medium|low` field a
*worker* self-reports in its structured output, `validation/verifier.py:73`.
That is a worker describing its own result, not the harness ranking a
request.)

**Pipelines run the skill their recipe declares.** A pipeline is the
compliance path — its procedure is written down before the run, not chosen
during it. `pipeline.yaml` has carried `generator.agents[].skill` and
`evaluator.skill` since feature-dev shipped; the standalone runner never read
them, and asked the ranker instead. `composer.declared_stage_skill` is the
flat per-role lookup that was missing (`injected_skill_ids` answers a
different question — which skill accompanies an agent when it READS a stage,
gated on `_prior_stage`). `_prepared_stage_skill` resolves, most specific
first:

1. the recipe's declaration, intersected with the role's allowlist — a recipe
   declares, it never widens (HI #3), and a dropped declaration is logged
   because a silently ignored compliance statement is worse than a missing one;
2. the role's native push (`SUBAGENT_ROLE_SKILLS`, HI #2), left to
   `build_subagent_context` to resolve;
3. neither — reported on the policy channel so the gap is auditable.

Every stage of both shipped pipelines now runs a prepared skill, where the
ranker had supplied one for a single role out of seven:

| stage | ranker | now | source |
|---|---|---|---|
| feature-dev planner | — | `request-triage` | role default |
| feature-dev designer | — | `api-design` | recipe |
| feature-dev coder | — | `python` | recipe |
| feature-dev reviewer | `code-review` | `code-review` | recipe |
| code-review scoper | — | `pr-scope-detection` | recipe |
| code-review finder | — | `per-file-review` | recipe |
| code-review synthesizer | — | `code-review` | recipe |

`dev-lite` composes from presets, which declare no skill, and its `coder`
stage has no role default because a coder's skill is task-dependent. That gap
is now named on the policy channel rather than passing silently as a ranker
`None`.

Kept, because it judges the PROJECT rather than the request:
`skill_router.applicable_skills` still hides a Python skill in a Rust repo.

## Follow-up: the recipe is the compliance artifact, so it must survive a save

Two defects found while answering "what does a recipe look like", both proved
by running the round-trip rather than reading it.

**The Studio rewrote a declared recipe into a shape that cannot hold its
declarations.** `.github/pipelines/*/pipeline.yaml` comes in two shapes: the
declared one (`generator:` / `evaluator:`, with a per-agent `skill:`) that both
shipped recipes use, and the flat `stages:` one composed from presets that the
Pipeline Studio reads and writes. `PipelineStageRecipe` models four fields —
`preset`, `agent`, `stage`, `spawns` — and the renderer emits exactly those.
Measured on feature-dev, open-then-save produced:

    BEFORE  skill: null · skills/api-design · skills/python · skills/code-review
    AFTER   (none)

and turned the canonical stage names `plan` / `code` / `review` into
`planner` / `coder` / `reviewer`, because the loader falls back to the role
name when `stage:` is absent and does not replicate composer's mapping. Top
level keys survive through `extras`; stage-level ones have no such route.

Those declarations are what `composer.declared_stage_skill` reads to decide
each stage's procedure — the pipeline's compliance statement. A round-trip
nobody would think to check turned a governed recipe into an ungoverned one.
`save_pipeline_recipe` now refuses rather than truncates: a recipe already
written in the declared shape may not be overwritten from the Studio's model.
Saving under a new name still works, and the flat shape still round-trips.

**A constant-keyed dict fell out of the scraped firewall.** `read_spawn_firewall`
models the fail-closed spawn allowlist by text-scraping
`scripts/policy_engine.py` for `MAIN_SUBAGENT_ALLOWLIST`. Its key detector only
recognised string literals, so when the depth-0 rename made the first key the
`ROOT_ROLE` constant, the root's whole entry vanished from the map. Nothing
looked it up — pipeline stages are workers, never the root — so no validation
changed, which is precisely why it would have sat there. The scraper resolves
module-level `NAME = "literal"` bindings now, and a test pins that the root
entry is present with its spawn list.

## Verification

`1766 passed` (pytest), `191 pass` (node --test), `85 passed` (cargo test).
Each new test was confirmed to fail against the pre-change code. The two
existing terminal-denial pins — an unauthorised spawn role, and a root
reaching for `musubi_write_file` — still pass unchanged, which is what says
the recoverable split did not soften authorization.
