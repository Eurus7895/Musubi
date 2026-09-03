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

1. **Opt-in Goal Contract and Work Package Root control.** Root now has an
   evidence-backed adaptive mode alongside the unchanged deterministic
   `PipelineRunner`. The mode freezes a versioned definition of done, executes
   bounded Work Packages, folds append-only criterion evidence into a Gap
   Report, blocks false completion and unbounded retry, leases goal/WP budgets,
   and supports byte-exact rollback for journaled file mutations. Worker rows
   carry goal/package/attempt/hash identity and the hierarchy is replayable.
   `legacy` remains the default while quality/cost metrics are compared;
   `work_package` is opt-in via CLI or environment configuration.

   ADR:
   [`0001-root-work-package-control.md`](./adr/0001-root-work-package-control.md)

   Plan:
   [`2026-09-03-goal-contract-work-packages.md`](./superpowers/plans/2026-09-03-goal-contract-work-packages.md)

1. **Skill catalog growth.** Skills remain the cheapest optimization surface.
   Each new skill should carry useful metadata such as `applies-to`, `triggers`,
   and relevant tools.
   First batch landed: `debugging`, `refactoring`, `git-workflow`, and `web-ui`
   (universal procedures) plus `typescript` (router-gated to JS/TS workspaces).
   Coder gains all five; the dispatcher agent gains only the read-safe pair
   (`debugging`, `git-workflow`) so the generator boundary holds. `web-ui` is
   deliberately universal so an HTML/CSS artifact emitted from a non-JS repo
   still matches it — closing the dashboard case where no catalog skill applied.
   Every prior catalog entry carries a `description:` — the line the model
   chooses on now that nothing ranks the catalog.
   Reachability closed: a direct worker carries no skill tool, so grown catalog
   entries were previously unreachable by workers. The root now selects a skill
   per spawned worker and pushes it (option 3, see Completed track below), so
   catalog growth reaches workers without adding a skill tool to their lean
   surface. The root reads `musubi_list_skills(for_role=…)` — id, title, and
   the one-line description each SKILL.md already declares — and chooses; the
   harness lists, it does not rank. Every catalog entry therefore needs a
   description that distinguishes it, which is now the load-bearing metadata
   (`triggers:` was for the deleted ranker). The current pipeline path still
   chooses from recipe declarations and role defaults because no model runs
   before a stage prompt is built. That is transitional and does not satisfy
   the final ownership rule. The stage-goal track adds a bounded driver-side
   preflight: the model selects the stage skill, while the harness only lists,
   validates, injects, and audits it. Missing or invalid selection fails closed;
   the harness never supplies, substitutes, drops, or defaults a skill. The
   extra preflight call is an explicit adapter with its own expiry trigger; the
   model-owned selection and enforcement boundary remain after that adapter is
   removed.

1. **LLM-owned scope, substrate-owned evidence.** The substrate stops judging
   what a request MEANS and starts proving what the record CONTAINS. Governing
   principle: deciding a turn's triage, scope, or change size is judging — code
   stops doing it; checking a claim or a measurement is enforcing — code keeps
   doing it and does more of it.
   Shipped: the destructive gate (see Completed Tracks) and `agent/evidence.py`
   — six facts per turn (`names_workspace_path`, `path_exists`,
   `has_conversation`, `explorer_findings`, `clarification_answered`,
   `barren_turns`, plus `escaped_paths` for targets outside every root the
   request was granted — the harness root and any folder attached to the
   session),
   rendered into the root prompt and logged. **It routes nothing yet**, by
   design: the distribution is measured before behavior depends on it.
   Remaining, in order — each depends on the one before, and the deletion lands
   last because it is the only step that changes the cost profile:

   - ~~**Sufficiency rule (plan step 2).**~~ **Shipped.**
     `GoalState.evidence_gap()` refuses a `coder` or `designer` spawn while all
     three are absent: a path named by the request, a read-only worker's
     report, and an accepted manifest. It answers a different question from the
     role-order gate — that one asks *is this the right role next*, this one
     asks *does anyone know what this turn targets* — which matters because a
     `single_coder` route sets no `next_role`, so "make it faster" reached a
     coder untouched. Only the request fact is stored; outcomes and the
     manifest are read live, so the root closes the gap itself within the turn.
     Known cost: a pure creation request names no path and pays one wasted
     explorer spawn. Always satisfiable, never a deadlock; if it proves
     expensive the fix is to move the check to the tool boundary, where
     `musubi_write_file` already knows whether the path exists.
   - ~~**Root triage prompt (plan step 3).**~~ **Shipped.** The routing block
     ended by saying the root owns the decision while its bodies said "Do NOT
     spawn a worker" — and between a disclaimer and an imperative the
     imperative wins, so a hint from ~12 regexes was in practice an order. Each
     entry now reads `suggested_route=` / `Suggests: …` and names what would
     justify departing from it. Because an overridable hint with no record of
     the override cannot be audited, `agent/triage.py` asks the root for one
     line — `[triage] <shape>: <why>` over `conversation | question | inspect |
     work` — captured mid-loop and stored in `agent_turns.root_triage`. Parsed,
     never judged: the harness does not check whether a shape was right, and an
     absent declaration is recorded absent rather than inferred, or an invented
     shape would be indistinguishable from a stated one.
   - ~~**Delete the lexical judgment (plan step 4).**~~ **Shipped.**
     `classify_task` is one regex asking whether a sentence reads like a
     deletion, answering with a warning that routes nothing. Gone:
     `assess_request`, 18 regexes, `_CASUAL_RE`'s zero-token fast path, the
     pre-run `ask_scope` halt, `BROAD_PRODUCT_QUESTION`, the
     `clarification_request` column and `db.pending_clarification` — **735
     lines removed against 127 added.** Every turn now starts
     `RouteKind.ROOT_DECIDES`; only `assess_manifest` narrows it, and only
     after a planner has read code.
     Two inversions fell out of it, both improvements: the root's tool surface
     and token target are now **lean by default** and widen only when a
     manifest calls the change medium or large — the old code widened by
     default and narrowed on a lexical hunch. The documented price is paid as
     stated: "hi" costs one root call, and a test asserts it.
     **Entry condition was not met.** The roadmap said to wait for
     `root_triage` rows in volume before removing the fallback; Eurus chose to
     proceed without them. What the deletion trades on is therefore untested
     in production: if the root's own triage turns out worse than the regexes
     were, the evidence to notice it is being collected now rather than
     beforehand.
   - ~~**Enforce the declaration (plan step 5).**~~ **Shipped.**
     `GoalState.overrun_stop()` refuses a further mutation spawn once workers
     have touched more files than the accepted manifest declared, enforced in
     `_spawn_overflow_reasons` beside the other gates. Deliberately not
     terminal: the run keeps what it wrote and may report or re-plan — making
     it fatal would discard completed work to punish a declaration, and the
     append-only stage store exists so a wrong attempt is superseded rather
     than lost. This matters more now than when it was written: with the
     lexical risk gates gone the manifest is the sole input to routing, so a
     declaration nobody enforces is trusted rather than governed.
   Plan:
   [`2026-07-29-llm-owned-scope-with-evidence-gate.md`](./superpowers/plans/2026-07-29-llm-owned-scope-with-evidence-gate.md)

