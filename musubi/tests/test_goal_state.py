"""Compact root goal state and terminal worker feedback projection."""

from __future__ import annotations

from agent.goal_state import GoalState, OutcomePacket, root_decision_tools
from agent.scope import classify_task


def test_deferred_unknowns_reach_the_worker_instead_of_halting() -> None:
    state = GoalState.create(
        "a simple front end page", "medium_change", "planner_then_coder_check",
    )
    assessment = state.apply_planner_manifest(
        '<change_manifest>{"files_expected":1,"subsystems":["markup"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":["color palette"],'
        '"validation_commands":1}</change_manifest>'
    )

    assert assessment.route == "single_coder"
    assert state.pending_clarification is None  # no halt
    assert state.next_role == "coder"
    assert state.deferred_unknowns == ("color palette",)
    block = state.render_decision_block()
    assert "choose_sensible_defaults=color palette" in block
    assert "do not ask the user" in block.lower()


def test_decision_block_surfaces_conversation_cost_and_stall() -> None:
    # The per-turn budget resets on every chat message, so without these
    # numbers the root cannot tell turn six from turn one.
    state = GoalState.create(
        "build the page", "medium_change", "planner_then_coder_check",
    )
    state.chat_turns = 6
    state.chat_tokens = 109_494
    state.chat_barren_turns = 3

    block = state.render_decision_block()
    assert "conversation_usage=turns:6,tokens:109494,turns_without_a_file:3" in block
    assert "conversation_warning=" in block
    assert "do not spawn another planner" in block.lower()


def test_decision_block_omits_conversation_lines_on_a_fresh_chat() -> None:
    state = GoalState.create(
        "build the page", "medium_change", "planner_then_coder_check",
    )

    block = state.render_decision_block()
    assert "conversation_usage=" not in block
    assert "conversation_warning=" not in block


def test_advisory_root_surface_offers_no_tools() -> None:
    # An advisory turn is answered by the root itself. Withholding the whole
    # catalog is what keeps it to one cycle: no spawn, and no
    # `musubi_recommend_skills` round trip either.
    tools = [
        {"name": name}
        for name in (
            "musubi_spawn_subagent",
            "musubi_recommend_skills",
            "musubi_get_skill",
            "musubi_get_reference",
        )
    ]
    state = GoalState.create("explain each", "advisory", "advisory")

    assert state.next_role is None
    assert root_decision_tools(tools, state) == []
    # Defensive: not even a recovery phase may hand an advisory turn a tool.
    assert root_decision_tools(tools, state, recovery_outcome=True) == []


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


def test_spawn_exhausted_root_surface_offers_no_tools() -> None:
    # Once the worker ceiling is spent, offering the spawn tool only lets the
    # root burn cycles on refused spawns; withhold every tool so it must
    # conclude from the evidence it has.
    tools = [
        {"name": name}
        for name in (
            "musubi_spawn_subagent",
            "musubi_recommend_skills",
            "musubi_get_skill",
        )
    ]
    state = GoalState.create(
        "add authentication", "medium_change", "planner_then_coder_check",
    )

    assert root_decision_tools(tools, state, spawn_exhausted=True) == []


def test_active_failure_recovery_outranks_spawn_exhaustion() -> None:
    # A pending worker failure keeps the full analysis surface even when the
    # worker ceiling is spent — the ceiling-driven halt is handled separately by
    # the loop's recovery path, not by starving the root of tools mid-analysis.
    tools = [
        {"name": "musubi_read_file"},
        {"name": "musubi_spawn_subagent"},
    ]
    state = GoalState.create(
        "create dashboard", "simple_artifact", "single_coder",
    )

    assert root_decision_tools(
        tools, state, recovery_outcome=True, spawn_exhausted=True,
    ) == tools


# ── planner manifest reclassification ────────────────────────────────────────

ELEVEN_FILE_MANIFEST = (
    '<change_manifest>{"files_expected":11,"subsystems":'
    '["config","routes","components","styles"],"public_contract":false,'
    '"data_migration":false,"security_sensitive":false,'
    '"external_side_effects":false,"destructive":false,"unknowns":[],'
    '"validation_commands":2}</change_manifest>'
)


def test_medium_goal_requires_planner_before_coder() -> None:
    state = GoalState.create("add route", "medium_change", "planner_then_coder_check")
    assert state.next_role == "planner"


def test_simple_goal_has_no_role_order_constraint() -> None:
    state = GoalState.create("create page", "simple_artifact", "single_coder")
    assert state.next_role is None


def test_goal_state_retains_initial_request_assessment() -> None:
    hint = classify_task("Add authentication to the app")
    state = GoalState.create(
        "Add authentication to the app",
        hint.kind.value,
        hint.route,
        assessment=hint.assessment,
    )

    assert state.assessment is hint.assessment
    assert state.route == "plan_design_workflow"



def test_eleven_file_manifest_reclassifies_goal_as_large() -> None:
    state = GoalState.create("create site", "medium_change", "planner_then_coder_check")
    state.apply_planner_manifest(ELEVEN_FILE_MANIFEST)
    assert state.scope == "large_feature"
    assert state.route == "plan_design_workflow"
    assert state.next_role is None


def test_small_manifest_opens_the_coder_gate() -> None:
    state = GoalState.create("add route", "medium_change", "planner_then_coder_check")
    state.apply_planner_manifest(
        'status: done\n'
        '<change_manifest>{"files_expected":1,"subsystems":["routes"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":[],"validation_commands":1}'
        '</change_manifest>'
    )
    assert state.next_role == "coder"
    assert state.pending_clarification is None
    block = state.render_decision_block()
    assert "next_role=coder" in block
    assert "assessment=" in block


def test_missing_manifest_fails_closed_to_clarification() -> None:
    state = GoalState.create("add route", "medium_change", "planner_then_coder_check")
    state.apply_planner_manifest("status: done\nsummary: did some planning")
    assert state.route == "ask_scope"
    assert state.next_role is None
    assert state.pending_clarification is not None
    assert "change manifest" in state.pending_clarification


def test_manifest_unknowns_set_pending_clarification() -> None:
    state = GoalState.create("add route", "medium_change", "planner_then_coder_check")
    state.apply_planner_manifest(
        '<change_manifest>{"files_expected":3,"subsystems":["routes"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":["deployment target"],'
        '"validation_commands":1}</change_manifest>'
    )
    assert state.route == "ask_scope"
    assert state.pending_clarification is not None
    assert "deployment target" in state.pending_clarification


# ── typed recovery decisions ─────────────────────────────────────────────────

from agent.run import FailureKind, RecoveryAction, WorkerOutcome, decide_recovery  # noqa: E402


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


def test_budget_and_policy_failures_halt_fail_closed() -> None:
    for kind in (FailureKind.BUDGET, FailureKind.POLICY):
        outcome = WorkerOutcome(
            role="coder", status="escalated", summary="stopped",
            touched_files=("app/page.tsx",), brief="b", failure_kind=kind,
        )
        assert decide_recovery(
            outcome, same_role_failures=1, worker_slots=1,
        ) is RecoveryAction.HALT


def test_blocked_failure_routes_to_root_analysis() -> None:
    outcome = WorkerOutcome(
        role="coder", status="escalated", summary="[blocked] too large",
        touched_files=("app/page.tsx",), brief="b",
        failure_kind=FailureKind.BLOCKED,
    )
    assert decide_recovery(
        outcome, same_role_failures=1, worker_slots=1,
    ) is RecoveryAction.ROOT_ANALYZE
