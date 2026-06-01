# Failure Patterns — Distilled from Sessions

> Updated automatically by `session_distiller.py` after each completed session.
> Load this when diagnosing recurring failures or before coder/reviewer runs.

---

## Format

Each entry records a recurring failure pattern detected across multiple sessions.
Entries below the `---` separator are auto-appended by `session_distiller.py`.

---

## Known Patterns

<!-- Auto-appended entries appear below. Each entry format:
### [agent] — [issue] (N occurrences, last seen: YYYY-MM-DD)
Sessions: session_id_1, session_id_2, ...
-->

### coder — produces incomplete artefacts when stage requires enumerating workspace files (1 occurrence, last seen: 2026-05-30)
Sessions: (user-reported, PR #58 test run — `@harness /feature-dev build unit-test plan`)

Symptom: Coder produced `docs/test-plan.md` with structurally-correct sections but a placeholder Mappings block, plus an explicit `implementation_notes`: *"I could not access the repository workspace to enumerate actual test files."* Reviewer correctly caught the gap and escalated with two issues (high + medium severity). Pipeline halted as escalated, ~135s + ~13 credits spent on a partial deliverable.

Root cause — structural, not LM-side:

- `runners/orchestrator.ts` registers `lm_tools:` from agent frontmatter and passes them to `vscode.lm.sendRequest`. The orchestrator turn surface is the only path that does so.
- `pipeline.ts::runAgentLM` calls `model.sendRequest(messages, {}, token)` with an **empty options** object — no tools surface. Pipeline-mode agents (planner / designer / coder / reviewer in feature-dev, scoper / finder / synthesizer in code-review) run blind.
- `.github/agents/coder.agent.md` lines 13-16 admit this explicitly: *"Forward-looking — pipeline.ts does not yet pass tools to sendRequest, so this is consumed only when a runner is wired."*
- The pre-spawn dispatcher (`preSpawnAndSplice` in `subagentDispatcherRun.ts`) does fire explorer/investigator before coder, but the decision is keyed on `chunkFilePaths` (what coder WRITES), not on what coder needs to READ.

Why this is a recurring class, not a one-off:

- Same root cause produces every "coder hallucinated a path" or "coder referenced a function that doesn't exist" failure. The coder can write code but can't *check* whether the surrounding code matches what its design assumes.
- 9 of 14 agents declare `lm_tools:` that the runtime never reaches them. The list grew in anticipation of runner wiring (commit annotations call this "forward-looking") but the wiring didn't follow.

Fix candidates (in priority order):

- **(A) Wire `lm_tools:` into `runAgentLM`'s `sendRequest`.** Largest change (~150 lines + tool-cycle accumulation in pipeline.ts), but the cleanest architectural fix. Turns pipeline stages from single-shot → bounded multi-cycle (similar to orchestrator turn structure). Requires schema validation to apply to the post-cycle final output rather than the first text chunk.
- **(B) Designer-driven `read_scope:`.** Designer declares a glob list of files coder will need to READ (separate from `chunkFilePaths` for writes). `preSpawnAndSplice` decision logic uses both. Splices enumeration into coder context. ~80 lines. Smaller blast radius.
- **(C) Document the constraint + escalate-on-blocked.** Update `coder.agent.md` + `skills/python/SKILL.md` to instruct: *"You have no tools to enumerate the workspace; if your task requires enumeration that wasn't provided, set confidence: low and escalate via implementation_notes."* Cheap (~30 min), treats the symptom, prevents wasted credits on guaranteed-incomplete attempts.

Related: this is the second harness-design failure pattern logged for the test-creation flow. The first cascade (planner placeholder T1 → designer placeholder filename → coder retry thrash) was captured on branch `docs/log-feature-dev-placeholder-failures` (commit `05b2694`, unmerged). Both stem from "agents are asked to produce work involving unknown workspace state without tools to discover that state."

Recommended order: (C) ships immediately for unblocking; (A) ships after a full audit of which agents are pipeline-mode vs runner-wired (see Phase J follow-up).
