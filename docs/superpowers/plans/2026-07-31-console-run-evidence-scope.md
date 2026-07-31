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

## Verification

`1762 passed` (pytest), `190 pass` (node --test), `82 passed` (cargo test).
Each new console test was confirmed to fail against the pre-change code.
