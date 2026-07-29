# LLM-owned scope, substrate-owned evidence

**Status: proposal. Nothing implemented. Two forks and one Hard-Invariant tag
change need Eurus's decision before step 1.**

## Context

Musubi decides the shape of a turn three times, in three different ways:

| # | Where | How | When |
|---|---|---|---|
| 1 | `agent/scope.py::classify_task` | ~12 regexes over the sentence | before any model call |
| 2 | `agent/change_assessment.py::assess_request` | 5 more regexes | inside #1 |
| 3 | `agent/change_assessment.py::assess_manifest` | arithmetic over the planner's declared JSON | after the planner reads code |

Only #3 is evidence-based. #1 and #2 judge English with pattern matching, and
the repository already says out loud that this cannot work. From the shipped
`request-triage` skill (`.github/skills/request-triage/SKILL.md`), pushed to
the planner today:

> **The harness makes no judgment about how large or how risky a change is.
> It cannot: nothing readable from one sentence establishes blast radius, and
> keyword matching proved it** — "fix the typo in the security section of the
> README" read as critical, while "wire up Okta" read as routine.

That sentence is true of `assess_manifest`. It is false of `classify_task`,
which is still making exactly the judgment the skill says is impossible. The
`2026-07-26-manifest-owns-blast-radius` track removed the two worst offenders
(`_CRITICAL_RISK_RE`, `_LARGE_RISK_RE >= 2`); what remains is the same species.

**Two defects fixed on 2026-07-29 are symptoms of this, not the disease.**
`b92dc23` (nothing counted to one clarification) and `6936093` (the question
asked what the gate could not test) both patch a layer this plan deletes. They
ship now because this track is not a weekend; they are ephemeral by design and
their expiry trigger is step 4 below.

**What is genuinely worth keeping.** Not everything deterministic here is a
judgment about work. Three things are facts or safety, and they stay:

- *Destructive-operation refusal* (`_DESTRUCTIVE_FILE_RE` → `manual_destructive`).
  Not a scope opinion — a safety gate, and cheap to be wrong about in the safe
  direction.
- *`assess_manifest`* — deterministic arithmetic over what an LLM declared. This
  is the governance model this plan generalizes, not replaces.
- *`GoalState.manifest_overrun`* — compares the declared radius against files
  actually touched. Without it an LLM-declared scope is *trusted* rather than
  governed.

## Goal

One component judges scope, and it is one that has read something. The
substrate stops guessing meaning and starts proving **evidence sufficiency** —
a question it can actually answer.

Concretely:

- The planner owns blast radius for anything that mutates (it already does, via
  the manifest; this plan removes the pre-judgment that competes with it).
- The root owns "what kind of turn is this", inside a model call it is already
  paying for.
- The substrate owns three provable things: *is the evidence present*, *does the
  declaration hold up*, and *is this operation destructive*.

## Non-goals

- Adding an LLM call to `server.py`, any `musubi_*` tool, `policy_engine.py`,
  the firewall, the validator, or the audit DB. **HI #1 is not touched** — see
  the invariant analysis below.
- Removing `assess_manifest`, the `_STAGE_PERMISSIONS` firewall, token budgets,
  or the append-only stage store.

## Hard Invariant analysis (read before approving)

**HI #1 — zero LLM calls in the substrate: strengthened, not violated.** HI #1
forbids the *substrate* from reaching a model. This plan moves judgment out of
`scope.py` (a driver-side module that makes no model calls today and will make
none after) and into the planner worker and the root — both of which already
reach the model through `LMRouter`. No new component gains an LLM dependency.
The substrate's remaining role gets *more* deterministic, not less.

**HI #9 — tag and expire: requires a change you must approve.**
`agent/scope.py` and `agent/change_assessment.py` are both tagged:

```
musubi-tier: substrate
expires-when: never - risk/ambiguity/blast-radius hints are durable routing
  context even as model quality improves.
```

That "never" is the claim this plan contradicts. Step 4 re-tiers the *lexical
judgment* portion to `ephemeral` with `expires-when: the root triages its own
turn` and `cost-lever: deletes ~12 regexes and the pre-run halt`. The safety
gate and `assess_manifest` keep `substrate`. **CLAUDE.md says a change that
would break an invariant stops and asks — this is the ask.**

## Design: three layers, honestly named

### Layer 1 — Safety gates (deterministic, keep, ~20 lines)

Runs before anything. Judges no work size:

- destructive file operation → manual operator steps, zero tokens;
- empty message → no-op.

Everything else falls through. Notably `_CASUAL_RE` ("hi", "thanks") is
*demoted* to a hint: it is a cost saver, not a safety property, and it belongs
with the other hints in layer 3.

### Layer 2 — Evidence sufficiency (deterministic, NEW)

The substrate's new job. It answers questions about **the record**, never about
meaning, so every answer is checkable:

| Predicate | Source of truth |
|---|---|
| `names_workspace_path` | does any token in the merged request resolve *inside* `_workspace_root()` (`tools/fs.py:63`)? |
| `path_exists` | does that resolved path exist on disk? |
| `has_conversation` | `conversations.has_history(chat_id)` |
| `explorer_findings` | has an explorer outcome landed in this `GoalState` / this chat? |
| `clarification_answered` | `db.pending_clarification(chat_id)` (shipped in `b92dc23`) |
| `barren_turns` | `db.chat_turn_usage(chat_id)` (shipped) |

