"""Scope-aware routing hints for the standalone root agent."""

from __future__ import annotations

from agent.scope import ScopeKind, classify_task


def test_classifies_known_file_edit_as_simple_edit() -> None:
    hint = classify_task("Update weather-dashboard.html to refresh every 5 minutes")

    assert hint.kind is ScopeKind.SIMPLE_EDIT
    assert hint.route == "single_coder"
    assert hint.max_workers == 1
    assert "known file" in hint.reason
    assert "hard cumulative root-run ceiling" in hint.prompt_block()


def test_classifies_small_artifact_as_simple_artifact_without_html_special_case() -> None:
    hint = classify_task("Create a compact CSV report showing current sales")

    assert hint.kind is ScopeKind.SIMPLE_ARTIFACT
    assert hint.route == "single_coder"
    assert hint.max_workers == 1
    assert "artifact" in hint.reason


def test_large_risky_feature_requires_plan_design_workflow() -> None:
    hint = classify_task("Add billing auth, database migration, and public API endpoints")

    assert hint.kind is ScopeKind.LARGE_FEATURE
    assert hint.route == "plan_design_workflow"
    assert "plan" in hint.requires
    assert "design" in hint.requires


def test_medium_change_routes_through_planner_before_coder() -> None:
    hint = classify_task("Improve the dashboard weather display")

    assert hint.kind is ScopeKind.MEDIUM_CHANGE
    assert hint.route == "planner_then_coder_check"
    assert hint.max_workers == 2
    assert "plan" in hint.requires
    assert "implementation" in hint.requires
    assert "verification" in hint.requires


def test_vague_request_asks_scope_before_spawning() -> None:
    hint = classify_task("fix this")

    assert hint.kind is ScopeKind.UNKNOWN
    assert hint.route == "ask_scope"
    assert hint.max_workers == 0


def test_greeting_routes_to_direct_answer_without_workers() -> None:
    hint = classify_task("hi")

    assert hint.kind is ScopeKind.UNKNOWN
    assert hint.route == "direct_answer"
    assert hint.max_workers == 0


def test_delete_file_request_routes_to_manual_destructive_answer() -> None:
    hint = classify_task("delete all *-dashboard.html files")

    assert hint.kind is ScopeKind.UNKNOWN
    assert hint.route == "manual_destructive"
    assert hint.max_workers == 0
