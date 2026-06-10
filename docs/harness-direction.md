# CopilotHarness — direction note

> Status: **draft** · written 2026-06-09 after PR #64 merged the
> sub-agent-for-exploration shift.
> Updates: this is a living document. Every quarterly review or
> major model release should land an amendment.

This document answers three questions:

1. **What is the goal of CopilotHarness?** — sharpens CLAUDE.md's
   one-sentence framing into something operational.
2. **What should we do next?** — concrete next moves through the
   lens of Han Lee's "Hidden Technical Debt of AI Systems: Agent
   Harness" (May 2026), with cost-of-high-end-model as a first-class
   constraint.
3. **How do we stay aligned as models evolve?** — the mechanisms
   for not building load-bearing scaffolding around a model that's
   about to outgrow it.

It is not a roadmap. It is the **lens** the roadmap should be read
through.

---

## 1. The goal

The current one-liner — _"Copilot Chat reasons. CopilotHarness controls
the environment."_ — is correct but soft. Sharpened:

> **CopilotHarness is a governance layer for agentic software-
> engineering work in VS Code. It provides firewall, audit, validator,
> budget, and skill-injection primitives that survive model releases.
> It is NOT a wrapper around the model's intelligence; it is the
> environment the model acts within.**

Two value sources, with very different treatment:

| Value type | What it is | Discipline |
|---|---|---|
| **Durable substrate** | Audit DB, skill catalog, 3-tier memory, session artefacts, policy engine, Hard Invariants, firewall, budget enforcement, evaluation data | Invest like permanent infrastructure. Refactor for clarity. Build dependencies on it. |
| **Ephemeral structure** | The 4-stage pipeline shape, sub-agent-for-exploration split, correction loop, cycle-loop guards (bail-out / salvage / path-preamble), validator-style soft retries | Treat as 90-day artefacts. Design for removal. Resist hardening. |

A piece is **durable** if it would still be useful when GPT-6 / Sonnet 5
/ Opus 5 lands. A piece is **ephemeral** if its purpose is to compensate
for a current model limitation. Substrate gets refactored; ephemera
gets deleted when its limitation dissolves.

The model-cost constraint adds a third axis on top: **every ephemeral
piece is also a cost lever**. Today's cycle-loop guards save credits by
preventing the model from spinning fruitlessly. Today's sub-agent-for-
exploration shift saves credits by using a cheap model for the heavy
reading. These savings are real. They also stop mattering the moment
prompt-cache reads + native long-context become cheap enough that one
strong model can do both jobs. **Track both the engineering debt and
the savings** so when the calculus flips you can spot it.

---

## 2. Current state — substrate vs ephemeral, with removability cost

Snapshot after PR #64. Annotate each component going forward.

### Substrate (keep, invest)

