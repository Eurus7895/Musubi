"""Standalone agent tool-call boundary controls.

musubi-tier: substrate
expires-when: never - policy and audit belong at every agent/tool
  boundary, independent of whether the caller is VS Code or the CLI.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Verdict = Literal["ALLOW", "DENY"]


@dataclass(frozen=True)
class PolicyDecision:
    verdict: Verdict
    role: str
    tool: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.verdict == "ALLOW"


_TOOL_CAPABILITIES: dict[str, str] = {
    "musubi_read_file": "Read",
    "musubi_write_file": "Write",
    "musubi_edit_file": "Edit",
    "musubi_run_command": "Bash",
    "musubi_run_lint": "Bash",
    "musubi_run_typecheck": "Bash",
    "musubi_run_tests": "Bash",
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
    "musubi_session_credits",
    "musubi_credits_since",
    "musubi_query_schema_migrations",
    "musubi_list_skills",
    "musubi_recommend_skills",
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
    clean_role = (role or "agent").lower()
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
        if clean_role == "agent":
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
        if role == "agent":
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
    if role == "agent":
        return set(_ROOT_AGENT_TOOLS)
    _ensure_scripts_path()
    import policy_engine  # type: ignore[import-not-found]

    return set(policy_engine.get_subagent_tools(role))


def _ensure_scripts_path() -> None:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))


def json_args(args: Any) -> dict[str, Any]:
    """Best-effort audit args shape."""
    if isinstance(args, dict):
        return args
    try:
        return json.loads(json.dumps(args, default=str))
    except Exception:
        return {"value": str(args)}
