# Roadmap - Musubi

> Current direction and live work only. Historical detail lives in git log,
> closed PRs, artifacts, and the audit DB.
> Repo rules -> [`/CLAUDE.md`](../CLAUDE.md).

---

## Discipline

Every PR must move Musubi toward either:

- **Thicker substrate:** queryable audit, sharper invariants, deterministic
  routing, better skill catalog, stronger boundaries.
- **Thinner ephemeral structure:** less pipeline scaffolding, fewer prompt
  preambles, fewer model-limit compensations.

Substrate is anything that still helps when a stronger model lands. Ephemeral
structure exists only to compensate for current model limits and should be
deleted when the limit dissolves.

---

## North Star

Musubi is a governed orchestration substrate: deterministic, zero-LLM
validation at every agent-agent and agent-tool boundary.

The standalone `agent` host is the driver surface; the desktop Console
observes and operates the same substrate through `audit.db`: policy, audit,
skill catalog, compression, memory, and boundary controls. The VS Code
extension was removed — one inject point (`LMRouter`), one prompt catalog.

---

## Current Work

### Active

1. **Session-scoped folder grants — keep Musubi as the fixed harness root.**
   The Musubi checkout/install remains the root for the driver, substrate,
   skills, agents, pipelines, policy, and audit. An Orchestrator session may
   attach multiple existing folders as explicit read/write grants without
   replacing that root, changing a global Setting, or restarting Console.
   Grants remain editable while the session is idle; each request captures an
   immutable snapshot so an in-flight run cannot gain or lose filesystem
   authority. Filesystem tools, command working directories, mechanical gates,
   and artifact verification must select and validate the applicable grant,
   reject path escape or unavailable roots fail-closed, and audit the resolved
   root for every operation. This is a Musubi harness boundary, not a launcher
   for external coding agents; the standalone driver through `LMRouter` remains
   the execution path. The superseded single-workspace plan remains historical.
   Design and plan:
   [`2026-07-29-session-folder-grants-design.md`](./superpowers/specs/2026-07-29-session-folder-grants-design.md) and
   [`2026-07-29-session-folder-grants.md`](./superpowers/plans/2026-07-29-session-folder-grants.md)

2. **Skill catalog growth.** Skills remain the cheapest optimization surface.
   Each new skill should carry useful metadata such as `applies-to`, `triggers`,
   and relevant tools.
   First batch landed: `debugging`, `refactoring`, `git-workflow`, and `web-ui`
   (universal procedures) plus `typescript` (router-gated to JS/TS workspaces).
   Coder gains all five; the dispatcher agent gains only the read-safe pair
   (`debugging`, `git-workflow`) so the generator boundary holds. `web-ui` is
   deliberately universal so an HTML/CSS artifact emitted from a non-JS repo
   still matches it — closing the dashboard case where no catalog skill applied.
   Every prior catalog entry was backfilled with `triggers:` so the recommender
   can rank the whole catalog, not just the newest skills.
   Reachability closed: a direct worker carries no skill tool, so grown catalog
   entries were previously unreachable by workers. The root now selects a skill
   per spawned worker and pushes it (option 3, see Completed track below), so
   catalog growth reaches workers without adding a skill tool to their lean
   surface. Extended to pipeline stages: the deterministic runner recommends a
   skill per stage (`musubi_recommend_skills(for_role=…)`, zero-LLM) and pushes
   it through `musubi_spawn_pipeline_stage` so feature-dev stages
   (designer/coder/reviewer) carry role-appropriate procedure instead of showing
   "no skill evidence"; `planner` has an empty skill allowlist and remains
   skill-less by design.

