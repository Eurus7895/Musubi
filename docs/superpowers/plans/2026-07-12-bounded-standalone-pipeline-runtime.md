# Bounded Standalone Pipeline Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent planner/designer loops from exhausting a pipeline run before coder/reviewer by making worker metadata, turn caps, context size, and stage token allowances explicit and enforceable.

**Architecture:** The standalone pipeline remains a recipe of ordinary standalone workers; it does not adopt the incompatible VS Code stage-store protocol. A validated `PipelineWorkerSpec` controls prompt, max cycles, and hard context size. Every stage receives a child token allowance that charges the shared run budget while reserving capacity for later stages.

**Tech Stack:** Python 3.11+, asyncio, MCP client, pytest, existing `LMRouter` and budget substrate.

## Global Constraints

- The substrate makes zero LLM calls; all model calls remain in the driver.
- Preserve the evaluator firewall: reviewer sees only the immediately prior stage.
- Keep policy fail-closed and audit spawn/completion append-only.
- Do not silently reroute a selected pipeline or create new prompt-agent variants.
- Keep the canonical flat standalone worker catalog; do not load the VS Code pipeline-stage protocol.
- Keep each pipeline worker model input at or below 16,000 serialized characters, including tool definitions.

---

### Task 1: Validated Pipeline Worker Specification

**Files:**
- Modify: `musubi/agent/pipeline_runner.py`
- Modify: `musubi/agent/subagent.py`
- Modify: `musubi/tests/test_spawn_pipeline.py`

**Interfaces:**
- Produces: `PipelineWorkerSpec(role, prompt, max_cycles, context_budget_chars)`.
- Produces: `resolve_pipeline_worker_spec(role, agents_dir) -> PipelineWorkerSpec`.
- Consumes: canonical standalone worker prompt resolved with `AgentPromptPurpose.WORKER`.

- [ ] **Step 1: Write failing specification tests**

Create a temporary canonical worker prompt:

```python
prompt = """---
name: Planner
maxTurns: 4
tools: [Read, View]
---
Return a compact implementation plan.
"""
```

Assert:

```python
spec = resolve_pipeline_worker_spec("planner", agents_dir)
assert spec.role == "planner"
assert spec.max_cycles == 4
assert spec.context_budget_chars == 16_000
assert "compact implementation plan" in spec.prompt
```

Add fail-closed cases for missing prompt, `maxTurns: 0`, non-integer maxTurns,
and a prompt resolved only from `pipeline-stages/feature-dev`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_spawn_pipeline.py -k worker_spec -q
```

Expected: FAIL because `PipelineWorkerSpec` and its resolver do not exist.

- [ ] **Step 3: Implement the specification**

Add:

```python
@dataclass(frozen=True)
class PipelineWorkerSpec:
    role: str
    prompt: str
    max_cycles: int
    context_budget_chars: int = PIPELINE_CONTEXT_BUDGET
```

Resolve the existing `WORKER` prompt intentionally, parse only the YAML
frontmatter needed for `maxTurns`, require `1 <= maxTurns <= 12`, strip the
frontmatter through the existing prompt builder, and raise `RuntimeError`
before stage spawn on invalid metadata.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the worker-spec tests and existing prompt-resolver tests. Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add musubi/agent/pipeline_runner.py musubi/agent/subagent.py musubi/tests/test_spawn_pipeline.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): validate pipeline worker contracts"
```

### Task 2: One Turn Cap Across Runner, State, and Audit

**Files:**
- Modify: `musubi/agent/pipeline_runner.py`
- Modify: `musubi/server.py`
- Modify: `musubi/tests/test_spawn_pipeline.py`
- Modify: `musubi/tests/test_subagent_audit.py`

**Interfaces:**
- Consumes: `PipelineWorkerSpec.max_cycles`.
- Produces from `musubi_spawn_pipeline_stage`: `max_turns` in the response.
- Guarantees: spawn row, `run_unit(max_cycles=...)`, and completion use one cap.

- [ ] **Step 1: Write the failing cap-consistency test**

Capture fake MCP calls and `run_unit` arguments for a worker with
`maxTurns: 4`:

```python
assert spawn_calls[0]["max_turns"] == 4
assert run_unit_calls[0]["max_cycles"] == 4
assert completed[0]["turns"] <= 4
```

Add a server test asserting the spawn response includes `"max_turns": 4` and
the audit spawn row records four.

- [ ] **Step 2: Run tests and verify RED**

Run the two focused test files. Expected: FAIL because the runner hard-codes
12 while the server defaults to eight and omits the cap from its response.

- [ ] **Step 3: Wire the single cap**

Build the worker spec before spawning, send:

```python
{
    "pipeline_session_id": psid,
    "pipeline_name": pname,
    "stage": stage,
    "brief": brief,
    "max_turns": spec.max_cycles,
}
```

Return `max_turns` from the server and fail if it differs from the requested
spec. Pass that exact value to `run_unit(max_cycles=...)`. Remove
`DEFAULT_STAGE_MAX_CYCLES` from terminal copy; use the spec value.

- [ ] **Step 4: Run tests and verify GREEN**

