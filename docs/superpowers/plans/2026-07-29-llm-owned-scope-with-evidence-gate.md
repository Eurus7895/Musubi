# LLM-owned scope, substrate-owned evidence

**Status: governing principle decided (2026-07-29). Step 0 shipped — the
destructive gate moved from judging a sentence to measuring a call, and the
tier change landed with it. Step 1 shipped — the evidence vector is rendered
and logged, and routes nothing. Steps 2–5 (the sufficiency rule and the
deletion it unlocks) not started. No open questions.**

## What shipped (step 0)

Not in the original step list: the destructive gate was the one open question
below, and answering it turned out to be a prerequisite, because the tier
change could not land while a safety guard lived inside the layer being
re-tagged.

| Commit | What it did |
|---|---|
| `a689dba` | `_describe_exc` unwraps nested `anyio` groups (the uninformative `!mcp … skipped` line); three duplicated helpers single-owned |
| `c70e899` | `RouteKind` StrEnum; `agent/config.py::config_candidates` shared by MCP and profile discovery |
| `d579c75` | Judging split from enforcing, one file each: `change_assessment.py` → `manifest.py` (substrate), all 19 lexical regexes into `scope.py` (ephemeral) |
| `dd41918` | `agent/blast_radius.py` — the gate measures the CALL. `_DESTRUCTIVE_FILE_RE` demoted from route to prompt warning |
| `c896bcc` | Approval is a token the harness mints and matches literally against a user message |
| `f61669d` | Console Approve/Reject; the harness re-appends any token the model dropped from its answer |
| *(step 1)* | `agent/evidence.py` — six facts about the record, rendered and logged, routing nothing |
| *(review)* | PR #164 review: the gate failed OPEN in five ways, and a leaf worker could never be granted an approval — see below |

### What review caught (PR #164)

An automated review found the gate answering "harmless" where the honest answer
was "unreadable". Each case was reproduced before it was fixed, and each now
has a test naming the deletion it would have performed:

| Command | Was | Why |
|---|---|---|
| `sudo rm -rf build` | **passed** — 3 files | only the first token was read, and it said `sudo` |
| `env rm x`, `command rm x`, `nice -n 10 rm x` | **passed** | same |
| `rm /abs/path/*.txt` | **passed** — 4 files | `lstrip("./")` stripped the leading `/` and globbed the remainder *under* the root, matching nothing |
| `del C:\ws\a.txt` | **passed** | posix `shlex` eats backslashes; `C:wsa.txt` resolves nowhere while `cmd.exe` deletes the real file |
| `rm a.txt` with `cwd=sub` | **passed** | measured `<root>/a.txt`, deleted `<root>/sub/a.txt` |

One root cause, five symptoms: `_expand` returned `[]` — "nothing there" — for
targets it could not attribute. It now returns `None` for "cannot read this",
which `measure` turns into `unanalyzable`. Bare wrappers are stepped over; a
wrapper carrying its own options is declared unreadable rather than guessed at;
a delete whose text contains a backslash is unreadable; `cwd` is honoured for
both measurement and the grant key, so an approval cannot travel between
directories. The negatives that make the gate tolerable are pinned in the same
file: `grep -r rm .`, `sudo apt install`, `rm never-existed.txt` all still pass.

Two more, neither about parsing:

- **A leaf worker could never be approved.** Gate state lived on
  `Orchestration`, which describes a position in the SPAWN TREE. A coder — the
  role most likely to delete something — is handed `orchestration=None` on
  purpose, so its refusal was recorded nowhere: the user could echo the exact
  token and the same deletion would be refused again, forever.
  `Orchestration.child()` dropped it too. The state moved to a run-scoped
  `DestructiveGate` behind a `ContextVar`, following the `_worker_touched_files`
  precedent. The overwrite ceiling now also counts across workers, which is
  what "per-run" claimed all along.
- **The clarification merge depended on how the ANSWER classified.** The
  pending request was consulted only when the new message itself routed to
  `ask_scope`, but the question offers "React" and "a single static HTML page",
  and both classify as bare advisory follow-ups once the chat has history.
  Those turns were answered as advice, and the completed turn cleared the
  pending marker — losing the build request. The pending request is now checked
  BEFORE classifying.

