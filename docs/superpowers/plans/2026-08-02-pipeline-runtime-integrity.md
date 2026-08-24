# Pipeline Runtime Integrity Implementation Plan

> **Status:** Implemented on 2026-08-02. The task breakdown remains as the
> review record; code and regression coverage are complete.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a pipeline stage fit a bounded, model-aware input budget and make every spawned stage worker produce one authoritative terminal lifecycle record.

**Architecture:** Keep `agent/pipeline_runner.py` as the ephemeral stage sequencer, but make it project only the immediate predecessor into a stage brief and calculate an effective input ceiling before the first model call. Keep lifecycle facts in the substrate: `server.py` persists a completion audit obligation before delivery, `sub_sessions.py` records the terminal status and explicit turn-cap acceptance, and the runner advances a stage only from the harness-returned terminal status.

**Tech Stack:** Python 3.11, stdlib SQLite/JSON, FastMCP, Pydantic supplied by the MCP dependency, pytest, Ruff.

## Global Constraints

- Preserve HI #1: no substrate model call.
- Preserve HI #2: the model selects the skill; the harness only validates and injects it.
- Preserve HI #3 evaluator firewall and HI #7 append-only stage attempts.
- Strengthen HI #8: every accepted stage spawn has exactly one terminal completion audit obligation.
- Use an 8,000 estimated-input-token operational cap with a 32,000-character compatibility cap; reserve resolved output tokens and 1,024 transport tokens when the profile declares a context window.
- Do not silently truncate role prompt, selected skill, task, or accepted predecessor output.
- No paid model smoke run without a later explicit operator request.

---

### Task 1: Add model-aware stage input sizing

**Files:**
- Modify: `musubi/agent/config.py:197-204`
- Modify: `musubi/agent/vendors/base.py:55-61`
- Modify: `musubi/agent/run.py:2040-2070`
- Modify: `musubi/agent/pipeline_runner.py:55-85, 209-235`
- Modify: `musubi/tests/test_agent_config.py:123-130`
- Modify: `musubi/tests/test_agent_loop.py:2845-2872`
- Modify: `musubi/tests/test_spawn_pipeline.py:797-836`

**Interfaces:**
- Produces `resolve_model_context_window(profile: dict[str, Any]) -> int | None`.
- Produces `LMRouter.context_window_tokens: int | None`.
- Produces `resolve_pipeline_context_budget_chars(vendor, worker_max_output, can_mutate) -> int`.
- `PipelineWorkerSpec.context_budget_chars` remains the compatibility hard cap exposed to `run_unit`.

- [ ] **Step 1: Write failing config and budget-resolution tests**

```python
def test_resolve_model_context_window_accepts_only_positive_ints() -> None:
    assert resolve_model_context_window({"context_window_tokens": 32768}) == 32768
    assert resolve_model_context_window({"context_window_tokens": 0}) is None
    assert resolve_model_context_window({"context_window_tokens": "32768"}) is None


def test_pipeline_context_budget_reserves_output_and_transport() -> None:
    router = FakeRouter([])
    router.context_window_tokens = 12_000
    router.max_output_tokens = 4_000
    assert resolve_pipeline_context_budget_chars(router, None, False) == 22_320
```

- [ ] **Step 2: Run the new focused tests and confirm the missing interfaces fail**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_agent_config.py tests/test_spawn_pipeline.py -k "context_window or context_budget" -v`

Expected: FAIL because the resolver and stage-budget helper do not exist.

- [ ] **Step 3: Implement profile propagation and the bounded calculation**

```python
PIPELINE_CONTEXT_BUDGET_TOKENS = 8_000
PIPELINE_CONTEXT_BUDGET = PIPELINE_CONTEXT_BUDGET_TOKENS * 4
PIPELINE_TRANSPORT_MARGIN_TOKENS = 1_024

