"""Standalone agent tool-call boundary controls.

musubi-tier: substrate
expires-when: never - policy and audit belong at every agent/tool
  boundary, independent of whether the caller is VS Code or the CLI.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

def _ensure_scripts_path() -> None:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))


_ensure_scripts_path()

# The fail-closed policy engine owns the canonical role vocabulary (HI #5):
# one definition of what the depth-0 driver is called, and one normalizer that
# every membership and capability lookup folds through. Imported at module
# scope rather than re-derived here so this file cannot drift from the table
# it enforces.
from policy_engine import (  # noqa: E402
    ROOT_ROLE,
    normalize_role,
)

Verdict = Literal["ALLOW", "DENY"]


@dataclass(frozen=True)
class PolicyDecision:
    verdict: Verdict
    role: str
    tool: str
    reason: str
    #: True when the denial is about the VALUES in this call rather than the
    #: caller's authority to make it. Both still deny and both are audited
    #: identically; they differ only in what the caller can do next.
    #:
    #: "role `agent` may not call `musubi_write_file`" cannot be fixed by
    #: retrying — the model has no way to become a different role, so the run
    #: ends. "skill 'x' is not permitted for role 'coder'" is one wrong string
    #: in an OPTIONAL argument of an otherwise authorised call; the model can
    #: correct it on the next cycle for the price of one round-trip. Routing
    #: the second through the terminal channel ended a turn 4 cycles and
    #: 12,383 tokens in, having delivered nothing, over a field the caller was
    #: free to omit entirely.
    recoverable: bool = False

    @property
    def allowed(self) -> bool:
        return self.verdict == "ALLOW"


_TOOL_CAPABILITIES: dict[str, str] = {
    "musubi_read_file": "Read",
    "musubi_glob": "Glob",
    "musubi_grep": "Grep",
    "musubi_write_file": "Write",
    "musubi_append_file": "Write",
    "musubi_edit_file": "Edit",
    "musubi_run_command": "Bash",
    "musubi_run_lint": "Bash",
    "musubi_run_typecheck": "Bash",
    "musubi_run_tests": "Bash",
}

_DENIED_TOOL_ROUTING_HINTS: dict[str, str] = {
    "musubi_write_file": (
        "do not retry this tool from the root agent; spawn `coder` for "
        "file creation or writes"
    ),
    "musubi_append_file": (
        "do not retry this tool from the root agent; spawn `coder` for "
        "large file appends or chunked writes"
    ),
    "musubi_edit_file": (
        "do not retry this tool from the root agent; spawn `coder` for "
        "file edits"
    ),
    "musubi_run_command": (
        "do not retry this tool from the root agent; spawn `investigator` "
        "for command-based diagnostics"
    ),
    "musubi_run_lint": (
        "do not retry this tool from the root agent; spawn `investigator` "
        "for lint diagnostics"
    ),
    "musubi_run_typecheck": (
        "do not retry this tool from the root agent; spawn `investigator` "
        "for typecheck diagnostics"
    ),
    "musubi_run_tests": (
        "do not retry this tool from the root agent; spawn `investigator` "
        "for test diagnostics"
    ),
}

_READLIKE_GOVERNANCE_TOOLS: frozenset[str] = frozenset({
    "musubi_get_active_session",
    "musubi_get_status",
    "musubi_get_skill",
    "musubi_get_reference",
    "musubi_get_memory_entry",
    "musubi_query_sessions",
    "musubi_list_subagents",
    "musubi_get_conversation",
    "musubi_retrieve",
    "musubi_compress",
    "musubi_compression_stats",
    "musubi_query_pipeline_runs",
    "musubi_query_stage_metrics",
    "musubi_query_agent_cycles",
    "musubi_query_agent_turns",
    "musubi_pipeline_stats",
    "musubi_get_pause_state",
    "musubi_get_correction_rules",
    "musubi_get_injected_skills",
    "musubi_get_pipeline_stages",
    "musubi_query_schema_migrations",
    "musubi_list_skills",
    "musubi_recommend_skills",
    "musubi_begin_direct",
    "musubi_begin_plan",
    "musubi_commit_plan",
    "musubi_get_memory_context",
    "musubi_query_subagent_events",
    "musubi_list_subagent_spawns",
})

_AGENT_SESSION_TOOLS: frozenset[str] = frozenset({
    # Kept for backward compatibility with existing standalone tests and
    # clients. The driver opens the parent session itself, but this call is
    # harmless enough to remain model-visible under the agent role.
    "musubi_new_session",
})

_SPAWN_TOOLS: frozenset[str] = frozenset({
    "musubi_spawn_subagent",
    "musubi_spawn_pipeline",
})

_DRIVER_ONLY_TOOLS: frozenset[str] = frozenset({
    "musubi_clear_active_session",
    "musubi_write_stage",
    "musubi_increment_attempt",
    "musubi_pause_session",
    "musubi_resume_session",
    "musubi_compute_chunks",
    "musubi_ensure_chunk_row",
    "musubi_consume_pending_action",
    "musubi_record_stage_metric",
    "musubi_finalize_pipeline_run",
    "musubi_record_agent_cycle",
    "musubi_record_agent_turn",
    "musubi_complete_subagent",
    "musubi_await_subagent",
    "musubi_delete_subsessions_for_parent",
    "musubi_spawn_pipeline_stage",
    "musubi_run_hook",
    "musubi_append_message",
    "musubi_append_failure_pattern",
    "musubi_distill_session",
    "musubi_compact_memory",
})

_ROOT_AGENT_TOOLS: frozenset[str] = frozenset({
    "Read",
    "View",
    "Grep",
    "Glob",
})

_POLICY_SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    verdict TEXT NOT NULL,
    tool    TEXT NOT NULL,
    role    TEXT NOT NULL,
    handle  TEXT,
    reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_policy_audit_ts
    ON policy_audit(ts);
"""


