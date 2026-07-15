"""Compact root goal state and terminal worker feedback projection."""

from __future__ import annotations

from agent.goal_state import GoalState, OutcomePacket, root_decision_tools


def test_outcome_packet_projects_worker_contract() -> None:
    packet = OutcomePacket.from_worker(
        role="coder",
        status="done",
        summary=(
            "status: done\n"
            "summary: created dashboard\n"
            "verification: valid HTML\n"
            "remaining_gap: none"
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
        role="planner",
        status="done",
        summary="x" * 5000,
        touched_files=(),
    )

    assert len(packet.summary) <= 800
    assert packet.summary.endswith("… [truncated]")


def test_goal_state_keeps_exact_intent_and_root_only_usage() -> None:
    state = GoalState.create(
        "create NYC dashboard", "simple_artifact", "single_coder",
    )
    state.record_root_usage(tokens_in=1200, tokens_out=100)
    state.record_outcome(
        role="coder",
        status="done",
        summary="summary: ready",
        touched_files={"nyc.html"},
    )

    block = state.render_decision_block()

    assert "intent=create NYC dashboard" in block
    assert "root_usage=calls:1,input:1200,output:100,target:3000" in block
    assert "coder (done)" in block


def test_simple_root_surface_is_spawn_only() -> None:
    tools = [
        {"name": name}
        for name in (
            "musubi_spawn_subagent",
            "musubi_read_file",
            "musubi_get_skill",
        )
    ]
    state = GoalState.create(
        "create dashboard", "simple_artifact", "single_coder",
    )

    assert [tool["name"] for tool in root_decision_tools(tools, state)] == [
        "musubi_spawn_subagent",
    ]


def test_non_simple_root_surface_keeps_spawn_and_skill_tools() -> None:
    tools = [
        {"name": name}
        for name in (
            "musubi_read_file",
            "musubi_spawn_subagent",
            "musubi_recommend_skills",
            "musubi_get_skill",
            "musubi_get_reference",
        )
    ]
    state = GoalState.create(
        "add authentication", "medium_change", "planner_then_coder_check",
    )

    assert [tool["name"] for tool in root_decision_tools(tools, state)] == [
        "musubi_spawn_subagent",
        "musubi_recommend_skills",
        "musubi_get_skill",
        "musubi_get_reference",
    ]


def test_recovery_analysis_preserves_existing_tools_until_decision_only() -> None:
    tools = [
        {"name": "musubi_read_file"},
        {"name": "musubi_spawn_subagent"},
    ]
    state = GoalState.create(
        "create dashboard", "simple_artifact", "single_coder",
    )

    assert root_decision_tools(tools, state, recovery_outcome=True) == tools
    assert root_decision_tools(
        tools, state, recovery_outcome=True, decision_only=True,
    ) == [{"name": "musubi_spawn_subagent"}]
