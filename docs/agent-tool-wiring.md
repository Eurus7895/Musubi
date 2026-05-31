# Agent tool wiring — design note for Phase J follow-up

> Status: **draft** · last updated 2026-05-31
> Owner: open · supersedes: nothing
> Linked failure pattern: `.github/memory/failure-patterns.md` § "coder — produces incomplete artefacts when stage requires enumerating workspace files"

This is a planning artefact, not an implementation. The goal is to
nail down the five open design questions raised in the Phase J agent
audit before any code lands, so the eventual A1 PR can be a
straightforward implementation of agreed decisions rather than a
revision-heavy exploration.

---

## Problem (one paragraph)

`runners/orchestrator.ts` reads `lm_tools:` from agent frontmatter and
passes them to `vscode.lm.sendRequest`. `pipeline.ts::runAgentLM`
calls `model.sendRequest(messages, {}, token)` with an **empty
options object**. The seven feature-dev and code-review pipeline
agents — plus `pipeline-builder` and `skill-builder` — declare
`lm_tools:` lists that the runtime never reaches them. The coder ends
up writing code without being able to read the workspace it's writing
into; the reviewer judges code without verifying its references; the
designer designs against assumed file layouts. The harness governance
catches the resulting gaps (reviewer firewall + correction loop) but
each "blind" stage wastes credits and adds turn-around latency.

## Goal

Land a small, reviewable PR (**A1**) that wires read-only tools into
pipeline-mode agents. Defer write/terminal access (**A2**) to a
follow-up after A1 is stable.

## Non-goals

- Change pipeline semantics around append-only stage outputs (HI #7
  preserved — final JSON still single artefact per attempt).