Run `test_spawn_pipeline.py`, `test_subagent_audit.py`, and
`test_g3_observability.py`. Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add musubi/agent/pipeline_runner.py musubi/server.py musubi/tests/test_spawn_pipeline.py musubi/tests/test_subagent_audit.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): unify pipeline stage turn caps"
```

### Task 3: Hard Model-Input Context Cap

**Files:**
- Modify: `musubi/agent/context.py`
- Modify: `musubi/agent/run.py`
- Modify: `musubi/tests/test_agent_loop.py`

**Interfaces:**
- Produces: `fit_model_input(messages, tools, *, budget_chars, compression_db_path) -> list[dict]`.
- Produces: `ContextBudgetExceededError(total_chars, budget_chars)`.
- Guarantees: serialized fitted messages plus tools do not exceed the cap.

- [ ] **Step 1: Write failing hard-cap tests**

Construct messages containing repeated glob/read results and three tool
schemas. Assert:

```python
fitted = fit_model_input(messages, tools, budget_chars=16_000)
size = len(json.dumps(fitted, ensure_ascii=False, default=str))
size += len(json.dumps(tools, ensure_ascii=False, default=str))
assert size <= 16_000
assert fitted[0] == messages[0]
assert "user goal" in json.dumps(fitted[1])
```

Add an unshrinkable system-prompt/tool-schema case and assert
`ContextBudgetExceededError` is raised before `vendor.call`.

- [ ] **Step 2: Run tests and verify RED**

Run `test_agent_loop.py -k context`. Expected: FAIL because protected recent
messages and tools can currently exceed the nominal limit.

- [ ] **Step 3: Implement a strict fitter**

Calculate the tool reservation first:

```python
tool_chars = len(json.dumps(tools, ensure_ascii=False, default=str))
message_budget = budget_chars - tool_chars
```

Reuse `fit_context` for reversible compression, then allow old and recent tool
results to be replaced with pairing-preserving stubs until the serialized
total fits. Never trim the system prompt or first user goal. Raise when the
minimum representation cannot fit.

Replace `_run_loop`'s direct `fit_context` call with `fit_model_input`; retain
the root default by passing its existing 40,000-character budget.

- [ ] **Step 4: Run tests and verify GREEN**

Run all `test_agent_loop.py` tests. Expected: PASS, including root-default and
truncated-write behavior.

- [ ] **Step 5: Commit**

```powershell
git add musubi/agent/context.py musubi/agent/run.py musubi/tests/test_agent_loop.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): enforce hard model input caps"
```

### Task 4: Stage Token Allowances With Later-Stage Reserve

**Files:**
- Modify: `musubi/agent/budget.py`
- Modify: `musubi/agent/pipeline_runner.py`
- Modify: `musubi/tests/test_budget.py`
- Modify: `musubi/tests/test_spawn_pipeline.py`

**Interfaces:**
- Produces: `ChildTokenBudget(parent, max_tokens)` with `preflight`, `charge`,
  `tokens_used`, `remaining`, `max_tokens`, and `warn_at_ratio`.
- Produces: `pipeline_stage_allowance(parent, stages_remaining) -> int`.

- [ ] **Step 1: Write failing child-budget tests**

```python
parent = TokenBudgetEnforcer(200_000)
child = ChildTokenBudget(parent, 50_000)
assert child.preflight(40_000) == "allow"
assert child.charge(40_000) in {"allow", "warn"}
assert child.tokens_used == 40_000
assert parent.tokens_used == 40_000
assert child.preflight(11_000) == "halt"
```

Verify a four-stage 200k run gives planner/design bounded allowances and leaves
a positive coder/reviewer reserve after both earlier stages exhaust theirs.

- [ ] **Step 2: Run tests and verify RED**

Run `test_budget.py` and pipeline budget tests. Expected: FAIL because every
stage currently charges the unpartitioned parent directly.

- [ ] **Step 3: Implement child allowances**

Use a fair-share burst rule that cannot consume later shares:

```python
def pipeline_stage_allowance(parent, stages_remaining):
    if stages_remaining <= 0:
        raise ValueError("stages_remaining must be positive")
    fair_share = parent.remaining // stages_remaining
    return max(1, min(parent.remaining, fair_share))
```

`ChildTokenBudget.charge()` charges the child and parent exactly once. Its
status is the stricter of the two. Pass a fresh child to each stage's
`run_unit`; do not change stateless/root-agent budgeting.

- [ ] **Step 4: Finalize stage-budget failures explicitly**

When a child allowance raises `TokenBudgetExhaustedError`, complete that stage
as `escalated`, finalize the pipeline once as `escalated`, and return non-zero
under `strict=True`. Include stage name, used allowance, and run remaining in
the process log without exposing credentials or full prompts.

- [ ] **Step 5: Run tests and verify GREEN**

Run budget, agent-loop, spawn-pipeline, and G3 observability tests. Expected:
all PASS.

- [ ] **Step 6: Commit**

```powershell
git add musubi/agent/budget.py musubi/agent/pipeline_runner.py musubi/tests/test_budget.py musubi/tests/test_spawn_pipeline.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): reserve tokens for later pipeline stages"
```

### Task 5: Pipeline Runtime Regression and Acceptance

**Files:**
- Modify: `docs/roadmap.md`
- Modify: `docs/superpowers/plans/2026-07-12-bounded-standalone-pipeline-runtime.md`

- [ ] **Step 1:** Run the full Python agent/pipeline/budget/observability suites.
- [ ] **Step 2:** Run Node tests, both Rust suites, production GUI build, and
  `git diff --check`.
- [ ] **Step 3:** Run `feature-dev` with a small standalone HTML artifact brief.
  Verify planner and designer stay within their common cap, coder and reviewer
  both start, every audit `max_turns` matches runtime, and no model input exceeds
  16,000 serialized characters.
- [ ] **Step 4:** Force planner allowance exhaustion and verify coder/reviewer
  reserve is not charged, the stage/run finalize once as `escalated`, and the
  retained process log explains the halt.
- [ ] **Step 5:** Verify reviewer input contains only coder output, then record
  the acceptance result in the roadmap and commit with
  `docs(roadmap): record bounded pipeline runtime`.
