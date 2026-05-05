"""Policy engine — maps (pipeline, agent) → allowed tools, plus the
sub-agent spawn allow-lists used by Phase A.

Used by scripts/pre_tool_use.py and copilot-harness/server.py. Kept as
plain dicts so hooks executed from the command line (not just from
Python) can import it cheaply without pulling in the harness core.

Rules:
  - A tool is allowed only if it appears in the agent's ALLOWED list.
    Any tool not in the list is denied — explicit allowlists > denylists.
  - A main agent may spawn a sub-agent role only if the role appears in
    `MAIN_SUBAGENT_ALLOWLIST[main]`.
  - The sub-agent's effective tools are
    `SUBAGENT_POLICIES[role] ∩ main's tool allow-list`. Unknown role
    → deny. Unknown main → deny.

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


# ── Sub-agent policies (Phase A) ───────────────────────────────────────────
#
# SUBAGENT_POLICIES — per-role tool allow-list. The role files live under
# `.github/agents/{explorer,investigator,reviewer-aux}.agent.md` (Phase A.3).
# Defining the policy here ahead of the role files is intentional: the
# spawn path must fail closed even if the .agent.md file is missing.
SUBAGENT_POLICIES: dict[str, list[str]] = {
    "explorer":     ["Read", "View", "Grep", "Glob"],
    "investigator": ["Read", "View", "Grep", "Glob", "Bash"],
    "reviewer-aux": ["Read", "View"],
    # Phase B.1 — pipeline roles spawnable as ad-hoc sub-agents by the
    # orchestrator. Tool sets mirror PIPELINE_POLICIES["feature-dev"] so
    # an ad-hoc spawn cannot exceed what the same role gets inside a
    # pipeline. Kept in sync manually; if PIPELINE_POLICIES changes,
    # update here too.
    "planner":      ["Read", "View", "Grep", "Glob"],
    "coder":        ["Read", "View", "Grep", "Glob", "Write", "Edit", "Bash"],
    "reviewer":     ["Read", "View", "Grep", "Glob"],
    # Phase C.2 — text-only sub-agent driving 90% reactive compaction.
    # No tools: the brief already carries the older conversation window
    # serialized as text, and the output is plain markdown.
    "summarizer":   [],
}

# MAIN_SUBAGENT_ALLOWLIST — which roles each main agent may spawn.
# - "orchestrator" (Phase B.1) may spawn the read-only Phase A roles plus
#   the pipeline roles ad-hoc. It must NOT spawn an entire pipeline —
#   that is reserved for user-invoked slash commands. Locked decision #4
#   in docs/roadmap.md.
# - Pipeline stages opt in via `pipeline.yaml subagents:` (Phase B); we
#   keep their entries empty here so the harness denies any spawn until
#   the pipeline runner explicitly adds the stage to this table at
#   load-time. Empty dict entry = "agent exists, but cannot spawn".
MAIN_SUBAGENT_ALLOWLIST: dict[str, list[str]] = {
    "orchestrator": [
        "explorer", "investigator", "reviewer-aux",
        "planner", "coder", "reviewer",
        "summarizer",
    ],
    "planner":  [],
    "designer": [],
    "coder":    [],
    "reviewer": [],
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


# ── Sub-agent helpers ─────────────────────────────────────────────────────

def list_subagent_roles(main_agent: str) -> list[str]:
    """Roles that `main_agent` is allowed to spawn. [] if none / unknown."""
    return list(MAIN_SUBAGENT_ALLOWLIST.get(main_agent.lower(), []))


def check_subagent_allowed(main_agent: str, role: str) -> bool:
    """True iff `main_agent` may spawn the sub-agent `role`."""
    return role in MAIN_SUBAGENT_ALLOWLIST.get(main_agent.lower(), [])


def get_subagent_tools(role: str) -> list[str]:
    """Tools the role itself is allowed to use. [] if role unknown."""
    return list(SUBAGENT_POLICIES.get(role, []))


def effective_subagent_tools(
    main_agent: str,
    main_tools: list[str],
    role: str,
    requested_tools: list[str] | None = None,
) -> list[str]:
    """Compute the sub-agent's effective tool set.

    rule: SUBAGENT_POLICIES[role] ∩ main_tools ∩ (requested_tools or all).

    The intersection guarantees a sub-agent can never exceed its parent's
    permissions or the role's hard cap. `requested_tools=None` means the
    caller did not narrow further.
    """
    role_tools = SUBAGENT_POLICIES.get(role)
    if role_tools is None:
        return []
    main_set = set(main_tools)
    requested_set = set(requested_tools) if requested_tools is not None else None
    out: list[str] = []
    for t in role_tools:
        if t not in main_set:
            continue
        if requested_set is not None and t not in requested_set:
            continue
        out.append(t)
    return out


def subagent_deny_reason(main_agent: str, role: str) -> str:
    """Human-readable reason a spawn was denied."""
    if role not in SUBAGENT_POLICIES:
        return (
            f"Unknown sub-agent role {role!r}. "
            f"Valid roles: {sorted(SUBAGENT_POLICIES.keys())}"
        )
    if main_agent.lower() not in MAIN_SUBAGENT_ALLOWLIST:
        return (
            f"Main agent {main_agent!r} has no spawn allow-list "
            f"(fail-closed)."
        )
    allowed = MAIN_SUBAGENT_ALLOWLIST[main_agent.lower()]
    return (
        f"Main agent {main_agent!r} may not spawn role {role!r}. "
        f"Allowed roles: {sorted(allowed)}"
    )