1. **Conversation-scoped planning artifacts.** A read-only planner now emits a
   human `<plan>` and machine `<change_manifest>` as one bounded response; the
   driver validates and persists them separately as `plan.md` and
   `manifest.json` under a stable conversation goal key. Runtime planning files
   are not implementation delivery and do not reset no-progress accounting.
   The manifest field `blocking_decisions` replaces `unknowns`: the planner
   chooses reversible defaults in the plan regardless of file count, while the
   substrate routes only the decisions the model explicitly declares unsafe to
   guess. This is an incremental goal boundary, not the `/goal` lifecycle
   command; explicit goal creation/switching remains a separate design.

   Plan:
   [`2026-07-30-goal-plan-artifacts.md`](./superpowers/plans/2026-07-30-goal-plan-artifacts.md)

1. **Root-owned planning and model-owned dispatch.** Direct orchestration is
   being collapsed from Root → Planner → manifest-classifier into one Root with
   two explicit model-selected modes. Direct mode declares `create` or `modify`
   plus a target path, letting a new artifact proceed without paying for an
   Explorer merely because the path does not exist. Planning mode gives Root a
   bounded read-only surface and persists Root's own `plan.md` plus
   `manifest.json`; the model declares change size and worker order, while the
   harness validates paths, manifest shape, role membership, order, radius, and
   a hard worker ceiling. Manifest arithmetic no longer decides whether work is
   large. Explorer remains workspace discovery; Investigator becomes
   diagnostics only and cannot establish a mutation target. Skill selection is a
   catalog LISTING the model chooses from — nothing ranks it and no ticket
   gates it. The Planner role
   remains only for explicit legacy pipelines until pipeline dissolution.

   Plan:
   [`2026-07-31-root-owned-planning.md`](./superpowers/plans/2026-07-31-root-owned-planning.md)

1. **Implemented: model-authored stage goals, substrate-enforced acceptance.**
   Before a stage attempt, a bounded driver model preflight selects the role's skill
   and, for an opt-in non-evaluator stage, translates the current task into a
   structured acceptance contract. The harness validates and freezes that
   declaration, injects the selected skill, runs only deterministic predicates,
   and persists every attempt and gate transition. Task-specific goals never
   live statically in `pipeline.yaml`;
   recipes declare the allowed checker and command ceilings, iteration cap,
   helper roles, and budgets. A failed gate creates a new append-only attempt;
   exhaustion or infrastructure failure escalates and stops the pipeline.
   V1 contains no LLM reviewer predicate over the goal: the final evaluator
   remains firewalled to the artifact under HI #3, and its structured fail or
   escalate verdict stops rather than silently succeeding. Deterministic
   acceptance remains substrate; the automatic retry loop is separately tagged
   ephemeral with a measurable first-pass-success expiry trigger. The work also
   generalizes the legacy four-stage attempt store, adds crash-safe gate
   checkpoints and events, extends Pipeline Studio losslessly, and depends on
   the durable HI #8 audit obligation before a worker may start.

   Design:
   [`2026-08-01-stage-goals-and-loop-design.md`](./superpowers/specs/2026-08-01-stage-goals-and-loop-design.md)

   Plan:
   [`2026-08-01-stage-goals-and-loop.md`](./superpowers/plans/2026-08-01-stage-goals-and-loop.md)

1. **Implemented: runtime convergence repair.** A production-like run
   exposed two independent convergence failures after stage preflight began
   working: cumulative plan/design handoff made the coder's protected input
   26,615 characters against a 16,000-character hard cap, and the direct root
   spent 188,778/200,000 tokens retrying an underspecified plan tool contract
   before any worker spawned. The repair is deliberately split. P0 makes stage
   input token-oriented with an 8,000-token operational ceiling (32,000-character
   compatibility fallback), forwards only the immediate passed predecessor,
   makes substrate completion status authoritative, and extends the durable
   audit outbox to worker completions. P1 publishes one closed manifest/role
   schema, returns typed corrections, stops after three consecutive contract
   failures, covers pre-worker control loops in the no-progress breaker, and
   projects control results into Request Log. Full stage output remains in the
   append-only store; the model still selects every skill.
   Verification covers 39 pipeline-stage regressions, 95 lifecycle/audit/config
   regressions, and 220 Root planning/runtime-log regressions; no paid model
   smoke run was needed.

   Design:
   [`2026-08-02-runtime-convergence-repair-design.md`](./superpowers/specs/2026-08-02-runtime-convergence-repair-design.md)

   Plans:
   [`2026-08-02-pipeline-runtime-integrity.md`](./superpowers/plans/2026-08-02-pipeline-runtime-integrity.md) and
   [`2026-08-02-root-planning-convergence.md`](./superpowers/plans/2026-08-02-root-planning-convergence.md)

1. **Implemented: truncated and empty model turns fail where they happen.**
   The next run on the repaired pipeline died at stage 1 of 4 with an
   unattributable `escalated`. One truncated DeepSeek response had crossed
   four layers as an empty success: the OpenAI wire converter discarded the
   `reasoning_content` a cut-off reasoning model returns instead of `content`,
   the agent loop's "no tool calls → final answer" branch ran ahead of its
   truncation check and recorded the resulting empty turn as a clean `final`
   cycle, and the runner's `answer is not None` test reported the blank result
   as `done` — which then failed the harness's non-empty-summary requirement
   for the read-only turn-cap waiver. Each layer now reports accurately: the
   wire recovers the thinking channel as a last resort, the loop retries or
   returns a typed `[blocked]` marker for a truncated or empty turn, and a
   blank stage answer is `escalated` with a summary that names the cause. The
   substrate's turn-cap coercion and terminal-status gate are unchanged and
   stay fail-closed. A planning role's `maxOutputTokens` no longer restates
   `MAX_STAGE_HANDOFF_CHARS`: at the effort floor it silently disabled the
   retry-at-ceiling rescue for every read-only role, so planner and designer
   move to 8,192 and the deterministic byte gate keeps owning handoff size.
   Verification: 1,759 regressions pass; Ruff and mypy findings unchanged on
   every touched file; no paid model smoke run.

   Plan:
   [`2026-08-03-truncated-and-empty-model-turns.md`](./superpowers/plans/2026-08-03-truncated-and-empty-model-turns.md)

1. **Implemented: a worker can report a skill mismatch.** The same run showed
   a `coder` handed `web-ui` for "an application to check weather" — a skill
   whose procedure forbids the network call the task requires — with no way to
   say so. HI #2 is unchanged: the push still happens, is still not
   opt-out-able, and a worker still cannot select its own skill. What is added
   is a statement rather than a choice. `musubi_report_skill_mismatch` is
   validated by the harness (running worker, non-empty reason, and any
   suggested skill passing the same role allowlist a spawn passes), granted to
   every role including toolless ones because a capability gate would silence
   exactly the roles that need it, and projected as one line onto the worker's
   summary whatever its status — a worker that delivered under a wrong-fitting
   skill tells the root the same thing as one that failed under it. The prompt
   that advertises the tool carries the worker's own handle, since a tool a
   worker cannot address is a tool it does not have.

   Not addressed, and the deeper constraint: `pushed_skill_id` is singular, so
   a task needing both presentation and data acquisition cannot be expressed at
   spawn time. Changing that arity is a HI #2 design discussion.

   Plan:
   [`2026-08-03-worker-skill-mismatch-report.md`](./superpowers/plans/2026-08-03-worker-skill-mismatch-report.md)