**Not fixed, and deliberately so: external MCP tools are outside the gate.**
`measure` knows two tool names. A federated server exposing its own delete tool
is measured as harmless. The tempting fix — inferring "is this destructive?"
from a name or JSON schema — is a guess about meaning that nothing can check,
i.e. the discredited lexical guard rebuilt one layer down and handed a security
job. Closing it properly needs a declared capability in the MCP contract or an
explicit operator allowlist, which is a design decision. Until then `run_agent`
logs the uncovered external tools by name at startup, so the boundary is
visible in every run's log rather than implied.

## The principle, in Eurus's words

> *"ai triage cũng giống scope và change request, đều không nên để code quyết
> định"* — who triages a turn, what its scope is, and how large the change is
> are the same kind of question, and none of them should be decided by code.

This settles the fork the plan opened between "the root triages" and "the
planner triages": neither is a rule the substrate encodes. The root is simply
the first model in the loop, so it is where a judgment first becomes possible —
not because a line of code awards it the job.

### Where the line falls: judging vs enforcing

The principle cannot mean "code decides nothing" — the policy engine, the
firewall, the budgets and the manifest ceilings are all code deciding, and all
of them stay. The distinguishing question is what the decision is made FROM:

- **Judging** = reading English and forming an opinion about it. *Is this
  request broad? Is it sensitive? Is it an edit or a question?* Text is the
  only input, and nothing checks the answer. **This is what code stops doing.**
- **Enforcing** = checking a claim or a fact against a rule. *This manifest
  declares 6 files, the ceiling is 5. This path resolves outside the workspace
  root. This worker touched 11 files after declaring 1.* The input is a
  measurement, and the answer is verifiable. **This is what code keeps doing,
  and does more of.**

Every deterministic decision in the host sorts cleanly into one column:

