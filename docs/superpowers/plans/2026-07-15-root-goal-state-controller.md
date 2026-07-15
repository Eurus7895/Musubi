# Root Goal-State Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep exact user intent and bounded worker feedback in a compact root-owned state so root decisions use phase-specific tools and delta-only context instead of replaying the full orchestration transcript.

**Architecture:** A new pure `agent.goal_state` module owns `GoalState`, `OutcomePacket`, prompt projection, phase tool filtering, and root-only token accounting. The existing `Orchestration` records both its recovery-grade `WorkerOutcome` and the compact packet; `_run_loop` narrows root tools and replaces accumulated root messages with a goal-state decision block only after a terminal direct worker outcome.

**Tech Stack:** Python 3.11+, dataclasses, regex, existing MCP tool dictionaries, pytest.

## Global Constraints

- The substrate and all `musubi_*` tools make zero LLM calls.
- Exact user intent remains owned by root; scope is advisory only.
- Add no database table, no new model call, and no pipeline policy change.
- Preserve evaluator firewall, fail-closed tool policy, append-only audit, bounded recovery, and the three-direct-worker ceiling.
- Root remains read-only and workers remain independently audited.
- A simple successful run targets at most 3,000 root tokens; 20,000 total task tokens is a regression guard, not a normal budget.
- Full verified worker summaries remain in audit; root-facing free text is deterministically bounded.

---

### Task 1: Add pure goal state and worker feedback projection

**Files:**
- Create: `musubi/agent/goal_state.py`
- Create: `musubi/tests/test_goal_state.py`

**Interfaces:**
- Produces `OutcomePacket.from_worker(...) -> OutcomePacket`.
- Produces `GoalState.create(intent, scope, route) -> GoalState`.
- Produces `GoalState.record_outcome(...)`, `record_root_usage(...)`, and `render_decision_block()`.
- Produces `root_decision_tools(tools, state, recovery_outcome=False, decision_only=False)`.

- [ ] **Step 1: Write failing packet parsing and bounding tests**

```python
from agent.goal_state import GoalState, OutcomePacket


def test_outcome_packet_projects_worker_contract() -> None:
    packet = OutcomePacket.from_worker(
        role="coder",
        status="done",
        summary=(
            "status: done\nsummary: created dashboard\n"
            "verification: valid HTML\nremaining_gap: none"
        ),
        touched_files={"artifacts/nyc.html"},
    )
    assert packet.status == "done"
    assert packet.summary == "created dashboard"
    assert packet.verification == "valid HTML"
    assert packet.remaining_gap is None
    assert packet.touched_files == ("artifacts/nyc.html",)


def test_outcome_packet_bounds_unstructured_fallback() -> None:
    packet = OutcomePacket.from_worker(
        role="planner", status="done", summary="x" * 5000,
        touched_files=(),
    )
    assert len(packet.summary) <= 803
    assert packet.summary.endswith("… [truncated]")
```

- [ ] **Step 2: Run packet tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_goal_state.py -q
```

Expected: collection fails because `agent.goal_state` does not exist.

- [ ] **Step 3: Implement immutable packet parsing**

Create `musubi/agent/goal_state.py` with:

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

SIMPLE_ROOT_TOKEN_TARGET = 3_000
DEFAULT_ROOT_TOKEN_TARGET = 8_000
MAX_SUMMARY_CHARS = 800
MAX_DETAIL_CHARS = 400

_FIELD_RE = re.compile(
    r"(?im)^\s*(status|summary|verification|remaining_gap)\s*:\s*(.*?)\s*$"
)


def _bounded(value: str, limit: int) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "… [truncated]"


def _fields(text: str) -> dict[str, str]:
    return {match.group(1).lower(): match.group(2) for match in _FIELD_RE.finditer(text)}


@dataclass(frozen=True)
class OutcomePacket:
    role: str
    status: str
    summary: str
    touched_files: tuple[str, ...] = ()
    verification: str | None = None
    remaining_gap: str | None = None

    @classmethod
    def from_worker(
        cls, *, role: str, status: str, summary: str,
        touched_files: Iterable[str],
    ) -> "OutcomePacket":
        parsed = _fields(summary)
        verification = _bounded(parsed.get("verification", ""), MAX_DETAIL_CHARS) or None
        gap = _bounded(parsed.get("remaining_gap", ""), MAX_DETAIL_CHARS) or None
        if gap is not None and gap.lower() in {"none", "n/a", "no", "nothing"}:
            gap = None
        return cls(
            role=role,
            status=status,
            summary=_bounded(parsed.get("summary", summary), MAX_SUMMARY_CHARS),
            touched_files=tuple(sorted(set(touched_files))),
            verification=verification,
            remaining_gap=gap,
        )
```

- [ ] **Step 4: Write failing GoalState and phase-tool tests**