def resolve_pipeline_context_budget_chars(vendor, worker_max_output, can_mutate):
    _, reserved_output = resolve_effort_bounds(
        can_mutate=can_mutate,
        worker_max_output=worker_max_output,
        model_output_override=getattr(vendor, "max_output_tokens", None),
    )
    window = getattr(vendor, "context_window_tokens", None)
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        return PIPELINE_CONTEXT_BUDGET
    safe_tokens = window - reserved_output - PIPELINE_TRANSPORT_MARGIN_TOKENS
    return max(0, min(PIPELINE_CONTEXT_BUDGET_TOKENS, safe_tokens * 80 // 100) * 4)
```

Set `resolved.context_window_tokens` in `_resolve_vendor` from the validated profile field. Do not widen a stage above `PIPELINE_CONTEXT_BUDGET`.

- [ ] **Step 4: Run focused sizing tests**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_agent_config.py tests/test_agent_loop.py tests/test_spawn_pipeline.py -k "context_window or pipeline_context_budget or resolve_pipeline_worker_spec" -v`

Expected: PASS; existing `16_000` assertions change to `32_000` and new small-window tests prove output/margin reservation.

- [ ] **Step 5: Commit the isolated sizing change**

```bash
git add musubi/agent/config.py musubi/agent/vendors/base.py musubi/agent/run.py \
  musubi/agent/pipeline_runner.py musubi/tests/test_agent_config.py \
  musubi/tests/test_agent_loop.py musubi/tests/test_spawn_pipeline.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(pipeline): size stage input from model limits"
```

### Task 2: Bound stage handoff and reject unfit protected input before a model call

**Files:**
- Modify: `musubi/agent/pipeline_runner.py:330-590, 861-874`
- Modify: `musubi/agent/context.py:336-478`
- Modify: `.github/agents/workers/planner.agent.md:1-14`
- Modify: `.github/agents/workers/designer.agent.md:1-14`
- Modify: `musubi/tests/test_spawn_pipeline.py:133-172, 893-1013`
- Modify: `musubi/tests/test_agent_loop.py:596-670, 2232-2253`

**Interfaces:**
- `_stage_brief(request: str, predecessor_output: str | None, idx: int, total: int) -> str`.
- `MAX_STAGE_HANDOFF_CHARS = 8_000`.
- `stage_input_breakdown(system_prompt, brief, child_tools) -> dict[str, int]`.
- `ContextBudgetExceededError` reports the effective cap and serialized total; the runner adds stage/component context in its failure reason.

- [ ] **Step 1: Write failing stage-handoff and pre-call tests**

```python
def test_middle_stage_brief_uses_only_immediate_predecessor() -> None:
    brief = _stage_brief("request", "### design\nlatest", 2, 4)
    assert "latest" in brief
    assert "### plan" not in brief


def test_pipeline_rejects_oversized_designer_handoff_before_coder_call(monkeypatch):
    outputs = iter(["plan", "x" * (MAX_STAGE_HANDOFF_CHARS + 1)])
    completions: list[dict[str, Any]] = []
    monkeypatch.setattr("agent.run.run_unit", lambda *a, **k: (next(outputs), 1))
    result = asyncio.run(run_pipeline(
        None,
        {"parent_session_id": "outer", "parent_agent_name": "agent",
         "pipeline_name": "feature-dev", "brief": "build dashboard"},
        PipelineRouter(), [], io.StringIO(), strict=False,
    ))
    assert "handoff exceeds" in result
    assert completions[-1]["status"] == "failed"
```

- [ ] **Step 2: Run the new tests and confirm cumulative handoff still fails them**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_spawn_pipeline.py tests/test_agent_loop.py -k "immediate_predecessor or oversized_designer_handoff" -v`

Expected: FAIL because `_stage_brief` accepts a list of all summaries and no producer cap exists.

- [ ] **Step 3: Implement immediate-predecessor projection and producer bounds**

```python
def _stage_brief(request: str, predecessor_output: str | None, idx: int, total: int) -> str:
    if idx == 0:
        return request
    assert predecessor_output is not None
    if idx == total - 1:
        return "Evaluate the output of the prior stage only.\n\n" + predecessor_output
    return f"{request}\n\n## Prior stage output\n\n{predecessor_output}"
```

Retain `summaries` only as an append-only in-process result projection; pass `summaries[-1]` to every non-first stage. Add `maxOutputTokens: 2048` to the planner/designer worker frontmatter. Before completing a planner/designer worker, fail it when its UTF-8 output exceeds `MAX_STAGE_HANDOFF_CHARS`.

Build the stage prompt after context fetch, calculate the fixed prompt/brief/tool breakdown, and invoke the hard fitter before `run_unit`. A protected-input overflow must produce a terminal stage failure without calling the vendor.

- [ ] **Step 4: Run focused handoff and context regression tests**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_spawn_pipeline.py tests/test_agent_loop.py -k "pipeline or context_budget or immediate_predecessor" -v`

Expected: PASS; the fixture equivalent to 26,615 serialized characters reaches coder cycle 0 under the 32,000-character cap, while an oversize producer fails before downstream work.

- [ ] **Step 5: Commit the bounded-handoff change**

```bash
git add musubi/agent/pipeline_runner.py musubi/agent/context.py \
  .github/agents/workers/planner.agent.md .github/agents/workers/designer.agent.md \
  musubi/tests/test_spawn_pipeline.py musubi/tests/test_agent_loop.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(pipeline): bound protected stage handoff"
```

### Task 3: Make completion audit delivery durable and idempotent

**Files:**
- Modify: `musubi/server.py:1507-1534, 1881-2005, 2152-2168`
- Modify: `musubi/storage/subagent_audit.py:196-236`
- Modify: `musubi/tests/test_subagent_audit.py:204-360`
- Modify: `musubi/tests/test_root_skill_injection.py:220-255`
- Modify: `musubi/tests/test_sub_sessions.py:480-510`

**Interfaces:**
- `deliver_complete_obligation(obligation: Mapping[str, Any], db_path: Path | None = None) -> None`.
- `_durable_completion_evidence(payload: dict[str, Any]) -> dict[str, Any] | None`.
- `_relay_pending_audit_evidence() -> None` relays both `worker_spawn` and `worker_complete` kinds.
- `record_complete` uses `INSERT OR IGNORE` against the existing unique `(handle_id, event)` index.

- [ ] **Step 1: Write failing completion-outbox tests**

```python
def test_complete_audit_failure_leaves_relayable_obligation(mcp_db, monkeypatch):
    monkeypatch.setattr(subagent_audit, "record_complete", raise_sqlite_error)
    result = json.loads(server.musubi_complete_subagent(
        handle_id=handle, summary="completed", turns=1, status="done",
    ))
    assert result["status"] == "error"
    assert result["error_kind"] == "audit_unavailable"
    obligations = db.get_audit_obligations(status="pending", db_path=mcp_db)
    assert [(item["kind"], item["handle_id"])] == [("worker_complete", handle)]


def test_completion_relay_is_idempotent(audit_db):
    deliver_complete_obligation(payload, audit_db)
    deliver_complete_obligation(payload, audit_db)
    assert len(query_events(handle_id=payload["handle_id"], db_path=audit_db)) == 1
```

- [ ] **Step 2: Run tests and confirm completion audit failures are currently swallowed**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_subagent_audit.py tests/test_sub_sessions.py -k "completion and obligation" -v`

Expected: FAIL because `musubi_complete_subagent` catches and discards the audit exception.

- [ ] **Step 3: Implement completion outbox delivery**

```python
def _durable_completion_evidence(payload: dict[str, Any]) -> dict[str, Any] | None:
    obligation_id = _db.record_audit_obligation(
        kind="worker_complete", handle_id=str(payload["handle_id"]),
        payload=payload, created_at=_now_iso(),
    )
    try:
        subagent_audit.deliver_complete_obligation(payload)
        _db.mark_audit_obligation_delivered(obligation_id, _now_iso())
        return None
    except Exception as exc:
        _db.mark_audit_obligation_failed(obligation_id, str(exc))
        return {"status": "error", "error_kind": "audit_unavailable",
                "audit_obligation_id": obligation_id, "error": str(exc)}
```

Build the payload from the final persisted sub-session row and verifier result. Return the error after the terminal state is persisted so callers cannot lose lifecycle ownership; treat it as a fail-closed result that blocks pipeline success. Relay pending completion obligations from `musubi_query_subagent_events` alongside spawn obligations.

- [ ] **Step 4: Run completion lifecycle tests**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_subagent_audit.py tests/test_sub_sessions.py tests/test_root_skill_injection.py -v`

Expected: PASS; every spawn/completion pair is durable or explicitly represented as one pending outbox obligation.

- [ ] **Step 5: Commit the audit-outbox change**

```bash
git add musubi/server.py musubi/storage/subagent_audit.py \
  musubi/tests/test_subagent_audit.py musubi/tests/test_root_skill_injection.py \
  musubi/tests/test_sub_sessions.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(audit): persist worker completion obligations"
```

### Task 4: Make terminal status authoritative and accept verified read-only cap completions

**Files:**
- Modify: `musubi/session/sub_sessions.py:25-300`
- Modify: `musubi/storage/db.py:122-160, 1266-1295`
- Modify: `musubi/storage/subagent_audit.py:45-124, 196-236`
- Modify: `musubi/server.py:1881-2005`
- Modify: `musubi/agent/pipeline_runner.py:470-790, 936-1030`
- Modify: `musubi/tests/test_sub_sessions.py:572-705`
- Modify: `musubi/tests/test_spawn_pipeline.py:372-444, 954-1078`

**Interfaces:**
- `sub_sessions.complete(handle_id, *, summary, structured=None, tools_used=None, turns=0, status="done", artifacts=None, accept_readonly_turn_cap=False, db_path=None) -> dict`.
- Persisted `sub_sessions.turn_cap_accepted: bool` and `turn_cap_acceptance: str | None`.
- Audit `accepted_at_turn_cap: bool` and `turn_cap_acceptance: str | None`.
- `_complete_pipeline_stage(session, handle_id, summary, turns, status, artifacts, stage_identity, db_path) -> dict[str, Any]` parses and validates the authoritative completion response.

- [ ] **Step 1: Write failing exact-cap and status-divergence tests**

```python
def test_readonly_verified_text_at_turn_cap_stays_done(mcp_db):
    handle = _spawn_readonly_at_cap()
    result = json.loads(server.musubi_complete_subagent(
        handle_id=handle, summary="bounded findings", turns=3, status="done",
    ))
    assert result["final_status"] == "done"
    assert result["accepted_at_turn_cap"] is True


def test_pipeline_stops_when_harness_coerces_done_to_escalated(monkeypatch):
    monkeypatch.setattr("agent.run._call_tool_text", fake_completion_escalation)
    with pytest.raises(RuntimeError, match="harness recorded escalated"):
        asyncio.run(run_pipeline(
            None,
            {"parent_session_id": "outer", "parent_agent_name": "agent",
             "pipeline_name": "feature-dev", "brief": "build dashboard"},
            PipelineRouter(), [], io.StringIO(), strict=True,
            compression_db_path=state_path, audit_db_path=audit_path,
        ))
    assert vendor.calls_for_role("reviewer") == 0
    assert db.get_stage_row("pipe", "code", 1)["phase"] == "escalated"
```

- [ ] **Step 2: Run the test pair and confirm current behavior fails**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_sub_sessions.py tests/test_spawn_pipeline.py -k "readonly and cap or coerces_done" -v`

Expected: FAIL because text-only cap completions always escalate and the runner ignores completion JSON.

- [ ] **Step 3: Implement substrate acceptance and runner terminalization**

```python
if turns >= row["max_turns"]:
    if status == "done" and _artifacts_verified(artifacts):
        accepted_note = "[harness] accepted at turn cap: verified artifact manifest"
    elif accept_readonly_turn_cap and _read_only_surface(row) and summary and summary.strip():
        accepted_note = "[harness] accepted at turn cap: verified read-only result"
    else:
        timeout_reasons.append(
            f"max_turns={row['max_turns']} reached (turns={turns})"
        )
```

`server.py` sets `accept_readonly_turn_cap` only when summary/schema verification succeeds and the recorded surface has no `Write`, `Edit`, or `Bash` capability. Persist the acceptance reason in the state and audit rows.

In `pipeline_runner.py`, centralize every `musubi_complete_subagent` call after a handle is known. A response with `status != "recorded"`, no terminal `final_status`, or an audit error transitions `worker_running` to `escalated`; `final_status != "done"` skips the stage gate and finalizes/returns fail-closed. Only a recorded `done` can call `_record_worker_complete_checkpoint` and gate evaluation.

- [ ] **Step 4: Run P0 focused suite**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_spawn_pipeline.py tests/test_stage_loop.py tests/test_sub_sessions.py tests/test_subagent_audit.py tests/test_agent_config.py tests/test_agent_loop.py -v`

Expected: PASS; injected context/vendor/policy/completion failures leave no `worker_running` stage with a terminal worker.

- [ ] **Step 5: Commit the terminal-state change**

```bash
git add musubi/session/sub_sessions.py musubi/storage/db.py \
  musubi/storage/subagent_audit.py musubi/server.py musubi/agent/pipeline_runner.py \
  musubi/tests/test_sub_sessions.py musubi/tests/test_spawn_pipeline.py \
  musubi/tests/test_subagent_audit.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(pipeline): honor harness terminal worker status"
```
