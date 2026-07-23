# Governed Scope, Worker Budget, and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace lexical-only scope routing and model-owned worker/recovery choices with deterministic ambiguity-impact-risk assessment, role-owned worker turn budgets, and one audited continuation for genuinely unfinished mutate workers.

**Architecture:** The root first performs a zero-LLM request assessment and returns one deterministic clarification when intent is underspecified. A planner may then emit a bounded `ChangeManifest`; the driver parses it, recomputes impact/risk, and constrains the next legal role. Direct-worker turn caps come only from role frontmatter, while recoverable turn-cap failures transition through the normal audited spawn path without waiting for the root model to remember the replacement tool.

**Tech Stack:** Python 3.11+, dataclasses, `StrEnum`, standard-library regex/JSON, pytest, existing `LMRouter` fake routers, MCP sub-session audit.

## Global Constraints

- Preserve HI #1: substrate code and every `musubi_*` tool make zero LLM calls.
- Preserve HI #5: pipeline membership and tool access remain fail-closed.
- Preserve HI #8: automatic continuation uses the normal spawn/completion path and writes `subagent_audit` rows.
- Role frontmatter is the only owner of a direct worker's turn cap; root worker and token ceilings remain unchanged.
- Scope controls routing but never grants tools; policy remains final authority.
- High ambiguity returns exactly one question before any parent session, model call, or worker spawn.
- Automatic continuation is limited to one same-role replacement for `turn_cap` plus surviving touched files.
- Repeated failure, absent artifacts, policy denial, or exhausted worker/token budgets terminate fail-closed.
- Large workflows remain explicitly user-invoked; this plan does not auto-launch a pipeline.
- Keep roadmap entries summary-only and link this plan for implementation detail.

---

## File Structure

- Create `musubi/agent/change_assessment.py`: pure request/manifest assessment.
- Modify `musubi/agent/scope.py`: keep intent detection; delegate complexity routing.
- Modify `musubi/agent/goal_state.py`: retain planner assessment and legal next role.
- Modify `musubi/agent/run.py`: enforce clarification, role order, and recovery transitions.
- Modify `musubi/agent/subagent.py`: enforce role-owned turns and record typed failures.
- Modify `.github/agents/workers/planner.agent.md`: require a bounded manifest.
- Add/modify focused tests under `musubi/tests/`.
- Modify `docs/roadmap.md`: planned summary, then completion correction when landed.

---

### Task 1: Deterministic Request Assessment and Clarification Gate

**Files:**
- Create: `musubi/agent/change_assessment.py`
- Modify: `musubi/agent/scope.py`
- Modify: `musubi/agent/run.py`
- Test: `musubi/tests/test_agent_scope.py`
- Test: `musubi/tests/test_agent_loop.py`

**Interfaces:**
- Produces `Band`, `ChangeAssessment`, and `assess_request(task: str) -> ChangeAssessment`.
- `ChangeAssessment.route` is one of `ask_scope`, `single_coder`, `planner_then_coder_check`, `plan_design_workflow`.
- `ChangeAssessment.clarifying_question` is non-null only for `ask_scope`.

- [x] **Step 1: Write failing request-assessment tests**

Append to `musubi/tests/test_agent_scope.py`:

```python
from agent.change_assessment import Band, assess_request


def test_bare_website_creation_requires_clarification() -> None:
    result = assess_request("create a new website")
    assert (result.ambiguity, result.impact, result.risk) == (
        Band.HIGH, Band.UNKNOWN, Band.UNKNOWN,
    )
    assert result.route == "ask_scope"
    assert result.clarifying_question == (
        "What should the website do, and should it be a static page or use "
        "a specific framework?"
    )


def test_constrained_single_file_website_is_simple() -> None:
    result = assess_request(
        "Create a static single-file website at landing.html with hero, "
        "features, and contact sections"
    )
    assert result.ambiguity is Band.LOW
    assert result.impact is Band.LOW
    assert result.risk is Band.LOW
    assert result.route == "single_coder"


def test_specific_framework_scaffold_is_medium() -> None:
    result = assess_request(
        "Create a Next.js app-router scaffold with home/about routes, shared "
        "navbar/footer, TypeScript, and a production build check"
    )
    assert result.impact is Band.MEDIUM
    assert result.route == "planner_then_coder_check"


def test_auth_database_payment_site_is_large() -> None:
    result = assess_request(
        "Build a website with authentication, a customer database, and payments"
    )
    assert result.risk is Band.HIGH
    assert result.route == "plan_design_workflow"
```

