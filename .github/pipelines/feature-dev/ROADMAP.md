# feature-dev — pipeline upgrade plan

Living plan for changes to the `feature-dev` pipeline. Nothing below is
started — each tier is a self-contained unit of work that can be picked up
independently. Order is by observed ROI, not priority.

---

## Tier 1 — Close the loop on observed failures

Highest ROI: these fix bugs we have actually seen in production runs.

- **T1.1 — Coder: severity-rubric awareness.** ✅ done
  Updated `coder.agent.md` with the severity rubric (mirroring the
  reviewer's) plus a Behavior Rule: medium/low fix_instructions are
  advisory, correctness/security come first, do not degrade code quality
  chasing nits. Coder uses fix_instruction wording (explicit "consider…",
  "would be nicer…", style-only) to identify nits, since the Reviewer's
  severity tag is not forwarded through the retry context.

- **T1.2 — Surface rubric coercions to the user.**
  Server already sets `status_coerced: true` on the review write
  (`server.py:261`); the chat surface ignores it. Render it as a
  governance tag on the review stage line — concretely, append
  `◇ policy: status coerced` to the existing tag row in
  `STAGE_TAGS["review"]` when the write response carries the flag, so
  the user understands why a "fail" review did not cause a retry.

- **T1.3 — Single source of truth for harness-rejected strings.**
  Do NOT duplicate the secrets-scanner regex prefixes into
  `reviewer.agent.md` — they will rot the moment the scanner changes.
  Instead expose them once via a new `musubi_get_constraints` MCP tool
  (or inject them into the reviewer skill at `musubi_read_stage` time).
  Reviewer reads the live list; coder gets the same list pushed by the
  harness. Prevents reviewer asks like "use a realistic secret" that the
  coder literally cannot satisfy.

- **T1.4 — Distill today's run into failure-patterns.md.**
  Call `musubi_distill_session` on the offending session rather than
  hand-editing `.github/memory/failure-patterns.md`. The Week 4 Day 4
  compactor's heuristics only understand the distiller's output format;
  hand-edits risk format drift. Same outcome, no risk.

---

## Tier 2 — Strengthen handoff contracts (Week 4 deferred gap)

Prevents the whole class of "forgot half the plan" failures.

- **T2.1 — Plan → Code acceptance-criterion trace.**
  Coder schema requires (not optional — Tier 2's whole point is
  schema-level guarantees) a map
  `criteria_covered: { "T1.a": ["tests/test_x.py::test_y"] }`.
  `verifier.py` checks every `plan.tasks[*].acceptance_criteria` string
  is mentioned; missing entries are a hard write-stage rejection routed
  back as a coder retry with `fix_instructions`.

- **T2.2 — Promote `_check_code_only_modifies_declared_files` to a hard reject.**
  Currently it lists undeclared files; make it reject the write. Give the
  coder an escape hatch via `wrong_plan` status when it legitimately needs
  a file the design missed — that routes back to Planner, not Coder retry.

- **T2.3 — Designer integration_notes contract.**
  Require `integration_notes` to mention at least one module from `modules`.
  Catches "design summary has nothing to do with the declared modules" drift.

---

## Tier 3 — Observability / UX

- **T3.1 — Move stage events from extension to harness.**
  Per-stage `### ⏳ / ✓` rendering already ships (v0.3.1 — see
  `emitStageStart` / `emitStageComplete` in `pipeline.ts`), but the
  events are *synthesized by the extension* around each `musubi_*`
  call. Push them from the harness instead: `mcpClient.ts` surfaces
  `stage_started` / `stage_complete` / `coercion_applied` notifications
  emitted by `server.py`, extension just forwards them. Removes the
  silent-divergence risk where a server-side state change (e.g. a
  coercion) exists but the extension never learns about it. Pairs with
  T1.2 — `coercion_applied` is the event T1.2 needs.

- **T3.2 — Friendlier escalation output.**
  When max retries hit, print the reviewer's first-attempt issues next
  to the final-attempt issues so the user sees what changed and what
  didn't — the current output only shows the final fail state.

- **T3.3 — `.harness/sessions/<id>/summary.md`** written at the end of every
  run (pass or escalate). One-page digest of plan + design + code files +
  final review. Saves the user from stitching the per-stage `.md` files
  together.

---

## Tier 4 — Week 5 prerequisite (enables, does not require Week 5)

- **T4.1 — `subagents:` block in `pipeline.yaml`.** Add the schema +
  validator so when Week 5 ships, the coder stage can opt into spawning
  `explorer` without re-plumbing the pipeline loader. Opt-in per stage,
  whitelist only.
  See CLAUDE.md Week 5 plan for the full sub-agent design.

---

## Dependencies

```
T1.1 ── independent
T1.2 ── depends on T3.1 (consumes the coercion_applied event)
T1.3 ── independent
T1.4 ── independent

T2.1 ── independent
T2.2 ── independent
T2.3 ── independent

T3.1 ── enables T1.2 (event source for the coercion marker)
T3.2 ── independent
T3.3 ── independent

T4.1 ── blocks Week 5 Phase B (pipeline-main spawning)
```

## Recommended first slice

Tier 1 + Tier 2 together, with **T3.1 pulled forward** so T1.2 has an
event source to render. Tier 1 fixes observed bugs; Tier 2 prevents a
related class of bugs and is cheap to add while the severity-rubric work
is fresh; T3.1 is the cheapest plumbing change that unblocks T1.2 and
removes a class of silent extension/server divergence at the same time.