```python
def test_goal_state_keeps_exact_intent_and_root_only_usage() -> None:
    state = GoalState.create("create NYC dashboard", "simple_artifact", "single_coder")
    state.record_root_usage(tokens_in=1200, tokens_out=100)
    state.record_outcome(
        role="coder", status="done", summary="summary: ready",
        touched_files={"nyc.html"},
    )
    block = state.render_decision_block()
    assert "intent=create NYC dashboard" in block
    assert "root_usage=calls:1,input:1200,output:100,target:3000" in block
    assert "coder (done)" in block


def test_simple_root_surface_is_spawn_only() -> None:
    tools = [{"name": name} for name in (
        "musubi_spawn_subagent", "musubi_read_file", "musubi_get_skill",
    )]
    state = GoalState.create("create dashboard", "simple_artifact", "single_coder")
    assert [tool["name"] for tool in root_decision_tools(tools, state)] == [
        "musubi_spawn_subagent"
    ]
```

- [ ] **Step 5: Implement GoalState and phase filtering**

Append:

```python
_SPAWN = "musubi_spawn_subagent"
_SKILL_TOOLS = frozenset({
    "musubi_recommend_skills", "musubi_get_skill", "musubi_get_reference",
})


@dataclass
class GoalState:
    intent: str
    scope: str
    route: str
    root_token_target: int
    root_calls: int = 0
    root_tokens_in: int = 0
    root_tokens_out: int = 0
    outcomes: list[OutcomePacket] = field(default_factory=list)

    @classmethod
    def create(cls, intent: str, scope: str, route: str) -> "GoalState":
        target = SIMPLE_ROOT_TOKEN_TARGET if scope in {
            "simple_edit", "simple_artifact",
        } else DEFAULT_ROOT_TOKEN_TARGET
        return cls(intent=intent, scope=scope, route=route, root_token_target=target)

    def record_root_usage(self, *, tokens_in: int, tokens_out: int) -> None:
        self.root_calls += 1
        self.root_tokens_in += max(0, int(tokens_in))
        self.root_tokens_out += max(0, int(tokens_out))

    def record_outcome(self, **kwargs: Any) -> OutcomePacket:
        packet = OutcomePacket.from_worker(**kwargs)
        self.outcomes.append(packet)
        return packet

    def render_decision_block(self) -> str:
        latest = self.outcomes[-1] if self.outcomes else None
        worker = "none"
        if latest is not None:
            files = ", ".join(latest.touched_files) or "none"
            worker = (
                f"{latest.role} ({latest.status}); files={files}; "
                f"summary={latest.summary}; verification={latest.verification or 'none'}; "
                f"remaining_gap={latest.remaining_gap or 'none'}"
            )
        return (
            "[root-goal-state]\n"
            f"intent={self.intent}\n"
            f"scope={self.scope}\nroute={self.route}\n"
            f"root_usage=calls:{self.root_calls},input:{self.root_tokens_in},"
            f"output:{self.root_tokens_out},target:{self.root_token_target}\n"
            f"latest_worker={worker}\n"
            "decision=Compare the latest evidence with the original intent. "
            "Stop if the goal is satisfied; otherwise summon only the cheapest "
            "worker needed for the remaining gap.\n[/root-goal-state]"
        )


def root_decision_tools(
    tools: list[dict[str, Any]], state: GoalState, *,
    recovery_outcome: bool = False, decision_only: bool = False,
) -> list[dict[str, Any]]:
    if recovery_outcome and not decision_only:
        return list(tools)
    allowed = {_SPAWN}
    if state.scope not in {"simple_edit", "simple_artifact"}:
        allowed.update(_SKILL_TOOLS)
    return [tool for tool in tools if tool.get("name") in allowed]
```

- [ ] **Step 6: Run pure tests to verify GREEN**

Run the Step 2 command. Expected: all tests pass.

- [ ] **Step 7: Commit Task 1**

```powershell
git add musubi/agent/goal_state.py musubi/tests/test_goal_state.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(agent): add compact root goal state"
```

---

### Task 2: Wire GoalState into orchestration and root economics

**Files:**
- Modify: `musubi/agent/run.py:52-234`
- Modify: `musubi/agent/run.py:307-532`
- Test: `musubi/tests/test_agent_loop.py`
- Test: `musubi/tests/test_subagent_orchestrator.py`

**Interfaces:**
- Consumes `GoalState` from Task 1.
- Adds `Orchestration.goal_state: GoalState | None`.
- Makes `record_worker_outcome` update both recovery state and root-facing feedback.

- [ ] **Step 1: Write failing orchestration reducer test**