2. **LLM-owned scope, substrate-owned evidence.** The substrate stops judging
   what a request MEANS and starts proving what the record CONTAINS. Governing
   principle: deciding a turn's triage, scope, or change size is judging — code
   stops doing it; checking a claim or a measurement is enforcing — code keeps
   doing it and does more of it.
   Shipped: the destructive gate (see Completed Tracks) and `agent/evidence.py`
   — six facts per turn (`names_workspace_path`, `path_exists`,
   `has_conversation`, `explorer_findings`, `clarification_answered`,
   `barren_turns`, plus `escaped_paths` for targets outside the workspace root),
   rendered into the root prompt and logged. **It routes nothing yet**, by
   design: the distribution is measured before behavior depends on it.
   Remaining, in order — each depends on the one before, and the deletion lands
   last because it is the only step that changes the cost profile:

   - **Sufficiency rule (plan step 2).** A `coder` spawn is refused while the
     evidence vector reports no named workspace path, no explorer findings, and
     no manifest. This is the enforceable core of "collect enough information
     first", and the same shape as today's role-order gate, which already
     refuses a coder before the planner's manifest lands. Fail-closed; the
     refusal names the legal next role. **First real behaviour change on the
     routing path** — it belongs in its own PR, where that is the only question
     on the table.
   - **Root triage prompt (plan step 3).** Replace the decided route with the
     evidence vector plus overridable hints. The root states its chosen turn
     shape in one logged, audited line, so a wrong triage is attributable
     afterwards instead of invisible.
   - **Delete the lexical judgment (plan step 4).** `classify_task` drops to two
     branches — is there work, and is it destructive. Removes `assess_request`,
     18 of 19 regexes, `_CASUAL_RE`'s zero-token fast path, the pre-run
     `ask_scope` halt, `BROAD_PRODUCT_QUESTION`, `clarification_request`, and
     the `pending_clarification` column: ~551 lines, and the trigger written
     into `agent/scope.py`'s `expires-when:`.
   - **Enforce the declaration (plan step 5).** `manifest_overrun` promoted from
     a prompt warning to a hard stop on the coder path. With scope
     LLM-declared, an under-declared radius is the primary abuse channel and
     must cost the run rather than a paragraph.

   Plan:
   [`2026-07-29-llm-owned-scope-with-evidence-gate.md`](./superpowers/plans/2026-07-29-llm-owned-scope-with-evidence-gate.md)

Runtime limits have one owner per dimension: the bounded runtime track owns
pipeline-stage turn caps, model-input characters, and total stage allowances;
per-worker effort owns output tokens for one LM call; root routing owns
worker-count and continuation-spawn policy. Do not introduce a second parser or
enforcement path for the same dimension.

### Backlog

- **A skipped MCP server should say which one and why.** External servers are
  fail-open by design — one that is misconfigured, missing, or slow is logged
  and skipped, never fatal. But the log line (`agent/mcp_gateway.py:313`) prints
  only the server's name and the exception, which leaves the two questions an
  operator actually has unanswered: *which transport was it* (a stdio `command`
  and an HTTP `url` fail for entirely different reasons), and *how long did it
  wait* — with `timeout_s` defaulting to 30 s, a timeout and an instant refusal
  read identically. In the traced session this produced
  `!mcp 'local' skipped: CancelledError`, which named no cause at all; the
  cause-unwrapping half was fixed in `a689dba`, the transport and elapsed-time
  half was not. Small and self-contained: add the transport kind and elapsed ms
  to the line. No behaviour change — the server is skipped either way.
- **Installer runtime reduction.** Prefer a bundled or locally repairable
  Python core payload so first run does not depend on global `pip install` or
  manual `PATH` edits. Keep network install as a fallback for development
  builds.
- **Signing and release hardening.** Sign the Windows installer and document
  the expected Defender / SmartScreen path for non-developer installs.
- **Stage extension by user grant.** When a pipeline stage exhausts its cycle
  cap it currently fails closed (`[stage <x>] exceeded N cycles`). Reuse the
  existing budget-grant gate (`pause_reason='budget_exhausted'`,
  `pending_extra_budget`, `grant` action) — today wired only into the
  server/GUI session path — inside the standalone pipeline runner, so an
  escalated stage (reviewer or any) can be extended by asking the user for
  more cycles instead of aborting the run. Design-gated: the grant is bounded,
  audited, and never waives the wall-clock rule. Plan to be written before
  implementation.
  Landed alongside: a **no-progress budget breaker** on the root run. A weak
  driver model that never converges (e.g. a flash model that emits tool calls
  as text and never signals done) otherwise burns the full 200k ceiling across
  the whole worker tree — the observed failure spent 202k/200k on five
  escalating workers. The breaker stops the run once ≥70% of the budget is
  spent with at least one failed worker and no worker having delivered a
  completed artifact (a `done` outcome with mutated files); it never fires on a
  run that is actually producing, and the remaining budget would not fund a
  fresh successful worker anyway. This does not fix a weak model — the real fix
  is a stronger driver — it only caps the wasted spend fail-fast.
- **Lines-of-substrate vs lines-of-skill ratio.** Track whether capability
  growth is moving into durable substrate and reusable skills rather than
  one-off prompt scaffolding.
- **Relocate substrate out of `.github/`.** Move skills, memory, agents, and
  pipeline definitions to a platform-neutral root now that the extension is
  gone and nothing pins the `.github/` location. Coordinate this before a large
  skill-catalog expansion so new catalog entries do not create avoidable move
  churn.

---

## Dissolution candidates

Every `musubi-tier: ephemeral` component, with the trigger that retires it and
what its removal buys. This is the table `CLAUDE.md` § Substrate vs ephemeral
points at; the tags in the source files are the source of truth, and this list
exists so the removal cost is visible in one place rather than by `grep`.

| Component | `expires-when:` | `cost-lever:` |
|---|---|---|
| `agent/scope.py` | the root triages its own turn from the evidence vector, leaving one deterministic question — is the request destructive? — whose answer is a warning, not a refusal | 18 of 19 regexes, `assess_request`, the pre-run `ask_scope` halt, `BROAD_PRODUCT_QUESTION`, and the `pending_clarification` column (~551 lines) |
| `agent/subagent.py` | models gain reliable native multi-agent tool-use | the standalone spawn→run→complete driver (~120 lines) |
| `session/sub_sessions.py` | models gain reliable native multi-agent tool-use | ~400 lines of lifecycle + cascade-abandon machinery |
| `agent/pipeline_runner.py` | models orchestrate multi-step pipelines natively | the driver-side stage sequencer (~90 lines) |
| `memory/session_distiller.py` | the 4-stage pipeline is dissolved | ~250 lines tied to the planner-designer-coder-reviewer shape |
| `session/correction_loop.py` | models pass verifier checks on first try at 95th-percentile rate | the retry agent + `validation_feedback` pipeline |
| `.github/agents/**` (14 agent files) | per-file; role variants dissolve into the canonical agent | prompt scaffolding per role |

Two triggers dominate: *native multi-agent tool-use* retires the spawn and
sub-session machinery together (~520 lines), and *the root triages its own turn*
retires the lexical layer. Neither is scheduled — they fire on model capability,
not on a date. `scripts/check_musubi_tier.py` fails CI when a new or modified
file in scope carries no tag, so this list cannot silently fall behind the code.

---

## Postponed

- **Dissolve the 4-stage pipeline shape.** The staged pipeline shape remains
  supported for now. If revisited, re-home its boundary primitives onto
  sub-agent and tool-call boundaries before removing the shape.

---

## Completed Tracks

