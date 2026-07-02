"""Sub-agent orchestrator for the standalone agent.

musubi-tier: ephemeral
expires-when: models gain reliable native multi-agent tool-use
cost-lever: deletes the standalone spawn→run→complete driver (~120 lines)

The MCP substrate exposes `musubi_spawn_subagent` / `musubi_get_subagent_context`
/ `musubi_complete_subagent`, but in the standalone path nothing *runs* the
spawned child. This module is that runner — a Python port of the extension's
`runners/subagentRunnerCore.ts` contract:

    spawn → get_subagent_context (firewalled brief + role skill + tools)
          → build child system prompt
          → run a turn-capped child loop on a restricted tool surface
          → complete_subagent (harness verifies/firewalls the summary)

The child sees only the firewalled brief — never the parent's session state,
memory, or sibling sub-agents (Hard Invariant #3 re-homed onto the sub-agent
boundary). Sub-agents are leaves in v1: their tool surface never includes the
spawn tool, so delegation is one level deep.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.prompt_resolver import AgentPromptPurpose, read_agent_prompt

# Symbolic capability (role allow-list) → MCP tool names. The role allow-list
# uses Copilot's symbolic names; the standalone path drives `musubi_*` MCP
# tools. Grep/Glob have no read-only MCP equivalent, so a read-only role is
# limited to file reads — it is never silently upgraded to shell access.
SYMBOLIC_TO_MCP: dict[str, list[str]] = {
    "Read": ["musubi_read_file"],
    "View": ["musubi_read_file"],
    "Write": ["musubi_write_file", "musubi_append_file"],
    "Edit": ["musubi_edit_file"],
    "Bash": ["musubi_run_command"],
}

DEFAULT_SUBAGENT_MAX_CYCLES = 8  # mirrors sub_sessions.DEFAULT_MAX_TURNS


async def run_subagent(
    session: Any,
    spawn_args: dict[str, Any],
    vendor: Any,
    tools: list[dict[str, Any]],
    log: Any,
    *,
    agents_dir: Path | None = None,
    orchestration: Any = None,
    compression_db_path: Path | None = None,
    budget: Any = None,
    stats: Any = None,
    audit_db_path: Path | None = None,
) -> str:
    """Spawn, run, and complete one worker. Returns the final summary text.

    `spawn_args` is the model's `musubi_spawn_subagent` input with the owning
    `parent_session_id` / `parent_agent_name` already injected by the caller.
    Any harness-side rejection (bad role, firewall, missing session) is returned
    verbatim so the parent model can react.

    `orchestration` is the SPAWNING worker's context. When it still has depth
    budget (`can_spawn_deeper`) and this worker's role is itself allowed to
    spawn, the worker is given the spawn tool and a one-level-deeper
    orchestration so it can summon its own workers (bounded by `max_depth`).
    Otherwise it is a leaf: no spawn tool, no orchestration.
    """
    # Lazy import avoids the run↔subagent module cycle.
    from agent.run import _call_tool_text, run_unit

    raw = await _call_tool_text(session, "musubi_spawn_subagent", spawn_args)
    spawn = _loads(raw)
    if spawn.get("status") != "spawned":
        return raw
    handle_id = str(spawn.get("handle_id", ""))
    role = str(spawn.get("role") or spawn_args.get("role", ""))
    max_turns = int(spawn.get("max_turns") or DEFAULT_SUBAGENT_MAX_CYCLES)

    ctx_raw = await _call_tool_text(
        session, "musubi_get_subagent_context", {"handle_id": handle_id}
    )
    ctx = _loads(ctx_raw)
    if ctx.get("status") != "ok":
        await _safe_complete(
            session, handle_id, status="failed",
            summary=f"sub-agent context fetch failed: {ctx_raw[:200]}",
        )
        return ctx_raw

    brief = str(ctx.get("brief", ""))
    role_skill = ctx.get("role_skill")
    allowed = ctx.get("allowed_tools") or []

    agent_md = _read_agent_md(role, agents_dir)
    system_prompt = build_subagent_system_prompt(agent_md, role_skill, brief)
    child_tools = select_child_tools(tools, allowed)

    # Nesting: this worker may itself spawn only when the parent still has depth
    # budget AND this role declares a spawn_allowlist (a leaf role like explorer
    # stays a leaf — its surface never gains the spawn tool). When nesting, hand
    # it the spawn tool plus a one-level-deeper orchestration, and pass the full
    # catalog as `spawn_catalog` so its own children can be sized.
    child_orch = None
    spawn_catalog = None
    if (
        orchestration is not None
        and getattr(orchestration, "can_spawn_deeper", False)
        and _frontmatter_spawn_allowlist(agent_md)
    ):
        spawn_tool = [t for t in tools if t.get("name") == "musubi_spawn_subagent"]
        if spawn_tool:
            child_tools = child_tools + spawn_tool
            child_orch = orchestration.child(role)
            spawn_catalog = tools

    print(
        f"[agent]   ⮑ worker {role} (handle={handle_id}, "
        f"tools={len(child_tools)}, max_turns={max_turns}, "
        f"nests={child_orch is not None})",
        file=log,
    )

    # The firewalled brief is baked into `system_prompt`, so the worker runs
    # through `run_unit` with no extra user turn. A leaf passes no orchestration
    # and a restricted surface; a nesting worker carries the deeper orchestration.
    try:
        answer, turns = await run_unit(
            session, vendor, child_tools,
            system_prompt=system_prompt,
            user_message=None,
            max_cycles=max_turns, log=log,
            orchestration=child_orch,
            spawn_catalog=spawn_catalog,
            compression_db_path=compression_db_path,
            role=role,
            stats=stats,
            budget=budget,
            audit_db_path=audit_db_path,
        )
    except Exception as exc:
        if type(exc).__name__ in {
            "BudgetExhaustedError",
            "TokenBudgetExhaustedError",
        }:
            await _safe_complete(
                session, handle_id, status="escalated",
                summary=f"[subagent {role}] budget exhausted: {exc}",
            )
        raise
    if answer is None:
        summary = f"[subagent {role}] exceeded {max_turns} cycles without a final answer"
        status = "escalated"
    elif answer.lstrip().lower().startswith("[incomplete]"):
        summary, status = answer, "escalated"
    else:
        summary, status = answer, "done"

    complete_raw = await _call_tool_text(
        session, "musubi_complete_subagent",
        {"handle_id": handle_id, "summary": summary, "turns": turns, "status": status},
    )
    comp = _loads(complete_raw)
    # Prefer the harness-verified summary (firewalled / truncated) when present.
    verified = comp.get("summary") if isinstance(comp, dict) else None
    return verified or summary


# ── prompt + tool surface (ported from subagentRunnerCore.ts) ───────────────


def build_subagent_system_prompt(
    agent_md: str,
    role_skill: str | None,
    brief: str,
) -> str:
    """Stripped agent.md + role skill + brief — the brief IS the task."""
    parts: list[str] = [_strip_frontmatter(agent_md).strip()]
    skill = (role_skill or "").strip()
    if skill:
        parts.append("\n\n## Skill (pushed by harness)\n\n" + _strip_frontmatter(skill).strip())
    parts.append(
        "\n\n## Brief\n\n" + brief.strip()
        + "\n\nFollow the Output Contract in your role section. Produce your "
        "answer as plain text; the harness captures and verifies it on completion."
    )
    return "".join(parts).strip()


def select_child_tools(
    tools: list[dict[str, Any]],
    allowed_symbolic: list[str],
) -> list[dict[str, Any]]:
    """Filter the MCP tool catalog to the role's mapped capabilities.

    Symbolic role tools (Read/Write/...) map to `musubi_*` MCP tools via
    SYMBOLIC_TO_MCP. Unmapped capabilities (Grep/Glob) contribute nothing — a
    read-only role is never silently granted shell. An empty result is valid
    (a text-only role, summarizer-shaped).
    """
    wanted: set[str] = set()
    for sym in allowed_symbolic:
        wanted.update(SYMBOLIC_TO_MCP.get(sym, []))
    return [t for t in tools if t.get("name") in wanted]


def _frontmatter_spawn_allowlist(agent_md: str) -> list[str]:
    """Roles declared in the agent.md frontmatter `spawn_allowlist:` (the source
    of truth since Increment 3). Empty list for a leaf role or a file with no
    frontmatter — so nesting is enabled only for roles that actually declare
    workers they may summon. The MCP server re-validates every spawn anyway, so
    this is a surface hint, not the security boundary.
    """
    text = (agent_md or "").lstrip()
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    try:
        import yaml  # type: ignore[import-untyped]

        fm = yaml.safe_load(text[3:end]) or {}
    except Exception:
        return []
    val = fm.get("spawn_allowlist") if isinstance(fm, dict) else None
    return [s for s in val if isinstance(s, str)] if isinstance(val, list) else []


def _strip_frontmatter(md: str) -> str:
    """Drop a leading `---`-fenced YAML frontmatter block."""
    text = md.lstrip()
    if not text.startswith("---"):
        return md
    end = text.find("\n---", 3)
    if end == -1:
        return md
    after = text[end + 4 :]
    return after.lstrip("\n")


def _read_agent_md(role: str, agents_dir: Path | None) -> str:
    base = agents_dir or _default_agents_dir()
    root = base.parent.parent if base.name == "agents" else base
    return read_agent_prompt([root], role, purpose=AgentPromptPurpose.WORKER)


def _default_agents_dir() -> Path:
    # musubi/agent/subagent.py → parents[2] is the repo root holding .github/.
    return Path(__file__).resolve().parents[2] / ".github" / "agents"


# ── helpers ─────────────────────────────────────────────────────────────────


def _loads(raw: str) -> dict[str, Any]:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


async def _safe_complete(session: Any, handle_id: str, *, status: str, summary: str) -> None:
    from agent.run import _call_tool_text

    try:
        await _call_tool_text(
            session, "musubi_complete_subagent",
            {"handle_id": handle_id, "status": status, "summary": summary},
        )
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass
