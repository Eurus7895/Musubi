from __future__ import annotations

from types import SimpleNamespace

import pytest

from tool_surface import (
    ROOT_AGENT_TOOL_NAMES,
    apply_fastmcp_tool_surface,
    filter_tool_catalog,
    tool_names_for_surface,
)


def _tool(name: str) -> dict:
    return {"name": name, "description": "", "input_schema": {}}


def test_agent_surface_has_expected_count_and_core_tools() -> None:
    assert len(ROOT_AGENT_TOOL_NAMES) == 16
    assert "musubi_read_file" in ROOT_AGENT_TOOL_NAMES
    # Read-only discovery tools let the agent find files instead of guessing.
    assert "musubi_glob" in ROOT_AGENT_TOOL_NAMES
    assert "musubi_grep" in ROOT_AGENT_TOOL_NAMES
    assert "musubi_list_skills" in ROOT_AGENT_TOOL_NAMES
    assert "musubi_retrieve" in ROOT_AGENT_TOOL_NAMES
    assert "musubi_spawn_subagent" in ROOT_AGENT_TOOL_NAMES
    assert "musubi_begin_direct" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_begin_plan" in ROOT_AGENT_TOOL_NAMES
    assert "musubi_commit_plan" in ROOT_AGENT_TOOL_NAMES


def test_agent_surface_excludes_whole_pipeline_spawn() -> None:
    # The root agent spawns bounded workers, not whole pipelines — auto-summoning
    # a pipeline is reserved for user-invoked commands / the CLI --pipeline flag.
    assert "musubi_spawn_pipeline" not in ROOT_AGENT_TOOL_NAMES


def test_agent_surface_excludes_mutating_tools() -> None:
    assert "musubi_write_file" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_append_file" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_edit_file" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_run_command" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_run_lint" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_run_typecheck" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_run_tests" not in ROOT_AGENT_TOOL_NAMES


def test_agent_surface_excludes_driver_and_pipeline_internals() -> None:
    assert "musubi_write_stage" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_read_stage" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_get_subagent_context" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_record_agent_cycle" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_complete_subagent" not in ROOT_AGENT_TOOL_NAMES


def test_agent_surface_excludes_removed_credit_tools() -> None:
    assert "musubi_session_credits" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_credits_since" not in ROOT_AGENT_TOOL_NAMES


def test_full_surface_returns_none_meaning_unfiltered() -> None:
    assert tool_names_for_surface("full") is None


def test_unknown_surface_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown tool surface"):
        tool_names_for_surface("ghost")


def test_filter_tool_catalog_preserves_order_and_filters_local_names() -> None:
    tools = [
        _tool("musubi_write_stage"),
        _tool("musubi_read_file"),
        _tool("musubi_list_skills"),
    ]

    assert [t["name"] for t in filter_tool_catalog(tools, "agent")] == [
        "musubi_read_file",
        "musubi_list_skills",
    ]


def test_filter_tool_catalog_full_returns_copy_of_all_tools() -> None:
    tools = [_tool("musubi_write_stage"), _tool("musubi_read_file")]

    out = filter_tool_catalog(tools, "full")

    assert out == tools
    assert out is not tools


def test_apply_fastmcp_tool_surface_filters_tool_manager() -> None:
    manager = SimpleNamespace(_tools={
        "musubi_write_stage": object(),
        "musubi_read_file": object(),
        "musubi_list_skills": object(),
    })
    mcp = SimpleNamespace(_tool_manager=manager)

    apply_fastmcp_tool_surface(mcp, "agent")

    assert set(manager._tools) == {
        "musubi_read_file",
        "musubi_list_skills",
    }