- Destruction is gated on a measurement, not on a sentence — the old guard read
  the user's *sentence* for delete-ish words, so it refused the honest request
  ("delete all \*.html") while `rm -rf build` reached `musubi_run_command`
  untouched, whose own contract says *"No 'dangerous command' detection"*. The
  lexical regex is now a **warning** to the model and routes nothing
  (`RouteKind.MANUAL_DESTRUCTIVE` was added during the refactor, then deleted
  once nothing could produce it). The hard stop moved to the tool boundary:
  `agent/blast_radius.py::measure` resolves what a call would destroy before it
  runs, counting deletes from argv verbs **in command position** (`grep -r rm .`
  passes; `find … | xargs rm` does not) and overwrites per `musubi_write_file`,
  at delete N=1 / overwrite N=5 per run. A command whose targets cannot be
  resolved statically is `unanalyzable`, which is over threshold — fail-closed.
  Consent is a token the harness mints (`allow-` + 6 hex over the sorted
  destruction keys, so one extra file mints a different token and approval
  cannot silently widen) and matches literally against the **user-role**
  message; a model cannot author a user turn, so the token is structural proof
  a human granted it, and it is held in a run-scoped `DestructiveGate` rather
  than on `Orchestration` — a leaf worker carries no `Orchestration`, so its
  refusals were recorded nowhere and could never be approved.
  `_ensure_grant_visible` re-appends any token the model
  dropped from its answer, and the Console renders Approve/Reject that submit
  that same token through the ordinary `send_chat` route — one mechanism, two
  surfaces, no GUI-only authority. Splitting judging from enforcing came with
  it: `change_assessment.py` → `manifest.py` (substrate, arithmetic over an
  LLM-declared radius), all 19 lexical regexes into `scope.py` (**re-tiered
  substrate → ephemeral**, HI #9 ask approved). Plan:
  [`2026-07-29-llm-owned-scope-with-evidence-gate.md`](./superpowers/plans/2026-07-29-llm-owned-scope-with-evidence-gate.md)

- Terminating clarification — the deterministic "stops at one clarification"
  halt had nothing counting to one. `classify_task` reads a single message, so
  the answer to *"What should the website do…?"* ("i would like to create a
  weather checking website") re-matched the same broad-product branch and drew
  the identical sentence back: three turns, zero model calls, zero files, a
  fixed point rather than a stall. `agent_turns` now carries
  `clarification_request` (the request a turn halted on, NULL when the turn
  ran), `db.pending_clarification` reads it from the latest turn only, and a
  second `ask_scope` on the same chat merges the pending request with the
  user's answer and routes it to a planner —
  `classify_task(…, allow_clarification=False)`, which rewrites every
  `ask_scope` return and strips the question out of the carried assessment. The
  escape moves one way only (it can remove a halt, never add one, never widen a
  route) and fails toward the old behavior if storage is unreadable. Plan:
  [`2026-07-29-clarification-terminates.md`](./superpowers/plans/2026-07-29-clarification-terminates.md)

- Console now-first Orchestrator and design tokens — the view that answers
  "what is the agent doing right now?" spent ~206 px of stacked chrome before
  any evidence, and the answer was an 11 px pill between "feature-dev mode" and
  "37 log rows". A Now banner naming the actor, the act, the elapsed time, and
  a labelled **Stop run** is now the largest element on screen; finished
  requests collapse to one line with absent values rendered as `—` rather than
  typeset zeros, and the running request expands in place with its last log
  lines. Orange is reclaimed for live attention only (selection became a
  neutral raise plus a blue bar, amber stays escalated), the session rail groups
  by Active / Needs you / Earlier with clock times, and the trust strip's four
  hard-coded invariant strings became four counters that move, so a deny is
  visible when it lands. Underneath, `index.css` gained a `:root` token layer —
  3 surfaces, 3 greys, 5 semantic colours, 6 type sizes, 3 radii, 4 px spacing
  — replacing ~20 greys across two hue families, 17 font sizes, and 11 radii,
  and the duplicated rule block that had been appended rather than applied was
  folded into its canonical definitions. Presentation only: no substrate,
  policy, audit, or `LMRouter` path changed and no Tauri command was added.
  Two factual bugs went with it — Policy's hard-coded "4 policy roles" now
  reads the live catalog, and Models' invented `.musubi/llm.toml` sample is the
  documented `llm.json` schema beside the operator's real path (the file itself
  stays unrendered because a profile may hold an inline `api_key`).
  Rendering the timeline newest-first surfaced a latent ordering defect: the
  request sort compared `agent_turns.started_at` (epoch seconds) against
  `runtime_log_events.id` (an AUTOINCREMENT rowid) in one expression, and since
  `_record_agent_turn` is called with `ended_at=time.time()` the running
  request has no turn row, so it took the rowid branch and was ranked oldest on
  every run — mislabelled R01 and handed the head of the continuation chain.
  Requests are now ranked by tier (finished before in-flight) and compared only
  against like keys within a tier. Rounding the surface out: a session log
  reachable without drilling into one request, with each line carrying the
  request that emitted it; the sessions rail toggle moved onto the Orchestrator
  entry in the activity bar, which sits beside the pane it hides, with a
  matching visible button in the strip so a hidden rail still advertises its
  way back; and "Back to graph" restyled as a button, having been borderless
  text in the same colour as the labels around it. Record:
  [`2026-07-27-console-now-first-orchestrator.md`](./superpowers/plans/2026-07-27-console-now-first-orchestrator.md)

- Commit identity is enforced, not remembered — the harness presets
  `GIT_AUTHOR_*` but leaves `GIT_COMMITTER_*` empty, so any command that writes
  a commit without explicit `-c user.*` flags silently takes the committer from
  `~/.gitconfig`. `git commit` is easy to remember to flag; `git rebase` is not,
  and it rewrites every commit in the branch at once — which is exactly how a
  12-commit rebase landed with the wrong committer on all twelve. A `pre-push`
  hook (`scripts/commit_guard.py`, installed by pointing `core.hooksPath` at the
  version-controlled `scripts/git-hooks/`) now refuses a push carrying a wrong
  author or committer, an AI/tool attribution trailer, or a branch name that
  names a tool. It checks only the commits the push would publish, so a bad
  commit already on the remote cannot wedge the branch. Deterministic, zero-LLM,
  per the hooks rule: never send a model to do a linter's job.

- The console's JS tests are run, not merely written — `gui/src/**/*.test.mjs`
  held 99 assertions that no script and no CI job ever executed, so a feature
  commit that reintroduced the `TokenEconomics` panel left two surface
  assertions red for several releases with nothing to report it. A `test`
  script now exists in both `gui/package.json` and the root workspace, and a
  blocking `console-js` CI job runs it; the suite needs no `npm ci`, since
  every test imports only `node:` builtins and local modules. The stale
  assertions were flipped from "forbidden" to "required" to match the
  deliberate reintroduction. `chatCommands.js` — the classifier that decides
  whether a chat message opens the pipeline picker, names a pipeline inline,
  or goes to the driver agent — went from 4 tests to 15, covering the full
  command vocabulary, filler stripping, normalization, name shapes and
  degenerate input. Writing them surfaced two live defects, both since fixed.
  `NAMED_PIPELINE` was a second hand-written copy of the picker vocabulary and
  had drifted from it — `open pipeline` opened the picker while `open pipeline
  feature-dev` matched neither gate and shipped to the driver agent as a work
  order — so it is now generated from `PIPELINE_COMMANDS` and cannot drift
  again. Separately, any single token in the name position parsed as a name,
  so "use the pipeline runner" resolved to `runner`; `TauriSource.sendChat`
  took the pipeline branch, failed the catalog lookup, cleared the composer
  and returned, losing the message with nothing sent and no error shown. The
  parser keeps its contract — it cannot know which recipes exist, so it still
  returns a candidate — and the caller now resolves that candidate against
  `pipelineCatalog` before branching, so an unrecognised name is ordinary
  prose and reaches the agent.

- A large change is more review, not a refusal — a manifest that reclassified
  a goal as large used to end the turn with a CLI string (`agent … --pipeline
  feature-dev`) the chat surface cannot run, so the work simply stopped; the
  case that prompted this was a ONE-file, ONE-subsystem change escalated
  solely because the planner set `external_side_effects`. The governance value
  of "large" is more review, not a different launcher, and the root may
  already spawn `designer`, `coder` and `reviewer` ad-hoc, so it now runs that
  chain itself. `GoalState` carries an ordered `role_chain` that advances only
  on a successful run of the role that was owed — a failed designer does not
  open the coder gate — and the role-order gate generalises from `coder` to
  all four ordered roles while leaving explorers and investigators free. The
  worker ceiling rises from 3 to 6 on reclassification, since the chain is
  four workers plus headroom for one recovery replacement. Locked decision #4
  is untouched: individual roles are spawned, never a pipeline.
  `_pipeline_recommendation` is deleted.

- The manifest owns blast radius; the catalog tells the truth — two lexical
  rules claimed to know how large a change was before anything read a line of
  code, and both were wrong in both directions: a keyword gate refused "fix
  the typo in the security section of the README" with zero model calls while
  "wire up Okta" and "store user passwords" passed untouched, and a `>= 2
  keyword` threshold scored two typos as a large feature and "migrate all 40
  services" as zero. Both are deleted, along with `_mentions_large_workflow`,
  so `assess_manifest` — fed by a planner that has read the code — is the only
  component that decides "large". What survives is one narrow guard that makes
  no size claim: it withholds the lone-coder shortcut for areas where a
  mistake is invisible, with vocabulary widened to the SSO/Okta/password/
  session/plural cases the old lists missed. Risk itself is now declared by
  the planner through a pushed `request-triage` skill that reads the change
  rather than the wording, reserves its last turn for the manifest, and sends
  workspace surveys to an explorer. The declaration is verified, not trusted:
  `manifest_overrun()` compares the declared radius against the files workers
  actually touched. Recovery was narrowed to the decision it exists to make,
  vendor tool-call markup leaking into the text channel is rejected instead of
  stored as a plan, and the agent catalog was made truthful — four agents
  understated the tools policy grants them, and a dead `model:` field
  hardcoded an Anthropic id in all fourteen. Plan:
  [`2026-07-26-manifest-owns-blast-radius.md`](./superpowers/plans/2026-07-26-manifest-owns-blast-radius.md)

- Conversation-aware routing, progress accounting, and deferred unknowns —
  four follow-ups to the advisory route. (1) `classify_task` takes a
  `has_history` boolean so a bare follow-up ("Okta", "skill?") is answered
  rather than planned; the flag says only that prior turns exist and is used
  only to route toward the cheaper answer, so it can never open a mutation
  path. (2) `agent_turns` gains `delivered_artifact`, and `chat_turn_usage`
  aggregates a conversation's turns, tokens, and trailing run of turns that
  wrote no file — the per-turn budget is process-scoped and resets on every
  message, so nothing could previously see a multi-turn spend loop. The root
  is warned at three barren turns and told to deliver or ask, not to plan
  again; it steers rather than halts. (3) Planner `unknowns` still block,
  except on a change with no critical flag and at most one file, where they
  ride to the next worker as `choose_sensible_defaults` — a wrong palette
  costs one turn to redo, while halting discarded the whole plan. (4) The
  chat surface accepts the pipeline command after conversational filler
  ("ok then run pipeline"), and the recommendation names that in-chat phrase
  before the shell command; the picker still requires the user to send, so
  locked decision #4 is untouched. Plan:
  [`2026-07-25-conversation-aware-routing-and-progress.md`](./superpowers/plans/2026-07-25-conversation-aware-routing-and-progress.md)

- Advisory routing and single-file manifest precedence — a consultative
  request ("explain each", "choose the best for me", "which auth provider
  should I choose?") is now its own scope kind instead of falling through two
  catch-alls into `medium_change`/`planner_then_coder_check`. The root answers
  it in one model call with an empty tool catalog: no planner spawn for a
  question that names no file, and no `musubi_recommend_skills` round trip.
  The branch is gated on the absence of a mutation verb, a diagnostic signal,
  and any path target, so edits, failure diagnosis, and codebase questions
  still route to workers. It is deliberately not routed through
  `_deterministic_scope_answer` — the model still reasons, it just gets no
  tools. Separately, the manifest subsystem ceiling now applies only above
  `MAX_SIMPLE_FILES`, so a one-file plan can no longer be escalated to the
  large workflow by subsystem count alone (which stranded the change: the
  orchestrator may not launch a pipeline, so no coder ever wrote the file).
  Critical flags and the file ceiling keep absolute precedence. Plan:
  [`2026-07-25-advisory-route-and-manifest-precedence.md`](./superpowers/plans/2026-07-25-advisory-route-and-manifest-precedence.md)

- Governed change assessment and recovery liveness — lexical-only mutation
  scope guesses are replaced by a deterministic ambiguity/impact/risk
  assessment (`agent/change_assessment.py`). A broad product request without
  deliverable constraints (`create a new website`) stops at one clarification
  before any parent session, model call, or worker spawn; a bounded nine-field
  planner `<change_manifest>` (4 KiB cap, fail-closed parse) reclassifies blast
  radius after planning, so an eleven-file/four-subsystem plan can no longer
  proceed as a medium change through a direct coder — it halts with a
  user-invoked `--pipeline feature-dev` recommendation and never auto-launches.
  Direct-worker role frontmatter is the sole owner of the turn cap
  (model-supplied `max_turns` is ignored, closing the starve-below-role-budget
  gap), and a genuinely unfinished turn-capped mutate worker with surviving
  files gets exactly one audited same-role continuation through the normal
  `_dispatch`/firewall/audit path (typed `FailureKind` + `decide_recovery`) —
  a second same-role failure, a spent worker slot, or a BUDGET/POLICY failure
  halts fail-closed. Recovery liveness no longer depends on the root
  voluntarily re-selecting the spawn tool. Plan:
  [`2026-07-22-governed-scope-budget-recovery.md`](./superpowers/plans/2026-07-22-governed-scope-budget-recovery.md)

- Bounded standalone pipeline runtime — one stage turn cap across
  runtime/state/audit, a hard 16k-character model-input cap including tool
  definitions, and reserved token capacity so planner/designer cannot consume
  coder/reviewer shares. A validated `PipelineWorkerSpec` resolves each stage's
  contract before spawn, so its declared `maxTurns` (clamped to [1, 12]) is the
  single cap flowing through the spawn row, `run_unit`, and the completion audit
  — the stage tool echoes `max_turns` and the driver fails closed on divergence.
  `fit_model_input` gives every explicit-budget worker (each pipeline stage) a
  hard input cap that counts tool definitions and raises before the model call
  rather than sending an over-budget request; the root keeps soft best-effort
  fitting. `ChildTokenBudget` + `pipeline_stage_allowance` give each stage a
  fair-share slice of the run budget charged through to the parent, so an early
  stage cannot spend a later stage's reserve, and allowance exhaustion finalizes
  the run once as `escalated`. The one-cap rule also covers direct workers: a
  role's `maxTurns:` frontmatter clamps the spawn's turn budget
  (direct-worker role frontmatter is authoritative; model-supplied turn
  counts are ignored), and a stage or worker that finishes on its
  last allowed turn attaches a substrate-verified artifact manifest so the
  audit records done instead of a false escalation. Plan:
  [`2026-07-12-bounded-standalone-pipeline-runtime.md`](./superpowers/plans/2026-07-12-bounded-standalone-pipeline-runtime.md)

- Root goal-state controller and token economics — the root now owns a
  current-run-only `GoalState` containing the exact user intent, deterministic
  scope/route, root usage, and bounded `OutcomePacket` feedback. After a worker
  terminates, the driver retains the stable system contract but replaces raw
  tool transcripts with one decision delta; model-visible root tools are
  reduced by phase (spawn plus skill *selection* in every scope, the
  content-loading skill tools added for broader work, recovery tools only
  inside the bounded recovery window, and **no tools once the worker ceiling
  is spent** — a root that all-succeeded into its `max_root_workers` cap is
  forced to conclude from the evidence it has instead of spinning refused
  spawns to the cycle limit, which previously wasted the whole budget and, on
  pre-salvage builds, surfaced as an `exceeded N cycles` failure). Skill
  selection is deliberately
  available even for simple artifacts: the root ranks a worker's skills with
  `musubi_recommend_skills(for_role=…)` and pushes the chosen `pushed_skill_id`
  into the spawn, so a direct worker (which carries no skill tool of its own)
  still receives role-appropriate procedure. The spawn re-validates the id
  against the worker role's `AGENT_SKILL_ALLOWLIST` entry (HI #3), so the root
  can never push a skill the role could not itself load. Adding the selection
  tool to a simple root costs ~1k tokens across the two-call projection, so the
  simple-root guard moved from 3k to ~4.5k; 20k remains the hard regression
  guard, not a normal budget. Plan and design:
  [`2026-07-15-root-goal-state-controller.md`](./superpowers/plans/2026-07-15-root-goal-state-controller.md) and
  [`2026-07-15-root-goal-state-controller-design.md`](./superpowers/specs/2026-07-15-root-goal-state-controller-design.md)

- Musubi System Atlas — a self-contained Vietnamese maintainer guide maps the
  driver, zero-LLM governance substrate, operator projection, and external
  boundaries; documents component rationale, invariants, token economics, and
  architecture evolution; and includes 13 interactive execution traces plus a
  24-question scored review. The artifact uses an explicit light palette so
  host dark-mode settings cannot obscure the content. [Open the atlas](../artifacts/musubi-system-atlas.html).
  Design and plan:
  [`2026-07-16-musubi-system-atlas-design.md`](./superpowers/specs/2026-07-16-musubi-system-atlas-design.md) and
  [`2026-07-16-musubi-system-atlas.md`](./superpowers/plans/2026-07-16-musubi-system-atlas.md)

- GUI/CLI orchestrator token economics — every logical root, child, pipeline,
  retry, and forced-final LM cycle records input, cached-input subset, output,
  LM time, usage source, worker identity, and tool names. The CLI and
  Orchestrator project selected-session totals from the same rows. The live contract is
  token-only; obsolete pricing and history-attribution fields are ignored in
  existing databases rather than destructively dropped. Plans:
  [`2026-07-13-orchestrator-token-economics.md`](./superpowers/plans/2026-07-13-orchestrator-token-economics.md)
- Console workspace separation — Orchestrator is the only GUI execution
  surface, with Direct/Pipeline launch modes, durable conversation, minimal
  summon topology, node-filtered runtime evidence, and evidence-backed skill
  provenance. Pipeline Studio is builder-only: create, drag/reorder, configure
  nested spawn allowlists, validate, and atomically save deterministic recipes.
  Design and plan:
  [`2026-07-14-console-workspace-separation-design.md`](./superpowers/specs/2026-07-14-console-workspace-separation-design.md) and
  [`2026-07-14-console-workspace-separation.md`](./superpowers/plans/2026-07-14-console-workspace-separation.md)
- Console request runtime history — every Orchestrator launch receives one
  durable `request_id`; host, root, and exact worker output is line-framed and
  appended to `runtime_log_events` without replacing prior requests. A session
  now projects `Request 01 → agents → Request 02 → agents`; selecting a request
  opens whole-request Overview/Request log, while selecting an agent opens
  Overview/Agent log filtered by exact handle, with one Back-to-graph path.
  Sessions fully hides instead of collapsing, and Conversation keeps chat,
  skills, and token economics without duplicate Summary/Verbose evidence.
  Design and plan:
  [`2026-07-26-console-request-runtime-history-design.md`](./superpowers/specs/2026-07-26-console-request-runtime-history-design.md) and
  [`2026-07-26-console-request-runtime-history.md`](./superpowers/plans/2026-07-26-console-request-runtime-history.md)
- Per-worker effort ceiling and output budget — mutate workers open at the
  shared 16,384-token per-call brake while read-only workers retain the cheap
  2,048-token floor and sticky escalation. Worker frontmatter may declare
  `maxOutputTokens`; an optional profile `max_output_tokens` clamps it. Empty
  create/append content is rejected before dispatch, replay elision markers
  instruct regeneration, and worker prompts identify the host shell. The
  cumulative root worker ceiling is classifier-independent; terminal worker
  outcomes now feed bounded replacement recovery. Plan:
  [`2026-07-13-agent-effort-ceiling-per-worker.md`](./superpowers/plans/2026-07-13-agent-effort-ceiling-per-worker.md)
- Project-scoped GUI sessions — exact runtime ownership, retained logs,
  cancellation, pipeline ancestry, shared project writer lease, durable session
  selection, and read-only browsing while another session runs. Historical
  Orchestrator sessions become resumable when the driver is idle; the follow-up
  atomically promotes and continues the viewed chat ID. Plans:
  [`2026-07-12-project-scoped-session-runtime.md`](./superpowers/plans/2026-07-12-project-scoped-session-runtime.md),
  [`2026-07-12-orchestrator-session-list.md`](./superpowers/plans/2026-07-12-orchestrator-session-list.md), and
  [`2026-07-14-resumable-historical-session.md`](./superpowers/plans/2026-07-14-resumable-historical-session.md)
- Per-cycle LM audit — `agent_cycles` persistence, query API, tool-surface
  exposure, and regression coverage
- Scope-aware root routing / agent gearbox — deterministic scope hints remain
  advisory and planning is explicit via `--plan`. Simple artifacts start with
  one coder without imposing a lifetime classifier cap; all direct runs share
  a three-worker ceiling, structured replacement handoff, and a two-cycle root
  recovery-analysis window. Read-only requests (reach/open/read/list/show a
  concrete path) now classify as `inspect` and route to a single read-only
  explorer instead of a `planner→coder` change — a mutation verb or the absence
  of a path target keeps the old routing. Plans:
  [`2026-07-04-scope-aware-root-routing-gearbox.md`](./superpowers/plans/2026-07-04-scope-aware-root-routing-gearbox.md)
  and [`2026-07-13-simple-artifact-recovery.md`](./superpowers/plans/2026-07-13-simple-artifact-recovery.md)
- MCP tool surface profiles — model-visible internal and external catalogs are
  trimmed without removing substrate tools. Plan:
  [`2026-07-01-mcp-tool-surface-trimming.md`](./superpowers/plans/2026-07-01-mcp-tool-surface-trimming.md)
- VS Code extension removal — one driver host. The embedded Copilot surface
  (`copilot-harness-extension/`, its slash commands, CI job, and ceremony
  prompts) was deleted; the CLI + Console are the surfaces. With it landed
  stage-nesting parity (pipeline stages spawn their declared helpers via
  `spawn_roles` = pipeline.yaml `spawns:` ∩ firewall) and full worker-prompt
  unification (code-review runs standalone: `agent "<diff>" --pipeline
  code-review`). Plan:
  [`2026-07-12-stage-nesting-worker-unification-extension-removal.md`](./superpowers/plans/2026-07-12-stage-nesting-worker-unification-extension-removal.md)
- Root prompt catalog cleanup — the purpose-dir catalog (root/, workers/,
  meta/) is canonical; no flat agent files remain
- Standalone worker model
- Model-agnostic `LMRouter` vendors
- Boundary policy and audit controls
- Reversible compression and deterministic compression eval
- Skill recommendation router and compression-aware context skill
- Root-agent mutation steering through bounded workers
- Token/context economics controls
- Windows GUI installer bootstrap
- Setup-aware GUI first run
- Agent catalog worker modes and chunk-safe large-file writes
- GUI audit/orchestrator Console first-run slice — the separate task launcher
  was removed so Orchestrator chat remains the single interactive session
  surface. Current operation is documented in [`guide.md`](./guide.md).
- Deterministic pipeline run: `agent --pipeline <name>` CLI entry point. The
  root agent does NOT auto-summon whole pipelines (`musubi_spawn_pipeline` is
  off the agent tool surface — a pipeline is a user-invoked run via the CLI
  flag, per policy locked decision #4), so a simple task can't be silently
  routed into a multi-stage pipeline. An operator may launch a registered
  recipe from Orchestrator Pipeline mode under the same durable conversation;
  Pipeline Studio only builds and saves recipes. Legacy Pipeline Studio chat
  rows remain readable for compatibility. Original implementation plan:
  [`2026-07-10-gui-pipeline-studio-sessions.md`](./superpowers/plans/2026-07-10-gui-pipeline-studio-sessions.md).
- Read-only discovery substrate: `musubi_glob` / `musubi_grep` MCP tools map the
  Grep/Glob capabilities, so standalone pipeline stages (and the root agent)
  find files deterministically instead of blind-guessing paths — closing the gap
  where stages authored for the embedded harness's injected workspace tree ran
  blind in the standalone runner