1. **Implemented: `web-ui` stops deciding architecture in its selection line.**
   Its description claimed "self-contained … or any browser-rendered artifact",
   so it was both selected for, and then wrongly applied to, "an application to
   check weather" — a task whose whole point is fetching live data, which the
   skill's procedure forbade. The description now scopes the skill to the
   presentation layer and says explicitly that it does not decide where data
   comes from. Step 1 asks the deciding question first — does the deliverable
   need data the file does not already have — and carries a third branch for
   an application needing live data, where self-contained does not apply:
   fetch at runtime, never inline a credential in client code, and handle the
   source being unreachable. The `file://` and inline-library rules are now
   stated as conditions of the self-contained branch rather than as universals.
   A brief matching no branch routes to `musubi_report_skill_mismatch`.

   Catalog audit findings not addressed here: the `applies-to` filter has never
   run (no `.github/memory/project-profile.md`, so `_load_project_profile`
   returns None and the router passes everything through — four skills declare
   gates, none fire); 18 of 22 skills declare an empty `completion-contract`,
   which is why the two that declare one read as the only skills that produce a
   deliverable; `docs-writing` is gated on doc TOOLING while the skill is about
   prose; `documentation` and `docs-writing` overlap inside designer's catalog.

1. **Implemented: a Direct declaration may not manufacture its own target
    evidence.** Tracing the weather-app run to its first domino landed on
    cycle 0, not on skill selection. With `target_unknown=True` and zero files
    read, Root called `musubi_begin_direct(target_intent="create",
    target_path=<a path it invented>)`. A `create` needs nothing on disk, so
    every existing check passed — and the declaration then set `target_named`,
    which is precisely what `GoalState.evidence_gap` reads. The sufficiency
    gate asked "does anyone know what this turn targets" and was answered by
    Root's own assertion that it did. What that locked in is irreversible:
    `route=SINGLE_CODER`, `role_chain=()`, one file, and `begin_plan` refuses
    once a mode is set. Everything downstream followed from there — no plan for
    the coder to work from, so it globbed 819 files itself; no designer or
    reviewer; a skill chosen to match a shape already decided.

    `request_named_target` now records the turn-start evidence separately from
    the field a declaration writes, and a create-Direct requires the target to
    come from somewhere other than the call making it: the request named a path
    inside the workspace, or an Explorer/Finder reported findings. Modify is
    unaffected — an existing file is established by the filesystem, which no
    guess can do. The refusal names both self-serve ways out, so Root fixes it
    without returning to the user.

    This subsumes the second gate considered alongside it. `coder.agent.md`
    rule 3 tells the coder to refuse owning planning and implementation
    together for broad work; breadth has no mechanical measure at spawn time
    (the manifest that carries it exists only in Planning mode), so the
    enforceable version is this gate routing unnamed work into Planning, where
    a plan and acceptance criteria are produced by construction.

1. **Implemented: Direct mode is gone; a worker chain is earned, never
    declared.** Item 10 stopped a Direct declaration answering the target
    question with its own invented path, but the declaration was making a
    second claim that gate never touched: `scope="simple_artifact"`,
    `route=SINGLE_CODER`. Knowing WHERE a file is says nothing about HOW BIG
    the work is — "refactor src/auth.py" names an existing path and is not
    simple — so a check on target evidence could never validate a claim about
    complexity. Nor can complexity be measured before the work starts: the
    thing that measures it is the change manifest, and the manifest only
    exists in Planning.

    So the declaration is removed rather than gated. `musubi_begin_direct`,
    `GoalState.begin_direct`, the declared-target fields, and item 10's
    `request_named_target` all go with it — the last of these existed only to
    stop a declaration self-answering, and there is no declaration left.
    `musubi_begin_plan` is now the single entry to any worker flow.

    `RouteKind.SINGLE_CODER` stays. It is still reachable, but only as an
    OUTCOME: `assess_manifest` classifies a committed manifest of one file and
    one subsystem as exactly that. The single-worker path is unchanged in
    substance and changed entirely in provenance — proved by a manifest that
    names what the change touches, instead of asserted from the request
    sentence before anything was read. A one-file change costs one extra
    control call and produces a plan the coder can work from, which is the
    thing its own role prompt (rule 3) has always asked for.

1. **Removed: `completion-contract`.** A skill's frontmatter could declare
    `required-output-fields` and `required-check-types`, and the same field was
    read at two boundaries with two different meanings — a LABEL in the catalog
    listing Root selects from (`server.py`), and TEETH in the stage gate
    (`stage_contract.py`, `pipeline_runner.py`). That conflation is what made
    it unusable: 4 of 22 skills declared one, so the catalog implied that only
    those four produced anything, and correcting the imbalance was impossible
    without also changing what every stage using those skills must emit.

    Nothing replaces it. A stage's exit predicates still come from the model's
    preflight proposal, still bounded by the recipe's `allowed_checks`, still
    frozen and hashed, still evaluated by the gate. What goes is the ability of
    a skill to add requirements on top of that — and with it the
    `required_output_fields` leg of `FrozenStageContract`, which the skill was
    the only source for.

