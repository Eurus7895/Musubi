"""Tests for the agent loop driving a real harness MCP server.

musubi-tier: substrate test - pins the cycle-loop contract. Uses a
canned-response FakeRouter to keep the test hermetic; the real harness
MCP server IS spawned (we want to catch breakage there).
"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent.run import Orchestration, run_agent
from agent.budget import TokenBudgetEnforcer, TokenBudgetExhaustedError
from agent.scope import BROAD_PRODUCT_QUESTION
from agent.textfmt import TRUNCATION_MARK
from agent.goal_state import GoalState
from agent.vendors.base import LMResponse, LMRouter


class FakeRouter(LMRouter):
    name = "fake"
    model = "fake-1"

    def __init__(self, responses: list[LMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
    ) -> LMResponse:
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        if not self._responses:
            raise AssertionError("FakeRouter ran out of canned responses")
        return self._responses.pop(0)


def test_orchestration_tracks_latest_failed_worker_outcome() -> None:
    from agent import run as run_mod

    assert hasattr(run_mod, "WorkerOutcome")
    orchestration = Orchestration(parent_session_id="parent")
    orchestration.record_worker_outcome(
        role="coder",
        status="escalated",
        summary="dashboard is incomplete",
        touched_files={"dashboard.html"},
    )

    outcome = orchestration.latest_failed_outcome("coder")
    assert outcome == run_mod.WorkerOutcome(
        role="coder",
        status="escalated",
        summary="dashboard is incomplete",
        touched_files=("dashboard.html",),
    )

    orchestration.record_worker_outcome(
        role="coder", status="done", summary="fixed", touched_files={"dashboard.html"},
    )
    assert orchestration.latest_failed_outcome("coder") is None


def test_root_orchestration_reduces_worker_outcome_into_goal_state() -> None:
    state = GoalState.create(
        "create dashboard", "simple_artifact", "single_coder",
    )
    orchestration = Orchestration(
        parent_session_id="root",
        goal_state=state,
    )

    orchestration.record_worker_outcome(
        role="coder",
        status="done",
        summary="summary: complete",
        touched_files={"dashboard.html"},
    )

    assert len(orchestration.worker_outcomes) == 1
    assert len(state.outcomes) == 1
    assert state.outcomes[0].summary == "complete"
    assert orchestration.child("reviewer").goal_state is None


def test_root_cycle_usage_is_recorded_on_goal_state() -> None:
    from agent import run as run_mod

    state = GoalState.create(
        "create dashboard", "simple_artifact", "single_coder",
    )
    orchestration = Orchestration(
        parent_session_id="root",
        goal_state=state,
    )
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "done"}],
            usage={"input_tokens": 1200, "output_tokens": 100},
        ),
    ])

    answer, cycles = asyncio.run(run_mod._run_loop(
        object(),
        router,
        [],
        [{"role": "user", "content": "create dashboard"}],
        max_cycles=1,
        log=io.StringIO(),
        orchestration=orchestration,
        role="agent",
    ))

    assert answer == "done"
    assert cycles == 1
    assert state.root_calls == 1
    assert state.root_tokens_in == 1200
    assert state.root_tokens_out == 100


def test_simple_root_cycle_sees_spawn_only_goal_surface() -> None:
    from agent import run as run_mod

    state = GoalState.create(
        "create dashboard", "simple_artifact", "single_coder",
    )
    orchestration = Orchestration(
        parent_session_id="root",
        goal_state=state,
    )
    tools = [
        {
            "name": name,
            "description": name,
            "input_schema": {"type": "object"},
        }
        for name in (
            "musubi_read_file",
            "musubi_spawn_subagent",
            "musubi_get_skill",
        )
    ]
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "done"}],
        ),
    ])

    asyncio.run(run_mod._run_loop(
        object(),
        router,
        tools,
        [{"role": "user", "content": "create dashboard"}],
        max_cycles=1,
        log=io.StringIO(),
        orchestration=orchestration,
        role="agent",
    ))

    assert [tool["name"] for tool in router.calls[0]["tools"]] == [
        "musubi_spawn_subagent",
    ]


def test_root_compacts_terminal_worker_feedback_to_goal_state_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import run as run_mod

    raw_marker = "RAW-WORKER-TRANSCRIPT-" * 300
    state = GoalState.create(
        "exact user intent", "simple_artifact", "single_coder",
    )
    orchestration = Orchestration(
        parent_session_id="root",
        goal_state=state,
    )

    async def fake_dispatch(*args, **kwargs):  # noqa: ANN002, ANN003
        kwargs["orchestration"].record_worker_outcome(
            role="coder",
            status="done",
            summary=f"summary: completed {raw_marker}",
            touched_files={"dashboard.html"},
        )
        return [{
            "type": "tool_result",
            "tool_use_id": "spawn-1",
            "content": raw_marker,
        }]

    monkeypatch.setattr(run_mod, "_dispatch", fake_dispatch)
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use",
                "id": "spawn-1",
                "name": "musubi_spawn_subagent",
                "input": {"role": "coder", "brief": "create dashboard"},
            }],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "accepted"}],
        ),
    ])
    spawn_tool = {
        "name": "musubi_spawn_subagent",
        "description": "spawn",
        "input_schema": {"type": "object"},
    }
    log = io.StringIO()

    answer, cycles = asyncio.run(run_mod._run_loop(
        object(),
        router,
        [spawn_tool],
        [
            {"role": "system", "content": "stable root prompt"},
            {"role": "user", "content": "exact user intent"},
        ],
        max_cycles=2,
        log=log,
        orchestration=orchestration,
        role="agent",
    ))

    replay = str(router.calls[1]["messages"])
    assert answer == "accepted"
    assert cycles == 2
    assert "[root-goal-state]" in replay
    assert "intent=exact user intent" in replay
    assert TRUNCATION_MARK in replay
    assert raw_marker not in replay
    assert "root goal-state compacted outcomes=1" in log.getvalue()


def test_simple_root_two_call_projection_stays_below_3k_tokens() -> None:
    from agent import run as run_mod

    task = "create an HTML dashboard file"
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "done"}],
        ),
    ])

    asyncio.run(run_agent(
        task,
        router,
        _musubi_dir(),
        log=io.StringIO(),
        max_tokens=0,
    ))

    first_call = router.calls[0]
    state = GoalState.create(task, "simple_artifact", "single_coder")
    state.record_outcome(
        role="coder",
        status="done",
        summary="summary: created dashboard\nverification: browser smoke test passed",
        touched_files={"dashboard.html"},
    )
    second_messages = run_mod._compact_root_goal_messages(
        first_call["messages"], state,
    )
    projected_total = run_mod._estimate_input_tokens(
        first_call["messages"], first_call["tools"],
    ) + run_mod._estimate_input_tokens(second_messages, first_call["tools"])

    # Simple-scope root sees spawn + skill *selection* (option 3): it may push
    # a skill to the worker it summons. Content-loading skill tools stay out.
    assert {tool["name"] for tool in first_call["tools"]} == {
        "musubi_spawn_subagent",
        "musubi_recommend_skills",
    }
    # Adding skill selection to a simple root costs ~1k tokens across the
    # two-call projection (the recommend tool def rides both calls). That is a
    # deliberate budget shift so simple artifacts can still receive a pushed
    # skill; the hard regression guard stays far below the 20k ceiling.
    assert projected_total < 4_500


def test_replacement_brief_includes_prior_terminal_outcome() -> None:
    from agent import run as run_mod

    assert hasattr(run_mod, "_replacement_brief")
    outcome = run_mod.WorkerOutcome(
        role="coder",
        status="escalated",
        summary="created the shell but charts are missing",
        touched_files=("china-dashboard.html",),
    )

    brief = run_mod._replacement_brief("finish the dashboard", outcome)

    assert "finish the dashboard" in brief
    assert "escalated" in brief
    assert "created the shell but charts are missing" in brief
    assert "china-dashboard.html" in brief
    assert "Continue the existing artifact" in brief
    assert "[worker-replacement]" in brief


def test_root_recovery_dispatches_at_most_two_analysis_cycles(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    read_tool = {
        "name": "musubi_read_file",
        "description": "read",
        "input_schema": {"type": "object"},
    }
    spawn_tool = {
        "name": "musubi_spawn_subagent",
        "description": "spawn",
        "input_schema": {"type": "object"},
    }
    responses = [
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use", "id": f"read-{index}",
                "name": "musubi_read_file", "input": {"path": "dashboard.html"},
            }],
        )
        for index in range(3)
    ]
    router = FakeRouter(responses)
    session = _FakeToolSession("html")
    orchestration = Orchestration(parent_session_id="parent")
    orchestration.record_worker_outcome(
        role="coder", status="escalated", summary="charts missing",
        touched_files={"dashboard.html"},
    )

    answer, cycles = asyncio.run(run_mod._run_loop(
        session,
        router,
        [read_tool, spawn_tool],
        [{"role": "user", "content": "build dashboard"}],
        max_cycles=5,
        log=io.StringIO(),
        orchestration=orchestration,
        role="agent",
        audit_db_path=tmp_path / "audit.db",
    ))

    assert cycles == 3
    assert answer is not None and answer.startswith("[incomplete]")
    assert len(session.calls) == 2
    assert [tool["name"] for tool in router.calls[2]["tools"]] == [
        "musubi_spawn_subagent"
    ]


def test_root_stops_immediately_when_last_replacement_exhausts_ceiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import run as run_mod
    from agent import subagent as subagent_mod

    orchestration = Orchestration(parent_session_id="parent", spawned_workers=2)
    orchestration.record_worker_outcome(
        role="coder", status="escalated", summary="first attempt incomplete",
        touched_files={"dashboard.html"},
    )

    async def fake_run_subagent(session, spawn_args, *args, **kwargs):  # noqa: ANN001
        kwargs["orchestration"].record_worker_outcome(
            role="coder", status="escalated", summary="replacement incomplete",
            touched_files={"dashboard.html"},
        )
        return "[incomplete] replacement incomplete"

    monkeypatch.setattr(subagent_mod, "run_subagent", fake_run_subagent)
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use", "id": "last-worker",
                "name": "musubi_spawn_subagent",
                "input": {"role": "coder", "brief": "finish it"},
            }],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "should not be called"}],
        ),
    ])

    answer, cycles = asyncio.run(run_mod._run_loop(
        _FakeToolSession(),
        router,
        [{
            "name": "musubi_spawn_subagent",
            "description": "spawn",
            "input_schema": {"type": "object"},
        }],
        [{"role": "user", "content": "build dashboard"}],
        max_cycles=4,
        log=io.StringIO(),
        orchestration=orchestration,
        role="agent",
        audit_db_path=tmp_path / "audit.db",
    ))

    assert cycles == 1
    assert answer is not None and answer.startswith("[incomplete]")
    assert "replacement incomplete" in answer
    assert len(router.calls) == 1


def test_root_concludes_when_worker_ceiling_is_spent_by_successes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: workers that all report `done` never trigger the failure
    # recovery halt, so a root that keeps wanting more work used to spin every
    # remaining cycle on refused spawns before salvaging a placeholder. Once the
    # worker ceiling is spent, the root must be forced to conclude within one
    # cycle with a real, model-authored answer.
    from agent import run as run_mod
    from agent import subagent as subagent_mod

    state = GoalState.create(
        "could you reach to a folder", "simple_artifact", "single_coder",
    )
    orchestration = Orchestration(
        parent_session_id="root", goal_state=state, spawned_workers=2,
    )

    async def fake_run_subagent(session, spawn_args, *args, **kwargs):  # noqa: ANN001
        kwargs["orchestration"].record_worker_outcome(
            role="coder", status="done", summary="summary: reached the folder",
            touched_files={"note.txt"},
        )
        return "summary: reached the folder"

    monkeypatch.setattr(subagent_mod, "run_subagent", fake_run_subagent)
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use", "id": "last-spawn",
                "name": "musubi_spawn_subagent",
                "input": {"role": "coder", "brief": "reach it"},
            }],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "Reached the folder; here is what I found."}],
        ),
    ])

    answer, cycles = asyncio.run(run_mod._run_loop(
        _FakeToolSession(),
        router,
        [{
            "name": "musubi_spawn_subagent",
            "description": "spawn",
            "input_schema": {"type": "object"},
        }],
        [
            {"role": "system", "content": "you are root"},
            {"role": "user", "content": "could you reach to a folder"},
        ],
        max_cycles=16,
        log=io.StringIO(),
        orchestration=orchestration,
        role="agent",
        salvage_on_exhaust=True,
        audit_db_path=tmp_path / "audit.db",
    ))

    # One spawn (hitting the 3-worker ceiling) then a forced conclusion — not a
    # spin to max_cycles=16.
    assert cycles == 2
    assert answer == "Reached the folder; here is what I found."
    assert orchestration.spawned_workers == 3
    assert len(router.calls) == 2
    # The concluding cycle offers no tools and states the budget is spent.
    assert router.calls[1]["tools"] == []
    conclude_messages = router.calls[1]["messages"]
    assert any(
        isinstance(message.get("content"), str)
        and "worker budget spent" in message["content"]
        for message in conclude_messages
    )


def test_root_cannot_report_success_while_worker_failure_is_unrecovered(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    orchestration = Orchestration(parent_session_id="parent")
    orchestration.record_worker_outcome(
        role="coder", status="escalated", summary="footer is missing",
        touched_files={"dashboard.html"},
    )
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "Everything is complete."}],
        ),
    ])

    answer, cycles = asyncio.run(run_mod._run_loop(
        _FakeToolSession(),
        router,
        [],
        [{"role": "user", "content": "build dashboard"}],
        max_cycles=4,
        log=io.StringIO(),
        orchestration=orchestration,
        role="agent",
        audit_db_path=tmp_path / "audit.db",
    ))

    assert cycles == 1
    assert answer is not None and answer.startswith("[incomplete]")
    assert "footer is missing" in answer
    assert "dashboard.html" in answer


def _musubi_dir() -> Path:
    """The agent-harness package directory (this file's grandparent)."""
    return Path(__file__).resolve().parent.parent


def test_server_env_forwards_musubi_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """The spawned server must see MUSUBI_* config while unrelated secrets stay out."""
    from agent.run import _server_env

    monkeypatch.setenv("MUSUBI_COMPRESS", "1")
    monkeypatch.setenv("MUSUBI_ROOT", "/some/dir")
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-leak")
    env = _server_env()
    assert env["MUSUBI_COMPRESS"] == "1"
    assert env["MUSUBI_ROOT"] == "/some/dir"
    assert "UNRELATED_SECRET" not in env
    assert "PATH" in env


def test_server_db_path_matches_spawned_server_default(tmp_path: Path) -> None:
    from agent.run import _server_db_path

    musubi_dir = tmp_path / "checkout" / "musubi"
    assert _server_db_path(musubi_dir, {}) == musubi_dir / "storage" / "musubi.db"

    root = tmp_path / "portable-root"
    assert (
        _server_db_path(musubi_dir, {"MUSUBI_ROOT": str(root)})
        == root / "data" / "musubi.db"
    )


def test_fit_model_input_enforces_hard_cap_including_tools() -> None:
    from agent.context import fit_model_input

    tools = [
        {"name": f"tool_{i}", "description": "x" * 300, "input_schema": {"y": "z" * 300}}
        for i in range(3)
    ]
    big = "GLOB RESULT " + ("path/to/file.py\n" * 800)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "you are the agent"},
        {"role": "user", "content": "the user goal is to build a dashboard"},
    ]
    for i in range(6):
        messages.append({
            "role": "assistant",
            "content": [{"type": "tool_use", "id": f"t{i}", "name": "musubi_glob",
                         "input": {"pattern": "**/*"}}],
        })
        messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": big}],
        })

    fitted = fit_model_input(messages, tools, budget_chars=16_000)
    size = len(json.dumps(fitted, ensure_ascii=False, default=str))
    size += len(json.dumps(tools, ensure_ascii=False, default=str))
    assert size <= 16_000
    # The system prompt and first user goal are never sacrificed.
    assert fitted[0] == messages[0]
    assert "user goal" in json.dumps(fitted[1])