None of these is an opinion. `names_workspace_path` is a `Path.relative_to`
call — the same one the firewall already makes. This vector is rendered into
the root's prompt as evidence, replacing today's `[agent-routing-scope]` block,
and it is what "the root needs to collect enough information" becomes in code:
**the root is told what it does and does not have, and the deterministic rule
is that a mutation may not reach a coder while the vector says the target is
unknown.**

### Layer 3 — Judgment (LLM)

- **The root**, in its first cycle — a model call it already pays for, so the
  marginal cost of triage is ~0 spawns — decides: conversation, question,
  read-only inspection, or work. Today's `_ADVISORY_RE` / `_INSPECT_RE` /
  `_DIAGNOSTIC_RE` verdicts become *hints* in the prompt, overridable, not
  routes.
- **The planner** owns blast radius for anything that mutates, exactly as the
  `request-triage` skill already instructs, and emits the manifest.
- **The substrate** enforces: `assess_manifest` routes on the declaration,
  `manifest_overrun` checks the declaration against reality.

## Tech stack

Python 3.11, existing modules. No new dependency, no new component. One new
module `agent/evidence.py` (layer 2) and net **deletion** in `scope.py` /
`change_assessment.py`.

## Implementation steps

- [ ] **Step 1 — evidence vector.** New `agent/evidence.py` with the six
  predicates above and a `prompt_block()` renderer. Pure functions over the
  request text, the workspace root, and the DB. Tagged `musubi-tier: substrate`,
  `expires-when: never` (it proves facts, not judgments). Wired into `run_agent`
  beside today's scope block; nothing routes on it yet, so this step is
  observable in the logs before it changes any behavior.

- [ ] **Step 2 — sufficiency rule for mutation.** `GoalState` gains a
  deterministic gate: a `coder` spawn is refused while the evidence vector says
  no workspace path is named *and* no explorer findings and no manifest exist.
  This is the enforceable core of "collect enough information first" — same
  shape as today's role-order gate, which already refuses a coder before the
  planner's manifest lands. Fail-closed; the refusal names the legal next role
  (`explorer` or `planner`).

- [ ] **Step 3 — root triage prompt.** Rewrite the routing block: evidence
  vector + overridable hints instead of a decided route. The root states its
  chosen turn shape in one line, which is logged and audited so a wrong triage
  is attributable post-hoc.

- [ ] **Step 4 — delete the lexical judgment.** Remove `assess_request` and
  `_BROAD_PRODUCT_RE`, `_STATIC_FILE_RE`, `_BOUNDED_ARTIFACT_RE`,
  `_FRAMEWORK_RE`, `_MULTIPART_RE`, `_ARTIFACT_RE`, `_SIMPLE_EDIT_RE`,
  `_NO_SHORTCUT_RE`, `_VAGUE_RE`, and with them the pre-run `ask_scope` halt —
  and therefore `BROAD_PRODUCT_QUESTION`, `clarification_request`, and
  `pending_clarification` (both of today's fixes). Re-tier what remains. This
  step is where the cost profile changes, so it lands last and behind the
  measurements from step 1.

- [ ] **Step 5 — enforce the declaration.** Promote `manifest_overrun` from a
  prompt warning to a hard stop on the coder path. With scope LLM-declared, an
  under-declared radius is the primary abuse channel and it must cost the run,
  not a paragraph.

## The cost trade-off you are buying

Today's cheap paths are cheap because a regex decided without a model:

| Turn today | Cost today | Cost after |
|---|---|---|
| "hi" | 0 tokens, 0 ms | 1 root call (~1–2k tokens) unless kept as a hint-level fast path |
| "explain each" | 1 root call, no tools | unchanged |
| "read run.py" | 1 explorer | 1 root call + 1 explorer |
| "create a website" | 0 tokens (halt) | 1 root call, then explorer/planner as the root judges |
| medium change | planner + coder | unchanged |

The observed planner round trip in the traced runs was **30–61 s and
10–27k tokens**. Routing *every* request through a planner would be the
expensive reading of "the planner decides scope" — which is why this plan
splits the question: the root (already paid for) decides *what kind of turn*,
the planner decides *how big the change* only when something will mutate.

## Verification

- Step 1 ships behind no behavior change: assert the vector's six predicates
  against fixtures, including a path outside the workspace root (the
  `09_CD_Team` case from the traced session) and a path that resolves inside but
  does not exist.
- Step 2: a coder spawn with an empty evidence vector is refused, writes no
  `subagent_audit` row, and names `explorer` as the legal next role — the same
  test shape as `test_root_coder_spawn_is_refused_until_planner_manifest_lands`.
- Step 4: the traced conversation replays end-to-end without the canned
  question, and the `create a website` → `weather checking` sequence reaches a
  file.
- Step 5: a manifest declaring 1 file while the worker touches 6 halts the run.

## Decisions required before step 1

1. **Who triages the turn** — root-in-first-cycle (my recommendation: it is
   already a model call, so triage is ~free) versus planner-always (evidence-
   based but pays 30–61 s on every "read run.py").
2. **Does `_CASUAL_RE` keep its zero-token fast path**, or does "hi" become a
   model call? Recommendation: keep it, as the one hint promoted to a route,
   and accept that it is a cost hack rather than a principle.
3. **Approve the `musubi-tier` change** on the lexical judgment layer
   (`substrate / expires-when: never` → `ephemeral / expires-when: the root
   triages its own turn`). Without this, step 4 cannot land.