def is_musubi_tool(tool_name: str) -> bool:
    """Only Musubi-owned tools are governed here.

    Federated external MCP tools intentionally remain outside Musubi's
    substrate controls; see agent/mcp_gateway.py for that boundary.
    """
    return tool_name.startswith("musubi_")


def evaluate_tool_call(role: str, tool_name: str) -> PolicyDecision:
    """Return the fail-closed policy decision for a model-requested tool."""
    clean_role = normalize_role(role)
    if not is_musubi_tool(tool_name):
        return PolicyDecision(
            "ALLOW", clean_role, tool_name,
            "external MCP tool; outside Musubi governance boundary",
        )

    if tool_name in _DRIVER_ONLY_TOOLS:
        return PolicyDecision(
            "DENY", clean_role, tool_name,
            "driver-only Musubi tool is not callable by the model",
        )

    if tool_name in _READLIKE_GOVERNANCE_TOOLS:
        return PolicyDecision(
            "ALLOW", clean_role, tool_name,
            "read-only governance/compression tool",
        )

    if tool_name in _AGENT_SESSION_TOOLS:
        if clean_role == ROOT_ROLE:
            return PolicyDecision(
                "ALLOW", clean_role, tool_name,
                "agent session-management compatibility tool",
            )
        return PolicyDecision(
            "DENY", clean_role, tool_name,
            "session-management tool is reserved for the root agent",
        )

    if tool_name in _SPAWN_TOOLS:
        return _evaluate_spawn_tool(clean_role, tool_name)

    capability = _TOOL_CAPABILITIES.get(tool_name)
    if capability is None:
        return PolicyDecision(
            "DENY", clean_role, tool_name,
            "unknown Musubi tool; fail-closed",
        )

    allowed = _allowed_capabilities(clean_role)
    if capability in allowed:
        return PolicyDecision(
            "ALLOW", clean_role, tool_name,
            f"capability {capability} allowed for role {clean_role}",
        )
    return PolicyDecision(
        "DENY", clean_role, tool_name,
        f"capability {capability} is not allowed for role {clean_role}; "
        f"allowed: {sorted(allowed)}",
    )


def evaluate_argument_policy(
    role: str,
    tool_name: str,
    args: dict[str, Any],
    *,
    pipeline_name: str | None = None,
) -> PolicyDecision | None:
    """Return an argument-dependent firewall denial without side effects.

    Base role/tool policy remains in evaluate_tool_call. This second, pure
    boundary mirrors spawn authorization before substrate state mutation.
    """
    clean_role = normalize_role(role)
    if tool_name != "musubi_spawn_subagent":
        return None

    _ensure_scripts_path()
    import policy_engine  # type: ignore[import-not-found]
    from validation.context_builder import check_skill_permission

    spawn_role = args.get("role")
    target_role = spawn_role.strip() if isinstance(spawn_role, str) else ""
    if not policy_engine.check_subagent_allowed(
        clean_role,
        target_role,
        pipeline_name=pipeline_name,
    ):
        return PolicyDecision(
            "DENY",
            clean_role,
            tool_name,
            policy_engine.subagent_deny_reason(
                clean_role,
                target_role,
                pipeline_name=pipeline_name,
            ),
        )

    requested = args.get("allowed_tools")
    if isinstance(requested, list):
        role_tools = policy_engine.get_subagent_tools(target_role)
        effective = [
            tool for tool in role_tools
            if isinstance(tool, str) and tool in requested
        ]
        if not effective:
            return PolicyDecision(
                "DENY",
                clean_role,
                tool_name,
                (
                    f"No tools available for sub-agent role {target_role!r} "
                    "after intersecting with caller's allow-list. Omit "
                    "`allowed_tools` — the worker role owns its tool surface."
                ),
                recoverable=True,
            )

    pushed_skill = args.get("pushed_skill_id")
    if isinstance(pushed_skill, str) and pushed_skill.strip():
        skill_id = pushed_skill.strip()
        if not check_skill_permission(target_role, skill_id):
            return PolicyDecision(
                "DENY",
                clean_role,
                tool_name,
                _pushed_skill_denial(
                    skill_id, target_role, args.get("recommendation_id"),
                ),
                recoverable=True,
            )
    return None