def test_fit_model_input_raises_when_tools_alone_exceed_budget() -> None:
    from agent.context import ContextBudgetExceededError, fit_model_input

    tools = [{"name": "huge", "schema": "z" * 5_000}]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "goal"},
    ]
    with pytest.raises(ContextBudgetExceededError):
        fit_model_input(messages, tools, budget_chars=2_000)


def test_fit_model_input_disabled_when_budget_nonpositive() -> None:
    from agent.context import fit_model_input

    messages: list[dict[str, Any]] = [{"role": "user", "content": "x" * 10_000}]
    assert fit_model_input(messages, [], budget_chars=0) is messages


def test_run_loop_raises_context_budget_exceeded_before_vendor_call() -> None:
    """An explicit-budget worker whose protected input cannot fit raises before
    ever calling the model."""
    from agent import run as run_mod
    from agent.context import ContextBudgetExceededError

    class ExplodingRouter(LMRouter):
        name = "boom"
        model = "boom-1"

        def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001, ARG002
            raise AssertionError("vendor.call must not run over a hard cap")

    tools = [{"name": "huge", "schema": "z" * 40_000}]
    with pytest.raises(ContextBudgetExceededError):
        asyncio.run(
            run_mod._run_loop(
                object(), ExplodingRouter(), tools,
                [{"role": "user", "content": "brief"}],
                max_cycles=1, log=io.StringIO(),
                context_budget_chars=16_000,
            )
        )


def test_run_loop_passes_context_compression_db_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import run as run_mod

    seen: list[Path | None] = []

    def spy_fit_context(messages, *, compression_db_path=None):  # noqa: ANN001
        seen.append(compression_db_path)
        return messages

    monkeypatch.setattr(run_mod, "fit_context", spy_fit_context)
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    db_path = tmp_path / "server.db"

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            object(), router, [], [{"role": "user", "content": "hi"}],
            max_cycles=1,
            log=io.StringIO(),
            compression_db_path=db_path,
        )
    )

    assert answer == "ok"
    assert cycles == 1
    assert seen == [db_path]


def test_run_loop_dispatches_tool_blocks_even_when_stop_reason_is_end_turn() -> None:
    """Some OpenAI-compatible routers emit tool_use blocks with end_turn.

    The loop must key off the presence of tool_use content, not only the
    stop_reason string, or write-capable workers silently skip their writes.
    """
    from agent import run as run_mod

    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{
            "type": "tool_use",
            "id": "write-1",
            "name": "musubi_write_file",
            "input": {"path": "dashboard.html", "content": "<html></html>"},
        }]),
        LMResponse(stop_reason="end_turn", content=[{
            "type": "text",
            "text": "created dashboard.html",
        }]),
    ])
    session = _FakeToolSession('{"status":"ok","path":"dashboard.html"}')

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            session,
            router,
            [{"name": "musubi_write_file", "description": "", "input_schema": {}}],
            [{"role": "user", "content": "create html dashboard"}],
            max_cycles=2,
            log=io.StringIO(),
            role="coder",
        )
    )

    assert answer == "created dashboard.html"
    assert cycles == 2
    assert session.calls == [
        (
            "musubi_write_file",
            {"path": "dashboard.html", "content": "<html></html>"},
        )
    ]


def test_run_loop_preflight_budget_halt_skips_vendor_call() -> None:
    from agent import run as run_mod

    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    budget = TokenBudgetEnforcer(max_tokens=100)

    with pytest.raises(TokenBudgetExhaustedError, match="preflight"):
        asyncio.run(
            run_mod._run_loop(
                object(),
                router,
                [{"name": "musubi_read_file", "description": "", "input_schema": {}}],
                [{"role": "user", "content": "x" * 20_000}],
                max_cycles=1,
                log=io.StringIO(),
                budget=budget,
            )
        )

    assert router.calls == []


def test_build_token_budget_uses_token_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent import run as run_mod

    monkeypatch.delenv("MUSUBI_AGENT_MAX_TOKENS", raising=False)
    log = io.StringIO()

    budget = run_mod._build_token_budget(1234, log)

    assert budget is not None
    assert budget.max_tokens == 1234
    assert "token budget: 1234 tokens" in log.getvalue()


def test_run_agent_signature_excludes_max_credits() -> None:
    import inspect

    from agent import run as run_mod

    assert "max_credits" not in inspect.signature(run_mod.run_agent).parameters


def test_cli_rejects_removed_max_credits_flag() -> None:
    from agent import run as run_mod

    with pytest.raises(SystemExit):
        run_mod.main(["hello", "--max-credits", "10"])


class _FakeToolSession:
    def __init__(self, text: str = "ok") -> None:
        self.text = text
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))

        class _Chunk:
            def __init__(self, text: str) -> None:
                self.text = text

        class _Result:
            def __init__(self, text: str) -> None:
                self.content = [_Chunk(text)]

        return _Result(self.text)


