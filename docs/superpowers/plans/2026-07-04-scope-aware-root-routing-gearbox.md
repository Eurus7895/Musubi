# Scope-Aware Root Routing Gearbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hybrid scope-aware routing layer so the standalone root agent can distinguish small edits/artifacts from larger feature work before spawning workers.

**Architecture:** A deterministic, zero-LLM classifier produces a compact scope hint for every root turn. The hint is injected into the root system prompt and passed into dispatch as a guardrail, while the model still makes the final routing decision. Simple routes get bounded worker fan-out; large routes require plan/design workflow guidance.

**Tech Stack:** Python agent host (`musubi/agent`), Markdown prompt/skill catalog, pytest.

## Global Constraints

- Do not hardcode one artifact type such as HTML dashboards in root routing policy.
- Define small tasks by risk, ambiguity, and blast radius, not by file extension alone.
- Preserve root agent autonomy: the substrate provides hints and guardrails, not a complete strict router.
- Keep root write/edit/bash tools denied; mutations still go through bounded workers.
- Log scope and route decisions so token/cycle behavior can be audited.

---

### Task 1: Deterministic Scope Classifier

**Files:**
- Create: `musubi/agent/scope.py`
- Test: `musubi/tests/test_agent_scope.py`

**Interfaces:**
- Produces: `ScopeKind`, `ScopeHint`, `classify_task(task: str) -> ScopeHint`
- Produces: `ScopeHint.prompt_block() -> str`

- [x] **Step 1: Write failing classifier tests**

```python
from agent.scope import ScopeKind, classify_task

def test_classifies_known_file_edit_as_simple_edit() -> None:
    hint = classify_task("Update weather-dashboard.html to refresh every 5 minutes")
    assert hint.kind is ScopeKind.SIMPLE_EDIT
    assert hint.route == "single_coder"
    assert hint.max_workers == 1
```

- [x] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest musubi\tests\test_agent_scope.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'agent.scope'`.

- [x] **Step 3: Implement classifier**

Implement `ScopeKind` values:

```python
simple_edit
simple_artifact
medium_change
large_feature
unknown
```

Classification rules:

- `unknown`: vague requests such as `fix this`, `refactor it`, `add tests`, with no concrete target.
- `large_feature`: auth, billing, schema/data migrations, persistence, public API, architecture, or multiple high-risk domains.
- `simple_edit`: concrete low-risk change with a path/symbol or obvious single target, expected <=2 files.
- `simple_artifact`: concrete create/generate/make artifact requests such as page, report, dashboard, CSV, Markdown, JSON, not limited to HTML.
- `medium_change`: concrete change that is not obviously simple and not large.

- [x] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest musubi\tests\test_agent_scope.py -q`

Expected: PASS.

### Task 2: Root Prompt Scope Hint

**Files:**
- Modify: `musubi/agent/run.py`
- Test: `musubi/tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `classify_task(task).prompt_block()`
- Produces: root system prompt containing `[agent-routing-scope]`

- [x] **Step 1: Write failing prompt injection test**

```python
def test_root_system_prompt_includes_scope_hint_for_simple_task() -> None:
    answer = asyncio.run(run_agent("Update weather-dashboard.html to refresh every 5 minutes", router, _musubi_dir()))
    assert "scope=simple_edit" in router.calls[0]["messages"][0]["content"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest musubi\tests\test_agent_loop.py::test_root_system_prompt_includes_scope_hint_for_simple_task -q`

Expected: FAIL because prompt lacks `[agent-routing-scope]`.

- [x] **Step 3: Inject hint**

In `run_agent`, compute `scope_hint = classify_task(task)`, log:

```text
[agent] scope=simple_edit route=single_coder max_workers=1 reason="known file + one behavior change"
```

Build root prompt with:

```python
system_prompt = build_system_prompt(scope_hint.prompt_block())
```

- [x] **Step 4: Run prompt test**

Expected: PASS.

### Task 3: Simple Route Worker Guard

**Files:**
- Modify: `musubi/agent/run.py`
- Test: `musubi/tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `ScopeHint.max_workers`
- Produces: refused tool result for extra coder spawns on `simple_*` routes

- [x] **Step 1: Write failing spawn guard test**

```python
def test_simple_task_refuses_extra_coder_spawns_in_same_turn() -> None:
    # two coder spawn tool calls in the same root turn
    # expect only first child to run, second result is status=refused
```

- [x] **Step 2: Run test to verify it fails**

Expected: FAIL because both coders run.

- [x] **Step 3: Add dispatch guard**

Extend spawn overflow detection to accept the current `ScopeHint`. If `scope.kind` is `simple_edit` or `simple_artifact`, allow only one `coder` spawn per root turn and return:

```json
{"status":"refused","reason":"simple task route allows only one coder worker"}
```

Do not restrict investigator/reviewer workers for non-simple routes.

- [x] **Step 4: Run guard test**

Expected: PASS.

### Task 4: Prompt + Skill Contract

**Files:**
- Modify: `.github/agents/root/agent.agent.md`
- Modify: `.github/skills/agent-routing/SKILL.md`

**Interfaces:**
- Consumes: `[agent-routing-scope]` prompt block
- Produces: model-facing contract for `scope`, `route`, and `reason`

- [x] **Step 1: Update root prompt**

Add a concise rule: read `[agent-routing-scope]` before spawning; for simple routes use at most one coder and do not re-spawn the same strategy; for large routes ask for/produce plan-design workflow guidance.

- [x] **Step 2: Update routing skill**

Add scope taxonomy and examples for `simple_edit`, `simple_artifact`, `medium_change`, `large_feature`, and `unknown`.

### Task 5: Verification + PR Update

**Files:**
- All above.

- [x] **Step 1: Run focused tests**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_agent_scope.py musubi\tests\test_agent_loop.py::test_root_system_prompt_includes_scope_hint_for_simple_task musubi\tests\test_agent_loop.py::test_simple_task_refuses_extra_coder_spawns_in_same_turn -q
```

- [x] **Step 2: Run regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_agent_loop.py musubi\tests\test_subagent_orchestrator.py -q
```

- [x] **Step 3: Run formatting/whitespace check**

```powershell
git diff --check
```

- [x] **Step 4: Commit**

Commit message:

```text
feat(agent): add scope-aware root routing hints
```

- [x] **Step 5: Push branch**

Push the current branch. Open or update a PR only when requested for this
implementation branch.