- Touch the evaluator firewall (HI #3 preserved — reviewer's harness-
  pushed context stays `code`-only; tools are pull-on-demand, same
  model as today's reviewer-aux).
- Re-architect the orchestrator (Phase F freeze still applies).
- Promote pipelines to multi-LM-call-per-stage *without bound* —
  cycle limits stay tight.

---

## Five design decisions

### 1. Cycle limit per stage → honour `maxTurns:` frontmatter

Today every pipeline agent declares `maxTurns: 1` in its frontmatter
and the field is ignored. Orchestrator-runner sub-agents already use
their `maxTurns` as a cycle cap (`explorer: 6`, `investigator: 6`,
`reviewer-aux: 4`). Pipeline-mode agents adopt the same semantics.

Proposed defaults per role:

| Agent | maxTurns today | Proposed | Why |
|---|---|---|---|
| planner | 1 | **3** | Initial enumeration + 1-2 follow-ups |
| designer | 1 | **5** | Architecture decisions need multiple lookups |
| coder | 1 | **10** | Read existing code → check errors → write → verify |
| reviewer | 1 | **5** | Verify code against existing callers / patterns |
| code-review-scoper | 1 | **2** | Mainly parses the diff |
| code-review-finder | 1 | **5** | Reads multiple files |
| code-review-synthesizer | 1 | **3** | Aggregates findings |
| pipeline-builder | 1 | **5** | Reads existing pipelines for patterns |
| skill-builder | 1 | **5** | Reads existing skills + agents |

Above the per-agent cap, the loop force-finalises (same pattern as
`MAX_TOOL_CYCLES` in the orchestrator). The `maxTurns` bump is a
no-behavioural-change on its own — current pipeline.ts ignores the
field; A1 starts honouring it. Old runs that didn't use tools still
exit at cycle 0 because no tool calls are emitted.

### 2. Schema-validation timing → on the final cycle's text buffer

Today: single `sendRequest` → single text response → `extractJson(text)`.

After A1: multi-cycle loop. Each cycle's response may contain text
chunks AND tool-call parts. Accumulate text in a buffer across
cycles; the loop terminates when a cycle's response has **zero tool
calls** (model is done) or when `maxTurns` is hit (forced finalise).
Schema-validation runs on the final buffer.

Termination logic mirrors `runOrchestrator`'s existing cycle loop:

```
buffer = ""
for cycle in 0..maxTurns:
  response = await sendRequest(history, tools)
  for part in response.parts:
    if part is TextPart: buffer += part.text
    if part is ToolCallPart: dispatch + append result to history
  if no ToolCallPart in this cycle: break  ← model is done
return extractJson(buffer)  ← validation runs here
```

A failed schema-validation falls through to the existing retry path
(`runAgentWithValidationRetry`), which already handles attempt
counting, hint passing, and escalation.

**Risk:** the model might emit text BETWEEN tool calls that's not the
final JSON (e.g. "let me think about that…" before calling a tool).
Concatenating everything would pollute the buffer. **Mitigation:**
only accumulate text from the FINAL cycle (the one with no tool
calls). Intermediate-cycle text is logged to the Output channel for
diagnostic but not used for JSON parsing.

### 3. Pre-spawn + in-stage tools → keep both

`preSpawnAndSplice` currently fires explorer/investigator before
coder/reviewer based on `chunkFilePaths`. After A1, agents can also
make in-stage tool calls. The question: keep the pre-spawn or remove
it once in-stage tools work?

**Keep both.** Pre-spawn results land in the agent's context before
the LM emits the first token — effectively a warm-cache for
known-needed reads. In-stage tools are slower (each call is a round-
trip with the model emitting a tool-call part and the runner
dispatching). Pre-spawn covers "we know what to read"; in-stage
covers "surprise enumeration the design didn't anticipate."

The pre-spawn decision logic stays unchanged. In-stage tools land
alongside, not replacing.

### 4. Tool-result storage → audit only, not stage outputs

When the LM calls `copilot_readFile` during a stage, where does the
result go?

**Audit only.** Stage outputs stay as the LM's final JSON artefact —
adding tool-call traces would pollute the artefact and break the
append-only contract's clean semantics (HI #7). Diagnostic record
goes to a new lightweight log line in the Output channel per call;
durable audit deferred to J.4 telemetry work (a future
`pipeline_tool_calls` table or extension of `stage_metrics`).

For A1 v1: just log to Output channel (`[planner] tool
copilot_readFile path=src/foo.ts: ok 4123 chars`). Persistent audit
follows if it proves needed.

### 5. A1 scope → all seven read-only pipeline agents at once

The wiring is per-agent-identical (read `agent.md`, convert
`lm_tools:` to `LanguageModelChatTool[]`, pass to `sendRequest`). The
marginal cost of wiring 7 agents vs 1 is negligible once the cycle
handler exists. Different agents will USE the tools to different
degrees; agents that don't need them simply emit zero tool calls and
exit at cycle 0, identical to today's behaviour.

**Reviewer asymmetry note:** reviewer reading the workspace is
**not** a HI #3 violation. The firewall says the **harness pushes**
`code` only to the reviewer. Tools are pull-on-demand at the LM's
discretion. The precedent is set — `reviewer-aux` (which is the
reviewer's own sub-agent) already has `copilot_readFile` and the
firewall holds. Extending the same model to the main reviewer is
consistent.

---

## A1 implementation outline

| Step | What | Touches |
|---|---|---|
| 1 | Bump `maxTurns:` in all 7+ pipeline agent frontmatters per the table above | `.github/agents/*.agent.md` |
| 2 | Extract the orchestrator's cycle loop into a shared helper `runAgentToolCycle()` | new `src/agentToolCycle.ts` |
| 3 | Wire the cycle helper into `runAgentLM` (replaces the single `sendRequest` call) | `copilot-harness-extension/src/pipeline.ts` |
| 4 | Read `lm_tools:` via existing `parseFrontmatterLmTools()` in `modelSelectorCore`; convert to `vscode.LanguageModelChatTool[]` | `pipeline.ts` |
| 5 | Add tool dispatch that handles in-stage `copilot_*` calls (the existing handlers in orchestrator are reusable) | `pipeline.ts` |
| 6 | Output-channel logging per tool call | `pipeline.ts` |
| 7 | Tests: cycle loop terminates correctly, maxTurns enforced, schema-validation runs on final buffer | new `src/agentToolCycleCore.test.ts` |

Estimated size: ~250 lines new + ~50 lines pipeline.ts modifications + ~150 lines tests = **~450 lines** total.

## A2 sketch (deferred, no detail yet)

Adds edit (`replaceString`, `insertEdit`, `create_file`) and terminal
(`runInTerminal`) tools to coder. Higher risk because:

- LM can modify files mid-stage → `_STAGE_PERMISSIONS` needs path
  constraints (coder writes only to `plan.scope.files`)
- `runInTerminal` could side-effect; needs sandbox or output-only
- Audit trail needs to capture every edit/terminal call (durable,
  not just Output channel)
- Schema-validation semantics: does coder still emit a final JSON
  artefact, or does the act of editing files mid-stage become the
  artefact? (Answer probably: both — JSON manifest of "what changed"
  + the actual file edits.)

A2 gets its own design doc once A1 has shaken out.

---

## Open questions for review

Before this design doc is closed and A1 starts:

1. **Does the schema-validation strategy hold up for the coder's case?**
   The coder's final JSON declares `files: [{path, summary}]` — the
   actual file contents aren't in the JSON (coder writes them via
   `harness_write_stage`). With in-stage edit tools (A2), the model
   could emit the files via tool calls. For A1 (read-only), nothing
   changes: coder still emits the same JSON, tools only add reads.

2. **Cycle-limit upgrade — should `maxTurns` be honoured retroactively
   for sub-agents too?** Sub-agents already use it; pipeline agents
   would adopt the same semantics. No mismatch expected. Confirm.

3. **What model-side tool-call cost does the rate table need to
   reflect?** Pre/post-flight credit estimation (J.3) currently treats
   each `sendRequest` as a single call. With multi-cycle tools, a
   single stage may make N sendRequests. Each one is a separate
   billable event. The cost accounting needs adjustment to charge per
   cycle, not per stage. Estimated work: 30 lines in
   `pipelineBudgetCore.ts`.

4. **Do we hard-code the tool dispatch handlers, or thread through the
   orchestrator's existing handler infrastructure?** The dispatch logic
   in `runners/orchestrator.ts` is fairly self-contained; extracting
   feels right. But it currently uses `_active` module state for the
   per-turn parent session — that won't work for pipeline mode where
   we don't have an orchestrator "turn." Need to thread the session
   context through a parameter instead.

5. **Should A1 ship behind a feature flag (`copilotHarness.pipelineTools`,
   default false)?** Pros: safer rollout; can A/B compare. Cons: more
   code paths to test; users who want the fix have to opt in. My lean:
   no flag — A1's behaviour is additive (adds tool calls when agents
   use them, otherwise identical to today). Old runs that don't use
   tools still work.

## Risk + rollback

- **Risk:** an agent's cycle loop hits an infinite-tool-call pattern
  (LM keeps calling the same tool with slightly different inputs).
  **Mitigation:** `maxTurns` as hard cap. Same protection
  orchestrator already relies on.
- **Risk:** schema-validation reads stale text from an intermediate
  cycle. **Mitigation:** only the final cycle's text is parsed; see
  decision #2.
- **Risk:** pre-existing pipeline runs in flight when A1 is installed
  behave differently. **Mitigation:** A1 is a fresh-extension-load
  thing; no in-flight pipeline state to corrupt.
- **Rollback:** revert the PR. `runAgentLM` returns to single-cycle
  behaviour; agents declare tools that aren't reached again (current
  state). No data migration required.

---

## Next step

Reader: review this doc and either:

- Approve as-is → A1 PR starts on a new branch (`feat/agent-tool-wiring-a1`)
- Push back on specific decisions → comment + adjust
- Request a different scope → discuss

Author: when approved, this file moves from `status: draft` to
`status: approved` with a note pointing at the implementing PR.