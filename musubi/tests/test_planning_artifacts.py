"""Planner two-file artifact parsing, persistence, and orchestration wiring."""

from __future__ import annotations

import json

from agent.planning_artifacts import (
    MAX_PLAN_BYTES,
    goal_artifact_key,
    parse_planning_artifacts,
    persist_planning_contract,
    persist_planning_artifacts,
)


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


def test_root_contract_persists_compact_manifest_with_defaults(tmp_path) -> None:
    persisted = persist_planning_contract(
        plan_markdown="# Plan\n\nImplement it.",
        manifest_object={
            "files_expected": 2,
            "subsystems": ["agent"],
        },
        target_dir=tmp_path / "goal",
    )
    assert persisted is not None
    (plan_path, manifest_path), artifacts = persisted

    assert plan_path.read_text(encoding="utf-8") == "# Plan\n\nImplement it.\n"
    assert artifacts.manifest.files_expected == 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["files_expected"] == 2
    assert manifest["security_sensitive"] is False
    assert manifest["blocking_decisions"] == []


def test_root_contract_rejects_missing_plan_before_writing(tmp_path) -> None:
    persisted = persist_planning_contract(
        plan_markdown="  ",
        manifest_object={
            "files_expected": 2,
            "subsystems": ["agent"],
        },
        target_dir=tmp_path / "goal",
    )
    assert persisted is None
    assert not (tmp_path / "goal").exists()