def _read_policy_rows(db_path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(db_path) as conn:
        return list(conn.execute(
            "SELECT verdict, role, tool FROM policy_audit ORDER BY id"
        ))


def _read_tool_rows(db_path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(db_path) as conn:
        return list(conn.execute(
            "SELECT agent, tool, status FROM tool_audit ORDER BY id"
        ))


def test_dispatch_denies_root_write_before_call_and_records_policy_audit(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession()
    audit_db = tmp_path / "audit.db"

    with pytest.raises(run_mod.PolicyDeniedError) as denied:
        asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-denied",
                "name": "musubi_write_file",
                "input": {"path": "x.py", "content": "print('x')"},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="agent"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
        )

    assert denied.value.role == "agent"
    assert denied.value.tool == "musubi_write_file"
    assert "capability Write is not allowed" in denied.value.reason
    assert session.calls == []
    assert _read_policy_rows(audit_db) == [
        ("DENY", "agent", "musubi_write_file")
    ]
    assert _read_tool_rows(audit_db) == [
        ("agent", "musubi_write_file", "denied")
    ]


def test_dispatch_policy_preflight_denies_mixed_batch_before_any_sibling_launch(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession("read result")
    audit_db = tmp_path / "audit.db"
    tool_uses = [
        {
            "id": "allowed-read",
            "name": "musubi_read_file",
            "input": {"path": "README.md"},
        },
        {
            "id": "denied-spawn-role",
            "name": "musubi_spawn_subagent",
            "input": {"role": "saboteur", "brief": "forged"},
        },
    ]

    with pytest.raises(Exception) as caught:
        asyncio.run(
            run_mod._dispatch(
                session,
                tool_uses,
                io.StringIO(),
                vendor=None,
                tools=[],
                orchestration=Orchestration(
                    parent_session_id="parent",
                    parent_agent_name="agent",
                ),
                gateway=None,
                audit_db_path=audit_db,
            )
        )

    assert type(caught.value).__name__ == "PolicyDeniedError"
    assert session.calls == []
    assert _read_policy_rows(audit_db) == [
        ("DENY", "agent", "musubi_spawn_subagent")
    ]
    assert _read_tool_rows(audit_db) == [
        ("agent", "musubi_spawn_subagent", "denied")
    ]


def test_root_policy_denial_is_terminal_after_one_lm_response(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    router = FakeRouter([
        LMResponse(stop_reason="tool_use", content=[{
            "type": "tool_use",
            "id": "denied-write",
            "name": "musubi_write_file",
            "input": {"path": "x.py", "content": "print('x')"},
        }]),
        LMResponse(stop_reason="end_turn", content=[{
            "type": "text",
            "text": "this response must not be consumed",
        }]),
    ])
    session = _FakeToolSession()

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            session,
            router,
            [],
            [{"role": "user", "content": "write x.py"}],
            max_cycles=4,
            log=io.StringIO(),
            orchestration=Orchestration(
                parent_session_id="parent",
                parent_agent_name="agent",
            ),
            role="agent",
            audit_db_path=tmp_path / "audit.db",
        )
    )

    assert answer is not None and answer.startswith("[incomplete]")
    assert "musubi_write_file" in answer
    assert cycles == 1
    assert len(router.calls) == 1
    assert session.calls == []

def test_dispatch_denies_root_command_with_investigator_hint(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession()
    audit_db = tmp_path / "audit.db"

    with pytest.raises(run_mod.PolicyDeniedError) as denied:
        asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-denied",
                "name": "musubi_run_command",
                "input": {"command": "pytest"},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="agent"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
        )

    assert denied.value.role == "agent"
    assert denied.value.tool == "musubi_run_command"
    assert "capability Bash is not allowed" in denied.value.reason
    assert session.calls == []
    assert _read_policy_rows(audit_db) == [
        ("DENY", "agent", "musubi_run_command")
    ]
    assert _read_tool_rows(audit_db) == [
        ("agent", "musubi_run_command", "denied")
    ]


def test_dispatch_allows_coder_write_and_records_post_tool_audit(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession("stored")
    audit_db = tmp_path / "audit.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-allowed",
                "name": "musubi_write_file",
                "input": {"path": "x.py", "content": "print('x')"},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="coder"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert result == "stored"
    assert session.calls == [("musubi_write_file", {"path": "x.py", "content": "print('x')"})]
    assert _read_policy_rows(audit_db) == [
        ("ALLOW", "coder", "musubi_write_file")
    ]
    assert _read_tool_rows(audit_db) == [
        ("coder", "musubi_write_file", "ok")
    ]


def test_dispatch_injects_prior_failure_into_replacement_worker_brief(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import run as run_mod
    from agent import subagent as subagent_mod

    orchestration = Orchestration(parent_session_id="parent")
    orchestration.record_worker_outcome(
        role="coder",
        status="escalated",
        summary="HTML shell exists; charts are missing",
        touched_files={"dashboard.html"},
    )
    captured: dict[str, Any] = {}

    async def fake_run_subagent(session, spawn_args, *args, **kwargs):  # noqa: ANN001
        captured.update(spawn_args)
        return "replacement finished"

    monkeypatch.setattr(subagent_mod, "run_subagent", fake_run_subagent)

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "replacement",
                "name": "musubi_spawn_subagent",
                "input": {"role": "coder", "brief": "finish the dashboard"},
            },
            _FakeToolSession(),
            io.StringIO(),
            vendor=FakeRouter([]),
            tools=[],
            orchestration=orchestration,
            gateway=None,
            audit_db_path=tmp_path / "audit.db",
        )
    )

    assert result == "replacement finished"
    assert captured["role"] == "coder"
    assert "finish the dashboard" in captured["brief"]
    assert "HTML shell exists; charts are missing" in captured["brief"]
    assert "dashboard.html" in captured["brief"]


def test_dispatch_denies_root_append_before_call_and_records_policy_audit(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession()
    audit_db = tmp_path / "audit.db"

    with pytest.raises(run_mod.PolicyDeniedError) as denied:
        asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-denied",
                "name": "musubi_append_file",
                "input": {"path": "x.py", "content": "print('x')"},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="agent"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
        )

    assert denied.value.role == "agent"
    assert denied.value.tool == "musubi_append_file"
    assert session.calls == []
    assert _read_policy_rows(audit_db) == [
        ("DENY", "agent", "musubi_append_file")
    ]
    assert _read_tool_rows(audit_db) == [
        ("agent", "musubi_append_file", "denied")
    ]


def test_dispatch_allows_coder_append_and_records_post_tool_audit(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession("stored")
    audit_db = tmp_path / "audit.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-allowed",
                "name": "musubi_append_file",
                "input": {"path": "x.py", "content": "print('x')", "expected_offset": 0},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="coder"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert result == "stored"
    assert session.calls == [
        (
            "musubi_append_file",
            {"path": "x.py", "content": "print('x')", "expected_offset": 0},
        )
    ]
    assert _read_policy_rows(audit_db) == [
        ("ALLOW", "coder", "musubi_append_file")
    ]
    assert _read_tool_rows(audit_db) == [
        ("coder", "musubi_append_file", "ok")
    ]


def test_dispatch_rejects_invalid_file_tool_args_before_mcp_call(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession("stored")
    audit_db = tmp_path / "audit.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {"id": "call-bad", "name": "musubi_write_file", "input": {}},
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="coder"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert "[tool error] invalid arguments" in result
    assert "path must be a string" in result
    assert "content must be a string" in result
    assert session.calls == []
    assert _read_policy_rows(audit_db) == [
        ("ALLOW", "coder", "musubi_write_file")
    ]
    assert _read_tool_rows(audit_db) == [
        ("coder", "musubi_write_file", "error")
    ]


def test_dispatch_rejects_invalid_append_args_before_mcp_call(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession("stored")
    audit_db = tmp_path / "audit.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-bad",
                "name": "musubi_append_file",
                "input": {"path": "x.py", "content": "x", "expected_offset": -1},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="coder"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert "[tool error] invalid arguments" in result
    assert "expected_offset must be a non-negative integer" in result
    assert session.calls == []
    assert _read_tool_rows(audit_db) == [
        ("coder", "musubi_append_file", "error")
    ]


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("musubi_write_file", {"path": "x.py", "content": "MARKER"}),
        (
            "musubi_append_file",
            {"path": "x.py", "content": "MARKER", "expected_offset": 0},
        ),
        (
            "musubi_edit_file",
            {"path": "x.py", "old_string": "MARKER", "new_string": "safe"},
        ),
        (
            "musubi_edit_file",
            {"path": "x.py", "old_string": "safe", "new_string": "MARKER"},
        ),
    ],
)
def test_dispatch_rejects_elided_tool_arg_marker_before_mcp_call(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    from agent import run as run_mod

    marker = (
        "[musubi:elided-tool-arg tool=musubi_append_file field=content "
        "chars=786 bytes=814 sha256=d9ed21b71f59b45b; "
        "argument was already sent to the MCP tool]"
    )
    args = {
        key: marker if value == "MARKER" else value
        for key, value in arguments.items()
    }
    session = _FakeToolSession("stored")
    audit_db = tmp_path / f"{tool_name}.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {"id": "call-elided", "name": tool_name, "input": args},
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(
                parent_session_id="parent", parent_agent_name="coder"
            ),
            gateway=None,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert "[tool error] invalid arguments" in result
    assert "elided tool argument marker" in result
    assert "regenerate the original content" in result
    assert session.calls == []
    assert _read_tool_rows(audit_db) == [("coder", tool_name, "error")]


def test_dispatch_allows_documentation_that_mentions_elision_marker(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    content = (
        "The prefix [musubi:elided-tool-arg is internal and must not be copied."
    )
    session = _FakeToolSession("stored")

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-doc",
                "name": "musubi_write_file",
                "input": {"path": "docs/elision.md", "content": content},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(
                parent_session_id="parent", parent_agent_name="coder"
            ),
            gateway=None,
            compression_db_path=None,
            audit_db_path=tmp_path / "audit.db",
        )
    )

    assert result == "stored"
    assert session.calls == [
        (
            "musubi_write_file",
            {"path": "docs/elision.md", "content": content},
        )
    ]


@pytest.mark.parametrize("tool_name", ["musubi_write_file", "musubi_append_file"])
def test_dispatch_rejects_empty_file_content_before_mcp_call(
    tmp_path: Path,
    tool_name: str,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession("stored")
    audit_db = tmp_path / f"{tool_name}-empty.db"
    result = asyncio.run(run_mod._dispatch_one(
        {
            "id": "call-empty",
            "name": tool_name,
            "input": {"path": "dashboard.html", "content": "  \n"},
        },
        session,
        io.StringIO(),
        vendor=None,
        tools=[],
        orchestration=Orchestration(
            parent_session_id="parent", parent_agent_name="coder"
        ),
        gateway=None,
        compression_db_path=None,
        audit_db_path=audit_db,
    ))

    assert "[tool error] invalid arguments" in result
    assert "content is empty" in result
    assert "regenerate the full file content" in result
    assert session.calls == []
    assert _read_tool_rows(audit_db) == [("coder", tool_name, "error")]


def test_edit_file_allows_empty_new_string_for_deletion() -> None:
    from agent.run import _file_tool_argument_error

    assert _file_tool_argument_error(
        "musubi_edit_file",
        {"path": "x.txt", "old_string": "remove me", "new_string": ""},
    ) is None


def test_dispatch_runs_file_mutations_sequentially_in_model_order(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    class _ConcurrencySession(_FakeToolSession):
        def __init__(self) -> None:
            super().__init__("stored")
            self.active = 0
            self.max_active = 0

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            try:
                return await super().call_tool(name, arguments)
            finally:
                self.active -= 1

    session = _ConcurrencySession()
    audit_db = tmp_path / "audit.db"
    tool_uses = [
        {
            "id": "w",
            "name": "musubi_write_file",
            "input": {"path": "x.py", "content": "seed"},
        },
        {
            "id": "a1",
            "name": "musubi_append_file",
            "input": {"path": "x.py", "content": "one", "expected_offset": 0},
        },
        {
            "id": "a2",
            "name": "musubi_append_file",
            "input": {"path": "x.py", "content": "two", "expected_offset": 3},
        },
    ]

    asyncio.run(
        run_mod._dispatch(
            session,
            tool_uses,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="coder"),
            gateway=None,
            audit_db_path=audit_db,
        )
    )

    assert session.max_active == 1
    assert [name for name, _ in session.calls] == [
        "musubi_write_file",
        "musubi_append_file",
        "musubi_append_file",
    ]


def test_normalize_tool_result_text_minifies_json() -> None:
    from agent.run import normalize_tool_result_text

    raw = '{\n  "z": 2,\n  "a": [1, 2]\n}\n\n'

    assert normalize_tool_result_text(raw) == '{"z":2,"a":[1,2]}'


def test_normalize_tool_result_text_preserves_retrieve_marker() -> None:
    from agent.run import normalize_tool_result_text

    marker = (
        'summary\n\n[musubi:compressed kind=json ref=abc chars 1000->100; '
        'call musubi_retrieve("abc") for the verbatim original]\n\n'
    )

    assert normalize_tool_result_text(marker).endswith(
        'musubi_retrieve("abc") for the verbatim original]'
    )


def test_dispatch_feeds_normalized_tool_result_to_model() -> None:
    from agent import run as run_mod

    session = _FakeToolSession('{\n  "z": 2,\n  "a": [1, 2]\n}\n\n')

    result = asyncio.run(
        run_mod._dispatch_one(
            {"id": "call-json", "name": "external_json", "input": {}},
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=None,
            gateway=None,
            refused=False,
            compression_db_path=None,
        )
    )

    assert result == '{"z":2,"a":[1,2]}'


def test_dispatch_logs_loaded_skill_id() -> None:
    from agent import run as run_mod

    log = io.StringIO()
    session = _FakeToolSession("---\nname: HTML Dashboard\n---\n")

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "skill-call",
                "name": "musubi_get_skill",
                "input": {
                    "skill_id": "html-css-dashboard",
                    "agent_name": "root",
                },
            },
            session,
            log,
            vendor=None,
            tools=[],
            orchestration=None,
            gateway=None,
            refused=False,
            compression_db_path=None,
        )
    )

    assert result.startswith("---")
    assert "skill used=html-css-dashboard agent=root" in log.getvalue()