```python
def test_root_orchestration_reduces_worker_outcome_into_goal_state() -> None:
    state = GoalState.create("create dashboard", "simple_artifact", "single_coder")
    orch = Orchestration(parent_session_id="root", goal_state=state)
    orch.record_worker_outcome(
        role="coder", status="done", summary="summary: complete",
        touched_files={"dashboard.html"},
    )
    assert len(orch.worker_outcomes) == 1
    assert len(state.outcomes) == 1
    assert state.outcomes[0].summary == "complete"
    assert orch.child("reviewer").goal_state is None
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py -k goal_state -q
```

Expected: FAIL because `Orchestration` has no `goal_state` field.

- [ ] **Step 3: Add GoalState to root orchestration**

Import `GoalState`; add `goal_state: GoalState | None = None` beside
`worker_outcomes`. After appending `WorkerOutcome`, call:

```python
if self.goal_state is not None:
    self.goal_state.record_outcome(
        role=role, status=status, summary=summary, touched_files=touched_files,
    )
```

Leave `child()` and `stage_child()` constructors without `goal_state`, so nested
workers never own or receive the root intent.

- [ ] **Step 4: Construct GoalState from exact task text**

In `run_agent`, after `scope_hint = classify_task(task)`, create:

```python
goal_state = GoalState.create(
    intent=task,
    scope=scope_hint.kind.value,
    route=scope_hint.route,
)
```

Pass it only to the root constructor:

```python
orchestration = Orchestration(
    parent_session_id=parent_session_id,
    goal_state=goal_state,
)
```

- [ ] **Step 5: Write and implement root-only usage test**

Add a fake root response with provider usage, call `_run_loop` with a root
GoalState, and assert `root_calls == 1`. Keep the existing worker stats tests to
prove worker usage still contributes to `AgentRunStats` but not GoalState.
In `_run_loop`, immediately after `usage = _cycle_token_usage(...)`, add:

```python
if (
    role == "agent" and orchestration is not None
    and orchestration.depth == 0 and orchestration.goal_state is not None
):
    orchestration.goal_state.record_root_usage(
        tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
    )
```

- [ ] **Step 6: Run orchestration tests to verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit Task 2**

```powershell
git add musubi/agent/run.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(agent): retain root intent across worker outcomes"
```

---

### Task 3: Narrow root tools and compact post-worker context

**Files:**
- Modify: `musubi/agent/run.py:621-925`
- Test: `musubi/tests/test_agent_loop.py`

**Interfaces:**
- Consumes `root_decision_tools` and `GoalState.render_decision_block`.
- Produces `_compact_root_goal_messages(messages, state) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write failing root tool-surface tests**

```python
def test_simple_root_cycle_sees_spawn_only_goal_surface() -> None:
    state = GoalState.create("create dashboard", "simple_artifact", "single_coder")
    orch = Orchestration(parent_session_id="root", goal_state=state)
    router = FakeRouter([_text("done")])
    asyncio.run(_run_loop(
        object(), router, [READ_TOOL, SPAWN_TOOL, GET_SKILL_TOOL],
        [{"role": "user", "content": "create dashboard"}],
        max_cycles=1, log=io.StringIO(), orchestration=orch,
        role="agent",
    ))
    assert [tool["name"] for tool in router.calls[0]["tools"]] == [
        "musubi_spawn_subagent"
    ]


def test_recovery_analysis_keeps_existing_root_tools() -> None:
    # Seed one escalated worker and assert the first recovery analysis call still
    # sees READ_TOOL plus SPAWN_TOOL. Existing decision-only tests continue to
    # prove that the third decision sees spawn only.
```

- [ ] **Step 2: Run tool-surface tests to verify RED**

Run the two named tests. Expected: simple root still sees all supplied tools.

- [ ] **Step 3: Apply phase-specific tools in `_run_loop`**

Replace the current initialization with:

```python
cycle_tools = tools
root_state = (
    orchestration.goal_state
    if role == "agent" and orchestration is not None
    and orchestration.depth == 0
    else None
)
if root_state is not None:
    cycle_tools = root_decision_tools(
        tools,
        root_state,
        recovery_outcome=recovery_outcome is not None,
        decision_only=recovery_decision_only,
    )
elif recovery_decision_only:
    cycle_tools = [
        tool for tool in tools if tool.get("name") == "musubi_spawn_subagent"
    ]
```

Use `cycle_tools`, not `tools`, in `fit_model_input`; otherwise hidden tool
schemas still consume the hard context budget.

- [ ] **Step 4: Write failing delta-context regression**

Use a fake dispatch result whose worker summary contains a unique 5,000-character
marker. Seed GoalState through `record_worker_outcome` during dispatch. Assert
the second root model call:

```python
replay = str(router.calls[1]["messages"])
assert "exact user intent" in replay
assert "[root-goal-state]" in replay
assert "… [truncated]" in replay
assert large_raw_marker not in replay
```

- [ ] **Step 5: Implement message compaction after terminal spawn**

Add:

```python
def _compact_root_goal_messages(
    messages: list[dict[str, Any]], state: GoalState,
) -> list[dict[str, Any]]:
    stable = [message for message in messages if message.get("role") == "system"]
    return [
        *stable,
        {"role": "user", "content": state.render_decision_block()},
    ]
