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
import os
from pathlib import Path
from typing import Any

from agent.prompt_resolver import AgentPromptPurpose, read_agent_prompt

# Symbolic capability (role allow-list) → MCP tool names. The role allow-list
# uses Copilot's symbolic names; the standalone path drives `musubi_*` MCP
# tools. Grep/Glob map to read-only discovery tools so a read-only role (e.g.
# a pipeline planner/designer/reviewer) can find files without guessing paths
# — it is still never silently upgraded to shell (Bash) access.
SYMBOLIC_TO_MCP: dict[str, list[str]] = {
    "Read": ["musubi_read_file"],
    "View": ["musubi_read_file"],
    "Grep": ["musubi_grep"],
    "Glob": ["musubi_glob"],
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
    from agent.run import (
        _call_tool_text,
        _worker_log_label,
        _worker_touched_files,
        run_unit,
    )

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
    worker_max_output = _frontmatter_max_output_tokens(agent_md)
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
    # Deterministic record of the files THIS worker mutates, collected by the
    # dispatch loop via a ContextVar. Set here so nested workers each get their
    # own sink; drives the mechanical gate after the run.
    touched: set[str] = set()
    token = _worker_touched_files.set(touched)
    label_token = _worker_log_label.set(f"{role}#{handle_id[:8]}")
    try:
        answer, turns = await run_unit(
            session, vendor, child_tools,
            system_prompt=system_prompt,
            user_message=None,
            max_cycles=max_turns, log=log,
            salvage_on_exhaust=True,
            orchestration=child_orch,
            spawn_catalog=spawn_catalog,
            compression_db_path=compression_db_path,
            role=role,
            stats=stats,
            budget=budget,
            audit_db_path=audit_db_path,
            worker_max_output=worker_max_output,
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
    finally:
        _worker_touched_files.reset(token)
        _worker_log_label.reset(label_token)
    if answer is None:
        summary = f"[subagent {role}] exceeded {max_turns} cycles without a final answer"
        status = "escalated"
    elif turns >= max_turns:
        summary, status = answer, "escalated"
    elif answer.lstrip().lower().startswith(("[incomplete]", "[blocked]")):
        summary, status = answer, "escalated"
    else:
        summary, status = answer, "done"

    # C1 — mechanical gate at the boundary the parent (root) receives this
    # worker. When the worker finished cleanly AND actually wrote files, the
    # harness runs a deterministic validator on exactly those files and hands
    # the verdict to the parent, so the goal-holding root accepts the mechanical
    # layer from a trustworthy signal instead of re-deriving it by eye.
    gate = None
    if status == "done" and touched:
        gate = await _run_mechanical_gate(session, touched, log)
        line = _mechanical_line(gate)
        summary = f"{line}\n{summary}"
        print(f"[agent]   {line}", file=log)

    complete_args: dict[str, Any] = {
        "handle_id": handle_id, "summary": summary, "turns": turns, "status": status,
    }
    if gate is not None:
        complete_args["structured"] = {"mechanical": gate}
    complete_raw = await _call_tool_text(
        session, "musubi_complete_subagent", complete_args,
    )
    comp = _loads(complete_raw)
    # Prefer the harness-verified summary (firewalled / truncated) when present.
    verified = comp.get("summary") if isinstance(comp, dict) else None
    return verified or summary


# ── C1: mechanical validation gate ──────────────────────────────────────────

# Extensions with an applicable deterministic validator. Anything else (an HTML
# artifact, JSON, markdown) reports exit=None — written, no linter to run.
_LINTABLE_EXT = (".py",)


def _mechanical_workspace_root() -> Path:
    """Workspace root, mirroring tools.fs so a relative path resolves the same."""
    env = os.environ.get("MUSUBI_ROOT")
    return Path(env).resolve() if env else Path.cwd().resolve()


def _file_still_exists(path: str) -> bool:
    p = Path(path)
    if not p.is_absolute():
        p = _mechanical_workspace_root() / p
    return p.exists()


def _lint_errors_preview(res: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for e in (res.get("errors") or [])[:2]:
        if isinstance(e, dict):
            code = str(e.get("code", "")).strip()
            msg = str(e.get("message", "")).strip()
            out.append(f"{code} {msg}".strip())
    return out


async def _run_mechanical_gate(
    session: Any, touched: set[str], log: Any,
) -> dict[str, Any]:
    """Deterministic mechanical check over the files a worker wrote.

    `result` is one of:
      - ``pass``    - ruff ran clean.
      - ``fail``    - ruff found real lint errors (the only state the root
                      should treat as "not acceptable, route a fix").
      - ``error``   - the validator could not run (e.g. an unparseable file);
                      NOT a failure.
      - ``skipped`` - nothing lintable survived.

    Files the worker wrote but then deleted (a generator/scratch file) are
    filtered out first (G1): linting a deleted file would otherwise yield a
    false failure. The verdict is always the tool's own, never the worker's
    summary. Returns a JSON-serialisable signal the root reads without
    re-deriving it.
    """
    from agent.run import _call_tool_text

    files = sorted(f for f in touched if _file_still_exists(f))
    lintable = [f for f in files if f.endswith(_LINTABLE_EXT)]
    artifact = files[0] if len(files) == 1 else next(
        (f for f in files if not f.endswith(_LINTABLE_EXT)), None
    )

    result = "skipped"
    detail: str | None = None
    errors: list[str] = []
    if not files:
        detail = "no surviving files (all writes deleted)"
    elif not lintable:
        detail = "no lintable files"
    else:
        raw = await _call_tool_text(session, "musubi_run_lint", {"files": lintable})
        res = _loads(raw)
        if not isinstance(res, dict):
            result, detail = "error", "validator returned no result"
        elif res.get("passed"):
            result = "pass"
        else:
            errors = _lint_errors_preview(res)
            # ruff ran but produced no structured errors → it could not lint
            # (missing/unparseable) rather than found real problems.
            result = "fail" if errors else "error"
            if result == "error":
                detail = "validator could not lint the file(s)"

    return {
        "validator": "ruff" if lintable else "none",
        "result": result,
        "errors": errors,
        "detail": detail,
        "files_touched": files,
        "artifact_path": artifact,
    }


def _mechanical_line(gate: dict[str, Any]) -> str:
    """Compact one-liner prepended to the summary so the root sees the signal."""
    parts = [
        f"[mechanical] result={gate.get('result')}",
        f"validator={gate.get('validator')}",
    ]
    if gate.get("artifact_path"):
        parts.append(f"artifact={gate['artifact_path']}")
    parts.append(f"files={len(gate.get('files_touched') or [])}")
    if gate.get("result") == "fail" and gate.get("errors"):
        parts.append("errors=" + "; ".join(gate["errors"]))
    elif gate.get("detail"):
        parts.append(f"reason={gate['detail']!r}")
    return " ".join(parts)


# ── prompt + tool surface (ported from subagentRunnerCore.ts) ───────────────


def build_subagent_system_prompt(
    agent_md: str,
    role_skill: str | None,
    brief: str,
    *,
    platform_name: str | None = None,
) -> str:
    """Stripped agent.md + role skill + brief — the brief IS the task."""
    parts: list[str] = [_strip_frontmatter(agent_md).strip()]
    skill = (role_skill or "").strip()
    if skill:
        parts.append("\n\n## Skill (pushed by harness)\n\n" + _strip_frontmatter(skill).strip())
    host = os.name if platform_name is None else platform_name
    if host == "nt":
        parts.append(
            "\n\nHost: Windows (cmd/PowerShell); use `del`, not `rm`, and "
            "Windows path separators."
        )
    else:
        parts.append(
            "\n\nHost: POSIX shell; use `rm`, not `del`, and `/` path separators."
        )
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

    Symbolic role tools (Read/Grep/Glob/Write/...) map to `musubi_*` MCP tools
    via SYMBOLIC_TO_MCP. A read-only role gets read + discovery (grep/glob) but
    is never silently granted shell. An unmapped capability contributes
    nothing; an empty result is valid (a text-only role, summarizer-shaped).
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


def _frontmatter_max_output_tokens(agent_md: str) -> int | None:
    """Return a positive per-worker output cap, or the shared-default signal."""
    text = (agent_md or "").lstrip()
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    try:
        import yaml  # type: ignore[import-untyped]

        fm = yaml.safe_load(text[3:end]) or {}
    except Exception:
        return None
    value = fm.get("maxOutputTokens") if isinstance(fm, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


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