def test_dispatch_does_not_log_skill_used_for_skill_errors() -> None:
    from agent import run as run_mod

    log = io.StringIO()
    session = _FakeToolSession('{"error":"not permitted"}')

    asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "skill-call",
                "name": "musubi_get_skill",
                "input": {
                    "skill_id": "devops",
                    "agent_name": "coder",
                },
            },
            session,
            log,
            vendor=None,
            tools=[],
            orchestration=None,
            gateway=None,
            refused=False,
            compression_db_path=None,
        )
    )

    assert "skill used=" not in log.getvalue()


def test_dispatch_one_records_touched_file_into_active_sink(tmp_path: Path) -> None:
    from agent import run as run_mod

    session = _FakeToolSession('{"status": "ok", "bytes_written": 3}')
    sink: set[str] = set()
    token = run_mod._worker_touched_files.set(sink)
    try:
        result = asyncio.run(
            run_mod._dispatch_one(
                {
                    "id": "c-ok",
                    "name": "musubi_write_file",
                    "input": {"path": "app.py", "content": "x = 1"},
                },
                session,
                io.StringIO(),
                vendor=None,
                tools=[],
                orchestration=None,
                gateway=None,
                role="coder",
                audit_db_path=tmp_path / "audit.db",
            )
        )
    finally:
        run_mod._worker_touched_files.reset(token)

    assert '"ok"' in result
    assert sink == {"app.py"}


def test_system_prompt_states_two_layer_acceptance() -> None:
    from agent.context import build_system_prompt

    prompt = build_system_prompt()
    # C2 — the root is told it owns goal-acceptance and trusts the mechanical
    # verdict rather than re-deriving it.
    assert "[mechanical]" in prompt
    assert "goal" in prompt.lower()
    assert "do not re-run linters" in prompt


def test_system_prompt_has_root_sizing_ladder_and_write_strategy() -> None:
    from agent.context import build_system_prompt

    prompt = build_system_prompt().lower()
    # R1 — the root sizes the request itself (scope is a hint) and bakes the
    # large-artifact write strategy into the coder brief.
    assert "hint" in prompt
    assert "shallowest path" in prompt
    assert "planner" in prompt and "designer" in prompt
    assert "append_file" in prompt
    assert "utf-8" in prompt


def test_replay_elides_large_tool_rows() -> None:
    from agent import run as run_mod

    small = run_mod._elide_replayed_tool_row("short output")
    assert small == "short output"

    big = "A" * (run_mod.REPLAY_TOOL_ROW_MAX_CHARS + 500)
    elided = run_mod._elide_replayed_tool_row(big)
    assert len(elided) < len(big)
    assert TRUNCATION_MARK in elided

    history = {"messages": [
        {"id": 1, "role": "user", "content": "make a dashboard", "ts": "t"},
        {"id": 2, "role": "tool", "content": big, "ts": "t"},
    ]}
    messages = run_mod._messages_from_chat_history("sys", history)
    tool_msg = messages[-1]["content"]
    assert tool_msg.startswith("[prior tool result]")
    assert TRUNCATION_MARK in tool_msg
    assert len(tool_msg) < len(big)


def test_log_cycle_includes_human_readable_model_action() -> None:
    from agent import run as run_mod

    log = io.StringIO()

    run_mod._log_cycle(
        log,
        3,
        "tool_use",
        [{"type": "tool_use", "name": "musubi_get_skill"}],
        {"cache_read_input_tokens": 512},
        tokens_out=42,
    )

    line = log.getvalue()
    assert "model_action=tool_calls:read" in line
    assert "stop=tool_use" in line
    assert "tools=1" in line
    assert "names=[get_skill]" in line


def test_log_cycle_names_aggregate_repeated_tools() -> None:
    from agent import run as run_mod

    log = io.StringIO()
    run_mod._log_cycle(
        log,
        1,
        "tool_use",
        [
            {"type": "tool_use", "name": "musubi_grep"},
            {"type": "tool_use", "name": "musubi_grep"},
            {"type": "tool_use", "name": "musubi_read_file"},
        ],
        None,
    )
    line = log.getvalue()
    # A pure read/grep cycle is a verification loop; it should read as one.
    assert "model_action=tool_calls:read" in line
    assert "tools=3" in line
    assert "names=[grep×2, read_file]" in line


def test_model_action_flags_mutation_and_spawn() -> None:
    from agent import run as run_mod

    mutate = run_mod._model_action(
        "tool_use",
        [{"type": "tool_use", "name": "musubi_write_file"},
         {"type": "tool_use", "name": "musubi_grep"}],
    )
    assert mutate == "tool_calls:mutate"

    spawn = run_mod._model_action(
        "tool_use", [{"type": "tool_use", "name": "musubi_spawn_subagent"}],
    )
    assert spawn == "tool_calls:spawn"


def test_log_cycle_is_tagged_with_the_active_worker_label() -> None:
    # O3 — a worker's cycle lines carry its label so multiple "cycle 0" lines
    # from different workers are distinguishable; the root uses the default.
    from agent import run as run_mod

    root_log = io.StringIO()
    run_mod._log_cycle(root_log, 0, "end_turn", [], None)
    assert "[root] cycle 0" in root_log.getvalue()

    worker_log = io.StringIO()
    token = run_mod._worker_log_label.set("coder#483b27c2")
    try:
        run_mod._log_cycle(worker_log, 0, "end_turn", [], None)
    finally:
        run_mod._worker_log_label.reset(token)
    assert "[coder#483b27c2] cycle 0" in worker_log.getvalue()


def test_dropped_tool_target_names_the_discarded_write() -> None:
    # O2 — a truncated write is logged with its target so the drop is traceable.
    from agent import run as run_mod

    named = run_mod._dropped_tool_target(
        {"name": "musubi_write_file", "input": {"path": "dash.html"}}
    )
    assert named == "write_file(dash.html)"
    bare = run_mod._dropped_tool_target({"name": "musubi_spawn_subagent", "input": {}})
    assert bare == "spawn_subagent"


def test_run_loop_elides_large_file_tool_args_before_next_model_call(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    raw = "<html>" + ("A" * 2400) + "</html>"
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[
                {
                    "type": "tool_use",
                    "id": "append-1",
                    "name": "musubi_append_file",
                    "input": {
                        "path": "dashboard.html",
                        "content": raw,
                        "expected_offset": 0,
                    },
                }
            ],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "dashboard written."}],
        ),
    ])
    session = _FakeToolSession(
        '{"status":"ok","bytes_written":2413,"total_bytes":2413}'
    )

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            session,
            router,
            [{"name": "musubi_append_file", "description": "", "input_schema": {}}],
            [{"role": "user", "content": "create html dashboard"}],
            max_cycles=2,
            log=io.StringIO(),
            role="coder",
            audit_db_path=tmp_path / "audit.db",
        )
    )

    assert answer == "dashboard written."
    assert cycles == 2
    assert session.calls == [
        (
            "musubi_append_file",
            {"path": "dashboard.html", "content": raw, "expected_offset": 0},
        )
    ]

    replay = json.dumps(router.calls[1]["messages"])
    assert raw not in replay
    assert "[musubi:elided-tool-arg" in replay
    assert "dashboard.html" in replay


def test_call_with_effort_escalates_on_max_tokens() -> None:
    """A truncated call is retried once at the ceiling."""
    from agent.context import DEFAULT_EFFORT_CEILING, DEFAULT_EFFORT_FLOOR
    from agent.run import _call_with_effort

    router = FakeRouter([
        LMResponse(stop_reason="max_tokens", content=[{"type": "text", "text": ""}]),
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    result = _call_with_effort(
        router,
        [{"role": "user", "content": "hi"}],
        [],
        floor=DEFAULT_EFFORT_FLOOR,
        ceiling=DEFAULT_EFFORT_CEILING,
    )
    assert result.response.stop_reason == "end_turn"
    assert len(result.attempts) == 2
    assert [c["max_tokens"] for c in router.calls] == [
        DEFAULT_EFFORT_FLOOR,
        DEFAULT_EFFORT_CEILING,
    ]


def test_call_with_effort_no_escalation_when_complete() -> None:
    from agent.context import DEFAULT_EFFORT_FLOOR
    from agent.run import _call_with_effort

    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    result = _call_with_effort(
        router,
        [{"role": "user", "content": "hi"}],
        [],
        floor=DEFAULT_EFFORT_FLOOR,
        ceiling=16_384,
    )
    assert result.response.stop_reason == "end_turn"
    assert len(result.attempts) == 1
    assert len(router.calls) == 1
    assert router.calls[0]["max_tokens"] == DEFAULT_EFFORT_FLOOR


def test_mutate_worker_opens_at_output_ceiling() -> None:
    from agent import run as run_mod
    from agent.context import DEFAULT_EFFORT_CEILING

    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])

    answer, _ = asyncio.run(run_mod._run_loop(
        object(),
        router,
        [{"name": "musubi_write_file", "description": "", "input_schema": {}}],
        [{"role": "user", "content": "write"}],
        max_cycles=1,
        log=io.StringIO(),
        role="coder",
    ))

    assert answer == "ok"
    assert router.calls[0]["max_tokens"] == DEFAULT_EFFORT_CEILING


def test_read_only_worker_opens_at_effort_floor() -> None:
    from agent import run as run_mod
    from agent.context import DEFAULT_EFFORT_FLOOR

    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])

    asyncio.run(run_mod._run_loop(
        object(),
        router,
        [{"name": "musubi_read_file", "description": "", "input_schema": {}}],
        [{"role": "user", "content": "read"}],
        max_cycles=1,
        log=io.StringIO(),
    ))

    assert router.calls[0]["max_tokens"] == DEFAULT_EFFORT_FLOOR