```

Before dispatch, capture `outcomes_before = len(root_state.outcomes)` when state
exists. After dispatch and recovery bookkeeping:

```python
if root_state is not None and len(root_state.outcomes) > outcomes_before:
    messages = _compact_root_goal_messages(messages, root_state)
    block_chars = len(str(messages[-1]["content"]))
    print(
        f"[agent] root goal-state compacted outcomes={len(root_state.outcomes)} "
        f"chars={block_chars} tools={len(cycle_tools)}",
        file=log,
    )
else:
    messages.append({"role": "user", "content": tool_results})
```

Do not append raw `tool_results` on the compacted branch.

- [ ] **Step 6: Add the synthetic token target regression**

Construct the real stable system prompt, a short simple task, GoalState, and one
spawn schema. Assert `_estimate_input_tokens(messages, tools) < 3_000` for the
initial decision and that the sum of initial plus compact-feedback estimates is
below the target fixture documented by the test. The test uses estimates only;
provider usage remains authoritative at runtime.

- [ ] **Step 7: Run focused tests to verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_goal_state.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_agent_scope.py -q
```

Expected: all tests pass, including existing recovery regressions.

- [ ] **Step 8: Commit Task 3**

```powershell
git add musubi/agent/run.py musubi/tests/test_agent_loop.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "perf(agent): compact root decision context"
```

---

### Task 4: Align root and worker contracts, roadmap, and verification

**Files:**
- Modify: `musubi/agent/context.py:20-70`
- Modify: `.github/agents/workers/coder.agent.md:68-78`
- Modify: `.github/agents/workers/planner.agent.md:29-37`
- Modify: `.github/agents/workers/reviewer.agent.md:29-35`
- Modify: `musubi/tests/test_agent_context.py`
- Modify: `docs/roadmap.md`

**Interfaces:**
- Root prompt defines goal ownership and cheapest-gap decision order.
- Worker contracts emit optional `remaining_gap:` without breaking existing text.

- [ ] **Step 1: Write failing prompt-contract tests**

```python
def test_root_prompt_owns_goal_and_optimizes_next_gap() -> None:
    prompt = build_system_prompt().lower()
    assert "exact user intent" in prompt
    assert "cheapest worker" in prompt
    assert "stop when the goal is satisfied" in prompt


def test_direct_worker_contracts_offer_remaining_gap_feedback() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in ("coder", "planner", "reviewer"):
        text = (root / ".github" / "agents" / "workers" /
                f"{name}.agent.md").read_text(encoding="utf-8")
        assert "remaining_gap" in text
```

- [ ] **Step 2: Run prompt tests to verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_context.py -k "goal or remaining_gap" -q
```

Expected: tests fail because the compact controller contract is absent.

- [ ] **Step 3: Add concise optimizer guidance and feedback fields**

Add one compact paragraph to `_VERBOSITY_NOTE`: root owns exact intent, compares
the latest verified feedback with it, stops when satisfied, and otherwise
spawns only the cheapest worker needed for one blocking gap. Add
`remaining_gap: none | one blocking gap` to coder output and equivalent explicit
fields to planner/reviewer contracts. Do not add verbose examples to the stable
prompt.

- [ ] **Step 4: Update roadmap**

Under the active agent runtime track, record the in-memory GoalState,
OutcomePacket feedback, root-only economics, phase tool surface, and delta-only
post-worker context. Link the design and this implementation plan. State the
current-run-only persistence boundary and the 3,000-root-token simple target.

- [ ] **Step 5: Run focused and full Python verification**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_goal_state.py musubi/tests/test_agent_context.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_agent_scope.py -q
.\.venv\Scripts\python.exe -m pytest musubi/tests -q
git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 6: Commit Task 4**

```powershell
git add musubi/agent/context.py .github/agents/workers/coder.agent.md .github/agents/workers/planner.agent.md .github/agents/workers/reviewer.agent.md musubi/tests/test_agent_context.py docs/roadmap.md docs/superpowers/plans/2026-07-15-root-goal-state-controller.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "docs(agent): define goal-state feedback contract"
```

## Final verification

- [ ] Confirm `git status --short` contains no unintended files.
- [ ] Confirm `git diff origin/dev...HEAD --check` exits `0`.
- [ ] Confirm no database schema or server tool signature changed.
- [ ] Confirm all commits use `Eurus <t.hoang7895@gmail.com>`.
- [ ] Record focused and full-suite counts in the handoff.