1. **Implemented: a direct worker gets a token slice, not the run.** A
    pipeline stage has had a `ChildTokenBudget` since the stage runner shipped;
    a direct worker was handed the parent `TokenBudgetEnforcer` itself,
    unwrapped. In the traced failure one coder charged 200,580 of a
    200,000-token run across eight cycles while the root had spent 9,685 — and
    when the worker failed there was nothing to recover with. `decide_recovery`
    halts a BUDGET failure fail-closed, but even a permitted continuation had
    no tokens to run on, so the halt was academic.

    `root_worker_allowance` splits the live remaining across this worker plus
    the root's unspent slots — `spawned_workers` is incremented before dispatch,
    so it already counts the worker about to run. On the traced numbers the
    first of three workers gets 63,438 and 126,877 stays reserved. A worker
    that overruns now stops itself while the run stays alive, which is the
    property the failure lacked. With no orchestration there is no ceiling to
    divide by and no continuation to reserve for, so the budget passes through
    unchanged rather than being invented.

1. **Settled: the token budget counts tokens processed, and now says so.**
    `charge()` bills the full `tokens_in + tokens_out` every cycle and never
    deducts the provider's cached prefix, which looked like an oversight worth
    fixing. Measuring it decided the opposite. On the traced run the two
    readings differ by 60% — 210,265 charged against 84,057 of marginal cost —
    and only the larger crossed the 200,000 cap. Marginal accounting would have
    left 115,943 and let the run continue; its last three cycles wrote zero
    bytes. Charging a cached prefix in full is what ended a loop that had
    stopped making progress, so the enforcer is not a cost meter and must not
    become one.

    Nothing about the arithmetic changes. What changes is that it stops
    misreporting: `TokenBudgetEnforcer` documents the unit and the measurement
    behind it, the per-cycle log states `charged=` beside the running total,
    and the reported cache figure is `cached_in=` rather than `cache_read=` —
    a name that read as a saving next to a number it never reduced. A cost
    figure, if wanted later, belongs beside this one as a second meter; two
    questions need two meters, not one meter re-denominated under historical
    rows written in the old unit.

1. **Implemented: a budget halt lands the writes it already paid for.**
    Measured on a four-turn run after the repairs above: 483,621 tokens across
    35 cycles and not one file written. A coder emitted 11,289 output tokens
    carrying three files, was charged 19,801 for them, and the postflight halt
    fired between generation and dispatch — the audit row recorded
    `tool_names=[]` and nothing reached disk. Discarding a paid-for write saves
    nothing, because dispatching it costs tool execution rather than model
    tokens. File mutations now land before the loop stops; reads and spawns are
    skipped, since their results only feed a model call that will never happen.
    The postflight halt also salvages a typed `[incomplete]` answer instead of
    raising, matching what the preflight halt has always done — the two paths
    differed only by which side of the model call the cap was crossed on, and
    that decided whether the parent got something it could act on.

    Sizing the preflight estimate off the effort router's real output ceiling
    was tried and reverted: it refuses the write cycle before the model writes
    anything, which is the outcome the salvage exists to prevent. An overrun
    that delivers the artifact beats a refusal that delivers nothing.

    Two more defects from the same run: Root declared five reversible defaults
    ("open-meteo free API, no key, vanilla JS, no framework, no build step") as
    `blocking_decisions`, which routes to `ask_scope` and ends the turn with a
    question — 169,013 tokens for two questions and no file. The rule that
    reserves that field for decisions which must not be guessed lived in
    `planner.agent.md`, and Root has owned planning since `0c68607`; it is now
    in `root.agent.md`. And a second `musubi_begin_plan` refused with "goal
    mode is already planning", which Root read as being stuck: it spent 80,286
    tokens on that cycle, then wrote its finished plan out to the user as prose
    claiming commit and spawn were unavailable. They were in the surface
    throughout; the refusal now names `musubi_commit_plan`.

1. **Open: plan continuity across turns, and what it costs the worker.**
    Two defects from the same run are deliberately not repaired here because
    both touch the session lifecycle. Every turn re-plans from nothing —
    `glob **/*` and a re-read of Musubi's own `.musubi/goals/<id>/plan.md`,
    which the harness had just persisted and the next turn had no way to
    receive — at 18,000–33,000 tokens a turn, roughly 20% of the run. And
    Root's planning spend leaves too little for the worker: 43% of the budget
    gone before the first spawn, 70% before the second, against write cycles
    costing ~19,800. The fair-share split is correct; the numerator is not,
    and most of it is rediscovery that continuity would remove. Deciding the
    split before fixing continuity would tune against a number expected to
    move.

    Design note:
    [`2026-08-05-plan-continuity-design.md`](./superpowers/specs/2026-08-05-plan-continuity-design.md)

1. **Added: `scripts/audit_report.py` — one run, rendered for someone who was
    not present.** A read-only query over the two databases the harness
    already writes, answering an outside reviewer's questions in their order:
    what was asked, what the run was allowed to touch, who it delegated to,
    what it did and what it was refused, what that cost, what reached disk,
    and whether the record has holes. The last section is the one that makes
    it an audit trail rather than a log: `audit_obligations` that never
    delivered, spawns with no terminal row, and cycles that cannot be
    attributed are reported as findings rather than passed over in silence.

    Run against a reconstruction of the 2026-08-05 four-turn trace it
    reproduces the run's charged total exactly (179,581), attributes every
    cycle to a worker, and shows three `musubi_write_file` calls recorded with
    a non-`ok` status against files that were never created — the discarded
    writes that item 15 repairs, visible from the record alone.

    Known gaps, ordered by what a regulated buyer would ask first: no human
    identity is recorded anywhere; there is no approval event, so the
    destructive gate's decision leaves no row; tool arguments are hashed but
    not retained, so "what was written" needs the git history; the committed
    plan lives on disk rather than in the record, so the REASONING behind a
    delegation is absent; and rows are append-only by convention with nothing
    chained or signed, so the record cannot answer whether it was edited after
    the fact.