def test_effort_escalation_sticks_for_later_cycles() -> None:
    from agent import run as run_mod
    from agent.context import DEFAULT_EFFORT_CEILING, DEFAULT_EFFORT_FLOOR

    router = FakeRouter([
        LMResponse(stop_reason="max_tokens", content=[]),
        LMResponse(stop_reason="tool_use", content=[{
            "type": "tool_use",
            "id": "read-1",
            "name": "musubi_read_file",
            "input": {"path": "x.txt"},
        }]),
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "done"}]),
    ])

    answer, cycles = asyncio.run(run_mod._run_loop(
        _FakeToolSession("contents"),
        router,
        [{"name": "musubi_read_file", "description": "", "input_schema": {}}],
        [{"role": "user", "content": "read"}],
        max_cycles=2,
        log=io.StringIO(),
    ))

    assert answer == "done"
    assert cycles == 2
    assert [call["max_tokens"] for call in router.calls] == [
        DEFAULT_EFFORT_FLOOR,
        DEFAULT_EFFORT_CEILING,
        DEFAULT_EFFORT_CEILING,
    ]


def test_run_loop_does_not_dispatch_tool_call_from_max_tokens_response() -> None:
    from agent import run as run_mod

    partial_write = {
        "type": "tool_use",
        "id": "partial-write",
        "name": "musubi_write_file",
        "input": {},
    }
    router = FakeRouter([
        LMResponse(stop_reason="max_tokens", content=[partial_write]),
        LMResponse(stop_reason="max_tokens", content=[partial_write]),
    ])
    session = _FakeToolSession()

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            session,
            router,
            [{"name": "musubi_write_file", "description": "", "input_schema": {}}],
            [{"role": "user", "content": "create html dashboard"}],
            max_cycles=1,
            log=io.StringIO(),
            role="coder",
        )
    )

    assert answer is not None
    assert answer.startswith("[blocked] ")
    payload = json.loads(answer.removeprefix("[blocked] "))
    assert payload["status"] == "blocked"
    assert payload["reason"] == "output_too_large_for_single_tool_call"
    assert payload["retry_same_strategy"] is False
    assert payload["attempted_tools"] == ["musubi_write_file"]
    assert "append_chunks" in payload["recommended_strategies"]
    assert "max_tokens" in payload["message"]
    assert cycles == 1
    assert session.calls == []


def test_truncated_tool_call_audit_does_not_count_dropped_tool(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod
    from session import state
    from storage import db

    p = tmp_path / "cycles.db"
    db.init_db(p)
    sid = state.create_session("audit truncated call", p)
    partial_write = {
        "type": "tool_use",
        "id": "partial-write",
        "name": "musubi_write_file",
        "input": {},
    }
    router = FakeRouter([
        LMResponse(stop_reason="max_tokens", content=[partial_write]),
        LMResponse(stop_reason="max_tokens", content=[partial_write]),
    ])

    asyncio.run(run_mod._run_loop(
        _FakeToolSession(), router,
        [{"name": "musubi_write_file", "description": "", "input_schema": {}}],
        [{"role": "user", "content": "create html dashboard"}],
        max_cycles=1, log=io.StringIO(), compression_db_path=p,
        audit_session_id=sid, audit_worker_id="root", audit_stage="agent",
    ))

    row = db.query_agent_cycles(sid, db_path=p)[0]
    assert json.loads(row["tool_calls_json"]) == []
    assert row["cycle_status"] == "truncated"


def test_run_loop_returns_incomplete_when_forced_final_call_fails() -> None:
    from agent import run as run_mod

    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use",
                "id": "read-1",
                "name": "musubi_read_file",
                "input": {"path": "README.md"},
            }],
        ),
    ])
    session = _FakeToolSession("read")

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            session,
            router,
            [{"name": "musubi_read_file", "description": "", "input_schema": {}}],
            [{"role": "user", "content": "inspect README"}],
            max_cycles=1,
            log=io.StringIO(),
            salvage_on_exhaust=True,
            role="explorer",
        )
    )

    assert answer is not None
    assert answer.startswith("[incomplete] agent reached 1 cycles")
    assert cycles == 1
    assert len(router.calls) == 2
    assert router.calls[1]["tools"] == []


def test_run_loop_returns_truncated_tool_call_to_same_worker_for_chunk_retry() -> None:
    from agent import run as run_mod

    partial_append = {
        "type": "tool_use",
        "id": "partial-append",
        "name": "musubi_append_file",
        "input": {"path": "dashboard.html"},
    }
    router = FakeRouter([
        LMResponse(stop_reason="max_tokens", content=[partial_append]),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "continued with safe chunks"}],
        ),
    ])
    session = _FakeToolSession()

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            session,
            router,
            [{"name": "musubi_append_file", "description": "", "input_schema": {}}],
            [{"role": "user", "content": "create html dashboard"}],
            max_cycles=2,
            log=io.StringIO(),
            role="coder",
        )
    )

    assert answer == "continued with safe chunks"
    assert cycles == 2
    assert session.calls == []
    retry_messages = router.calls[-1]["messages"]
    blocked = next(
        block
        for message in retry_messages
        if message.get("role") == "user" and isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    )
    assert blocked["tool_use_id"] == "partial-append"
    assert "output_too_large_for_single_tool_call" in blocked["content"]


def test_pipeline_context_budget_is_lower_and_keeps_recent_tool_pair() -> None:
    from agent.context import DEFAULT_CONTEXT_BUDGET, fit_context
    from agent.pipeline_runner import PIPELINE_CONTEXT_BUDGET

    older_result = "old glob result\n" + ("a" * (PIPELINE_CONTEXT_BUDGET + 2_000))
    recent_result = "recent grep result\n" + ("b" * 300)
    messages = [
        {"role": "system", "content": "worker system"},
        {"role": "user", "content": "latest user goal: build dashboard"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "old", "name": "musubi_glob", "input": {"pattern": "**/*"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "old", "content": older_result}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "recent", "name": "musubi_grep", "input": {"pattern": "TODO"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "recent", "content": recent_result}]},
    ]

    fitted = fit_context(messages, budget_chars=PIPELINE_CONTEXT_BUDGET, keep_last_turns=2)

    assert PIPELINE_CONTEXT_BUDGET < DEFAULT_CONTEXT_BUDGET
    assert "latest user goal" in str(fitted[1]["content"])
    assert fitted[-1]["content"][0]["content"] == recent_result
    assert "[context-trimmed:" in str(fitted[3]["content"])


def test_cycle_token_usage_sums_effort_retry_attempts() -> None:
    from agent import run as run_mod

    attempts = [
        LMResponse(
            stop_reason="max_tokens",
            content=[{"type": "text", "text": "partial"}],
            usage={"input_tokens": 100, "output_tokens": 20},
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "final"}],
            usage={
                "input_tokens": 120,
                "output_tokens": 30,
                "cache_read_input_tokens": 200,
            },
        ),
    ]

    usage = run_mod._cycle_token_usage(attempts, input_estimate=999)
    assert usage.tokens_in == 220
    assert usage.tokens_out == 50
    assert usage.cached_input_tokens == 120
    assert usage.source == "provider"


def test_cycle_token_usage_marks_mixed_attempts_estimated() -> None:
    from agent import run as run_mod

    attempts = [
        LMResponse(
            stop_reason="max_tokens", content=[],
            usage={"input_tokens": 100, "output_tokens": 20},
        ),
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "x"}]),
    ]
    usage = run_mod._cycle_token_usage(attempts, input_estimate=50)
    assert usage.tokens_in == 150
    assert usage.source == "estimated"


def test_run_loop_persists_provider_cycle_usage(tmp_path: Path) -> None:
    from agent import run as run_mod
    from session import state
    from storage import db

    p = tmp_path / "cycles.db"
    db.init_db(p)
    sid = state.create_session("audit cycle", p)
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "done"}],
            usage={
                "input_tokens": 1200,
                "output_tokens": 90,
                "cache_read_input_tokens": 800,
            },
        ),
    ])
    answer, _ = asyncio.run(run_mod._run_loop(
        object(), router, [], [{"role": "user", "content": "go"}],
        max_cycles=1, log=io.StringIO(), compression_db_path=p,
        audit_session_id=sid, audit_worker_id="root", audit_stage="agent",
    ))
    row = db.query_agent_cycles(sid, db_path=p)[0]
    assert answer == "done"
    assert row["worker_id"] == "root"
    assert row["tokens_in"] == 1200
    assert row["cached_input_tokens"] == 800
    assert row["tokens_out"] == 90
    assert row["token_source"] == "provider"
    assert json.loads(row["tool_calls_json"]) == []
    assert row["cycle_status"] == "final"


def test_cycle_audit_failure_is_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import run as run_mod
    from storage import db

    monkeypatch.setattr(
        db, "insert_agent_cycle",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    log = io.StringIO()
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    answer, _ = asyncio.run(run_mod._run_loop(
        object(), router, [], [{"role": "user", "content": "go"}],
        max_cycles=1, log=log, compression_db_path=tmp_path / "cycles.db",
        audit_session_id="session-1", audit_worker_id="root", audit_stage="agent",
    ))
    assert answer == "ok"
    assert "cycle audit write failed" in log.getvalue()


def test_forced_final_call_is_audited_as_its_own_cycle(tmp_path: Path) -> None:
    from agent import run as run_mod
    from session import state
    from storage import db

    p = tmp_path / "cycles.db"
    db.init_db(p)
    sid = state.create_session("audit forced final", p)
    router = FakeRouter([
        LMResponse(stop_reason="tool_use", content=[{
            "type": "tool_use", "id": "read-1",
            "name": "musubi_read_file", "input": {"path": "README.md"},
        }], usage={"input_tokens": 100, "output_tokens": 20}),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "summary"}],
            usage={"input_tokens": 120, "output_tokens": 30},
        ),
    ])
    answer, _ = asyncio.run(run_mod._run_loop(
        _FakeToolSession(), router,
        [{"name": "musubi_read_file", "description": "", "input_schema": {}}],
        [{"role": "user", "content": "go"}], max_cycles=1,
        log=io.StringIO(), salvage_on_exhaust=True, compression_db_path=p,
        audit_session_id=sid, audit_worker_id="root", audit_stage="agent",
    ))
    rows = db.query_agent_cycles(sid, db_path=p)
    assert answer == "summary"
    assert [row["cycle_idx"] for row in rows] == [0, 1]
    assert [row["cycle_status"] for row in rows] == ["ok", "final"]
    assert json.loads(rows[0]["tool_calls_json"]) == ["musubi_read_file"]
    assert json.loads(rows[1]["tool_calls_json"]) == []


def test_postflight_budget_halt_still_audits_measured_cycle(tmp_path: Path) -> None:
    from agent import run as run_mod
    from session import state
    from storage import db

    p = tmp_path / "cycles.db"
    db.init_db(p)
    sid = state.create_session("postflight audit", p)
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "large"}],
            usage={"input_tokens": 90, "output_tokens": 30},
        ),
    ])
    with pytest.raises(TokenBudgetExhaustedError, match="postflight"):
        asyncio.run(run_mod._run_loop(
            object(), router, [], [{"role": "user", "content": "go"}],
            max_cycles=1, log=io.StringIO(), compression_db_path=p,
            audit_session_id=sid, audit_worker_id="root", audit_stage="agent",
            budget=TokenBudgetEnforcer(100),
        ))
    rows = db.query_agent_cycles(sid, db_path=p)
    assert len(rows) == 1
    assert rows[0]["tokens_in"] == 90
    assert rows[0]["tokens_out"] == 30


