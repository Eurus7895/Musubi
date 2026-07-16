"""Scope-aware routing hints for the standalone root agent."""

from __future__ import annotations

from agent.scope import ScopeKind, classify_task


def test_reach_to_path_routes_to_read_only_single_explorer() -> None:
    hint = classify_task(
        r"could you reach to C:\Workspace\09_CD_Team\21_A2lPatcher\a2l-patcher-stla"
    )

    assert hint.kind is ScopeKind.INSPECT
    assert hint.route == "single_explorer"
    assert "read-only" in hint.reason
    block = hint.prompt_block().lower()
    assert "explorer" in block and "do not spawn a planner or coder" in block


def test_read_file_is_inspection_not_a_change() -> None:
    # The filename `run.py` embeds the mutation verb "run"; it must not be read
    # as intent to change anything.
    hint = classify_task("read run.py")

    assert hint.kind is ScopeKind.INSPECT
    assert hint.route == "single_explorer"


def test_list_and_show_directory_requests_are_inspection() -> None:
    for task in (
        "open the src folder",
        "show me what is in ./musubi/agent",
        "list the files in the tests directory",
        "look at the auth module",
    ):
        hint = classify_task(task)
        assert hint.kind is ScopeKind.INSPECT, task
        assert hint.route == "single_explorer", task


def test_explicit_edit_is_not_intercepted_by_inspection_route() -> None:
    # A read-only verb next to a mutation verb ("find and replace ... in run.py")
    # stays a change, not an inspection.
    hint = classify_task("find and replace TODO in run.py")

    assert hint.kind is ScopeKind.SIMPLE_EDIT
    assert hint.route == "single_coder"


def test_bare_intent_without_a_path_is_not_inspection() -> None:
    # No concrete path/dir target, so "open a PR" must not route to an explorer.
    hint = classify_task("open a PR")

    assert hint.kind is not ScopeKind.INSPECT


def test_classifies_known_file_edit_as_simple_edit() -> None:
    hint = classify_task("Update weather-dashboard.html to refresh every 5 minutes")

    assert hint.kind is ScopeKind.SIMPLE_EDIT
    assert hint.route == "single_coder"
    assert not hasattr(hint, "max_workers")
    assert "known file" in hint.reason
    assert "initial routing recommendation" in hint.prompt_block()


def test_classifies_small_artifact_as_simple_artifact_without_html_special_case() -> None:
    hint = classify_task("Create a compact CSV report showing current sales")

    assert hint.kind is ScopeKind.SIMPLE_ARTIFACT
    assert hint.route == "single_coder"
    assert not hasattr(hint, "max_workers")
    assert "start with one coder" in hint.prompt_block().lower()
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
    assert not hasattr(hint, "max_workers")
    assert "plan" in hint.requires
    assert "implementation" in hint.requires
    assert "verification" in hint.requires


def test_vague_request_asks_scope_before_spawning() -> None:
    hint = classify_task("fix this")

    assert hint.kind is ScopeKind.UNKNOWN
    assert hint.route == "ask_scope"
    assert not hasattr(hint, "max_workers")


def test_greeting_routes_to_direct_answer_without_workers() -> None:
    hint = classify_task("hi")

    assert hint.kind is ScopeKind.UNKNOWN
    assert hint.route == "direct_answer"
    assert not hasattr(hint, "max_workers")


def test_delete_file_request_routes_to_manual_destructive_answer() -> None:
    hint = classify_task("delete all *-dashboard.html files")

    assert hint.kind is ScopeKind.UNKNOWN
    assert hint.route == "manual_destructive"
    assert not hasattr(hint, "max_workers")