Runtime limits have one owner per dimension: the bounded runtime track owns
pipeline-stage turn caps, model-input size, and total stage allowances;
per-worker effort owns output tokens for one LM call; root routing owns
worker-count and continuation-spawn policy. Do not introduce a second parser or
enforcement path for the same dimension.

### Backlog

- **Installer runtime reduction.** Prefer a bundled or locally repairable
  Python core payload so first run does not depend on global `pip install` or
  manual `PATH` edits. Keep network install as a fallback for development
  builds.
- **Signing and release hardening.** Sign the Windows installer and document
  the expected Defender / SmartScreen path for non-developer installs.
- **Stage extension by user grant.** When a pipeline stage exhausts its cycle
  cap it currently fails closed (`[stage <x>] exceeded N cycles`). Reuse the
  existing budget-grant gate (`pause_reason='budget_exhausted'`,
  `pending_extra_budget`, `grant` action). Token-budget exhaustion now pauses
  and resumes the exact standalone pipeline checkpoint with an audited grant;
  the remaining work is to route the distinct per-stage turn/cycle cap through
  the same gate instead of aborting the run. Design-gated: the cycle grant is
  bounded, audited, and never waives the wall-clock rule. Plan to be written
  before implementation.
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
| `automatic-stage-retry` in `agent/pipeline_runner.py` | latest 500 eligible attempts: at least 95% pass on attempt 1, Wilson 95% lower bound at least 93%, and no P0/P1 incident in the window was prevented only by retry | repeat workers, retry preflights, cross-attempt feedback, and resume branches |
| `agent/stage_preflight.py` | worker runtime can require model selection and load one permitted skill before work tools without a separate model call or harness default | one model call per stage attempt |
| `.github/agents/**` (12 agent files) | per-file; role variants dissolve into the canonical agent | prompt scaffolding per role |

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

- Harness evidence integrity repair — root prompts are cross-checked against
  the live MCP surface, policy verdicts persist request/session provenance and
  never fall back to the newest request, legacy verdicts stay unattributed,
  and stage checkpoints abort when their append-only attempt row is missing.
  Design and plan:
  [`2026-08-04-harness-evidence-integrity-design.md`](./superpowers/specs/2026-08-04-harness-evidence-integrity-design.md) and
  [`2026-08-04-harness-evidence-integrity.md`](./superpowers/plans/2026-08-04-harness-evidence-integrity.md)