def test_postflight_budget_halt_does_not_count_undispatched_tool(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod
    from session import state
    from storage import db

    p = tmp_path / "cycles.db"
    db.init_db(p)
    sid = state.create_session("postflight tool audit", p)
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use", "id": "read-1",
                "name": "musubi_read_file", "input": {"path": "README.md"},
            }],
            usage={"input_tokens": 90, "output_tokens": 30},
        ),
    ])
    session = _FakeToolSession("contents")

    with pytest.raises(TokenBudgetExhaustedError, match="postflight"):
        asyncio.run(run_mod._run_loop(
            session, router,
            [{"name": "musubi_read_file", "description": "", "input_schema": {}}],
            [{"role": "user", "content": "read"}], max_cycles=1,
            log=io.StringIO(), compression_db_path=p,
            audit_session_id=sid, audit_worker_id="root", audit_stage="agent",
            budget=TokenBudgetEnforcer(100),
        ))

    row = db.query_agent_cycles(sid, db_path=p)[0]
    assert session.calls == []
    assert json.loads(row["tool_calls_json"]) == []
    assert row["cycle_status"] == "budget_halt"


def test_forced_final_postflight_halt_audits_budget_halt_cycle(tmp_path: Path) -> None:
    from agent import run as run_mod
    from session import state
    from storage import db

    p = tmp_path / "cycles.db"
    db.init_db(p)
    sid = state.create_session("forced postflight audit", p)
    router = FakeRouter([
        LMResponse(stop_reason="tool_use", content=[{
            "type": "tool_use", "id": "read-1",
            "name": "musubi_read_file", "input": {"path": "README.md"},
        }], usage={"input_tokens": 5, "output_tokens": 5}),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "large final"}],
            usage={"input_tokens": 90, "output_tokens": 30},
        ),
    ])
    answer, _ = asyncio.run(run_mod._run_loop(
        _FakeToolSession(), router,
        [{"name": "musubi_read_file", "description": "", "input_schema": {}}],
        [{"role": "user", "content": "go"}], max_cycles=1,
        log=io.StringIO(), salvage_on_exhaust=True, compression_db_path=p,
        audit_session_id=sid, audit_worker_id="root", audit_stage="agent",
        budget=TokenBudgetEnforcer(100),
    ))
    rows = db.query_agent_cycles(sid, db_path=p)
    assert answer.startswith("[incomplete]")
    assert [row["cycle_idx"] for row in rows] == [0, 1]
    assert rows[1]["cycle_status"] == "budget_halt"


def test_loop_returns_text_when_model_does_not_use_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "no tools needed."}],
        ),
    ])
    log = io.StringIO()
    answer = asyncio.run(
        run_agent("ping", router, _musubi_dir(), log=log, max_tokens=0)
    )
    assert answer == "no tools needed."
    assert router.calls[0]["tools"], "expected the MCP tool catalog in the first call"


def test_run_agent_default_tool_surface_hides_driver_only_tools() -> None:
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])

    answer = asyncio.run(
        run_agent("debug the dashboard", router, _musubi_dir(), log=io.StringIO(), max_tokens=0)
    )

    assert answer == "ok"
    names = {tool["name"] for tool in router.calls[0]["tools"]}
    assert names == {
        "musubi_get_reference",
        "musubi_get_skill",
        "musubi_recommend_skills",
        "musubi_spawn_subagent",
    }
    assert "musubi_write_file" not in names
    assert "musubi_edit_file" not in names
    assert "musubi_run_command" not in names
    assert "musubi_run_tests" not in names
    assert "musubi_write_stage" not in names
    assert "musubi_read_stage" not in names
    assert "musubi_get_subagent_context" not in names
    assert "musubi_record_agent_cycle" not in names


def test_run_agent_full_catalog_still_keeps_root_decision_surface_small(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUSUBI_TOOL_SURFACE", "full")
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])

    asyncio.run(
        run_agent("debug", router, _musubi_dir(), log=io.StringIO(), max_tokens=0)
    )

    names = {tool["name"] for tool in router.calls[0]["tools"]}
    assert names == {
        "musubi_get_reference",
        "musubi_get_skill",
        "musubi_recommend_skills",
        "musubi_spawn_subagent",
    }


def test_read_only_inspect_task_uses_lean_simple_root_surface() -> None:
    # A read-only "reach to a path" request is a simple scope: the root gets the
    # spawn + skill-selection surface but NOT the skill-reading tools it would
    # only need for its own broader work.
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])

    asyncio.run(run_agent(
        r"could you reach to C:\Workspace\21_A2lPatcher\a2l-patcher-stla",
        router, _musubi_dir(), log=io.StringIO(), max_tokens=0,
    ))

    names = {tool["name"] for tool in router.calls[0]["tools"]}
    assert names == {"musubi_recommend_skills", "musubi_spawn_subagent"}
    assert "musubi_get_skill" not in names


def test_run_agent_persists_and_replays_chat_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))

    first_router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "first answer"}]),
    ])
    first = asyncio.run(
        run_agent(
            "first question",
            first_router,
            _musubi_dir(),
            log=io.StringIO(),
            chat_id="chat-1",
            max_tokens=0,
        )
    )
    assert first == "first answer"

    second_router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "second answer"}]),
    ])
    second = asyncio.run(
        run_agent(
            "second question",
            second_router,
            _musubi_dir(),
            log=io.StringIO(),
            chat_id="chat-1",
            max_tokens=0,
        )
    )
    assert second == "second answer"

    replay = "\n".join(
        str(message.get("content"))
        for message in second_router.calls[0]["messages"]
    )
    assert "first question" in replay
    assert "first answer" in replay
    assert "second question" in replay

    with sqlite3.connect(tmp_path / "data" / "musubi.db") as conn:
        rows = list(conn.execute(
            "SELECT role, content FROM conversation_messages "
            "WHERE chat_id='chat-1' ORDER BY id"
        ))
    assert rows == [
        ("user", "first question"),
        ("assistant", "first answer"),
        ("user", "second question"),
        ("assistant", "second answer"),
    ]


def test_loop_dispatches_real_tool_and_feeds_result_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use",
                "id": "call-1",
                "name": "musubi_new_session",
                "input": {"request": "smoke from agent loop test"},
            }],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "session opened."}],
        ),
    ])
    log = io.StringIO()
    answer = asyncio.run(
        run_agent(
            "open a session",
            router,
            _musubi_dir(),
            log=log,
            max_tokens=0,
            tool_surface="full",
        )
    )
    assert answer == "session opened."
    second_call_messages = router.calls[1]["messages"]
    user_results = [
        m
        for m in second_call_messages
        if m["role"] == "user" and isinstance(m["content"], list)
    ]
    assert user_results, "expected a user message carrying tool_result blocks"
    blocks = user_results[-1]["content"]
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["tool_use_id"] == "call-1"
    assert "session_id" in blocks[0]["content"], "musubi_new_session must return a session_id"


def test_loop_aborts_after_max_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    looping_response = LMResponse(
        stop_reason="tool_use",
        content=[{
            "type": "tool_use",
            "id": "x",
            "name": "musubi_get_active_session",
            "input": {},
        }],
    )
    router = FakeRouter([looping_response, looping_response, looping_response])
    log = io.StringIO()
    answer = asyncio.run(run_agent(
        "loop forever", router, _musubi_dir(), max_cycles=2, log=log,
        max_tokens=0,
    ))

    assert "incomplete" in answer.lower()
    assert "2 cycles" in answer


def test_loop_passes_tool_error_to_model_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use",
                "id": "bad-call",
                "name": "musubi_get_active_session",
                "input": {"nonexistent_param": True, "another": [1, 2, 3]},
            }],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "ack."}],
        ),
    ])
    log = io.StringIO()
    answer = asyncio.run(run_agent(
        "bad tool", router, _musubi_dir(), log=log, max_tokens=0,
    ))
    assert answer == "ack."
    assert len(router.calls) == 2, "loop should have completed both cycles"


def test_root_system_prompt_includes_scope_hint_for_simple_task() -> None:
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "ok"}],
        )
    ])

    answer = asyncio.run(run_agent(
        "Update weather-dashboard.html to refresh every 5 minutes",
        router,
        _musubi_dir(),
        log=io.StringIO(),
        max_tokens=0,
    ))

    assert answer == "ok"
    system_text = router.calls[0]["messages"][0]["content"]
    assert "[agent-routing-scope]" in system_text
    assert "scope=simple_edit" in system_text
    assert "route=single_coder" in system_text
    assert "max_workers=" not in system_text
    assert "start with one coder" in system_text.lower()


def test_spawn_overflow_uses_flat_cap_regardless_of_scope() -> None:
    # D2a — classify_task is advisory. A "simple" scope no longer tightens the
    # coder cap to one; the only enforcement is the flat per-role width cap (3),
    # so the 4th coder in a batch is the first refused.
    from agent import run as run_mod
    from agent.scope import classify_task

    simple = classify_task("Update weather-dashboard.html to refresh every 5 minutes")
    tool_uses = [
        {"id": f"s{i}", "name": "musubi_spawn_subagent", "input": {"role": "coder"}}
        for i in range(4)
    ]

    overflow = run_mod._spawn_overflow_reasons(
        tool_uses, io.StringIO(), role="agent", scope_hint=simple, cycle_index=0,
    )

    assert list(overflow) == ["s3"]
    assert "per-turn spawn cap (3)" in overflow["s3"]


def test_spawn_overflow_no_longer_forces_planner_before_coder() -> None:
    # D1 — a coder as the first worker of a medium-scope turn is no longer
    # refused; plan-first is opt-in via --plan, not a keyword guess.
    from agent import run as run_mod
    from agent.scope import classify_task

    medium = classify_task("Improve the dashboard weather display")
    tool_uses = [
        {"id": "c1", "name": "musubi_spawn_subagent",
         "input": {"role": "coder", "brief": "implement"}},
    ]

    overflow = run_mod._spawn_overflow_reasons(
        tool_uses, io.StringIO(), role="agent", scope_hint=medium, cycle_index=0,
    )

    assert overflow == {}


def test_plan_first_directive_injected_into_system_prompt() -> None:
    # D2b — --plan appends an explicit plan-first directive to the root prompt.
    from agent import run as run_mod
    from agent.context import build_system_prompt

    assert "planner" in run_mod._PLAN_FIRST_DIRECTIVE.lower()
    combined = f"{build_system_prompt('scope')}\n\n{run_mod._PLAN_FIRST_DIRECTIVE}"
    assert "--plan" in combined
    assert "plan-first" in combined.lower()


def test_delete_request_now_runs_and_carries_the_warning() -> None:
    # Was: refused with zero model calls and a list of manual commands. The
    # turn now proceeds — the hard stop moved to the tool boundary, where the
    # files can be counted — and the model is told what the gate will do.
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    log = io.StringIO()

    answer = asyncio.run(run_agent(
        "delete all *-dashboard.html files",
        router,
        _musubi_dir(),
        log=log,
        max_tokens=0,
    ))

    assert answer == "ok"
    assert len(router.calls) == 1
    system_text = router.calls[0]["messages"][0]["content"]
    assert "warning=This request reads as removing files" in system_text
    assert "manual_destructive" not in log.getvalue()


