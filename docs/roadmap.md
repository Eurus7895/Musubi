# Build Roadmap

> Forward-looking plan and active work. Read through the lens of
> [`docs/harness-direction.md`](./harness-direction.md).
> Architecture and schemas → [`docs/design.md`](./design.md).
> Repo rules → [`/CLAUDE.md`](../CLAUDE.md).

This is **not** a history of what's been built. The history lives in
git log + closed PRs + the audit DB. This file is **what's next** and
**what's labelled for dissolution**, organised so that the discipline
from `harness-direction.md` is visible per item.

---

## The PR-review sentence (the discipline)

> Every PR moves CopilotHarness either toward **thicker substrate**
> (queryable audit, more skill markdown, sharper invariants) OR toward
> **thinner ephemeral structure** (less pipeline scaffolding, fewer
> compensating preambles). PRs that add ephemeral structure without
> retiring something equivalent — or strengthening the substrate — get
> pushed back.

---

## Active branches

| Branch | Purpose | Status |
|---|---|---|
| `feat/credit-totals-surfacing` | MVP item 1 — credit visibility in `/status` + sidebar + `/credits` | Open PR |
| `docs/convergence-path` | Track D + this MVP plan | Open PR |

---

## MVP Sprint

Synthesised cross-track plan: the minimum set of items that gets
CopilotHarness from "working pipeline" to **defensibly MVP**. Each
item is drawn from Track A (substrate investment), Track C (anti-debt
discipline), or Track D (convergence). Order respects dependencies;
items 1-4 are parallelisable.

After the MVP ships:
- Cost is visible at every surface
- Per-cycle audit data drives dissolution decisions
- The discipline self-enforces at PR review (tags + ratio)
- Butler mode is a genuine governed surface for non-coding work
- The skill catalog filters to project-relevant entries
- A standing eval suite tells us when the next-model release dissolves
  ephemeral structure