- Run evidence is conversation-scoped, attributable, and says which quantity it
  is showing. Three operator reports from one session, three separate defects,
  none of them the model. **Attached folders were unreachable in the prompt:**
  `agent/evidence.py` measured path containment against the `musubi` root
  alone, so a folder the operator had just granted rendered as
  `outside_workspace=… (no worker can reach these; say so and stop)` directly
  above the roots listing that offered it — and `goal_state.target_named`,
  fed from the same field, additionally blocked a mutation worker at that
  folder. Containment is measured against every granted root now; a contained
  path is named `<alias>/<rest>` and the block states that the alias is the
  tool's `root=` argument. **Token figures were not comparable:** the driver
  hands one `AgentRunStats` to the root and every worker under it, so a turn's
  recorded tokens already contain its workers'; printing that above each
  worker's own made two figures look additive when one contains the other.
  Both now come from `agent_cycles`, the turn node carries `ownTokens`, and the
  overview labels them *turn total* / *root only*. Underneath it, the reader
  took the **oldest** 120 `agent_turns` rows (`ORDER BY id ASC LIMIT 120`), so
  past 120 turns the Console stopped seeing new ones at all — recent sessions
  read "no agent activity yet" and their token ledger under-reported.
  **Skills and policy read empty on sessions that used both:** HI #2's push is
  not opt-out-able, but only the root's rare per-spawn *override* was audited,
  the push emits no tool call and so wrote nothing to the runtime ledger,
  ALLOW verdicts were recorded but never emitted, and the view model replaced
  the whole derived log stream with the ledger projection while scoping skills
  to the latest root turn instead of the conversation. Each of those four is
  closed; `SubagentContext` gained `role_skill_id` so a pushed skill is
  nameable at all. **A wrong argument stopped being a terminal policy
  failure:** `evaluate_argument_policy` routed optional-argument denials
  through `PolicyDeniedError`, the channel built for "this role may not call
  this tool", so a root that put a `recommendation_id` into `pushed_skill_id`
  ended its turn 4 cycles and 12,383 tokens in over a field it could have
  omitted. `PolicyDecision` carries `recoverable` now: argument-shaped
  denials return through the per-call refusal channel with the legal values
  named — including this turn's own ranker candidates, which
  `open_recommendation_skills` had held unread since recommendations shipped —
  while authorization denials stay terminal and every verdict is still
  audited as a deny. Fixed alongside: `refused_reason` was honoured only
  inside the spawn-with-orchestration branch, so a refused call could still
  reach the MCP server on any other path. **The depth-0 driver has one
  name now:** it answered to `agent` (authorization), `root` (runtime ledger)
  and `driver` (console prose), with `agent/run.py` passing two of them for
  itself in adjacent lines and both readers carrying hard-coded spelling lists
  to join a verdict to a node. `root` is canonical, defined once as
  `policy_engine.ROOT_ROLE` with a `normalize_role()` that every membership,
  capability and skill lookup folds through, so append-only rows written as
  `agent` still resolve and nothing rewrites history. `driver` is pointedly
  NOT an alias — it never carried the root's membership, so aliasing it would
  hand it the whole spawn firewall. **Skill ranking stopped scoring the
  conversation instead of the request:** `recommend_skills` concatenated the
  task and `context_summary` into one bag of text, so on turn 3 of a chat that
  had built an HTML dashboard, "change the language of the application"
  matched no skill on its own while the context hit five `web-ui` triggers for
  a score of 200 — capped to `confidence: 0.99` and pushed into a coder that
  was there to change strings. The request elects now and context is a
  quarter-weight tiebreaker that can never elect alone; confidence derives
  from the request score, so it discriminates instead of saturating. No test
  had ever exercised `context_summary`. **Then the ranker was deleted
  outright.** Weighting the request over the context made it less wrong, not
  entitled: scoring text to decide what a request is ABOUT is the same
  judgement this track already deleted `assess_request` for, and its
  `expires-when: never` tag was falsified by the first trace that hit it.
  `musubi_recommend_skills`, the recommendation ticket, and the per-stage
  pipeline ranker are gone — **831 lines removed against 226** — replaced by
  `musubi_list_skills(for_role=…)` returning each permitted skill's one-line
  description for the model to choose from. The ticket constrained where the
  root got a name, never which names are legal, and the allowlist and catalog
  checks that do answer that are untouched. Pipeline stage recipes expose the
  role's permitted skill catalog to the model. The model selects one exact
  skill id during preflight; the harness validates its allowlist membership,
  version and content hash, then injects that selected skill without choosing,
  defaulting, substituting or dropping it. `dev-lite` was removed with the
  ranker: a sample recipe sitting in
  `.github/pipelines/` is indistinguishable from a supported one — it appeared
  in the console catalog, in `--pipeline` help and in the README beside
  `feature-dev`. The
  preset MECHANISM stays; the test that covered it now authors its own recipe
  over the real catalog instead of leaning on a shipped sample.
  **The recipe survives a save now.** Pipeline Studio models four stage fields
  and rewrote the declared `generator:`/`evaluator:` shape into the flat one,
  dropping every per-stage `skill:` and turning plan/code/review into
  planner/coder/reviewer — measured on feature-dev. Those declarations are the
  compliance statement the substrate reads, so a save that cannot carry them is
  refused rather than truncated; a new name and the flat shape are unaffected.
  Alongside it, `read_spawn_firewall` scrapes `policy_engine.py` for the spawn
  allowlist and its key detector only understood string literals, so the
  depth-0 rename silently dropped the root's entry from the map; it resolves
  constant keys now, with a test. Nothing carries a confidence: the listing is ids, titles and
  descriptions, because a score is the harness stating an opinion about a
  request it is not entitled to have one about.
  `skill_router.applicable_skills` stays, because it judges the project rather
  than the request. Plan:
  [`2026-07-31-console-run-evidence-scope.md`](./superpowers/plans/2026-07-31-console-run-evidence-scope.md)

- Pipeline Studio can reopen a recipe, and updating one no longer destroys it —
  `load_pipeline_recipe` had existed since the Studio shipped and reached
  `actions.onLoad`, but nothing ever rendered a control that called it, so the
  three recipes in `.github/pipelines/` could not be opened and read as fixed
  presets. Wiring Open alone would have been destructive: the Studio models six
  keys and rendered from those alone, while `code-review/pipeline.yaml` carries
  `level`, `max_credits: 20`, `warn_at`, and the `musubi-tier` header block that
  **Hard Invariant #9** requires — Open-then-Save would have deleted all six
  lines. Saving now carries across the leading comment block and every top-level
  key the model does not own, so an update is lossless; a save under a new name
  still starts clean, which is what makes Clone a new recipe rather than a copy
  of the original's tag and credit budget. Because the renderer emits no
  comments, a `musubi-tier` tag is an exact marker for "checked in and
  hand-authored" — no git dependency, no schema change, no hard-coded name list
  — so `delete_pipeline_recipe` refuses tagged recipes fail-closed and Remove is
  disabled with a reason before the click. Clone is a local rename that writes
  nothing until Save. Plan:
  [`2026-07-31-pipeline-studio-recipe-management.md`](./superpowers/plans/2026-07-31-pipeline-studio-recipe-management.md)

