"""Named MCP tool surfaces for model-visible catalogs.

musubi-tier: substrate
expires-when: never - tool-surface shaping is the LM boundary contract.
"""

from __future__ import annotations

from typing import Any, Literal

ToolSurface = Literal["agent", "operator", "pipeline", "full"]

ROOT_AGENT_TOOL_NAMES: frozenset[str] = frozenset({
    "musubi_read_file",
    "musubi_glob",
    "musubi_grep",
    "musubi_list_skills",
    "musubi_begin_direct",
    "musubi_begin_plan",
    "musubi_commit_plan",
    "musubi_get_skill",
    "musubi_get_reference",
    "musubi_compress",
    "musubi_retrieve",
    "musubi_compression_stats",
    "musubi_get_memory_context",
    "musubi_get_memory_entry",
    "musubi_query_sessions",
    "musubi_spawn_subagent",
    # NOTE: musubi_spawn_pipeline is intentionally NOT here. The root agent
    # spawns bounded workers, not whole pipelines — "spawning an entire
    # pipeline is reserved for user-invoked commands" (policy_engine.py locked
    # decision #4). Leaving it out stops the driver from auto-summoning a
    # pipeline for tasks the user never asked to run as one. Pipelines run
    # deterministically via the CLI `agent --pipeline <name>`; the tool stays
    # registered and reachable in the "full" surface.
    "musubi_list_subagents",
})

OPERATOR_TOOL_NAMES: frozenset[str] = ROOT_AGENT_TOOL_NAMES | frozenset({
    "musubi_get_active_session",
    "musubi_get_status",
    "musubi_get_pause_state",
    "musubi_query_pipeline_runs",
    "musubi_query_stage_metrics",
    "musubi_query_agent_cycles",
    "musubi_query_agent_turns",
    "musubi_pipeline_stats",
    "musubi_query_schema_migrations",
    "musubi_query_subagent_events",
    "musubi_list_subagent_spawns",
})

PIPELINE_TOOL_NAMES: frozenset[str] = OPERATOR_TOOL_NAMES | frozenset({
    "musubi_new_session",
    "musubi_read_stage",
    "musubi_write_stage",
    "musubi_increment_attempt",
    "musubi_pause_session",
    "musubi_resume_session",
    "musubi_compute_chunks",
    "musubi_ensure_chunk_row",
    "musubi_consume_pending_action",
    "musubi_get_correction_rules",
    "musubi_get_injected_skills",
    "musubi_get_pipeline_stages",
    "musubi_record_stage_metric",
    "musubi_finalize_pipeline_run",
})

_SURFACES: dict[str, frozenset[str] | None] = {
    "agent": ROOT_AGENT_TOOL_NAMES,
    "operator": OPERATOR_TOOL_NAMES,
    "pipeline": PIPELINE_TOOL_NAMES,
    "full": None,
}


def tool_names_for_surface(surface: str) -> frozenset[str] | None:
    key = (surface or "").strip().lower()
    if key not in _SURFACES:
        raise ValueError(
            f"unknown tool surface {surface!r}; expected one of "
            f"{sorted(_SURFACES)}"
        )
    return _SURFACES[key]


def filter_tool_catalog(
    tools: list[dict[str, Any]],
    surface: str,
) -> list[dict[str, Any]]:
    allowed = tool_names_for_surface(surface)
    if allowed is None:
        return list(tools)
    return [tool for tool in tools if tool.get("name") in allowed]


def apply_fastmcp_tool_surface(mcp: Any, surface: str) -> None:
    allowed = tool_names_for_surface(surface)
    if allowed is None:
        return
    manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(manager, "_tools", None)
    if not isinstance(tools, dict):
        raise RuntimeError("FastMCP tool manager shape changed; cannot filter tools")
    manager._tools = {name: tool for name, tool in tools.items() if name in allowed}