#: A `recommendation_id` is `sha256(...)[:20]` (`server.py::
#: musubi_recommend_skills`), so it is exactly twenty hex characters — a shape
#: no skill id in the catalog has. Matching it lets the refusal name the
#: mistake instead of only its symptom.
_RECOMMENDATION_ID_RE = re.compile(r"^[0-9a-f]{20}$")


def _pushed_skill_denial(
    skill_id: str, target_role: str, recommendation_id: Any,
) -> str:
    """Say what to pass instead, not only that this value was wrong.

    The bare form of this message — "Skill 'x' is not permitted for worker role
    'coder'" — is true and useless: it names neither the legal values nor the
    likeliest cause. The observed failure was the model passing the ticket id
    into the skill field, which the message could not distinguish from a
    genuinely unauthorised skill.
    """
    from validation.context_builder import AGENT_SKILL_ALLOWLIST

    lines = [
        f"Skill {skill_id!r} is not permitted for worker role "
        f"{target_role!r}."
    ]
    ticket = recommendation_id.strip() if isinstance(recommendation_id, str) else ""
    if skill_id == ticket or _RECOMMENDATION_ID_RE.fullmatch(skill_id):
        lines.append(
            "That value is a recommendation_id, not a skill_id. "
            "`recommendation_id` carries the ticket; `pushed_skill_id` carries "
            "one `skill_id` you picked from that ticket's `recommended` list."
        )
    permitted = sorted(AGENT_SKILL_ALLOWLIST.get(target_role, set()))
    if permitted:
        lines.append(f"Permitted for {target_role!r}: {permitted}.")
    lines.append("Or omit `pushed_skill_id` — the role's own skill is pushed anyway.")
    return " ".join(lines)


def denied_tool_guidance(role: str, tool_name: str) -> str:
    """Return a short model-facing recovery hint for denied root tool calls."""
    clean_role = normalize_role(role)
    if clean_role != ROOT_ROLE:
        return ""
    hint = _DENIED_TOOL_ROUTING_HINTS.get(tool_name)
    if not hint:
        return ""
    return f" Next action: {hint}."


def record_policy_decision(
    decision: PolicyDecision,
    *,
    db_path: Path,
    handle: str | None = None,
) -> None:
    """Append the PreToolUse verdict to policy_audit."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_POLICY_SCHEMA)
        conn.execute(
            "INSERT INTO policy_audit (ts, verdict, tool, role, handle, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                decision.verdict,
                decision.tool,
                decision.role,
                handle,
                decision.reason,
            ),
        )


def record_tool_audit(
    *,
    session_id: str | None,
    role: str,
    tool: str,
    args: dict[str, Any],
    status: str,
    db_path: Path,
    result_text: str | None = None,
) -> None:
    """Append the PostToolUse audit row, including denied attempts."""
    _ensure_scripts_path()
    import post_tool_use  # type: ignore[import-not-found]

    result_hash = None
    if result_text is not None:
        result_hash = "sha256:" + hashlib.sha256(
            result_text.encode("utf-8", errors="replace")
        ).hexdigest()
    post_tool_use.record(
        {
            "session_id": session_id,
            "pipeline": "standalone-agent",
            "agent": role,
            "tool": tool,
            "args": args,
            "result_hash": result_hash,
            "status": status,
        },
        db_path=db_path,
    )


def _evaluate_spawn_tool(role: str, tool_name: str) -> PolicyDecision:
    _ensure_scripts_path()
    import policy_engine  # type: ignore[import-not-found]

    if tool_name == "musubi_spawn_pipeline":
        if normalize_role(role) == ROOT_ROLE:
            return PolicyDecision(
                "ALLOW", role, tool_name,
                "root agent may summon user-defined worker pipelines",
            )
        return PolicyDecision(
            "DENY", role, tool_name,
            "only the root agent may summon a pipeline",
        )

    roles = policy_engine.list_subagent_roles(role)
    if roles:
        return PolicyDecision(
            "ALLOW", role, tool_name,
            f"role {role} may spawn: {roles}",
        )
    return PolicyDecision(
        "DENY", role, tool_name,
        f"role {role} has no spawn allow-list",
    )


def _allowed_capabilities(role: str) -> set[str]:
    if normalize_role(role) == ROOT_ROLE:
        return set(_ROOT_AGENT_TOOLS)
    _ensure_scripts_path()
    import policy_engine  # type: ignore[import-not-found]

    return set(policy_engine.get_subagent_tools(role))



def json_args(args: Any) -> dict[str, Any]:
    """Best-effort audit args shape."""
    if isinstance(args, dict):
        return args
    try:
        return json.loads(json.dumps(args, default=str))
    except Exception:
        return {"value": str(args)}