- Console panel toggles round-trip, and geometry is testable — hiding the
  sessions rail unmounted the header that carried the hide control, and the
  show control that replaced it rendered *after* the Now banner, so its
  position was a function of banner height: the gesture closed at (213, 23.5)
  and reopened at (36, 132) mid-run or (36, 79.5) at rest. Hide now leads its
  header and show is pinned to the workspace corner — **207.6 px of travel
  became 0.5 px**, the same at both breakpoints. The conversation panel carried
  the same defect on the opposite edge: collapsing dropped its 48 px header band
  outright, leaving the toggle a bare flex child under a 14 px pad, so it
  returned at cy 28 and 23.5 px from the console edge rather than the 23.5 and
  26 it left from — **5.2 px of round-trip travel, now 0 on both axes**, with
  the console's header rule running unbroken across the panel in either state.
  Below 1180 px the rail header
  had been overflowing its 58 px column by 30.3 px and painting the hide button
  on top of the banner's live dot, because the media query dropped the label
  and the count but not "Clean all"; it fits now. The four bare `←` / `→`
  characters became one icon whose bar names the edge the panel is on and whose
  chevron names the way it will move, centred in its box rather than riding the
  text baseline 1.5 px high. Every button group has a hover state, including
  `.pause-panel__actions` — the one place an operator commits an irreversible
  decision on a halted pipeline — and the four hover literals duplicated across
  the sheet became tokens. None of this could have failed a check: the console
  suite reads the JSX as a string, and a substring carries no coordinate, so
  `Orchestrator.geometry.test.mjs` drives headless Chromium over a DOM fixture
  and asserts the coordinates directly. Presentation only: no substrate,
  policy, audit, `LMRouter`, or Tauri path changed. Plan:
  [`2026-07-30-console-panel-toggle-affordances.md`](./superpowers/plans/2026-07-30-console-panel-toggle-affordances.md)

- A skipped MCP server now says which one, how, and how long — external
  servers are fail-open by design, but the log line named only the server and
  the exception, leaving the two questions an operator actually has
  unanswered. A stdio `command` that is not installed and an HTTP `url` whose
  host is unreachable need opposite first moves and produced identical lines;
  with `timeout_s` defaulting to 30 s, a real timeout and an instant refusal
  also read the same. The line now carries the elapsed ms, an explicit
  `(timeout Ns)` marker when the wait reached the ceiling, and
  `via <stdio|http> <command-or-url>` — never `headers` or `env`, which is
  where the `${VAR}`-interpolated secrets live, and a test pins that. Completes
  the defect whose other half (`_describe_exc` losing the cause inside nested
  `anyio` groups, which produced the traced `!mcp 'local' skipped:
  CancelledError`) was fixed in `a689dba`.

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
- Console session inbox, cleanup, and exact pipeline continuation — **Needs
  you** contains only unread sessions and selecting one marks it viewed.
  Operators can delete the selected session or clean the visible session list
  without deleting append-only audit, stage, or request evidence. A genuinely
  paused pipeline projects its reason and legal actions into the selected
  session; approve, retry, grant, force, and abort decisions are validated and
  audited before the Console relaunches the same profile, task, folder
  snapshot, pipeline ID, and first incomplete stage. Windows command execution
  also normalizes extended-length paths only at the subprocess boundary.
  Design and implementation plan:
  [`2026-07-29-session-inbox-resume-cleanup-design.md`](./superpowers/specs/2026-07-29-session-inbox-resume-cleanup-design.md) and
  [`2026-07-29-session-inbox-resume-cleanup.md`](./superpowers/plans/2026-07-29-session-inbox-resume-cleanup.md)

- Session-scoped multi-folder grants — Musubi remains the fixed harness root,
  while each idle Orchestrator session may attach, rename, and remove up to 16
  non-overlapping external folder roots without a Settings change or restart.
  Every request snapshots the exact aliases, grant IDs, and canonical paths;
  all filesystem tools accept an explicit root and relative path, command cwd
  changes only inside the selected root, and mechanical/artifact checks retain
  root-qualified evidence. The standalone CLI exposes the same boundary with
  repeatable `--add-folder [ALIAS=]PATH`. Unknown, moved, overlapping, absolute,
  and escaping paths fail closed. This grants the Musubi harness filesystem
  authority for one session; it does not launch Codex or Claude. Design and
  implementation plan:
  [`2026-07-29-session-folder-grants-design.md`](./superpowers/specs/2026-07-29-session-folder-grants-design.md) and
  [`2026-07-29-session-folder-grants.md`](./superpowers/plans/2026-07-29-session-folder-grants.md)

- Console now-first Orchestrator and design tokens — the view that answers
  "what is the agent doing right now?" spent ~206 px of stacked chrome before
  any evidence, and the answer was an 11 px pill between "feature-dev mode" and
  "37 log rows". A Now banner naming the actor, the act, the elapsed time, and
  a labelled **Stop run** is now the largest element on screen; finished
  turns collapse to one line with absent values rendered as `—` rather than
  typeset zeros, and the running turn expands in place with its last log
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
