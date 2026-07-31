"""Planner two-file artifact parsing, persistence, and orchestration wiring."""

from __future__ import annotations

import json

from agent.goal_state import GoalState
from agent.planning_artifacts import (
    MAX_PLAN_BYTES,
    goal_artifact_key,
    parse_planning_artifacts,
    persist_planning_artifacts,
)
from agent.run import Orchestration


def _planner_output(*, plan: str = "# Plan\n\nImplement it.") -> str:
    return (
        "status: done\n"
        "summary: bounded implementation plan\n"
        "verification: inspected target files\n"
        "remaining_gap: none\n"
        f"<plan>{plan}</plan>\n"
        '<change_manifest>{"files_expected":2,'
        '"subsystems":["agent"],"public_contract":false,'
        '"data_migration":false,"security_sensitive":false,'
        '"external_side_effects":false,"destructive":false,'
        '"blocking_decisions":[],"validation_commands":2}'
        "</change_manifest>"
    )


def test_parse_requires_one_nonempty_plan_and_valid_manifest() -> None:
    artifacts = parse_planning_artifacts(_planner_output())
    assert artifacts is not None
    assert artifacts.plan_markdown.startswith("# Plan")
    assert artifacts.manifest.files_expected == 2

    assert parse_planning_artifacts(
        _planner_output().replace("<plan>", "").replace("</plan>", "")
    ) is None
    assert parse_planning_artifacts(_planner_output(plan="   ")) is None
    assert parse_planning_artifacts(
        _planner_output() + "\n<plan>second</plan>"
    ) is None


def test_plan_byte_limit_is_enforced() -> None:
    assert parse_planning_artifacts(
        _planner_output(plan="x" * (MAX_PLAN_BYTES + 1))
    ) is None


def test_persist_writes_separate_human_and_machine_files(tmp_path) -> None:
    paths = persist_planning_artifacts(_planner_output(), tmp_path / "goal")
    assert paths is not None
    plan_path, manifest_path = paths

    assert plan_path.read_text(encoding="utf-8") == "# Plan\n\nImplement it.\n"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["files_expected"] == 2
    assert manifest["blocking_decisions"] == []
    assert "<change_manifest>" not in plan_path.read_text(encoding="utf-8")


def test_goal_key_is_stable_across_conversation_turns() -> None:
    assert goal_artifact_key("chat-1", "session-a") == goal_artifact_key(
        "chat-1", "session-b",
    )
    assert goal_artifact_key(None, "session-a") != goal_artifact_key(
        None, "session-b",
    )


def test_planner_outcome_persists_files_without_marking_delivery(tmp_path) -> None:
    state = GoalState.create(
        "build the app", "medium_change", "planner_then_coder_check",
    )
    orchestration = Orchestration(
        parent_session_id="root",
        goal_state=state,
        planning_artifact_dir=tmp_path / "goal",
    )

    orchestration.record_worker_outcome(
        role="planner",
        status="done",
        summary=_planner_output(),
        touched_files=(),
    )

    assert (tmp_path / "goal" / "plan.md").is_file()
    assert (tmp_path / "goal" / "manifest.json").is_file()
    assert state.next_role == "coder"
    assert len(state.planning_artifacts) == 2
    assert orchestration.delivered_artifact is False


def test_missing_plan_fails_closed_before_coder(tmp_path) -> None:
    state = GoalState.create(
        "build the app", "medium_change", "planner_then_coder_check",
    )
    orchestration = Orchestration(
        parent_session_id="root",
        goal_state=state,
        planning_artifact_dir=tmp_path / "goal",
    )
    manifest_only = _planner_output().replace(
        "<plan># Plan\n\nImplement it.</plan>\n",
        "",
    )

    orchestration.record_worker_outcome(
        role="planner",
        status="done",
        summary=manifest_only,
        touched_files=(),
    )

    assert state.route == "ask_scope"
    assert state.next_role is None
    assert state.pending_clarification is not None
    assert not (tmp_path / "goal").exists()