Add to `musubi/tests/test_agent_loop.py`:

```python
def test_high_ambiguity_returns_question_without_model_or_worker() -> None:
    from agent import run as run_mod
    from agent.scope import classify_task

    hint = classify_task("create a new website")
    answer = run_mod._deterministic_scope_answer("create a new website", hint)
    assert hint.route == "ask_scope"
    assert answer == (
        "What should the website do, and should it be a static page or use "
        "a specific framework?"
    )
```

- [x] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_scope.py musubi/tests/test_agent_loop.py -k "website or high_ambiguity" -q
```

Expected: FAIL because `change_assessment` does not exist and `ask_scope` still reaches the model loop.

- [x] **Step 3: Implement the pure assessment types**

Create `musubi/agent/change_assessment.py`:

```python
"""Deterministic request and change-manifest assessment.

musubi-tier: substrate
expires-when: never - ambiguity, blast radius, and risk gates remain useful
  independently of model quality.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Band(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ChangeAssessment:
    ambiguity: Band
    impact: Band
    risk: Band
    route: str
    evidence: tuple[str, ...]
    clarifying_question: str | None = None


_BROAD_PRODUCT_RE = re.compile(
    r"(?i)\b(create|make|build|generate|implement)\b.*\b"
    r"(website|site|web app|application|app|platform|system)\b"
)
_STATIC_FILE_RE = re.compile(
    r"(?i)\b(static|single[- ]file)\b.*\b(html|website|page)\b|"
    r"\b[\w.-]+\.html\b"
)
_BOUNDED_ARTIFACT_RE = re.compile(
    r"(?i)\b(create|make|generate|write|build)\b.*\b"
    r"(file|page|dashboard|report|summary|csv|markdown|json|html|chart|doc)\b"
)
_FRAMEWORK_RE = re.compile(r"(?i)\b(next(?:\.js)?|react|vue|svelte|angular)\b")
_MULTIPART_RE = re.compile(
    r"(?i)\b(routes?|pages?|shared|navbar|footer|typescript|build check)\b"
)
_CRITICAL_RISK_RE = re.compile(
    r"(?i)\b(auth|authentication|authorization|payment|billing|database|"
    r"migration|oauth|rbac|security)\b"
)


def assess_request(task: str) -> ChangeAssessment:
    text = " ".join((task or "").split())
    risks = tuple(sorted(set(
        match.group(1).lower() for match in _CRITICAL_RISK_RE.finditer(text)
    )))
    if risks:
        return ChangeAssessment(
            Band.LOW, Band.HIGH, Band.HIGH, "plan_design_workflow",
            tuple(f"critical-risk:{item}" for item in risks),
        )
    if _BROAD_PRODUCT_RE.search(text) and not (
        _STATIC_FILE_RE.search(text) or _FRAMEWORK_RE.search(text)
    ):
        return ChangeAssessment(
            Band.HIGH, Band.UNKNOWN, Band.UNKNOWN, "ask_scope",
            ("broad-product-without-deliverable-constraints",),
            "What should the website do, and should it be a static page or use a specific framework?",
        )
    if _STATIC_FILE_RE.search(text) and not _FRAMEWORK_RE.search(text):
        return ChangeAssessment(
            Band.LOW, Band.LOW, Band.LOW, "single_coder",
            ("bounded-static-artifact",),
        )
    if _BOUNDED_ARTIFACT_RE.search(text) and not _FRAMEWORK_RE.search(text):
        return ChangeAssessment(
            Band.LOW, Band.LOW, Band.LOW, "single_coder",
            ("bounded-named-artifact",),
        )
    if _FRAMEWORK_RE.search(text) and _MULTIPART_RE.search(text):
        return ChangeAssessment(
            Band.LOW, Band.MEDIUM, Band.LOW, "planner_then_coder_check",
            ("framework-multifile-change",),
        )
    return ChangeAssessment(
        Band.MEDIUM, Band.MEDIUM, Band.UNKNOWN,
        "planner_then_coder_check", ("insufficient-deterministic-evidence",),
    )
```

- [x] **Step 4: Integrate without replacing inspect/destructive detection**

Add `assessment: ChangeAssessment | None = None` to `ScopeHint`. Keep casual, destructive, and read-only branches unchanged. For mutation requests, map `assess_request()` into the existing `ScopeKind`, `route`, `reason`, and `requires` fields.

Extend `_deterministic_scope_answer` in `run.py`:

```python
if scope_hint.route == "ask_scope":
    assessment = scope_hint.assessment
    if assessment is not None and assessment.clarifying_question:
        return assessment.clarifying_question
    return "What exact target and acceptance criteria should this change satisfy?"
```

- [x] **Step 5: Verify GREEN and existing routes**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_scope.py musubi/tests/test_agent_loop.py -k "scope or website or ambiguity" -q
```

Expected: PASS; bare website creation costs zero model/worker calls.

- [x] **Step 6: Commit Task 1**

```powershell
git add musubi/agent/change_assessment.py musubi/agent/scope.py musubi/agent/run.py musubi/tests/test_agent_scope.py musubi/tests/test_agent_loop.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): gate ambiguous product requests"
```

---

### Task 2: Planner Manifest and Post-Plan Reclassification

**Files:**
- Modify: `musubi/agent/change_assessment.py`
- Modify: `.github/agents/workers/planner.agent.md`
- Modify: `musubi/agent/goal_state.py`
- Modify: `musubi/agent/run.py`
- Create: `musubi/tests/test_change_assessment.py`
- Modify: `musubi/tests/test_goal_state.py`
- Modify: `musubi/tests/test_agent_loop.py`

**Interfaces:**
- Produces `ChangeManifest`, `parse_change_manifest(text)`, and `assess_manifest(manifest)`.
- Produces `GoalState.next_role` and `GoalState.pending_clarification`.
- Enforces planner before coder for medium routes; large manifests cannot escape through a direct coder.

- [x] **Step 1: Write failing manifest tests**

Create `musubi/tests/test_change_assessment.py`:

```python
from agent.change_assessment import Band, assess_manifest, parse_change_manifest

ELEVEN_FILE_MANIFEST = (
    '<change_manifest>{"files_expected":11,"subsystems":'
    '["config","routes","components","styles"],"public_contract":false,'
    '"data_migration":false,"security_sensitive":false,'
    '"external_side_effects":false,"destructive":false,"unknowns":[],'
    '"validation_commands":2}</change_manifest>'
)


def test_manifest_many_files_or_subsystems_is_large() -> None:
    manifest = parse_change_manifest(ELEVEN_FILE_MANIFEST)
    assert manifest is not None
    assert manifest.files_expected == 11
    assert manifest.subsystems == ("components", "config", "routes", "styles")
    result = assess_manifest(manifest)
    assert result.impact is Band.HIGH
    assert result.route == "plan_design_workflow"


def test_manifest_unknowns_require_clarification() -> None:
    manifest = parse_change_manifest(
        '<change_manifest>{"files_expected":3,"subsystems":["routes"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":["deployment target"],'
        '"validation_commands":1}</change_manifest>'
    )
    assert manifest is not None
    assert assess_manifest(manifest).route == "ask_scope"


def test_missing_or_oversized_manifest_fails_closed() -> None:
    assert parse_change_manifest("status: done") is None
    assert parse_change_manifest(
        "<change_manifest>" + "x" * 4097 + "</change_manifest>"
    ) is None
```

- [x] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_change_assessment.py -q
```

Expected: FAIL because manifest interfaces do not exist.

- [x] **Step 3: Implement bounded manifest parsing**

Add to `change_assessment.py`:

```python
import json
from typing import Any

MAX_MANIFEST_CHARS = 4096
_MANIFEST_RE = re.compile(
    r"(?s)<change_manifest>\s*(\{.*?\})\s*</change_manifest>"
)


@dataclass(frozen=True)
class ChangeManifest:
    files_expected: int
    subsystems: tuple[str, ...]
    public_contract: bool
    data_migration: bool
    security_sensitive: bool
    external_side_effects: bool
    destructive: bool
    unknowns: tuple[str, ...]
    validation_commands: int


def parse_change_manifest(text: str) -> ChangeManifest | None:
    match = _MANIFEST_RE.search(text or "")
    if match is None or len(match.group(1)) > MAX_MANIFEST_CHARS:
        return None
    try:
        raw: dict[str, Any] = json.loads(match.group(1))
        result = ChangeManifest(
            files_expected=int(raw["files_expected"]),
            subsystems=tuple(sorted(set(map(str, raw["subsystems"])))),
            public_contract=raw["public_contract"] is True,
            data_migration=raw["data_migration"] is True,
            security_sensitive=raw["security_sensitive"] is True,
            external_side_effects=raw["external_side_effects"] is True,
            destructive=raw["destructive"] is True,
            unknowns=tuple(sorted(set(map(str, raw["unknowns"])))),
            validation_commands=int(raw["validation_commands"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if result.files_expected < 0 or result.validation_commands < 0:
        return None
    return result
```

Implement `assess_manifest()` with this precedence:

1. Any `unknowns` → `ask_scope`.
2. Any critical boolean, more than five files, or more than one subsystem → `plan_design_workflow`.
3. At most one file and one subsystem → `single_coder`.
4. Otherwise → `planner_then_coder_check`.

Update the planner Output Contract to require exactly one compact JSON object between `<change_manifest>` tags with all nine fields. Missing evidence goes into `unknowns`; it must never be guessed.

- [x] **Step 4: Store and enforce planner assessment**

Add to `GoalState`:

```python
assessment: ChangeAssessment | None = None
next_role: str | None = None
pending_clarification: str | None = None

def apply_planner_manifest(self, text: str) -> ChangeAssessment:
    manifest = parse_change_manifest(text)
    assessment = (
        assess_manifest(manifest)
        if manifest is not None
        else ChangeAssessment(
            Band.HIGH, Band.UNKNOWN, Band.UNKNOWN, "ask_scope",
            ("missing-or-invalid-change-manifest",),
            "The planner could not produce a valid change manifest. Which files or deliverables should this change include?",
        )
    )
    self.assessment = assessment
    self.route = assessment.route
    self.scope = {
        "single_coder": "simple_artifact",
        "planner_then_coder_check": "medium_change",
        "plan_design_workflow": "large_feature",
        "ask_scope": "unknown",
    }[assessment.route]
    self.next_role = (
        "coder"
        if assessment.route in {"single_coder", "planner_then_coder_check"}
        else None
    )
    self.pending_clarification = assessment.clarifying_question
    return assessment
```

Initialize medium goals with `next_role="planner"`. After a done planner outcome, call `apply_planner_manifest(summary)`. Include the assessment and next role in `render_decision_block()`.

In `run.py`:

- refuse root coder spawn when `next_role != "coder"`;
- return `pending_clarification` before another model call;
- return a deterministic Pipeline-mode recommendation when the manifest route is large;
- never auto-launch the pipeline.

- [x] **Step 5: Add goal-state and role-order tests**

```python
def test_medium_goal_requires_planner_before_coder() -> None:
    state = GoalState.create("add route", "medium_change", "planner_then_coder_check")
    assert state.next_role == "planner"


def test_eleven_file_manifest_reclassifies_goal_as_large() -> None:
    state = GoalState.create("create site", "medium_change", "planner_then_coder_check")
    state.apply_planner_manifest(ELEVEN_FILE_MANIFEST)
    assert state.scope == "large_feature"
    assert state.route == "plan_design_workflow"
    assert state.next_role is None
```

Add an agent-loop test where root attempts coder before planner; assert refusal, zero coder audit rows, and a tool result naming `planner` as the legal next role.

- [x] **Step 6: Verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_change_assessment.py musubi/tests/test_goal_state.py musubi/tests/test_agent_scope.py musubi/tests/test_agent_loop.py -q
```

Expected: PASS.

- [x] **Step 7: Commit Task 2**

```powershell
git add musubi/agent/change_assessment.py .github/agents/workers/planner.agent.md musubi/agent/goal_state.py musubi/agent/run.py musubi/tests/test_change_assessment.py musubi/tests/test_goal_state.py musubi/tests/test_agent_loop.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(agent): govern routing with change manifests"
```

---

### Task 3: Role-Owned Direct Worker Turn Budgets

**Files:**
- Modify: `musubi/agent/subagent.py`
- Modify: `musubi/tests/test_subagent_orchestrator.py`
- Modify: `docs/roadmap.md`

**Interfaces:**
- Consumes `_frontmatter_max_turns(agent_md)`.
- Produces one effective turn cap sourced from role frontmatter and propagated unchanged through spawn, `run_unit`, and completion audit.
- Roles without `maxTurns:` retain existing server defaults.

- [x] **Step 1: Write failing role-ownership tests**

Replace `test_model_spawn_request_may_ask_for_fewer_turns` with:

```python
def test_model_spawn_request_cannot_reduce_frontmatter_maxturns(
    monkeypatch, tmp_path: Path,
) -> None:
    spawn, run_kwargs = _run_direct_spawn(
        monkeypatch, tmp_path,
        agent_md=_CODER_MD_8,
        spawn_args={"max_turns": 2},
    )
    assert spawn["max_turns"] == 8
    assert run_kwargs["max_cycles"] == 8


def test_replacement_receives_full_role_turn_budget(
    monkeypatch, tmp_path: Path,
) -> None:
    spawn, run_kwargs = _run_direct_spawn(
        monkeypatch, tmp_path,
        agent_md=_CODER_MD_8,
        spawn_args={"max_turns": 1, "brief": "[worker-replacement] continue"},
    )
    assert spawn["max_turns"] == 8
    assert run_kwargs["max_cycles"] == 8
```

- [x] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_subagent_orchestrator.py -k "maxturns or full_role_turn_budget" -q
```

Expected: FAIL because `min(requested, declared_turns)` resolves 2 and 1.

- [x] **Step 3: Make role frontmatter authoritative**

Replace the cap block in `subagent.py`:

```python
declared_turns = _frontmatter_max_turns(agent_md)
if declared_turns is not None:
    requested_turns = spawn_args.get("max_turns")
    if requested_turns is not None and requested_turns != declared_turns:
        print(
            f"[agent] ignored model max_turns={requested_turns}; "
            f"role {role_hint} owns max_turns={declared_turns}",
            file=log,
        )
    spawn_args = {**spawn_args, "max_turns": declared_turns}
```

Do not modify pipeline `PipelineWorkerSpec`.

- [x] **Step 4: Correct roadmap history**

In the bounded-runtime completed track, replace “the model may request fewer turns, never more” with “direct-worker role frontmatter is authoritative; model-supplied turn counts are ignored.”

- [x] **Step 5: Verify direct and pipeline caps**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_subagent_orchestrator.py musubi/tests/test_sub_sessions.py musubi/tests/test_pipeline_yaml.py musubi/tests/test_spawn_pipeline.py -q
```

Expected: PASS.

- [x] **Step 6: Commit Task 3**

```powershell
git add musubi/agent/subagent.py musubi/tests/test_subagent_orchestrator.py docs/roadmap.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): make roles own worker turn caps"
```

---

### Task 4: Typed Recovery and One Audited Continuation

**Files:**
- Modify: `musubi/agent/run.py`
- Modify: `musubi/agent/subagent.py`
- Modify: `musubi/agent/goal_state.py`
- Modify: `musubi/tests/test_agent_loop.py`
- Modify: `musubi/tests/test_goal_state.py`
- Modify: `musubi/tests/test_subagent_audit.py`

**Interfaces:**
- Produces `FailureKind`, `RecoveryAction`, and `decide_recovery(outcome: WorkerOutcome, *, same_role_failures: int, worker_slots: int) -> RecoveryAction`.
- Extends `WorkerOutcome` with `brief` and `failure_kind`.
- Produces at most one automatic same-role replacement through normal `_dispatch`.
- Preserves the current two-cycle root analysis window for unknown failures.

- [x] **Step 1: Write failing pure decision tests**

Append to `musubi/tests/test_goal_state.py`:

```python
from agent.run import FailureKind, RecoveryAction, WorkerOutcome, decide_recovery


def _turn_cap_outcome(*, files: tuple[str, ...]) -> WorkerOutcome:
    return WorkerOutcome(
        role="coder", status="escalated", summary="unfinished scaffold",
        touched_files=files, brief="create the scaffold",
        failure_kind=FailureKind.TURN_CAP,
    )


def test_first_turn_cap_with_files_auto_replaces() -> None:
    assert decide_recovery(
        _turn_cap_outcome(files=("app/page.tsx",)),
        same_role_failures=1, worker_slots=1,
    ) is RecoveryAction.AUTO_REPLACE


def test_repeated_turn_cap_halts_instead_of_looping() -> None:
    assert decide_recovery(
        _turn_cap_outcome(files=("app/page.tsx",)),
        same_role_failures=2, worker_slots=1,
    ) is RecoveryAction.HALT


def test_turn_cap_without_files_needs_root_analysis() -> None:
    assert decide_recovery(
        _turn_cap_outcome(files=()),
        same_role_failures=1, worker_slots=1,
    ) is RecoveryAction.ROOT_ANALYZE


def test_exhausted_worker_slots_halt() -> None:
    assert decide_recovery(
        _turn_cap_outcome(files=("app/page.tsx",)),
        same_role_failures=1, worker_slots=0,
    ) is RecoveryAction.HALT
```

- [x] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_goal_state.py -k "turn_cap or worker_slots" -q
```

Expected: FAIL because typed recovery interfaces do not exist.

- [x] **Step 3: Add typed failure evidence**

In `run.py`:

```python
class FailureKind(StrEnum):
    TURN_CAP = "turn_cap"
    BLOCKED = "blocked"
    BUDGET = "budget"
    POLICY = "policy"
    UNKNOWN = "unknown"


class RecoveryAction(StrEnum):
    AUTO_REPLACE = "auto_replace"
    ROOT_ANALYZE = "root_analyze"
    HALT = "halt"
```

Extend `WorkerOutcome`:

```python
brief: str = ""
failure_kind: FailureKind | None = None
```

Implement:

```python
def decide_recovery(
    outcome: WorkerOutcome,
    *,
    same_role_failures: int,
    worker_slots: int,
) -> RecoveryAction:
    if worker_slots <= 0 or same_role_failures >= 2:
        return RecoveryAction.HALT
    if outcome.failure_kind is FailureKind.TURN_CAP and outcome.touched_files:
        return RecoveryAction.AUTO_REPLACE
    if outcome.failure_kind in {FailureKind.BUDGET, FailureKind.POLICY}:
        return RecoveryAction.HALT
    return RecoveryAction.ROOT_ANALYZE
```

In `subagent.py`, derive failure kind from control flow, not summary text:

- `turns >= max_turns` without verified done artifacts → `TURN_CAP`;
- typed `[blocked]` → `BLOCKED`;
- token-budget exception → `BUDGET`;
- all other escalations → `UNKNOWN`.

Record the firewalled brief and typed kind with the outcome.

- [x] **Step 4: Write failing automatic-continuation integration test**

Add `test_turn_cap_failure_auto_spawns_one_audited_replacement` to `test_agent_loop.py`. Its canned sequence must prove:

1. root spawns primary coder;
2. coder reaches its role cap after touching `app/page.tsx`;
3. driver starts replacement before another root LM call;
4. replacement gets `[worker-replacement]`, the path, and prior summary;
5. replacement completes done;
6. root concludes success;
7. only two coder handles exist.

Expected initial failure: current code asks the root model again and may emit the old `[incomplete] root ended recovery...` marker.

- [x] **Step 5: Dispatch automatic continuation through `_dispatch`**

Before `cycles_used` is incremented and before a root LM call, evaluate the
newest unrecovered failure in a small deterministic loop. For `AUTO_REPLACE`,
synthesize:

```python
auto_tool_use = {
    "type": "tool_use",
    "id": f"auto-recovery-{len(orchestration.worker_outcomes)}",
    "name": "musubi_spawn_subagent",
    "input": {
        "role": recovery_outcome.role,
        "brief": recovery_outcome.brief,
    },
}
```

Pass it through `_dispatch(...)`, never directly to `run_subagent`. This preserves the root worker ceiling, `_replacement_brief`, policy checks, tool audit, subagent audit, and touched-file tracking. Log:

```text
[agent] automatic recovery: coder turn_cap -> audited replacement
```

Do not append a synthetic assistant message: the model did not make this tool
call. After `_dispatch` returns, rebuild the compact root goal-state message
from the newly recorded outcome. If the replacement is done, proceed with the
same root LM cycle so it can conclude; if the replacement fails, re-run
`decide_recovery` and halt before an LM call. The deterministic transition must
not increment `cycles_used` or create an `agent_cycles` row because no LM call
occurred.

Never auto-replace `BLOCKED`, `BUDGET`, `POLICY`, missing touched files, or the second same-role failure.

- [x] **Step 6: Add audit assertions**

Extend `test_subagent_audit.py`:

```python
assert [row["event"] for row in coder_rows] == [
    "spawned", "completed", "spawned", "completed",
]
assert coder_rows[1]["final_status"] == "escalated"
assert coder_rows[3]["final_status"] == "done"
assert coder_rows[0]["handle_id"] != coder_rows[2]["handle_id"]
assert "[worker-replacement]" in coder_rows[2]["brief"]
```

Also assert no third coder spawn after a second escalation.

- [x] **Step 7: Verify recovery and economics**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_goal_state.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_subagent_audit.py musubi/tests/test_agent_budget.py -q
```

Expected: PASS; recoverable continuation is deterministic/audited and budget/policy failures remain fail-closed.

- [x] **Step 8: Commit Task 4**

```powershell
git add musubi/agent/run.py musubi/agent/subagent.py musubi/agent/goal_state.py musubi/tests/test_agent_loop.py musubi/tests/test_goal_state.py musubi/tests/test_subagent_audit.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): make worker recovery deterministic"
```

---

### Task 5: Incident Regression, Roadmap Completion, and Full Verification

**Files:**
- Modify: `musubi/tests/test_agent_loop.py`
- Modify: `musubi/tests/test_subagent_orchestrator.py`
- Modify: `docs/roadmap.md`
- Modify: `docs/superpowers/plans/2026-07-22-governed-scope-budget-recovery.md`

**Interfaces:**
- Consumes all Task 1–4 interfaces.
- Produces end-to-end regressions for the attached website failure.

- [x] **Step 1: Add incident regressions**

Append to `musubi/tests/test_subagent_orchestrator.py`:

```python
def test_bare_new_website_request_stops_at_clarification() -> None:
    from agent import run as run_mod
    from agent.scope import classify_task

    hint = classify_task("create a new website")
    answer = run_mod._deterministic_scope_answer("create a new website", hint)

    assert hint.route == "ask_scope"
    assert answer == (
        "What should the website do, and should it be a static page or use "
        "a specific framework?"
    )


def test_bounded_scaffold_cannot_be_starved_or_abandon_recovery(
    monkeypatch, tmp_path: Path,
) -> None:
    from agent.run import FailureKind, RecoveryAction, WorkerOutcome, decide_recovery

    spawn, run_kwargs = _run_direct_spawn(
        monkeypatch, tmp_path,
        agent_md=_CODER_MD_8,
        spawn_args={"max_turns": 6},
    )
    outcome = WorkerOutcome(
        role="coder",
        status="escalated",
        summary="Next.js scaffold unfinished",
        touched_files=("app/page.tsx", "app/layout.tsx"),
        brief="create the bounded scaffold",
        failure_kind=FailureKind.TURN_CAP,
    )

    assert spawn["max_turns"] == 8
    assert run_kwargs["max_cycles"] == 8
    assert decide_recovery(
        outcome, same_role_failures=1, worker_slots=1,
    ) is RecoveryAction.AUTO_REPLACE
```

The Task 4 loop integration test must also assert:

```python
assert "root ended recovery without a successful replacement worker" not in answer
```

- [x] **Step 2: Run all affected suites**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_scope.py musubi/tests/test_change_assessment.py musubi/tests/test_goal_state.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_subagent_audit.py -q
```

Expected: PASS.

- [x] **Step 3: Move the roadmap entry only after verification**

Move “Governed change assessment and recovery liveness” from Active to Completed Tracks only after every check passes. Summarize the ambiguity gate, planner manifest, role-owned cap, one audited continuation, and unchanged worker/token/policy ceilings.

- [x] **Step 4: Run full repository verification**

```powershell
.\.venv\Scripts\python.exe scripts/check_musubi_tier.py
Push-Location musubi
..\.venv\Scripts\python.exe -m pytest
Pop-Location
npm run build
git diff --check
```

Expected: tier check `0`, all Python tests pass, GUI build `0`, and `git diff --check` prints nothing.

- [x] **Step 5: Review audit economics**

Confirm separate primary/replacement worker ids, primary escalated, replacement done, no hidden third worker, and no root LM charge for the deterministic recovery transition.

- [x] **Step 6: Commit Task 5**

```powershell
git add musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py docs/roadmap.md docs/superpowers/plans/2026-07-22-governed-scope-budget-recovery.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "test(agent): lock governed recovery workflow"
```

---

## Plan Self-Review Result

- **Coverage:** ambiguity, impact/risk reassessment, deterministic clarification, role-owned turns, recovery liveness, audit visibility, and roadmap state each map to a task.
- **Boundary:** all new decisions are deterministic driver logic; substrate tools remain zero-LLM and retain policy authority.
- **Types:** `Band`, `ChangeAssessment`, `ChangeManifest`, `FailureKind`, `RecoveryAction`, and `GoalState` field names are consistent across tasks.
- **Scope:** no automatic pipeline launch and no new worker/token ceiling are introduced.
