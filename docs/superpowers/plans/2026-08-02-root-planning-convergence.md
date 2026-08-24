# Root Planning Convergence Implementation Plan

> **Status:** Implemented on 2026-08-02. The task breakdown remains as the
> review record; code and regression coverage are complete.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Root a closed, model-visible plan contract and stop repeated pre-worker contract loops with attributable Request Log evidence.

**Architecture:** Put manifest field definitions in `agent/manifest.py`, generate a Pydantic input schema for FastMCP from those definitions, and retain the same deterministic parser at runtime. Keep the failure counter in `GoalState`, use it to narrow the root planning surface after two errors, and count it in the existing root no-progress breaker before the next model call.

**Tech Stack:** Python 3.11, Pydantic v2 supplied by FastMCP, stdlib JSON, pytest, Ruff.

## Global Constraints

- Root, not the harness, chooses Direct versus Planning and the worker chain.
- The harness validates declaration shape, permitted roles, route order, and hard ceilings fail-closed.
- `planner` stays excluded from Root's `worker_chain` because Root owns planning.
- Preserve accepted manifest persistence format and append-only audit/log records.
- Never add an LLM call to the substrate.
- The three-failure correction breaker is ephemeral and must be tagged with its measurable lifecycle trigger.

---

### Task 1: Publish one closed model-visible manifest and worker-chain schema

**Files:**
- Modify: `musubi/agent/manifest.py:42-180`
- Modify: `musubi/server.py:35-60, 1254-1268`
- Modify: `musubi/tests/test_manifest.py:1-296`
- Modify: `musubi/tests/test_server_surface.py:1-60`
- Modify: `musubi/tests/test_agent_loop.py:3492-3506`

**Interfaces:**
- Produces `ChangeManifestInput(BaseModel)` with `ConfigDict(extra="forbid")`.
- Produces `ROOT_PLAN_WORKER_ROLE = Literal["designer", "coder", "reviewer"]` (plus any existing ordered role that is legal for Root).
- `musubi_commit_plan(plan_markdown: str, change_manifest: ChangeManifestInput, change_size: Literal["small", "medium", "large"], worker_chain: list[ROOT_PLAN_WORKER_ROLE]) -> str`.
- `manifest_schema() -> dict[str, Any]` returns the same contract used in correction responses.

- [ ] **Step 1: Write a schema-introspection regression test**

```python
def test_commit_plan_tool_exposes_closed_manifest_and_chain_enum() -> None:
    tool = server.mcp._tool_manager._tools["musubi_commit_plan"]
    schema = tool.parameters
    manifest = schema["$defs"]["ChangeManifestInput"]
    assert manifest["additionalProperties"] is False
    assert set(manifest["required"]) == {"files_expected", "subsystems"}
    assert "planner" not in schema["properties"]["worker_chain"]["items"]["enum"]
```

- [ ] **Step 2: Run it and confirm the current free-form schema fails**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_server_surface.py -k commit_plan -v`

Expected: FAIL because `change_manifest` has `additionalProperties: true` and worker roles are arbitrary strings.

- [ ] **Step 3: Implement the shared Pydantic schema**

```python
class ChangeManifestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files_expected: int = Field(ge=0)
    subsystems: list[str]
    public_contract: bool = False
    data_migration: bool = False
    security_sensitive: bool = False
    external_side_effects: bool = False
    destructive: bool = False
    blocking_decisions: list[str] = Field(default_factory=list)
    validation_commands: int = Field(default=0, ge=0)
```

Use the `ChangeManifestInput` annotation in the FastMCP tool, convert with `model_dump()`, and make parser field constants the one source for schema defaults. Keep runtime `parse_change_manifest_object` as the authoritative defensive validation for non-conforming providers.

- [ ] **Step 4: Run schema and manifest parser tests**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_manifest.py tests/test_server_surface.py tests/test_agent_loop.py -k "manifest or commit_plan" -v`

Expected: PASS; extra properties, missing required radius fields, invalid booleans, and `planner` chain entries are visible/refused consistently.

- [ ] **Step 5: Commit the typed contract**

```bash
git add musubi/agent/manifest.py musubi/server.py musubi/tests/test_manifest.py \
  musubi/tests/test_server_surface.py musubi/tests/test_agent_loop.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(root): expose closed planning contract"
```

### Task 2: Add machine-readable corrections and bounded Root planning control

**Files:**
- Modify: `musubi/agent/goal_state.py:140-220, 560-610`
- Modify: `musubi/agent/run.py:1190-1215, 3340-3770`
- Modify: `musubi/tests/test_goal_state.py:52-110`
- Modify: `musubi/tests/test_agent_budget.py:126-186`
- Modify: `musubi/tests/test_agent_loop.py:2999-3120`

**Interfaces:**
- `GoalState.planning_contract_failures: int = 0`.
- `GoalState.record_planning_contract_failure(error_kind: str) -> int`.
- `GoalState.reset_planning_contract_failures() -> None`.
- `root_decision_tools(tools, state, recovery_outcome=False, decision_only=False, spawn_exhausted=False)` offers only `musubi_commit_plan` after the second consecutive failure.
- `_root_control_error(error_kind, message, state) -> str` returns status/error_kind/message/expected_schema/allowed_roles/consecutive_failures.