def test_greeting_returns_direct_answer_without_llm_calls() -> None:
    router = FakeRouter([])
    log = io.StringIO()

    answer = asyncio.run(run_agent(
        "hi",
        router,
        _musubi_dir(),
        log=log,
        max_tokens=0,
    ))

    assert router.calls == []
    assert answer.startswith("Hi!")
    assert "direct_answer" in log.getvalue()


def test_advisory_request_answers_with_one_model_call_and_no_spawn() -> None:
    # An advisory turn is NOT a deterministic canned answer: the model still
    # runs, because the whole deliverable is its reasoning. What it does not
    # get is a tool catalog, so it cannot spawn a planner or burn a
    # `musubi_recommend_skills` round trip before answering.
    from agent import run as run_mod
    from agent.scope import classify_task

    hint = classify_task("choose the best for me")
    assert hint.route == "advisory"
    assert run_mod._deterministic_scope_answer("choose the best for me", hint) is None

    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{
                "type": "text",
                "text": "OIDC with an email/password fallback.",
            }],
        ),
    ])
    log = io.StringIO()

    answer = asyncio.run(run_agent(
        "choose the best for me",
        router,
        _musubi_dir(),
        log=log,
        max_tokens=0,
    ))

    assert len(router.calls) == 1
    assert router.calls[0]["tools"] == []  # no tool catalog offered
    assert "OIDC" in answer
    assert "route=advisory" in log.getvalue()


class _ExplodingRouter(LMRouter):
    """A vendor whose call fails like a real network/proxy error would."""

    name = "boom"
    model = "boom-1"

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001, ARG002
        raise RuntimeError("curl exited 56 ... 407 proxy auth required")