| # | Item | Source track | Effort | Why MVP-blocking |
|---|---|---|---|---|
| **1** | Credit visibility — `/status` + sidebar + `/credits` summed from `stage_metrics.credits` (per-row + total) | Track A.4 | ½ day | Users can't manage spend they can't see |
| **2** | Per-cycle audit table (`agent_cycles`) — one row per `sendRequest` cycle inside `runAgentLM` | Track A.2 | 1-2 days | Dissolution decisions need data; without this, "stay updated" is vibes |
| **3** | `BudgetEnforcer` per butler turn — register an enforcer at butler-turn start so `@harness <task>` carries the same cost guardrail pipelines have today | Track D.4 | ~30 lines | Today butler turns are uncapped; lifts the Phase F cost concern from "freeze the surface" to "govern the surface" |
| **4** ✓ | Project profile detection — `copilot-harness/workspace/detector.py` writes `.github/memory/project-profile.md` at session start (language, framework, doc tool, conventions auto-detected) | Track D.1 | 2-3 days | Skill applicability requires per-workspace context; memory layer is where this belongs. **DONE — PR #71 (commit `0abf648`).** Unblocks items 5, 6, 9. |
| **5** ✓ | SKILL.md `applies-to` frontmatter — parsed into `SkillMeta.applies_to` by `skill_loader.py`; shipped on the `python` and `testing` skills. `output_contract` deferred with D.5 (its only consumer). **DONE — `feat/skill-applies-to`.** | Track D.2 | 1 day | Needed before the skill router (item 6) can filter the catalog |
| **6** | Skill router — `applicable_skills(profile, all_skills)` filters `harness_list_skills` so the model never sees skills that don't fit the workspace | Track D.3 | 1 day | Eliminates "tried C skill on Python" class of failures; depends on 4 + 5 |
| **7** | `harness-tier` tag walkthrough — one PR walks every TS module, Python module, agent.md, SKILL.md, pipeline.yaml; adds the tag (HI #9 takes effect) | Track C.1 | 1-2 days | Discipline becomes self-enforcing at code review |
| **8** | README polish — first-time reader sees substrate/ephemeral framing within first 60 lines; pointer to `docs/harness-direction.md` and `docs/design.md`; the "three surfaces" choice (`/feature-dev` for code, `@harness` + skill for governed work, plain Copilot Chat for chat) called out | Stage 4 | ½ day | External-facing MVP |
| **9** | First non-coding skills with `applies-to` + `output_contract` declared from day one — at minimum: `docs-writing`, `research`, `test-writing` | Track D.9 (first slice) | per skill | Without at least 2-3 non-coding skills, the butler is unproven for anything but conversation; depends on 5 + 6 |
| **10** | Eval suite — `.harness/evals/` with 5 tasks; dual-mode runner (mocked default + `--real-lm` flag); reports per-task pass/fail + cycles + lm_ms + credits | Track A.1 (Stage 5) | 1-2 weeks | The keystone — without standing evaluation, dissolution decisions are speculation. Run on every model release. |

**Order rationale**

- **Items 1 + 3 are independent quick wins** — credit visibility (½ day) and butler-turn budget (½ day). Ship these in parallel as morale + governance wins.
- **Items 4 + 5 are the substrate foundations** for everything else in Track D — can ship in parallel.
- **Item 6 plugs 4 + 5 together** — the skill router.
- **Item 2 (per-cycle audit)** is independent — ship anytime, the longer it runs the more data.
- **Item 7 (tagging)** is independent — schedule as a focused PR session.
- **Item 8 (README)** waits for items 1-7 so it can reflect the actual state.
- **Item 9 (first skills)** depends on items 5 + 6 — without `applies-to` parsing, new skills get partial visibility.
- **Item 10 (eval suite)** is the longest-running but doesn't block items 1-9; ideally starts in parallel with item 2 so a baseline exists by the time items 6-9 land.

**Estimated timeline (focused, sequential schedule)**

| Window | Items |
|---|---|
| Week 1 | Items 1 ✓ (shipping now) + 3 (parallel) |
| Week 2 | Items 2 + 4 ✓ (parallel) |
| Week 3 | Items 5 + 6 + 7 |
| Week 4 | Items 8 + 9 (first skill draft) |
| Weeks 3-5 (overlapping) | Item 10 (eval suite curation) |

**What this leaves explicitly out of MVP**

Per Track B (Restrain) AND because these items either depend on the MVP
data OR represent premature optimisation:

- D.5 skill-driven correction loop (needs eval data first)
- D.6 distillation → profile-update path (premature)
- D.7 `/profile` command (edge case, ships when there's a profile to debug)
- D.8 push heuristic for code-like intent (refinement after baseline runs)
- D.10 delete the pipeline (gated on item 10's empirical signal)
- A.3 distillation hook (post-eval-suite — distillation works better with eval-grade signal)
- L3 cycle history replay (deferred indefinitely per Track B.1)
- A2 in current shape (deferred per Track B.2 — will be a deletion PR when models cross threshold)

**Verification**

The MVP is shippable when:
- All 10 items merged
- 838+ Python tests pass; 341+ TS tests pass
- A real `/feature-dev` run shows `/credits` + sidebar showing credits at every level
- A real `@harness <docs task>` run uses the new docs-writing skill and stays under butler budget
- The eval suite runs end-to-end at least once (mocked OK)
- `lines-of-harness vs lines-of-skill` ratio recorded as a baseline number

---

## Track A — Invest in substrate

These items strengthen what's expected to outlive model releases. They
get priority over Track B.

### A.1 — Eval suite (BLOCKING)

**The keystone.** Without standing evaluation, "stay updated" is vibes.
Every other Track A and Track C item depends on this for signal.

What:
- `.harness/evals/` directory with 5-10 representative tasks
- Each task: a request, fixture inputs, known-good outputs
- Runner that invokes `/feature-dev` programmatically and diffs against
  fixtures
- Captures per-task: pass/fail, per-stage cycle count, lm_ms, credits,
  preamble fire rate, sub-agent spawn count
- Outputs to the same SQLite audit DB

Tasks (initial):
1. **bootstrap-mcp-tool** — "add a new MCP tool returning hello"
2. **cross-cutting-rename** — "rename a class across the codebase"
3. **test-existing-helper** — "write tests for an existing pure function"
4. **new-skill** — "create a new SKILL.md for X"
5. **docs-from-reference** — "bootstrap docs for project A from project B"

Schedule:
- Nightly run on dev
- On-demand run on any model release

Effort: ~1-2 weeks for the first usable version.
harness-tier: **substrate** (eval infrastructure is durable).

### A.2 — L2 per-cycle audit table

What:
- New SQLite table `agent_cycles(session_id, stage, attempt, chunk_id,
  cycle_idx, started_at, ended_at, lm_ms, tool_calls_json, text_chars,
  credits, cycle_status)`
- New MCP tools: `harness_record_agent_cycle`, `harness_query_agent_cycles`
- Wire into `pipeline.ts::runAgentLM` after each cycle's post-flight
  budget charge

Why now: gives objective per-cycle visibility so we can measure when
ephemeral guards stop earning their keep.

**Schema sketch:**
```sql
CREATE TABLE agent_cycles (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id      TEXT NOT NULL,
  stage           TEXT NOT NULL,
  attempt         INTEGER NOT NULL,
  chunk_id        TEXT,
  cycle_idx       INTEGER NOT NULL,
  started_at      REAL NOT NULL,
  ended_at        REAL,
  lm_ms           INTEGER,
  tool_calls_json TEXT,
  text_chars      INTEGER,
  credits         REAL,
  cycle_status    TEXT NOT NULL,           -- 'completed' | 'interrupted' | 'budget_halt' | 'error'
  UNIQUE(session_id, stage, attempt, COALESCE(chunk_id,''), cycle_idx)
);
```

Effort: ~120 lines + ~10 tests = 1-2 days.
harness-tier: **substrate** (audit data is durable).

### A.3 — Skill distillation from sessions

What:
- Hook `harness_finalize_pipeline_run` to fire `harness_distill_session`
  when `final_status != "success"`
- Append one-liner to `failure-patterns.md` (light path)
- Propose `.github/agents/proposed/<name>.skill.md` for the heavy path
  when same pattern fires 3+ times

Why: skills are the cheapest optimisation surface (`harness-direction.md`
§ 3-surface table). Growing the catalog is the article-aligned
investment.

Effort: ~50 lines glue + reviewer flow = ~1 day.
harness-tier: **substrate** (the skill catalog itself is durable).

### A.4 — Budget telemetry in `/status` + sidebar

What:
- `/status` shows running credit count: "session X — 12.4 / 50 credits used (24%)"
- Tasks sidebar: per-session total at the session header; per-stage
  subtotals at stage nodes
- New `/credits` slash command: today's / this week's / this month's
  totals across all sessions

Effort: ~80 lines = half a day.
harness-tier: **substrate** (cost visibility is universal).

---

## Track B — Restrain (the article's core ask)

These items are explicitly NOT going to be built in their currently-
sketched forms. The instinct to extend exists; the discipline is to
hold off.

### B.1 — L3 cycle history replay → DEFERRED INDEFINITELY

~250 lines for a problem L2 data is expected to show isn't common.
The original idea: serialise every assistant-turn + tool-result-turn
to SQLite so an interrupted attempt can rehydrate `messages[]` and
resume mid-cycle. Wait for empirical evidence (L2 must ship first).
If interrupted-cycle rate stays below 1% of cycles after 200 sessions,
**kill it permanently.**

### B.2 — A2 (coder write/edit/terminal tools) → DEFERRED until model evidence

The original A2 sketch ("add edit/create/terminal tools alongside JSON
manifest contract") is extension, not dissolution. The right A2 when
Sonnet 5 / Opus 5 lands with reliable file editing:

> **Delete `materializeCoderFiles`. Delete JSON manifest contract.
> Let the model write files directly under the firewall.**

That's a deletion PR, not an addition PR. Wait for the model capability
before shipping.

### B.3 — No 5th pipeline stage

Any proposal of the form "add a critic / security-reviewer / docs-writer
/ refactorer agent" gets the response: **can this be a SKILL.md loaded
by an existing agent?** Almost always yes.

The 4 stages already at the structural ceiling per the article. Every
additional agent role is multi-agent topology debt.

### B.4 — Stop iterating on the cycle-loop preamble

The `runAgentLM` preamble has accumulated path-rules + empty-project +
workspace-root + progress-tracking blocks. Each is ephemeral. **No new
blocks without retiring an existing one.**

Each existing block must be annotated with an `expires-when:` comment
in the source. When its fire rate (from L2 data) drops below 5%,
delete it — even though it might still help edge cases.

### B.5 — No auto-optimised harness

DSPy / Meta-Harness / AutoHarness-style outer-loop optimisation is a
legitimate research direction and a bad production direction (per the
article: overfits, widens train/prod gap, no audit trail).

If discoveries from auto-optimisation research are useful, translate
them to hand-written `SKILL.md` files. Don't ship the optimiser.

---

## Track C — Anti-debt discipline (the meta-track)

These items make the substrate / ephemeral split visible and enforce
the dissolution cycle.

### C.1 — `harness-tier` tag on every component

One PR that walks every `pipeline.ts` function, `runners/*.ts`,
`validation/*.py`, agent `.md` file, and adds a one-line tag:

```
harness-tier: substrate
```

or

```
harness-tier: ephemeral
expires-when: <model capability that would dissolve this>
cost-lever: <credits saved per session>
```

Effort: 1-2 days for the walk-through. Ongoing discipline at PR time.

### C.2 — Dissolution Candidates table (THIS DOC, below)

Maintained in this file. See the section below.

### C.3 — Quarterly delete-pass

First Monday of each quarter, 4 hours, walked agenda:
1. 30 min: review the Dissolution Candidates table for cost-lever trend
2. 2 hours: walk `pipeline.ts` + agent files; apply the 1h-vs-1w question
3. 30 min: walk skill catalog; promote / retire
4. 1 hour: write the memo as `docs/quarterly-reviews/<yyyy-qN>.md`

Memo format: "Removed X (delta -N harness lines). Added Y (delta +M
skill lines). Net: -K harness, +L skill. Ratio: <current>:1."

First scheduled: **2026 Q3 Monday** (or whichever first Monday makes
sense post first model release).

### C.4 — Lines-of-harness vs lines-of-skill ratio

Tracked over time. Tooling: `scripts/harness_skill_ratio.py` (to be
written). Browser Use ships ~600 lines of harness as the limit case.
CopilotHarness sits at roughly 10:1 today. **Goal: ratio improves over
time even as features grow.**

Every PR description should declare net delta:
```
Net delta: -23 harness lines, +47 skill lines (ratio improved)
```

### C.5 — HI #9 added to CLAUDE.md → ✅ DONE

The "tag and expire" invariant is now in `CLAUDE.md` Hard Invariants
section.

---

## Track D — Convergence (butler as universal governed surface)

Background lens: [`docs/harness-direction.md`](./harness-direction.md)
§ 3 — Convergence path. Goal: lift the pipeline's governance primitives
into the substrate so both `/feature-dev` and `@harness <task>` share
one set of guarantees, then dissolve the staged pipeline.

Ordered for substrate-first execution. Steps 1-4 are independent and
parallelisable; steps 5-9 build on them; step 10 is the deletion
payoff gated on the eval suite (A.1).

| # | What | Substrate gain | Effort | Depends on |
|---|---|---|---|---|
| **D.1** ✓ | **Project profile detection** — `copilot-harness/workspace/detector.py` scans for stack signals (`pyproject.toml`, `package.json`, `Cargo.toml`, `conf.py`, file-extension distribution) and writes `.github/memory/project-profile.md` (new tier-2 entry) at session start. **DONE — PR #71 (commit `0abf648`).** Detector covers Python / TS / JS / Rust / Go / Java/Kotlin / Ruby / PHP, three doc tools (sphinx / mkdocs / mdbook), pytest + jest. 16 unit tests; integrated into `scripts/session_start.py` as best-effort. | Memory gains contextual awareness; substrate THICKER | 2-3 days | — |
| **D.2** | **SKILL.md frontmatter extensions** — add `applies-to` (per-skill applicability: language, framework, file types) AND `output_contract` (JSON schema for skill-driven validator) as parsed frontmatter fields | Skill catalog gains both applicability + validation declarations | 1 day | — |
| **D.3** | **Skill router** — `applicable_skills(profile, all_skills)` helper + filter inside `harness_list_skills`. Skills with no `applies-to` declaration are treated as universal | Model sees only project-relevant skills; eliminates "tried C skill on Python" class of failures | 1 day | D.1 + D.2 |
| **D.4** | **BudgetEnforcer per butler turn** — `runOrchestrator` (code name kept until rename PR) registers an enforcer for the turn (config from `copilotHarness.butlerBudget` setting, defaults from current butler cost data); same primitives used by pipelines | Cost governance applies to `@harness` work too | ~30 lines | — (quick win, independent) |
| **D.5** | **Skill-driven correction loop on `output_contract`** — when a loaded skill declares `output_contract` and the model's response fails the schema, re-enter the turn with `validation_feedback` injected (same pattern as `runAgentWithValidationRetry` but skill-scoped) | Butler gains pipeline-style retries when warranted, off when not | ~60 lines | D.2 |
| **D.6** | **Failure-pattern → profile-update path** — extend `harness_distill_session` so that when a skill is applied + fails in a way that reveals profile is wrong, both `failure-patterns.md` (the pattern) AND `project-profile.md` (the corrected field) get updated | Memory self-corrects on observed evidence | 1 day | D.1 + D.5 |
| **D.7** | **`/profile` command** — manual inspection (`/profile`) and override (`/profile <field>=<value>`) of project-profile.md. Rare; emergency hatch for auto-detection misses | User has clear escape hatch | half-day | D.1 |
| **D.8** | **Heuristic skill push** — when `@harness <task>` matches "code-like" intent (regex on keywords + presence of file paths in the request), auto-push the same skills the planner would inject; otherwise stay pull-only. The push-vs-pull boundary becomes a context decision, not a mode decision | Bridges the butler and pipeline behaviour gap without forcing pipeline structure | ~50 lines | D.3 |
| **D.9** | **Skill catalog growth** — `docs-writing`, `refactoring`, `research`, `test-writing`, each with `applies-to` and `output_contract` declared from the start. Each new skill ships with at least one applicability tag | Fat-skills direction shipped; non-coding work finally has a governed surface | per skill (ongoing) | D.2 + D.3 |
| **D.10** | **Delete the 4-stage pipeline shape** — `runPipeline`, `runChunkedCodeAndReview`, `runAgentWithValidationRetry`, `runCorrectionLoop`, the 4-stage agent fanout, materializeCoderFiles + JSON manifest contract. Keep `_STAGE_PERMISSIONS` but apply it via skill-context restriction in a single trace | NET DELETE — biggest dissolution win; substrate intact, ephemeral structure gone | ~big deletion PR | D.1-D.9 + Track A.1 eval suite showing no regression |

**Why this order**

- D.1-D.4 are independent foundations that can ship in any order
- D.5 closes the validator loop in the butler (depends on D.2's `output_contract` field)
- D.6 closes the memory feedback loop (depends on D.1 + D.5)
- D.7 is the user escape hatch (depends on D.1)
- D.8 is the bridge that makes butler behave like pipeline when warranted
- D.9 grows the skill catalog with proper applicability + contracts from day one
- D.10 is gated on the eval suite from Track A.1 — never dissolve speculatively

**Estimated timeline**

| Window | Work |
|---|---|
| Sprint 1 (week 1) | D.1 + D.4 in parallel (independent quick wins) |
| Sprint 2 (week 2) | D.2 + D.3 (skill catalog work) |
| Sprint 3 (week 3) | D.5 + D.6 + D.7 (validator, memory loop, escape hatch) |
| Sprint 4 (week 4) | D.8 + start D.9 (push heuristic + first non-coding skills) |
| Ongoing | D.9 catalog growth, A.1 eval suite collecting data |
| Eventually | D.10 deletion PR when eval suite signals threshold crossed |

**What this dissolves (from the table below)**

After D.10 fires, several entries in the Dissolution Candidates table
get marked DONE and removed from this doc:

- "4-stage pipeline shape" → deleted
- "Correction loop (`runAgentWithValidationRetry`)" → deleted (skill-driven loop in D.5 replaces it)
- "`materializeCoderFiles` + JSON manifest contract" → deleted (model writes files directly under firewall)
- "Pre-spawn fanout (`preSpawnAndSplice`)" → deleted (skill router + push heuristic replace it)
- "`runStageReviewGate` 4-button UX" → deleted or reshaped (one-trace agent has different intervention points)

CopilotHarness shrinks from ~10k+ lines toward Browser-Use-scale
(~600 lines of harness). Substrate intact; product surface unified.

---

## Dissolution Candidates

Every **ephemeral** component with its expiration trigger and current
cost-lever value. Updated each quarterly review.

| Component | Tagged since | Expires when | Cost-lever today | Last quarter | Trend |
|---|---|---|---|---|---|
| 4-stage pipeline shape (`planner → designer → coder → reviewer`) | 2026-06 | Single agentic-thinking model + extended thinking + standalone reviewer skill | ~30 credits/run (Sonnet × 4 stages) avoided cost of unconstrained one-shot | n/a | n/a |
| Sub-agent-for-exploration split (PR #64) | 2026-06 | Prompt-cache reads + native long-context cheap enough that single-model wins | ~75% exploration cost cut = ~15 credits/session saved | n/a | n/a |
| Correction loop (`runAgentWithValidationRetry`) | 2026-06 | Native structured-output reliability | 1× stage cost per retry avoided | n/a | n/a |
| Cycle-loop bail-out (`CONSECUTIVE_EMPTY_CYCLE_LIMIT=3`) | 2026-06 | Agentic-thinking models pace own exploration | 6 credits/fire × ~5% fire rate = ~0.3 credits/session | n/a | n/a |
| Cycle-loop salvage (most-recent-cycle text fallback) | 2026-06 | Same as bail-out | Recovers some salvage cases that would otherwise escalate | n/a | n/a |
| Path-rules preamble block (~15 lines in `runAgentLM`) | 2026-06 | Model reads OpenAPI / infers workspace conventions natively | Prevents path-shape failure loops; ~8 credits/avoided-stuck-stage | n/a | n/a |
| Empty-project fallback block (~13 lines) | 2026-06 | Same as path-rules | ~8 credits saved per stuck planner | n/a | n/a |
| Workspace-root injection (~8 lines) | 2026-06 | Tools accept relative paths universally | Prevents "use absolute path" failures | n/a | n/a |
| `materializeCoderFiles` + JSON manifest contract | 2026-06 | Model file-edit tool reliability + path-scoped enforcement substrate | Deterministic disk writes today | n/a | n/a |
| Pre-spawn fanout (`preSpawnAndSplice`) | 2026-06 | Cheap one-model context-cached exploration | ~3-10 credits/stage saved | n/a | n/a |
| `runStageReviewGate` 4-button UX | 2026-06 | Pipeline collapses to 1-stage; per-stage gating moot | Human-in-the-loop value (not a cost lever) | n/a | n/a |

**Reading rule:** when `cost-lever today` falls below maintenance cost,
delete the component **even if the expiration trigger hasn't fully
fired.** The quarterly review (C.3) makes this call.

---

## Cost discipline

CopilotHarness already has the substrate primitives (`BudgetEnforcer`,
`stage_metrics`, per-call display, `pipeline.yaml::max_credits`). What
needs explicit thought:

**The article's recommendations are CHEAPER per session than the
status quo** because dissolving redundant stages = fewer LM calls.

**During the transition**, structures that save credits today (sub-
agent-for-exploration shift, bail-out guard) are themselves ephemera.
When the next model makes them unnecessary, the **savings collapse**.
Track both the engineering debt AND the savings; when the calculus
flips, delete.

**Concrete rule:** any new ephemeral structure declares BOTH:
1. `expires-when:` — when does this dissolve?
2. `cost-lever:` — how many credits/session does it save today?

When cost-lever falls below maintenance cost (roughly: maintenance
hours × engineering rate / credits saved per period), delete.

---

## Phase J — cost control under token billing (active)

Token billing on Copilot moved this from "phase I planned" to "phase J
active." Most of Phase J is shipped:

- **J.1** (model selection): per-agent `model:` frontmatter + global
  `copilotHarness.modelOverride` → ✅ shipped
- **J.2** (context cap): three-layer resolver (`pipeline.yaml > setting
  > default 50k`) → ✅ shipped
- **J.3** (credit budget): `BudgetEnforcer` + `max_credits` + `warn_at`
  → ✅ shipped
- **J.4** (cost telemetry surfacing): see Track A.4 above
- **J.5** (cache verification): empirical confirmation that
  `copilot_cache_control` actually hits cache. Quota-gated, zero code.

---

## See also

- [`docs/harness-direction.md`](./harness-direction.md) — the lens. Read
  this first.
- [`docs/design.md`](./design.md) — substrate architecture and schemas.
- [`docs/usecase-diagram.md`](./usecase-diagram.md) — what `/feature-dev`
  does from a user's POV.
- [`/CLAUDE.md`](../CLAUDE.md) — Hard Invariants, conventions, git rules.
