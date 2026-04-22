"""Policy engine — maps (pipeline, agent) → allowed tools.

Used by scripts/pre_tool_use.py. Kept as a plain dict so that hooks
executed from the command line (not just from Python) can import it
cheaply without pulling in the harness core.

Rule: a tool is allowed only if it appears in the agent's ALLOWED list.
Any tool not in the list is denied — explicit allowlists > denylists.

Back-compat: agents that have an entry in the per-pipeline table
override the defaults; agents unknown to the engine are denied all
tools (fail-closed).
"""

from __future__ import annotations

PIPELINE_POLICIES: dict[str, dict[str, list[str]]] = {
    "feature-dev": {
        "planner":  ["Read", "View", "Grep", "Glob"],
        "designer": ["Read", "View", "Grep", "Glob"],
        "coder":    ["Read", "View", "Grep", "Glob", "Write", "Edit", "Bash"],
        "reviewer": ["Read", "View", "Grep", "Glob"],
    },
}


def check_tool_allowed(pipeline: str, agent: str, tool: str) -> bool:
    """Return True if the tool call is permitted, False otherwise.

    Unknown pipeline or agent → deny (fail-closed).
    """
    pipeline_rules = PIPELINE_POLICIES.get(pipeline)
    if pipeline_rules is None:
        return False
    allowed = pipeline_rules.get(agent.lower())
    if allowed is None:
        return False
    return tool in allowed


def deny_reason(pipeline: str, agent: str, tool: str) -> str:
    """Return a human-readable reason for a deny decision."""
    if pipeline not in PIPELINE_POLICIES:
        return f"Unknown pipeline: {pipeline!r}"
    if agent.lower() not in PIPELINE_POLICIES[pipeline]:
        return f"Agent {agent!r} has no policy entry in pipeline {pipeline!r}"
    allowed = PIPELINE_POLICIES[pipeline][agent.lower()]
    return (
        f"Tool {tool!r} is not permitted for agent {agent!r} in pipeline "
        f"{pipeline!r}. Allowed: {allowed}"
    )