- [ ] **Step 1: Write failing control-state tests**

```python
def test_second_plan_contract_failure_withholds_read_tools() -> None:
    state.begin_plan()
    state.record_planning_contract_failure("invalid_change_manifest")
    state.record_planning_contract_failure("invalid_change_manifest")
    assert [tool["name"] for tool in root_decision_tools(tools, state)] == ["musubi_commit_plan"]


def test_third_plan_contract_failure_returns_terminal_incomplete() -> None:
    result = _handle_root_control_tool("musubi_commit_plan", bad_args, orchestration)
    assert json.loads(result)["status"] == "incomplete"
```

- [ ] **Step 2: Run the focused tests and confirm the normal planning surface persists indefinitely**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_goal_state.py tests/test_agent_loop.py -k "plan_contract or commit_plan" -v`

Expected: FAIL because there is no failure counter or correction-only surface.

- [ ] **Step 3: Implement the counter, correction envelope, and tool narrowing**

```python
def _planning_error(state: GoalState, kind: str, message: str) -> str:
    failures = state.record_planning_contract_failure(kind)
    payload = {
        "status": "incomplete" if failures >= 3 else "error",
        "error_kind": kind,
        "message": message,
        "expected_schema": manifest_schema(),
        "allowed_roles": sorted(ORDERED_ROLES),
        "consecutive_failures": failures,
    }
    return json.dumps(payload)
```

Call the helper only for manifest/chain/plan-contract validation errors while `state.mode == "planning"`. A valid `commit_root_plan` resets the counter. On the third error, set `pending_clarification` to the incomplete reason so `_run_loop` returns before another model call.

- [ ] **Step 4: Extend the no-progress breaker to pre-worker control loops**

```python
if state.planning_contract_failures >= 3:
    return (
        "[incomplete] run stopped: three consecutive planning-contract "
        "failures occurred before any worker was spawned"
    )
```

Add the condition before the existing worker-outcome requirement, but retain the budget ratio and artifact-delivery exemption. This captures the observed 188,778-token root-only loop without stopping a productive run.

- [ ] **Step 5: Run focused convergence tests**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_goal_state.py tests/test_agent_budget.py tests/test_agent_loop.py -k "plan_contract or no_progress or commit_plan" -v`

Expected: PASS; eight invalid fixture attempts halt after three and a valid retry resets the counter.

- [ ] **Step 6: Commit the bounded control-loop change**

```bash
git add musubi/agent/goal_state.py musubi/agent/run.py \
  musubi/tests/test_goal_state.py musubi/tests/test_agent_budget.py \
  musubi/tests/test_agent_loop.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(root): bound planning contract retries"
```

### Task 3: Emit sanitized planning-control outcomes into Request Log

**Files:**
- Modify: `musubi/agent/run.py:3430-3510`
- Modify: `musubi/agent/runtime_log.py:85-104`
- Modify: `musubi/tests/test_agent_loop.py:1178-1228, 3492-3506`
- Modify: `musubi/tests/test_runtime_log.py:1-100`

**Interfaces:**
- `emit_runtime_log(log, "[agent] control musubi_commit_plan status=error error_kind=invalid_change_manifest", category="tools")`.
- `sanitize_control_result(result: str, tool_name: str) -> str` emits only tool, status, error_kind, bounded message, and failure count.

- [ ] **Step 1: Write a failing log-sanitization test**

```python
def test_commit_plan_error_is_emitted_as_request_scoped_tool_event() -> None:
    result = '{"status":"error","error_kind":"invalid_change_manifest","message":"files_expected is required"}'
    assert sanitize_control_result(result, "musubi_commit_plan") == (
        "[agent] control musubi_commit_plan status=error "
        "error_kind=invalid_change_manifest reason=files_expected is required"
    )
```

- [ ] **Step 2: Run it and confirm control results currently only reach tool audit**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_agent_loop.py tests/test_runtime_log.py -k "control or runtime_log" -v`

Expected: FAIL because `_handle_root_control_tool` results are not emitted to `RuntimeLogWriter`.

- [ ] **Step 3: Implement request-scoped sanitized control logging**

```python
result = _handle_root_control_tool(name, args, orchestration)
emit_runtime_log(log, sanitize_control_result(result, name), category="tools")
```

Do not serialize full `expected_schema`, raw `plan_markdown`, or `change_manifest` into the runtime event. `RuntimeLogWriter` remains unchanged in scope: when called at root scope it automatically writes `agent_handle=null`, which maps the line to Request Log rather than any Agent Log.

- [ ] **Step 4: Run targeted log tests**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_agent_loop.py tests/test_runtime_log.py -k "control or runtime_log or tool_result" -v`

Expected: PASS; errors are visible in Request Log with no schema/body leak and no worker handle.

- [ ] **Step 5: Commit logging and documentation**

```bash
git add musubi/agent/run.py musubi/agent/runtime_log.py \
  musubi/tests/test_agent_loop.py musubi/tests/test_runtime_log.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(console): log root planning control outcomes"
```