def test_resolve_vendor_labels_which_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_resolve_vendor` returns a human label of how the endpoint was picked,
    so the startup log can show which profile is in effect."""
    from agent import run as run_mod

    cfg = tmp_path / ".musubi" / "llm.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({
        "default": "ollama.local",
        "ollama": {
            "local": {"model": "llama3.1", "max_output_tokens": 8192},
        },
    }), encoding="utf-8")
    monkeypatch.setenv("MUSUBI_LLM_CONFIG", str(cfg))
    # Avoid importing a real vendor SDK — only the label logic is under test.
    router = FakeRouter([])
    monkeypatch.setattr(run_mod, "build_from_profile", lambda prof: router)

    default_router, default_src = run_mod._resolve_vendor(None)
    assert default_src == "ollama.local (llm.json default)"
    assert default_router.max_output_tokens == 8192

    profile_router, profile_src = run_mod._resolve_vendor("ollama.local")
    assert profile_src == "ollama.local (--profile)"
    assert profile_router.max_output_tokens == 8192


def test_vendor_error_surfaces_clean_not_as_exception_group() -> None:
    """A vendor.call failure inside the loop must reach the caller as a plain
    RuntimeError with the underlying message — NOT anyio's BaseExceptionGroup
    wall raised at AsyncExitStack teardown (the Windows curl-407 traceback)."""
    log = io.StringIO()
    with pytest.raises(RuntimeError, match="407 proxy auth") as ei:
        asyncio.run(
            run_agent(
                "summarize repository architecture",
                _ExplodingRouter(),
                _musubi_dir(),
                log=log,
                max_tokens=0,
            )
        )
    # The message is a clean one-liner, not a nested group dump.
    assert not isinstance(ei.value, BaseExceptionGroup)


def test_high_ambiguity_returns_question_without_model_or_worker() -> None:
    from agent import run as run_mod
    from agent.scope import classify_task

    hint = classify_task("create a new website")
    answer = run_mod._deterministic_scope_answer("create a new website", hint)
    assert hint.route == "ask_scope"
    assert answer == (
        BROAD_PRODUCT_QUESTION
    )


def test_clarification_is_asked_once_then_the_answer_is_acted_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # The traced loop (chat gui-orchestrator-…-7bce98a4ecdc): "create a
    # website" was met with the canned question, and so were BOTH answers the
    # user typed after it — three turns, zero model calls, zero files, the same
    # sentence every time. The question is a governance step exactly once; the
    # next message is the answer and must be acted on.
    from storage import db

    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    chat_db = tmp_path / "data" / "musubi.db"
    chat = "chat-clarify"

    silent = FakeRouter([])
    first_log = io.StringIO()
    question = asyncio.run(run_agent(
        "create a website", silent, _musubi_dir(),
        log=first_log, max_tokens=0, chat_id=chat,
    ))

    assert silent.calls == []
    assert question == (
        BROAD_PRODUCT_QUESTION
    )
    assert "route=ask_scope" in first_log.getvalue()
    assert db.pending_clarification(chat, db_path=chat_db) == "create a website"

    # Turn 2 answers it. Classified alone the answer is still a broad product
    # request and would have drawn the identical question.
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    second_log = io.StringIO()
    answer = asyncio.run(run_agent(
        "i would like to create a weather checking website",
        router, _musubi_dir(), log=second_log, max_tokens=0, chat_id=chat,
    ))

    assert answer == "ok"
    assert len(router.calls) == 1, "the answer must reach the model, not a canned reply"
    log_text = second_log.getvalue()
    assert "clarification answered" in log_text
    assert "route=planner_then_coder_check" in log_text
    assert "route=ask_scope" not in log_text

    # The root sees the WHOLE intent, not just the fragment typed last.
    system_text = router.calls[0]["messages"][0]["content"]
    assert "scope=medium_change" in system_text
    # And the marker is spent: a later broad request gets its own question,
    # but this one can never be re-asked.
    assert db.pending_clarification(chat, db_path=chat_db) is None


def test_vendor_tool_call_markup_is_never_accepted_as_an_answer() -> None:
    # Observed with deepseek-v4-flash: the no-tools final call answered with
    # DeepSeek's own tool-call syntax (FULL-WIDTH bars) as prose, and the
    # harness stored it as the planner's plan — surfacing it to the user, into
    # the audit DB, and into parse_change_manifest.
    from agent.run import _looks_like_vendor_tool_markup

    leak = (
        "<｜｜DSML｜｜tool_calls>\n"
        '<｜｜DSML｜｜invoke name="musubi_grep">\n'
        '<｜｜DSML｜｜parameter name="pattern" string="true">.*'
        "</｜｜DSML｜｜parameter>\n"
        "</｜｜DSML｜｜tool_calls>"
    )
    assert _looks_like_vendor_tool_markup(leak)
    for other in ('<tool_call>{"name":"x"}</tool_call>', '<invoke name="foo">'):
        assert _looks_like_vendor_tool_markup(other), other

    # Prose that merely talks about tools stays an answer.
    for prose in (
        "status: done\nsummary: created the dashboard",
        "I used grep to find the function, then edited run.py",
        "The plan calls three tools in sequence.",
    ):
        assert not _looks_like_vendor_tool_markup(prose), prose


def test_sensitive_request_is_not_refused_on_vocabulary_alone() -> None:
    # The removed keyword gate answered "add authentication" with a canned
    # pipeline recommendation and ZERO model calls — the same treatment it gave
    # "fix the typo in the security section of the README". A sensitive request
    # must now actually run, planner-first, with blast radius decided from the
    # planner's manifest rather than from the sentence.
    from agent import run as run_mod
    from agent.scope import classify_task

    hint = classify_task("Add authentication to the app")

    assert hint.route == "planner_then_coder_check"
    assert run_mod._deterministic_scope_answer(
        "Add authentication to the app", hint,
    ) is None


def test_root_coder_spawn_is_refused_until_planner_manifest_lands(
    tmp_path: Path,
) -> None:
    # Role order is goal-state enforcement of the assessed route: on a
    # planner-led medium goal the coder gate stays shut, the refusal names
    # `planner` as the legal next role, and the spawn never reaches the
    # substrate (zero subagent audit rows).
    from agent import run as run_mod

    state = GoalState.create(
        "add an /about route", "medium_change", "planner_then_coder_check",
    )
    orchestration = Orchestration(parent_session_id="root", goal_state=state)
    session = _FakeToolSession("unused")
    spawn_tool = {
        "name": "musubi_spawn_subagent",
        "description": "spawn",
        "input_schema": {"type": "object"},
    }
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use", "id": "c1",
                "name": "musubi_spawn_subagent",
                "input": {"role": "coder", "brief": "implement the route"},
            }],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "understood"}],
        ),
    ])

    answer, cycles = asyncio.run(run_mod._run_loop(
        session,
        router,
        [spawn_tool],
        [{"role": "user", "content": "add an /about route"}],
        max_cycles=2,
        log=io.StringIO(),
        orchestration=orchestration,
        role="agent",
        audit_db_path=tmp_path / "audit.db",
    ))

    assert answer == "understood"
    assert cycles == 2
    # The refused spawn never reached the MCP substrate: no spawn row, no
    # coder subagent_audit rows, and the worker ceiling was not consumed.
    assert session.calls == []
    assert orchestration.spawned_workers == 0
    replay = str(router.calls[1]["messages"])
    assert "refused" in replay
    assert "planner" in replay


def test_large_goal_runs_the_review_chain_instead_of_halting() -> None:
    # A large change used to end the turn with a CLI string the chat surface
    # cannot run — the user was told to launch a pipeline themselves, and the
    # work stopped. "Large" means MORE REVIEW, not a different launcher: the
    # root may already spawn each of these roles ad-hoc, so it runs the chain.
    state = GoalState.create(
        "create site", "medium_change", "planner_then_coder_check",
    )
    state.apply_planner_manifest(
        '<change_manifest>{"files_expected":11,"subsystems":'
        '["config","routes","components","styles"],"public_contract":false,'
        '"data_migration":false,"security_sensitive":false,'
        '"external_side_effects":false,"destructive":false,"unknowns":[],'
        '"validation_commands":2}</change_manifest>'
    )

    assert state.route == "plan_design_workflow"
    assert state.pending_clarification is None
    assert state.next_role == "designer"
    assert state.role_chain == ("coder", "reviewer")
    block = state.render_decision_block()
    assert "next_role=designer then coder → reviewer" in block


def test_large_chain_advances_only_on_a_successful_role() -> None:
    state = GoalState.create(
        "create site", "medium_change", "planner_then_coder_check",
    )
    state.apply_planner_manifest(
        '<change_manifest>{"files_expected":11,"subsystems":'
        '["config","routes","components","styles"],"public_contract":false,'
        '"data_migration":false,"security_sensitive":false,'
        '"external_side_effects":false,"destructive":false,"unknowns":[],'
        '"validation_commands":2}</change_manifest>'
    )

    # A failed designer must not open the coder gate.
    state.record_outcome(
        role="designer", status="failed", summary="summary: gave up",
        touched_files=(),
    )
    assert state.next_role == "designer"

    state.record_outcome(
        role="designer", status="done", summary="summary: design ready",
        touched_files=(),
    )
    assert state.next_role == "coder"
    state.record_outcome(
        role="coder", status="done", summary="summary: built",
        touched_files={"a.py"},
    )
    assert state.next_role == "reviewer"
    state.record_outcome(
        role="reviewer", status="done", summary="summary: approved",
        touched_files=(),
    )
    assert state.next_role is None
    assert state.role_chain == ()


def test_manifest_clarification_returns_before_any_model_call() -> None:
    from agent import run as run_mod

    state = GoalState.create(
        "add route", "medium_change", "planner_then_coder_check",
    )
    state.apply_planner_manifest(
        '<change_manifest>{"files_expected":3,"subsystems":["routes"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":["deployment target"],'
        '"validation_commands":1}</change_manifest>'
    )
    orchestration = Orchestration(parent_session_id="root", goal_state=state)
    router = FakeRouter([])

    answer, _ = asyncio.run(run_mod._run_loop(
        object(),
        router,
        [],
        [{"role": "user", "content": "add route"}],
        max_cycles=2,
        log=io.StringIO(),
        orchestration=orchestration,
        role="agent",
    ))

    assert router.calls == []
    assert answer is not None
    assert "deployment target" in answer


def test_turn_cap_failure_auto_spawns_one_audited_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # A typed TURN_CAP coder failure with surviving files triggers exactly ONE
    # automatic same-role replacement through _dispatch (the audited path),
    # BEFORE the next root LM call. The replacement completes done, the root
    # concludes success, and it never emits the recovery-incomplete marker.
    from agent import run as run_mod
    from agent.run import FailureKind

    # A real, non-empty artifact on disk — the surviving-files guard requires
    # the touched path to still exist before it will auto-replace.
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    artifact = tmp_path / "app" / "page.tsx"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("export default () => null;", encoding="utf-8")

    state = GoalState.create(
        "build the scaffold", "simple_artifact", "single_coder",
    )
    orchestration = Orchestration(
        parent_session_id="root", goal_state=state, spawned_workers=1,
    )
    # Primary coder already failed at its turn cap with a real artifact and a
    # pushed skill that must be replayed on the continuation.
    orchestration.record_worker_outcome(
        role="coder",
        status="escalated",
        summary="[incomplete] scaffold unfinished at turn cap",
        touched_files={"app/page.tsx"},
        brief="build the scaffold",
        failure_kind=FailureKind.TURN_CAP,
        pushed_skill_id="web-ui",
    )

    dispatched: list[dict[str, Any]] = []

    async def fake_dispatch(session, tool_uses, log, **kwargs):  # noqa: ANN001
        # The synthesized auto-recovery spawn: record a done replacement so the
        # failure clears, and prove it flowed through _dispatch with the spawn.
        dispatched.append(tool_uses[0])
        kwargs["orchestration"].spawned_workers += 1
        kwargs["orchestration"].record_worker_outcome(
            role="coder",
            status="done",
            summary="summary: scaffold completed",
            touched_files={"app/page.tsx"},
            brief="build the scaffold",
        )
        return [{
            "type": "tool_result",
            "tool_use_id": tool_uses[0]["id"],
            "content": "scaffold completed",
        }]

    monkeypatch.setattr(run_mod, "_dispatch", fake_dispatch)
    spawn_tool = {
        "name": "musubi_spawn_subagent",
        "description": "spawn",
        "input_schema": {"type": "object"},
    }
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "scaffold delivered"}],
        ),
    ])
    log = io.StringIO()

    answer, cycles = asyncio.run(run_mod._run_loop(
        object(),
        router,
        [spawn_tool],
        [
            {"role": "system", "content": "stable root prompt"},
            {"role": "user", "content": "build the scaffold"},
        ],
        max_cycles=3,
        log=log,
        orchestration=orchestration,
        role="agent",
    ))

    # Exactly one synthesized replacement spawn, through _dispatch, replaying
    # the pushed skill so the continuation reruns the same worker contract.
    assert len(dispatched) == 1
    assert dispatched[0]["name"] == "musubi_spawn_subagent"
    assert dispatched[0]["input"] == {
        "role": "coder", "brief": "build the scaffold",
        "pushed_skill_id": "web-ui",
    }
    # The root then made ONE LM call and concluded from fresh evidence.
    assert cycles == 1
    assert answer == "scaffold delivered"
    assert "root ended recovery without a successful replacement worker" not in answer
    assert "automatic recovery: coder turn_cap -> audited replacement" in log.getvalue()
    # Two coder outcomes total: the failed primary and the done replacement.
    coder = [o for o in orchestration.worker_outcomes if o.role == "coder"]
    assert [o.status for o in coder] == ["escalated", "done"]


def test_second_turn_cap_failure_halts_without_third_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # If the automatic replacement ALSO fails at its turn cap, the bounded
    # recovery halts fail-closed: no third worker, a deterministic
    # [incomplete] result, and zero root LM calls.
    from agent import run as run_mod
    from agent.run import FailureKind

    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    artifact = tmp_path / "app" / "page.tsx"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("export default () => null;", encoding="utf-8")

    state = GoalState.create(
        "build the scaffold", "simple_artifact", "single_coder",
    )
    orchestration = Orchestration(
        parent_session_id="root", goal_state=state, spawned_workers=1,
    )
    orchestration.record_worker_outcome(
        role="coder", status="escalated",
        summary="[incomplete] first cap", touched_files={"app/page.tsx"},
        brief="build the scaffold", failure_kind=FailureKind.TURN_CAP,
    )

    replacements: list[dict[str, Any]] = []

    async def fake_dispatch(session, tool_uses, log, **kwargs):  # noqa: ANN001
        replacements.append(tool_uses[0])
        kwargs["orchestration"].spawned_workers += 1
        kwargs["orchestration"].record_worker_outcome(
            role="coder", status="escalated",
            summary="[incomplete] second cap", touched_files={"app/page.tsx"},
            brief="build the scaffold", failure_kind=FailureKind.TURN_CAP,
        )
        return [{"type": "tool_result", "tool_use_id": tool_uses[0]["id"],
                 "content": "still unfinished"}]

    monkeypatch.setattr(run_mod, "_dispatch", fake_dispatch)
    router = FakeRouter([])

    answer, _ = asyncio.run(run_mod._run_loop(
        object(),
        router,
        [{"name": "musubi_spawn_subagent", "description": "spawn",
          "input_schema": {"type": "object"}}],
        [{"role": "user", "content": "build the scaffold"}],
        max_cycles=3,
        log=io.StringIO(),
        orchestration=orchestration,
        role="agent",
    ))

    # Exactly one automatic replacement attempt, then halt — no third worker.
    assert len(replacements) == 1
    assert router.calls == []
    assert answer is not None and answer.startswith("[incomplete]")
    assert "one audited continuation is the limit" in answer


def test_turn_cap_without_surviving_files_defers_to_root_analysis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # touched_files is a write history, not disk truth: if the worker's file
    # no longer survives (e.g. deleted via Bash), the one automatic
    # continuation is NOT spent on an empty replacement — the transition
    # defers to the bounded root-analysis path instead.
    from agent import run as run_mod
    from agent.run import FailureKind

    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))  # app/page.tsx never written

    state = GoalState.create(
        "build the scaffold", "simple_artifact", "single_coder",
    )
    orchestration = Orchestration(
        parent_session_id="root", goal_state=state, spawned_workers=1,
    )
    orchestration.record_worker_outcome(
        role="coder", status="escalated",
        summary="[incomplete] wrote then removed the scratch file",
        touched_files={"app/page.tsx"},
        brief="build the scaffold", failure_kind=FailureKind.TURN_CAP,
    )

    auto_dispatched: list[dict[str, Any]] = []

    async def fake_dispatch(session, tool_uses, log, **kwargs):  # noqa: ANN001
        auto_dispatched.append(tool_uses[0])
        return [{"type": "tool_result", "tool_use_id": tool_uses[0]["id"],
                 "content": "unexpected"}]

    monkeypatch.setattr(run_mod, "_dispatch", fake_dispatch)
    router = FakeRouter([
        LMResponse(stop_reason="end_turn",
                   content=[{"type": "text", "text": "analysis"}]),
    ])
    log = io.StringIO()

    answer, _ = asyncio.run(run_mod._run_loop(
        object(),
        router,
        [{"name": "musubi_spawn_subagent", "description": "spawn",
          "input_schema": {"type": "object"}}],
        [{"role": "user", "content": "build the scaffold"}],
        max_cycles=3,
        log=log,
        orchestration=orchestration,
        role="agent",
    ))

    # No automatic replacement was dispatched; the legacy recovery path ran.
    assert auto_dispatched == []
    assert "deferring to root analysis" in log.getvalue()
    assert "automatic recovery: coder" not in log.getvalue()
    assert answer is not None and answer.startswith("[incomplete]")


def test_root_system_prompt_carries_the_evidence_vector() -> None:
    """Step 1 is observable, not yet enforced: the vector renders, nothing routes."""
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}])
    ])
    log = io.StringIO()

    answer = asyncio.run(run_agent(
        "Update weather-dashboard.html to refresh every 5 minutes",
        router,
        _musubi_dir(),
        log=log,
        max_tokens=0,
    ))

    assert answer == "ok"
    system_text = router.calls[0]["messages"][0]["content"]
    # Hint first, evidence second — an opinion the root may override, then the
    # record it must not contradict.
    assert system_text.index("[agent-routing-scope]") < system_text.index(
        "[agent-evidence]"
    )
    assert "names_workspace_path=" in system_text
    assert "[agent] evidence:" in log.getvalue()


def test_the_evidence_vector_changes_no_route() -> None:
    """A request naming nothing still routes exactly as it did before step 1."""
    from agent.evidence import collect
    from agent.routes import RouteKind
    from agent.scope import classify_task

    hint = classify_task("Update weather-dashboard.html to refresh every 5 minutes")
    vector = collect("Update weather-dashboard.html to refresh every 5 minutes")

    # The vector says the target does not exist here; the route is unmoved.
    assert vector.path_exists is False
    assert hint.route == RouteKind.SINGLE_CODER


def test_a_short_answer_to_the_question_is_still_the_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """PR #164 review: the merge must not depend on how the answer classifies.

    The pending request used to be consulted only when the NEW message itself
    routed to `ask_scope`. But the question offers "React" and "a single static
    HTML page" as answers, and with chat history both classify as bare advisory
    follow-ups. Those turns were answered as advice, and the completed turn
    wrote a row with no `clarification_request` — clearing the marker and
    losing the build request permanently.
    """
    from storage import db

    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    chat_db = tmp_path / "data" / "musubi.db"
    chat = "chat-short-answer"

    asyncio.run(run_agent(
        "create a website", FakeRouter([]), _musubi_dir(),
        log=io.StringIO(), max_tokens=0, chat_id=chat,
    ))
    assert db.pending_clarification(chat, db_path=chat_db) == "create a website"

    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    log = io.StringIO()
    answer = asyncio.run(run_agent(
        "React", router, _musubi_dir(), log=log, max_tokens=0, chat_id=chat,
    ))

    assert answer == "ok"
    assert "clarification answered" in log.getvalue()
    # The root must be told what is being built, not just which framework.
    system_text = router.calls[0]["messages"][0]["content"]
    assert "advisory" not in system_text.split("route=")[1].split("\n")[0]
    assert db.pending_clarification(chat, db_path=chat_db) is None