| Judges (dies) | Enforces (stays) |
|---|---|
| `_BROAD_PRODUCT_RE`, `_STATIC_FILE_RE`, `_BOUNDED_ARTIFACT_RE`, `_FRAMEWORK_RE`, `_MULTIPART_RE` — is this request broad? | `assess_manifest` — arithmetic over the planner's declaration |
| `_ARTIFACT_RE`, `_SIMPLE_EDIT_RE` — is this a small edit? | `manifest_overrun` — declared radius vs files actually touched |
| `_NO_SHORTCUT_RE` — is this sensitive? (wrong in both directions, proven) | `policy_engine` membership + tool gates (HI #5) |
| `_ADVISORY_RE`, `_INSPECT_RE`, `_DIAGNOSTIC_RE` — what does the user want? | `_STAGE_PERMISSIONS` evaluator firewall (HI #3) |
| `_VAGUE_RE`, `_CASUAL_RE` — is this a real request? | `TokenBudgetEnforcer`, stage allowances |
| `_DESTRUCTIVE_FILE_RE` — see below | `fs.resolve_path` workspace containment |
| all of `assess_request` | the evidence vector (step 1) — facts about the record |

### The destructive gate — decided, and shipped as (b)

`_DESTRUCTIVE_FILE_RE` refuses a turn whose *sentence* mentions deleting files.
Under the principle it is a judgment and dies with the rest. Two facts say it
should die regardless:

1. It is already inconsistent with the substrate's own stated position.
   `musubi_run_command`'s contract says, verbatim: *"No 'dangerous command'
   detection — the user is in control of what the model can do."* The substrate
   declines to guess at the shell boundary, where it can see the real command,
   while guessing at the sentence boundary, where it can only see intent.
2. It therefore blocks the honest case and misses the rest. "delete all
   \*.html" is refused; a model that reaches the same outcome through
   `musubi_run_command` is not.

What replaces it is a real decision:

- **(a) Nothing.** Consistent with `musubi_run_command`; the user is in control.
- **(b) A fact-based confirmation at the tool boundary.** A call that would
  delete or overwrite files pauses for the user. This is enforcement on a
  measurement, not judgment on a sentence — it fits the principle and covers
  the path the lexical gate never saw.

**(b) was chosen** (`dd41918`, `c896bcc`, `f61669d`), with the shape settled in
conversation rather than assumed here:

- **The lexical regex became a warning, not a refusal.** `_DESTRUCTIVE_FILE_RE`
  survives as `DESTRUCTIVE_WARNING` on `ScopeHint.warnings` — it tells the model
  what the sentence looks like and routes nothing. `RouteKind.MANUAL_DESTRUCTIVE`
  was added during the refactor and then deleted, because after the demotion no
  code path could produce it.
- **The hard stop is arithmetic at the tool boundary.** `agent/blast_radius.py`
  `measure(tool_name, args)` resolves what a call would destroy, before the call
  runs. Deletes are counted from the argv (`rm`, `rmdir`, `del`, `git clean`, …
  **in command position** — `grep -r rm .` passes, `find … | xargs rm` does
  not); overwrites are counted per `musubi_write_file`. Thresholds: **delete
  N=1, overwrite N=5 per run.** A shell command whose targets cannot be resolved
  statically (`|`, `;`, backticks, `$( )`, `xargs`, `find`, `eval`) is
  `unanalyzable`, which is over threshold — fail-closed.
- **Consent is verifiable, not interpreted.** The refusal carries a token
  `allow-` + 6 hex of `sha256` over the SORTED destruction keys, so the same
  file set always mints the same token and one extra file mints a different one
  — approval cannot silently widen. The harness matches that literal string
  against the **user-role** message. A model cannot author a user turn
  (`_append_chat_message(chat_id, "user", …)` is fed by the CLI argument or the
  Console input box), so the token's presence is structural proof a human put it
  there. Unreadable storage yields an empty grant set: the gate stays shut.
- **Two surfaces, one mechanism.** The Console renders Approve/Reject beside the
  refusal; Approve submits the token through the same `send_chat` route the
  composer uses, so the GUI holds no authority the CLI lacks. Reject sends
  nothing — the call was already blocked and the turn already ended, so
  declining is simply not granting.
- **The token no longer depends on the model.** The refusal reaches the model as
  a tool result and the model writes the user's answer itself; a paraphrase used
  to leave the user with no token and therefore no way to approve.
  `run.py::_ensure_grant_visible` re-appends any grant the answer dropped.

`MUSUBI_ALLOW_DESTRUCTIVE` remains, narrowed in purpose to unattended
automation. It is not the interactive path: it grants for a whole run rather
than the measured files, and an exported value disables the gate silently and
permanently.

**Why this is enforcement, not judgment.** The old guard read the user's
sentence and formed an opinion; the new one reads the call's arguments and
counts. It answers "how many files does *this* delete", which is checkable, in
place of "does this person sound destructive", which is not. That is why it
survives a principle that deletes the rest of the lexical layer — and why the
two are not in tension.

### Consequence for the tier change — shipped in `d579c75`

The principle settles the `musubi-tier` question: a layer whose only job is
judging English is temporary by definition. The split was also cheaper than this
plan first described — TWO files moved, not three:

- `agent/scope.py` became `ephemeral` **whole**, with
  `expires-when: the root triages its own turn from the evidence vector` and
  `cost-lever: deletes 18 of 19 regexes, assess_request, the pre-run ask_scope
  halt, BROAD_PRODUCT_QUESTION, and the pending_clarification storage column`.
- `assess_request` + its 5 regexes + `BROAD_PRODUCT_QUESTION` moved OUT of
  `change_assessment.py` INTO `scope.py`, so the deletion is one file.
- `change_assessment.py` was renamed `manifest.py`, keeps only the manifest
  half, and stays `substrate`. Its "never" justification is now the honest one:
  *arithmetic over an LLM-declared blast radius is governance, not a
  compensation for a weak model — a stronger planner makes the DECLARATION
  better; it does not remove the need to check it.*

No new module for the split itself; `blast_radius.py` and `routes.py` are new
because the gate needed a home outside the layer being retired.

**Why one file could not carry both tiers.** A `musubi-tier` tag is a promise
about a whole file's lifetime — `scripts/check_musubi_tier.py` reads one tag per
file, and "delete this when X" cannot be true of half a module. The old
`change_assessment.py` held a judging half that dies and an enforcing half that
never dies, so no single honest tag existed for it. Splitting the file *is* the
tier change; the rename follows from it.

## Context

Musubi decides the shape of a turn three times, in three different ways:

| # | Where | How | When |
|---|---|---|---|
| 1 | `agent/scope.py::classify_task` | ~12 regexes over the sentence | before any model call |
| 2 | `agent/change_assessment.py::assess_request` | 5 more regexes | inside #1 |
| 3 | `agent/change_assessment.py::assess_manifest` | arithmetic over the planner's declared JSON | after the planner reads code |

*(Paths as of writing. After `d579c75`: #1 and #2 both live in `agent/scope.py`,
#3 in `agent/manifest.py`, and a fourth decision — `agent/blast_radius.py`, at
the tool boundary — was added.)*

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

- ~~*Destructive-operation refusal* (`_DESTRUCTIVE_FILE_RE` →
  `manual_destructive`). Not a scope opinion — a safety gate, and cheap to be
  wrong about in the safe direction.~~ **Superseded.** "Cheap to be wrong about
  in the safe direction" was the assumption, and it was false in both
  directions: the regex refused the honest request ("delete all \*.html") while
  `rm -rf build` reached `musubi_run_command` untouched. What is kept is the
  *intent*, re-founded on a measurement — see the destructive gate above.
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

That "never" is the claim this plan contradicts. **Asked and approved
(2026-07-29); shipped in `d579c75`** — the lexical judgment moved to
`ephemeral`, `assess_manifest` kept `substrate` under a rewritten justification,
and `blast_radius.py` was born `substrate` because measuring a call is not a
judgment that a better model makes unnecessary.

## Design: three layers, honestly named

### Layer 1 — Safety gates (deterministic, keep, ~20 lines)

Runs before anything. Judges no work size:

- destructive file operation → manual operator steps, zero tokens;
- empty message → no-op.

Everything else falls through. Notably `_CASUAL_RE` ("hi", "thanks") is
*demoted* to a hint: it is a cost saver, not a safety property, and it belongs
with the other hints in layer 3. **Decided 2026-07-29: it is deleted outright**
— Eurus, *"hi không cần giữ"* — so "hi" costs one root call like everything
else, and no branch survives to justify itself on price.

After step 0 this layer is thinner than the plan assumed: the destructive check
is no longer here at all. It moved to the tool boundary, where it can see the
call. What remains for step 4 to remove is the empty-message no-op and the
`DESTRUCTIVE_WARNING` string.

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

- [x] **Step 0 — the destructive gate (not originally in this list).** Measure
  the call instead of judging the sentence; re-tier the lexical layer once the
  safety guard no longer lives inside it. See "What shipped" above.

- [x] **Step 1 — evidence vector.** `agent/evidence.py`, `musubi-tier:
  substrate`, `expires-when: never`. Rendered into the root prompt after the
  scope hint and printed once per turn as `[agent] evidence: …`. **Nothing
  routes on it**, which a test pins directly.

  Two departures from the sketch above, both from writing it:

  - **The DB facts are passed in, not queried.** `run_agent` already reads
    `_chat_has_history`, `_pending_clarification`, and `chat_turn_usage` for its
    own purposes; a second query path would be a second thing to keep true.
    `evidence.py` imports no storage module and needs no database in tests.
  - **A fourth output: `escaped_paths`.** The sketch had one path predicate
    resolving to true/false. In practice three situations matter and the sketch
    conflated two of them:

    | Request | `names_workspace_path` | `path_exists` | Meaning |
    |---|---|---|---|
    | `read agent/run.py` | true | true | target known and present |
    | `edit agent/gone.py` | true | false | target known, **not vague** — the filesystem already answered |
    | `create a website` | false | false | nothing establishes a target |
    | `summarize /etc/hosts` | false | false | + `escaped_paths` — no worker can reach it |

    The middle row is the one the lexical layer could never see: it read
    "gone.py" as a fine target and spent a turn on it, or read a vague sentence
    as a clarification case. The last row is the traced session's ending — the
    refusal arrived *after* a spawn; the vector states it before one.

  Containment reuses the firewall's own test (`Path.relative_to` against
  `_workspace_root()`), inlined rather than called because `resolve_path`
  raises on escape and this needs the escape as data. A test asserts both
  agree, so the vector cannot promise a path the firewall will then refuse.

- [x] **Step 2 — sufficiency rule for mutation.** `GoalState.evidence_gap()`
  refuses a `coder` or `designer` spawn while all three are absent: a workspace
  path named by the request, a report from a read-only worker, and an accepted
  change manifest. Enforced in `_spawn_overflow_reasons` beside the role-order
  gate it mirrors, root only, fail-closed, and the refusal names both roles
  that can close the gap.

  Two things the writing settled:

  - **It answers a different question from the role-order gate.** That gate
    asks *is this the right role next*; this one asks *does anyone know what
    this turn is about*. A `single_coder` route sets no `next_role` at all, so
    "make it faster" passed the order gate untouched and reached a coder that
    wrote files in a place nobody had named. That hole is the reason this step
    exists, and it is not covered by anything else.
  - **Only `target_named` is stored; the other two are read live.** The
    request's text cannot change mid-turn, so it is captured once from the
    evidence vector. Worker outcomes and the manifest DO change mid-turn, and
    reading them fresh at every spawn is what lets the root close the gap
    itself — summon an explorer, then retry the coder in the same turn — rather
    than having to return to the user.

  **The cost, measured rather than assumed.** Four existing tests failed on
  this gate, and all four for the same honest reason: their requests
  (`create a file`, `create a report file`, `create html dashboard`,
  `create one policy test artifact`) name no path, so the gate refused the
  coder. That is the gate working, but it exposes a case worth naming — a pure
  CREATION request has no existing target to find, so the remedy it suggests
  (summon an explorer) buys nothing except clearing the gate. The gate is
  always satisfiable, so this is a wasted spawn, never a deadlock; the fixtures
  were updated to name the file they create, which is what a real request of
  that shape looks like anyway.

  The harness cannot separate "create something, you pick the name" from
  "change something, I didn't say what" — both read as `names_workspace_path =
  false`, and telling them apart means judging the sentence. The layer that
  CAN tell them apart is the tool boundary, where `musubi_write_file` already
  knows whether the path exists. **If the wasted spawn on creation requests
  proves to cost real money, the fix is to move this check down there** — the
  same move that fixed the destructive gate — rather than to add a lexical
  exception here. Deferred until there is data; noted so the option is not
  rediscovered from scratch.

  **What review caught (PR #166).** Three holes in the gate as first written,
  each reproduced against the code before it was fixed:

  - **An accepted manifest was treated as target evidence.** `ChangeManifest`
    carries `files_expected`, `subsystems`, and flags — counts and labels, no
    paths — and a planner may legally declare `files_expected=0` with no
    subsystems. That manifest cleared the gate while identifying nothing. Size
    is not a location. What the planner does establish is that it READ the
    workspace, so the gate now keys on its `done` outcome and `planner` joined
    `EVIDENCE_ROLES`.
  - **…which also closed a hole underneath it.** `apply_planner_manifest` runs
    only when `next_role == "planner"`, and on a `single_coder` route — exactly
    where this gate fires — `next_role` is `None`. A planner spawned to follow
    the refusal's own advice therefore never set `declared_files_expected`, and
    the retried coder was refused again. The advice was unfollowable.
  - **`escalated` and `abandoned` cleared the gate.** The status test was a
    denylist (`not in {failed, error}`), and an explorer that runs out of
    cycles finishes `escalated` carrying no findings — opening the mutation
    gate on the strength of having been spawned. It is an allowlist now: only
    `done`.

- [x] **Step 3 — root triage prompt.** Two halves, and the second is what makes
  the first safe.

  **The hint stopped giving orders.** The block ended by saying the root owns
  the routing decision while its bodies said *"Do NOT spawn a worker"* and
  *"Do NOT spawn a planner or coder"*. A model reading both cannot obey both,
  and between a disclaimer and an imperative the imperative wins — so the
  trailer was decoration and the hint was, in practice, an order issued by ~12
  regexes over one sentence. Each entry now says what the route is FOR and what
  would justify departing from it (`suggested_route=`, `guidance=Suggests: …`),
  and the trailer states plainly that the hint has read no file, that the
  evidence block below it is the part that was checked, and that overriding
  costs nothing.

  **The override became reviewable.** An overridable hint with no record of the
  override is a hint nobody can audit — the log recorded what the REGEX chose
  and never what the model did with it, so "the harness mis-routed" and "the
  model ignored a correct hint" left identical evidence. `agent/triage.py` asks
  the root for one line, `[triage] <shape>: <why>`, over a closed four-word
  vocabulary (`conversation | question | inspect | work`). It is captured
  mid-loop rather than from the final answer, because the line rides alongside
  the first tool call and by the time an answer exists the declaration has
  stopped being a plan. Stored in `agent_turns.root_triage`.

  Reading that line is **parsing, not judging**: the harness never checks
  whether the shape was correct — it cannot — and an absent declaration is
  recorded as absent rather than inferred from behaviour. A shape the harness
  invented would be indistinguishable in the column from one the model stated,
  which would ruin the only record that makes an override reviewable.

  Cost, caught by a ratchet rather than by review: the first draft of the
  prompt block overran
  `test_simple_root_two_call_projection_stays_below_3k_tokens` by ~50 tokens
  (4553 against a 4500 ceiling). The block was trimmed rather than the ceiling
  raised — that test exists for exactly this.

  **What review caught (PR #166).** Three more, all honesty rather than
  parsing:

  - **The trailer claimed overriding costs nothing.** On the planner-led routes
    it does not: `GoalState.create` sets `next_role="planner"` and the
    role-order gate refuses a writer until the manifest lands. A root that
    followed the new instruction and recorded a direct-coder override would pay
    the planner round trip and be refused anyway. The trailer now names that
    one ordering as enforced instead of implying everything is negotiable.
  - **A late triage was stored as the plan.** The slot filled on any cycle, so
    a root that declared nothing first, called tools, saw a worker result and
    only then emitted `[triage] …` had its CONCLUSION recorded as its
    intention — destroying the one thing the column is for. Only cycle 0 is
    read now, and a silent first cycle records absence permanently.
  - **A failed turn recorded nothing at all.** `_record_agent_turn` sat on the
    success path only, so a crashed turn — the kind most worth a post-mortem —
    left no triage, no cycle count, and nothing for `chat_turn_usage` to see.
    Three failures in a row looked like zero turns to the no-progress breaker.
    The row is written before the re-raise now.

- [ ] **Step 4 — delete the lexical judgment.** Reduce `classify_task` to the
  two branches that remain meaningful — is there work to do, and is it
  destructive — by removing `assess_request` and `_BROAD_PRODUCT_RE`,
  `_STATIC_FILE_RE`, `_BOUNDED_ARTIFACT_RE`, `_FRAMEWORK_RE`, `_MULTIPART_RE`,
  `_ARTIFACT_RE`, `_SIMPLE_EDIT_RE`, `_NO_SHORTCUT_RE`, `_VAGUE_RE`,
  `_CASUAL_RE`, and with them the pre-run `ask_scope` halt — and therefore
  `BROAD_PRODUCT_QUESTION`, `clarification_request`, and `pending_clarification`
  (both of 2026-07-29's earlier fixes). Re-tiering already happened in step 0,
  so this step is pure deletion: **18 of 19 regexes, ~551 lines.** It is where
  the cost profile changes, so it lands last and behind the measurements from
  step 1.

- [ ] **Step 5 — enforce the declaration.** Promote `manifest_overrun` from a
  prompt warning to a hard stop on the coder path. With scope LLM-declared, an
  under-declared radius is the primary abuse channel and it must cost the run,
  not a paragraph.

## The cost trade-off you are buying

Today's cheap paths are cheap because a regex decided without a model:

| Turn today | Cost today | Cost after |
|---|---|---|
| "hi" | 0 tokens, 0 ms | 1 root call (~1–2k tokens) — the fast path is deleted, decision 2 |
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

- Step 0 shipped with `tests/test_blast_radius.py` (16 tests). The ones that
  matter are the negatives: `grep -r rm .` is not a deletion, `find … | xargs
  rm` is, `"ok xoá đi"` / `"yes delete them"` / `"go ahead"` do **not** open the
  gate, a token approves only its own key set, and unreadable pending storage
  grants nothing. `tests/test_tool_name_references.py` freezes the tool-name
  literals the gate keys on.
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

## Decisions taken (2026-07-29) — none outstanding

1. **Who triages the turn** — the **root**, in its first cycle. It is already a
   model call, so triage costs ~0 extra spawns; planner-always would pay the
   observed 30–61 s on every "read run.py". Settled by the governing principle
   rather than by the cost table: triage is a judgment, so no line of code
   awards it — the root is simply where a judgment first becomes possible.
2. **`_CASUAL_RE` does not keep its zero-token fast path.** Eurus: *"hi không
   cần giữ"*. The earlier recommendation (keep it as a cost hack) is withdrawn —
   a branch that exists only because it is cheap is exactly the kind of
   exception that makes the rest of the layer defensible.
3. **The `musubi-tier` change is approved and shipped** (`d579c75`).
4. **The destructive gate is (b), at the tool boundary**, with delete N=1 and
   overwrite N=5, harness-verified token consent, and Approve/Reject in the
   Console. Shipped in `dd41918`, `c896bcc`, `f61669d`.
5. **Harness-verified consent is preferred over model-mediated confirmation.**
   Eurus: *"có lẽ vẫn nên ưu tiên harness"*. A confirmation the model relays is
   only as reliable as the model's relaying of it — which is precisely the
   failure `_ensure_grant_visible` had to close.