| Component | Why durable | Investment direction |
|---|---|---|
| `storage/db.py` tables (`stage_outputs`, `stage_metrics`, `subagent_audit`, `sessions`, `pipeline_runs`, `orchestrator_turns`, `conversation_messages`) | Evaluation data outlives every model release | Add `agent_cycles` (L2) so per-cycle data is queryable |
| `.github/skills/<name>/SKILL.md` catalog | Article's "fat skills" — domain expertise in the cheapest-to-edit surface | Distill more skills from real sessions via `harness_distill_session` |
| `.github/memory/{MEMORY,architecture,failure-patterns}.md` | Plain text, what the model already knows how to read | Keep populating; resist any "upgrade to vector DB" temptation |
| `.harness/sessions/<sid>/*.md` artefacts | Append-only audit trail; HI #7 | No change |
| Hard Invariants #1–#8 (CLAUDE.md) | Governance contracts, model-shape-agnostic | Add HI #9 (see § 4): "every component carries a removability tag" |
| `scripts/policy_engine.py` fail-closed policy | Verification primitive (HI #5) | Add explicit firewall for any new spawn dispatch in A2 |
| `BudgetEnforcer` (`pipelineBudgetCore.ts`) | Cost guardrail — universal regardless of pipeline shape | Surface per-stage credit history in `/status` and Tasks sidebar (J.4) |
| Firewall via `_STAGE_PERMISSIONS` (HI #3) | A verification primitive, not a workflow scaffold; survives pipeline collapse | Stays even if the pipeline becomes single-stage |
| MCP tool catalog (`harness_*`) | The interface between harness and any agent runtime | Stable; new tools added on need only |

### Ephemeral (label, schedule for dissolution)

| Component | Removability cost | Expiration trigger | Cost-lever signal |
|---|---|---|---|
| 4-stage pipeline (`planner → designer → coder → reviewer`) | **>1 week** — the single biggest scaffold | Single agentic-thinking model + extended thinking + standalone reviewer skill | Today: ~30 credits/run (Sonnet × 4 stages). Future: one trace at ~12 credits. |
| Sub-agent-for-exploration shift (just shipped in PR #64) | **<1 day** — flat config across agent .md files | Cheap prompt-cache reads from one strong model | Today saves ~75% on exploration work. Worth keeping until per-token cache hit is documented. |
| Correction loop (`runAgentWithValidationRetry`) | **<1 week** — coupled to attempt counter + gate UI | Reliable structured output | Today: every retry costs 1× the stage. Future: zero retries. |
| Cycle-loop bail-out (`CONSECUTIVE_EMPTY_CYCLE_LIMIT`) | **<1 hour** — small guard in `runAgentLM` | Native agentic exploration pacing | Today: prevents 5-15 credit waste per stuck stage. |
| Cycle-loop salvage (most-recent-cycle text fallback) | **<1 hour** | Same as above | Same |
| Path-rules / empty-project / workspace-root preamble blocks | **<1 hour** each | Model reads OpenAPI specs natively + infers workspace conventions | Today: prevents path-shape failure loops. Each block ~10 lines. |
| `materializeCoderFiles` + JSON manifest contract | **<1 week** — coupled to coder Output Contract + reviewer firewall | Reliable file-edit tool use; A2's right shape | Today: keeps coder bounded. Future: model writes files under firewall directly. |
| Pre-spawn explorer/investigator/reviewer-aux fanout | **<1 day** — `preSpawnAndSplice` is one helper | Cheap one-model context-cached exploration | Today: ~3-10 credits saved per stage. |
| `runStageReviewGate` + 4-button UX | **<1 day** | The pipeline collapsing to one stage makes per-stage gating moot | Today: human-in-the-loop value, not a cost lever |

### Tagging convention

Going forward, every component should carry a one-line frontmatter
tag in its source-comment or `agent.md` file. Two tags:

- `harness-tier: substrate` — invest, refactor, build on
- `harness-tier: ephemeral · expires-when: <model capability> · cost-lever: <credits saved>` — track, don't harden, ready to delete

Every PR review should check: does this PR move a component from
ephemeral toward substrate (debt growth) or substrate toward ephemeral
(dissolution path)?

---

## 3. Convergence path — butler as the universal governed surface

The substrate-vs-ephemeral split clarifies what to delete but not what
the post-deletion shape looks like. This section is the answer.

**Today: two governed surfaces with different shapes**

| Surface | Today | Governance applied |
|---|---|---|
| Pipeline mode (`/feature-dev`) | 4-stage scaffold; coding-only | Firewall, validator, correction loop, per-stage budget halt, audit |
| Butler mode (`@harness <prompt>`) | Free-form chat; any task | Sub-agent firewall, audit (`conversation_messages`), partial — no validator, no correction loop, no budget enforcer registered |

The product gap users hit ("the pipeline can only do coding") is real
and intentional: the pipeline's governance only earns its cost when
the output is structured artefacts AND wrong output has real cost.
Coding hits both; docs / refactor / research / brainstorming don't.

**The dissolution direction is convergence, not deletion-then-rebuild.**

The butler already has the right shape — agent + tools + skills,
model-driven. What it lacks are governance primitives that today only
fire inside pipelines. Lift those into the substrate so they apply to
both surfaces, and the two modes collapse into one.

**Convergent target**

```
ONE surface: agent + skills + tools + governance primitives applied
             uniformly via substrate

  user invokes:   @harness <task>          # any task, governed
                  /<pipeline> <task>       # special case: push-heavy
                                             skill set for code-shaped work
                  plain Copilot Chat       # casual chat (unchanged)
```

The model picks the skill (already true in butler mode via
`harness_get_skill` + `harness_list_skills`). The **harness picks
governance** based on what skill is loaded — if a skill declares
`output_contract`, the validator fires; if `correction_loop: true`,
retries happen; budget enforcement is always-on.

**Project profile in memory — the missing applicability layer**

The skill catalog is universal procedural knowledge. The MEMORY layer
holds project-specific applicability:

| Layer | What it stores | Lifecycle |
|---|---|---|
| Skill catalog (`.github/skills/*/SKILL.md`) | Procedure: "how to do X" + `applies-to` + `output_contract` | Static, curated, slow-changing |
| Project profile (memory tier-2, NEW) | "This project is Python + Sphinx, not C + Word" | Per-workspace, auto-detected at session start, refined by failures |
| Failure patterns (memory tier-2, existing) | "Tried skill X here, failed because Y" | Grows as `harness_distill_session` fires |

The skill router (`applicable_skills(profile, all_skills)`) intersects
declared `applies-to` against the detected profile so the model never
sees skills that don't fit — no "tried C skill on Python", no "applied
Word skill to PDF".

This makes the butler's pull model **context-aware** without
forcing a push: the model's catalog is already filtered to what makes
sense in this workspace. Push-when-warranted (Track D step 8) is the
upgrade for explicit coding-shaped intent.

**Merged sequence**

The full path lives in `docs/roadmap.md` § Track D — Convergence. Ten
steps, ~2-3 weeks of substrate work plus ongoing skill curation, plus
the big deletion PR (gated on eval suite from Track A.1 showing no
regression).

**What this means for the pipeline**

`/feature-dev` (the 4-stage shape) keeps its cost-lever value until:

1. The eval suite shows one Sonnet-5 / Opus-5-class trace produces
   plan-through-review output at equal-or-lower cost
2. The model self-evaluates correctly when given the reviewer skill at
   the end of its own trace
3. Correction-loop fire rate drops near zero on the new model

When all three hit, Track D step 10 fires: delete `runPipeline`,
`runChunkedCodeAndReview`, `runAgentWithValidationRetry`,
`runCorrectionLoop`, the 4-stage agent fanout. Keep `_STAGE_PERMISSIONS`
but apply it via skill-context restriction inside a single trace.
Substrate intact; ephemeral structure gone. Roughly the deletion that
gets CopilotHarness to Browser-Use-scale (~600 lines of harness).

---

## 4. What to do next — three tracks

Concrete moves for the next 3 months, prioritised so each strengthens
the substrate AND keeps the cost line visible.

### Track A — Invest in the substrate (highest leverage)

**A.1 — Build the eval suite (BLOCKING; nothing else matters as much).**
Today the project has zero standing evaluation. Build `.harness/evals/`
with 5-10 representative tasks each with known-good outputs:
- "Add a new MCP tool returning `hello`"
- "Rename a class across the codebase"
- "Write tests for `pipelineBudgetCore::estimateCallCredits`"
- "Create a new SKILL.md for X"
- "Bootstrap docs for a new project from a reference repo"

Run on every model release. Capture:
- Pass / fail per task
- Per-stage cycle count, lm_ms, credits
- Which preambles fired (empty-project fallback, salvage, bail-out)
- Sub-agent spawn count

This is the article's "evaluation harness" — the most durable layer
the team can build. Without it, "stay updated" has no signal.
Estimate: 1-2 weeks. Justifies skipping every other Track B item.

**A.2 — Ship L2 (per-cycle audit table).** Per `docs/agent-progress-
tracking.md`. New SQLite table `agent_cycles`, one row per
`sendRequest`. Tells you objectively which model dissolved which
stage. ~120 lines. Pure substrate.

**A.3 — Distill skills from real sessions.** Push `harness_distill_
session` harder: any session that escalated should mine a pattern
into `failure-patterns.md` OR propose a new `SKILL.md`. The skill
catalog is the cheapest place to put domain knowledge.

**A.4 — Surface budget telemetry in the durable surfaces.** `/status`
shows running credit count. Tasks sidebar shows per-stage breakdown
(L1 already does the structure; J.4 wires the dollars). This is
substrate because it's about visibility, not workflow.

### Track B — Stop adding ephemeral structure (RESTRAINT)

**B.1 — Do NOT ship L3 (cycle history replay).** ~250 lines for a
problem L2 will reveal isn't common. Defer until L2 data shows
mid-cycle cancels are >1% of cycles. Likely answer: never.

**B.2 — Do NOT ship A2 in its currently-sketched form.** A2 as written
("re-enable coder edit/terminal tools + Output Contract change") is
adding more ephemeral structure. The right A2 when models support it:
**delete `materializeCoderFiles`, delete JSON manifest contract,
the model writes files directly under firewall.** That's a dissolution
move, not an extension. Wait for the model capability before shipping.

**B.3 — Do NOT add a 5th pipeline stage.** Any structure with the
shape "another planner / critic / judge agent" extends the dissolving
pattern. Article-direct violation.

**B.4 — Stop iterating on the cycle-loop preamble.** The current
preamble is ~60 lines of path-advice + empty-project + workspace-root
hints. It's helping today; it's also ephemera growing. Annotate
existing blocks with expiration triggers; don't add new blocks
without retiring something.

**B.5 — Don't auto-optimise the harness.** DSPy / Meta-Harness /
AutoHarness style outer-loop optimisation widens the train/prod gap
and produces unauditable structure. Article-direct warning.

### Track C — Anti-debt discipline (new, ongoing)

**C.1 — Add the `harness-tier` tag to every component.** One PR that
walks the codebase and annotates each agent `.md` file, each TS
function, each Python module. Becomes the live discipline document.

**C.2 — Add a "Dissolution Candidates" section to `docs/roadmap.md`.**
List every ephemeral component with its expiration trigger AND its
current cost-lever value (credits saved per session). Maintains the
tension: this is debt, but it's also savings.

**C.3 — Quarterly delete-pass.** First Monday of each quarter, take
4 hours, walk the codebase, ask the 1-hour-vs-1-week question per
component. Net delta tracked: what was removed, what was added.

**C.4 — Lines-of-harness vs lines-of-skill ratio.** Browser Use ships
~600 lines of harness. CopilotHarness is ~10k+ TS + Python.
Track over time. Goal: ratio improves (more skills, less harness)
even as features grow.

**C.5 — Add HI #9 to CLAUDE.md.** "Every component is tagged either
`substrate` or `ephemeral`. Ephemeral components carry an expiration
trigger. New PRs that add ephemeral components without retiring
something get pushed back."

---

## 5. The cost-of-high-end-model constraint

A real tension in the article's framing the original piece doesn't
fully address: pushing work onto the model (the "fat skills, thin
harness" direction) costs more credits per session when the model is
expensive. Today's Sonnet 4.5/4.6 at $3/$15 per million tokens means
a 4-cycle 30k-input stage is $0.36 (36 credits) — and the pipeline
might have 5 stages, so a single `/feature-dev` run is $1.50+
unbudgeted.

CopilotHarness already has the right primitives:
- `BudgetEnforcer` per session (substrate)
- Per-stage `stage_metrics` (substrate)
- Per-call credit display in chat (ephemeral display, substrate data)
- Per-pipeline `max_credits:` in yaml (substrate config)

What needs explicit thought:

**The article's recommendations are CHEAPER per session than the
status quo** because it dissolves redundant LM calls (4-stage → 1-stage
= roughly 4× fewer calls). The cost saving compounds with model
improvements.

**But during the transition, the structure that delivers cheap-model
exploration (today's sub-agent-for-exploration shift) is its own form
of ephemera.** When prompt-cache + native long-context land, the
two-model split stops paying. Don't extend it into more roles.

**Concrete rule:** any new ephemeral structure should declare BOTH:
1. Its expiration trigger (when does it dissolve?)
2. Its cost-lever value (how many credits / second does it save today?)

When the cost-lever value falls below the engineering debt cost
(roughly: maintenance hours × engineering rate / saved credits),
delete it even if the expiration trigger hasn't fully fired.

**Track this quarterly** in the Dissolution Candidates section.
Components with falling cost-lever values get promoted to the
"remove this quarter" queue.

---

## 6. How to stay updated

Five mechanisms ordered by leverage.

**S.1 — Eval suite on every model release.** Track A.1 is the keystone.
The day Anthropic / OpenAI / Google ships a notable model: 30-minute
run of the eval suite. Compare:
- Per-stage cycle count: dropping = stage compensated for capability
  that just landed
- Sub-agent spawn count: dropping = exploration moved into the main
  agent's native ability
- Preamble fire rate: dropping = preamble teaching obsolete
- Credits per task: this is the cost-lever measurement

This converts the article's abstract argument into a quarterly
removal queue. **Without the eval suite, "stay updated" is vibes.**

**S.2 — Reading list (curated, narrow).** The signal sources:
- Han Lee (`leehanchung.github.io`) — full "Hidden Technical Debt"
  series, including Agent Runtime and Agent Harness
- Cognition blog (Walden Yan) — multi-agent skepticism
- Anthropic engineering — harness/agent posts
- Lance Martin — open-deep-research write-ups (the 2024 → 2025
  evolution is the canonical case study)
- Browser Use team (Gregor Zunic) — self-healing harness pattern
- Hyung Won Chung — model architecture insights with harness
  implications
- Anthropic / OpenAI / Google release notes — capability deltas

Set up RSS or Twitter lists. Don't follow everything; these are
high-signal.

**S.3 — Quarterly harness review.** First Monday of each quarter, 4
hours. Walk:
1. `pipeline.ts` — for each function, classify via the substrate /
   ephemeral table
2. `.github/agents/*.agent.md` — apply the 1h-vs-1w question
3. The Dissolution Candidates section in `docs/roadmap.md` — has
   anything dissolved? Anything's cost-lever fallen?

Output: a short memo on this date, committed as
`docs/quarterly-reviews/<yyyy-qN>.md`. "Removed X. Added Y. Net
delta: <-N> lines of harness, <+M> lines of skill."

**S.4 — Watch your own audit data.** L2's `agent_cycles` table
combined with `stage_metrics` gives queryable answers to:
- Has planner cycle count dropped after model X release?
- Is the empty-project fallback firing less?
- Are sub-agents being spawned less often?

Sample query:
```sql
SELECT
  strftime('%Y-%m', datetime(started_at, 'unixepoch')) AS month,
  stage,
  AVG(lm_ms) AS avg_ms,
  AVG(tokens_in_estimate) AS avg_tokens_in
FROM stage_metrics
WHERE stage IN ('plan', 'design', 'code', 'review')
GROUP BY month, stage
ORDER BY month DESC, stage;
```

A consistent downward trend in `avg_ms` for a stage = that stage is
becoming dissolution-ready.

**S.5 — Thin user-facing contract.** Keep `/feature-dev` as the user
contract, regardless of what's underneath. This decouples the user
expectation from the internal structure. When the 4-stage pipeline
collapses to 1-stage in 2027, the user keeps typing `/feature-dev`
and doesn't notice. **Never expose internal stage names in the chat
UI as if they were stable.**

---

## 7. The one sentence to enforce in PR reviews

> _"Every PR moves CopilotHarness either toward thicker substrate
> (queryable audit, more skill markdown, sharper invariants) OR toward
> thinner ephemeral structure (less pipeline scaffolding, fewer
> compensating preambles). PRs that add ephemeral structure without
> retiring an equivalent amount get pushed back."_

If this sits at the top of CLAUDE.md and gets cited in code reviews,
the discipline is self-enforcing.

---

## 8. What this means in practice for the next merged branch

The sub-agent-for-exploration shift (PR #64) is a good case study of
the tension this document tries to make tractable:

- **Cost-lever value**: ~75% reduction in exploration token cost
  (haiku vs sonnet)
- **Removability cost**: <1 day (revert agent .md frontmatter)
- **Expiration trigger**: prompt-cache reads documented + native
  long-context cheap enough that single-model exploration costs
  less than two-model coordination
- **Substrate or ephemeral?**: **Ephemeral.** This is correct to
  ship today and correct to delete in 2027.

The right way to remember this: **annotate it in the agent .md files
now.** When a future reviewer reads `explorer.agent.md` and sees
`harness-tier: ephemeral · expires-when: one-model context-cached
reads cheap · cost-lever: ~75% exploration cost cut`, they have the
context to decide whether to harden or delete.

---

## References

- Lee Hanchung, "Hidden Technical Debt of AI Systems: Agent Harness"
  (May 2026) — the article this document is a faithful application of
- Lee Hanchung, "Hidden Technical Debt of AI Systems: Agent Runtime"
  (April 2026) — the predecessor piece on sandboxes / runtimes
- Hyung Won Chung (Meta) — "Add structure for the level of compute
  you have, then remove it, because the structure becomes the
  bottleneck for the next level of compute." Source quote applied
  here unchanged.
- Rich Sutton, "The Bitter Lesson" (2019) — the original argument
  that general compute beats hand-crafted structure
- Sculley et al., "Hidden Technical Debt in Machine Learning Systems"
  (NeurIPS 2015) — the framing the article inherits
- Cognition (Walden Yan), "Don't build multi-agents" — the case
  against multi-agent topologies
- Lance Martin, open-deep-research evolution — the canonical 2024 →
  2025 simplification case study
- Browser Use (Gregor Zunic) — ~600-line self-healing harness as the
  thin-harness limit case

---

## Next concrete step

Apply this lens to the next PR. The lens has three checks:

1. Does this PR strengthen the substrate or extend the ephemera?
2. If ephemera: what's the expiration trigger? What's the cost-lever?
3. If substrate: does it have a clear failure mode (graceful
   degradation when the model can't do what we expect)?

When the answers fit, ship. When they don't, push back. That is the
discipline.
