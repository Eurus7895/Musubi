# Truncated and Empty Model Turns Implementation Plan

> **Status:** Implemented on 2026-08-03. The task breakdown remains as the
> review record; code and regression coverage are complete.

**Goal:** Make a model turn that produced no usable result fail where its cause
is still visible, instead of travelling four layers as an empty success.

**Architecture:** Three independent defects turned one truncated reasoning
response into a fatal, unattributable pipeline abort. Fix each at its own
layer: the vendor wire recovers the thinking channel it was discarding, the
agent loop stops treating a truncated or empty turn as a final answer, and the
pipeline runner stops reporting a blank answer as a completion. Keep the
substrate's terminal-status gate exactly as strict — with the layers above it
correct, it now only fires on genuine failure.

**Tech Stack:** Python 3.11, stdlib JSON, pytest, Ruff.

## Observed failure

A `feature-dev` run on DeepSeek `deepseek-v4-flash` died at stage 1 of 4:

```
[agent] [root] cycle 3: model_action=truncated stop=max_tokens tools=0 out_tokens=2048
agent-agent: RuntimeError: [pipeline feature-dev] stage 'plan' harness recorded
  terminal status escalated … {"turns": 4, "turn_cap_accepted": false,
  "summary": "[harness] max_turns=4 reached (turns=4)"}
```

`tools=0` with `out_tokens=2048` is the signature: the provider billed a full
output cap and the loop saw zero content blocks. The recorded summary contains
only the harness note, which is what `sub_sessions.complete` writes when the
caller's summary was empty — the receipt proving the stage answer was `""`.

## Causal chain

1. `.github/agents/workers/planner.agent.md` declared `maxOutputTokens: 2048`.
   For a read-only role `resolve_effort_bounds` (`agent/context.py:165`) sets
   `floor = min(effort_floor(), ceiling)`, so floor and ceiling both became
   2048 — and `_call_with_effort` only retries while `floor < ceiling`. The
   retry-at-ceiling rescue was disabled without a diagnostic.
2. DeepSeek's reasoning family spends that same cap on `reasoning_content`.
   Cut at `finish_reason="length"` mid-thought, it returned empty `content`,
   no `tool_calls`, and 2048 tokens of thinking.
3. `openai_message_to_blocks` read only `content` and `tool_calls`, so the
   response converted to zero blocks.
4. In `_run_loop` the `not tool_uses` branch preceded the `max_tokens` branch,
   so the empty turn became `final_answer = ""`, audited `cycle_status="final"`.
5. `pipeline_runner` used `answer is not None` for `done`, reporting a blank
   summary at the turn cap. The read-only turn-cap waiver added the same day
   requires a non-empty summary, so it could not fire; the harness coerced the
   row to `escalated`, and the terminal-status gate raised.

## Global Constraints

- Preserve HI #1: no substrate model call is added.
- Preserve HI #7: no completion path is made to overwrite a prior attempt.
- Do not weaken the substrate's turn-cap coercion or the runner's
  terminal-status gate; make the layers above them report accurately instead.
- Reasoning text is a last-resort recovery, never preferred over what the model
  chose to say: a normal response must convert byte-identically.

---

### Task 1: Recover the reasoning channel at the wire boundary

**Files:**
- Modify: `musubi/agent/vendors/openai_wire.py:157-215`
- Modify: `musubi/tests/test_agent_vendors.py:240-290`

**Interfaces:**
- Produces `reasoning_text(message) -> str`, reading `reasoning_content` then
  `reasoning`.
- `openai_message_to_blocks` appends the thinking text as a text block only
  when no content and no tool call survived.

**Steps:**
- [x] Add `_REASONING_FIELDS` and `reasoning_text`.
- [x] Append the fallback block only when `blocks` is empty.
- [x] Cover: recovery from SDK object and wire dict, the alternate field name,
      never preferring reasoning over content or a tool call, and whitespace
      reasoning staying empty.

### Task 2: Stop the loop accepting a truncated or empty turn

**Files:**
- Modify: `musubi/agent/run.py:1328-1332, 1394-1470, 3245-3305`
- Modify: `musubi/tests/test_agent_loop.py:2180-2330`

**Interfaces:**
- Produces `_truncated_text_answer(partial) -> str` with reason
  `output_too_large_for_single_response`.
- Produces `_empty_response_answer(stop_reason) -> str` with reason
  `empty_model_response`.
- Both reuse the `[blocked] ` prefix every existing failure-typing caller
  already treats as non-final.

**Steps:**
- [x] Check `stop_reason == "max_tokens"` with no tool calls BEFORE the
      "no tool calls → final answer" branch; retry with a shorter-answer
      instruction while cycles remain, else return the typed marker.
- [x] Reject a turn with neither tool call nor text, audited `cycle_status`
      `"empty"`.
- [x] Never append a content-less assistant entry to the replayed conversation.
- [x] Cover: truncated text at the cap, truncated text with cycles left, empty
      turn, empty turn not replayed, and the `empty` audit row.

### Task 3: Stop the runner reporting a blank stage as done

**Files:**
- Modify: `musubi/agent/pipeline_runner.py:72-86, 843-857, 918-928, 1145-1162`
- Modify: `musubi/tests/test_spawn_pipeline.py:180-290`

**Interfaces:**
- `_BLOCKED_INCOMPLETE_REASONS` covers all three typed no-result markers.
- A blank answer becomes `escalated` with a summary naming the cause.
- The terminal-status error names the runner-side status alongside the
  harness-recorded one.

**Steps:**
- [x] Treat a whitespace-only answer as `escalated`, not `done`.
- [x] Extend the incomplete-outcome check to the two new reasons.
- [x] Name both statuses in the gate's message.
- [x] Cover: blank stage answer, and a text-truncation `[blocked]` marker.

### Task 4: Restore escalation headroom for the planning roles

**Files:**
- Modify: `.github/agents/workers/planner.agent.md:8`
- Modify: `.github/agents/workers/designer.agent.md:7`
- Modify: `musubi/agent/pipeline_runner.py:72-86`
- Modify: `musubi/tests/test_context.py:100-135`

**Rationale:** `MAX_STAGE_HANDOFF_CHARS` (8,000 bytes) already bounds a
planning handoff deterministically, at the boundary that owns it. Restating
that bound as a 2,048-token output cap is not the same rule twice: a reasoning
model spends the cap on thinking too, and the value collapses the effort floor
onto its ceiling. Raise both roles to 8,192 and let the byte gate do the
bounding.

**Steps:**
- [x] Raise `maxOutputTokens` to 8192 for planner and designer.
- [x] Record the two-limits rationale next to `MAX_STAGE_HANDOFF_CHARS`.
- [x] Pin the collapse as a regression test, and assert both shipped planning
      roles stay above the effort floor.

---

## Verification

- `pytest musubi/tests` — 1759 passed, 1 skipped.
- `ruff check --config musubi/pyproject.toml` on every touched file — 42
  findings before and after the change; no new finding introduced.
- `mypy agent/vendors/openai_wire.py agent/pipeline_runner.py` — 77 errors
  before and after; no new error introduced.
- No paid model smoke run.

## Not addressed here

Measured on the same run, tracked separately:

- A direct worker receives the parent `TokenBudgetEnforcer` unwrapped
  (`agent/subagent.py:228`) while a pipeline stage gets a `ChildTokenBudget`
  slice, so one worker can spend a whole run (observed: 210,265/200,000).
- `charge()` bills gross input every cycle, so `cache_read` is a reporting
  field with no budget effect.
- The elided-tool-arg rejection asks the model to regenerate content without
  handing back the file's length or tail (observed cost: 27,472 tokens).
