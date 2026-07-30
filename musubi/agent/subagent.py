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

import os
import re
from pathlib import Path
from typing import Any

from agent.jsonio import loads_dict
from agent.prompt_resolver import AgentPromptPurpose, read_agent_prompt
from workspace.grants import MANIFEST_ENV, RootRegistry

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
        FailureKind,
        PolicyDeniedError,
        _call_tool_text,
        _policy_incomplete,
        _worker_log_label,
        _worker_touched_files,
        run_unit,
    )
    from agent.runtime_log import runtime_worker_scope

    # One-cap rule, mirrored from the pipeline path (resolve_pipeline_worker
    # _spec): the role prompt is resolved BEFORE the spawn so its declared
    # `maxTurns:` frontmatter IS the turn budget recorded in the spawn row —
    # the same value then drives the runtime loop and the completion audit.
    # Role frontmatter is the SOLE owner of the cap: a model-supplied
    # max_turns is ignored in both directions. Allowing "fewer, never more"
    # let the root starve a worker below its role budget (max_turns=2 for a
    # coder whose contract declares 8 guarantees a turn-cap escalation), and
    # a replacement worker re-spawned with the failed run's leftover count
    # inherited that starvation. Roles without a valid `maxTurns:` omit any
    # model-supplied cap so the server default is the sole owner.
    role_hint = str(spawn_args.get("role", ""))
    agent_md = _read_agent_md(role_hint, agents_dir)
    declared_turns = _frontmatter_max_turns(agent_md)
    if declared_turns is not None:
        requested_turns = spawn_args.get("max_turns")
        if requested_turns is not None and requested_turns != declared_turns:
            print(
                f"[agent] ignored model max_turns={requested_turns}; "
                f"role {role_hint} owns max_turns={declared_turns}",
                file=log,
            )
        spawn_args = {**spawn_args, "max_turns": declared_turns}
    else:
        # With no role-owned declaration, omit any model-supplied value so
        # the substrate's server default remains the sole owner of the cap.
        spawn_args = {**spawn_args}
        spawn_args.pop("max_turns", None)


    raw = await _call_tool_text(session, "musubi_spawn_subagent", spawn_args)
    spawn = loads_dict(raw)
    if spawn.get("status") != "spawned":
        if spawn.get("error_kind") == "policy_denied":
            raise PolicyDeniedError(
                role=str(spawn_args.get("parent_agent_name") or "agent"),
                tool="musubi_spawn_subagent",
                reason=str(spawn.get("error") or "subagent spawn denied"),
            )
        return raw
    handle_id = str(spawn.get("handle_id", ""))
    role = str(spawn.get("role") or role_hint)
    max_turns = int(spawn.get("max_turns") or DEFAULT_SUBAGENT_MAX_CYCLES)
    if role != role_hint:
        # The server canonicalised the role differently; re-resolve so the
        # prompt matches the role that actually spawned.
        agent_md = _read_agent_md(role, agents_dir)

    ctx_raw = await _call_tool_text(
        session, "musubi_get_subagent_context", {"handle_id": handle_id}
    )
    ctx = loads_dict(ctx_raw)
    if ctx.get("status") != "ok":
        failure_summary = f"sub-agent context fetch failed: {ctx_raw[:200]}"
        await _safe_complete(
            session, handle_id, status="failed",
            summary=failure_summary,
        )
        if orchestration is not None:
            orchestration.record_worker_outcome(
                role=role,
                status="failed",
                summary=failure_summary,
                touched_files=(),
            )
        return ctx_raw

    brief = str(ctx.get("brief", ""))
    role_skill = ctx.get("role_skill")
    allowed = ctx.get("allowed_tools") or []

    worker_max_output = _frontmatter_max_output_tokens(agent_md)
    system_prompt = build_subagent_system_prompt(agent_md, role_skill, brief)
    system_prompt = f"{system_prompt}\n\n{_mechanical_registry().prompt_block()}"
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
        with runtime_worker_scope(role, handle_id):
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
                audit_session_id=spawn_args.get("parent_session_id"),
                audit_worker_id=handle_id,
                audit_stage=role,
            )
    except PolicyDeniedError as exc:
        policy_summary = _policy_incomplete(exc)
        await _safe_complete(
            session,
            handle_id,
            status="escalated",
            summary=policy_summary,
        )
        if orchestration is not None:
            orchestration.record_worker_outcome(
                role=role,
                status="escalated",
                summary=policy_summary,
                touched_files=touched,
                brief=brief,
                failure_kind=FailureKind.POLICY,
                pushed_skill_id=spawn_args.get("pushed_skill_id"),
            )
        return policy_summary
    except Exception as exc:
        if type(exc).__name__ in {
            "BudgetExhaustedError",
            "TokenBudgetExhaustedError",
        }:
            budget_summary = f"[subagent {role}] budget exhausted: {exc}"
            await _safe_complete(
                session, handle_id, status="escalated",
                summary=budget_summary,
            )
            # Typed BUDGET evidence: the raise skips the normal recording
            # below, and a budget failure must reach the parent as a
            # fail-closed terminal, never as recoverable unfinished work.
            if orchestration is not None:
                orchestration.record_worker_outcome(
                    role=role,
                    status="escalated",
                    summary=budget_summary,
                    touched_files=touched,
                    brief=brief,
                    failure_kind=FailureKind.BUDGET,
                    pushed_skill_id=spawn_args.get("pushed_skill_id"),
                )
        raise
    finally:
        _worker_touched_files.reset(token)
        _worker_log_label.reset(label_token)
    # Typed failure evidence, derived from CONTROL FLOW (which branch
    # terminated the worker), never from parsing summary prose (HI #1-adjacent:
    # deterministic, no judgement call).
    failure_kind = None
    done_artifacts: list[Any] | None = None
    if answer is None:
        summary = f"[subagent {role}] exceeded {max_turns} cycles without a final answer"
        status = "escalated"
        failure_kind = FailureKind.TURN_CAP
    elif answer.lstrip().lower().startswith(("[incomplete]", "[blocked]")):
        # A typed incomplete/blocked marker (e.g. a truncated tool call caught
        # mid-mutation) is always an escalation, even at the turn cap.
        summary, status = answer, "escalated"
        failure_kind = (
            FailureKind.BLOCKED
            if answer.lstrip().lower().startswith("[blocked]")
            else FailureKind.UNKNOWN
        )
    elif turns >= max_turns:
        # Force-concluded at the turn cap. That is a real escalation ONLY if the
        # deliverable is not already produced. If the forced-final answer
        # self-declares `status: done` AND the surviving files this worker
        # mutated are all non-empty on disk, the artifact was written before
        # the cutoff (the cap was spent on post-write verification, not the
        # work itself) — accept it as done rather than sending the root into a
        # pointless recovery that reports a finished artifact as [incomplete].
        # The surviving paths are also sent to the harness as the `artifacts`
        # manifest: the substrate re-verifies them itself before waiving its
        # own turn-cap coercion (sub_sessions.complete), so this driver-side
        # judgement is a claim, never the verdict.
        done_artifacts = _forced_final_artifacts(answer, touched)
        if done_artifacts:
            summary, status = answer, "done"
        else:
            summary, status = answer, "escalated"
            failure_kind = FailureKind.TURN_CAP
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
    if done_artifacts:
        complete_args["artifacts"] = done_artifacts
    if gate is not None:
        complete_args["structured"] = {"mechanical": gate}
    complete_raw = await _call_tool_text(
        session, "musubi_complete_subagent", complete_args,
    )
    comp = loads_dict(complete_raw)
    # Prefer the harness-verified summary (firewalled / truncated) when present.
    verified = comp.get("summary") if isinstance(comp, dict) else None
    returned_summary = verified or summary
    recorded_status = (
        comp.get("final_status")
        if isinstance(comp, dict)
        and comp.get("final_status")
        in {"done", "failed", "escalated", "abandoned"}
        else status
    )
    # Keep the typed kind consistent with the status the substrate actually
    # recorded: a success carries no failure kind, and a harness-side
    # turn-cap coercion (driver said done, substrate re-verified and said
    # escalated at the cap) is still control-flow-derivable as TURN_CAP.
    if recorded_status == "done":
        failure_kind = None
    elif failure_kind is None and turns >= max_turns:
        failure_kind = FailureKind.TURN_CAP
    if orchestration is not None:
        orchestration.record_worker_outcome(
            role=role,
            status=recorded_status,
            summary=returned_summary,
            touched_files=touched,
            brief=brief,
            failure_kind=failure_kind,
            pushed_skill_id=spawn_args.get("pushed_skill_id"),
        )
    return returned_summary


# ── C1: mechanical validation gate ──────────────────────────────────────────

# Extensions with an applicable deterministic validator. Anything else (an HTML
# artifact, JSON, markdown) reports exit=None — written, no linter to run.
_LINTABLE_EXT = (".py",)


def _mechanical_workspace_root() -> Path:
    """Workspace root, mirroring tools.fs so a relative path resolves the same.

    Precedence must stay identical to `tools.fs._workspace_root`: a worker
    writes through those tools, so anchoring the survivor check anywhere else
    reports every delivered file as missing.
    """
    env = os.environ.get("MUSUBI_ROOT")
    return (
        Path(env).resolve()
        if env
        else Path(__file__).resolve().parents[1]
    )


def _mechanical_registry() -> RootRegistry:
    root = _mechanical_workspace_root()
    raw = os.environ.get(MANIFEST_ENV, "")
    return RootRegistry.from_json(raw, root) if raw else RootRegistry.build(root)


def _split_touched_ref(reference: str) -> tuple[str, str]:
    if "::" in reference:
        root, path = reference.split("::", 1)
        return root, path
    return "musubi", reference


def _resolve_touched_ref(reference: str) -> Path:
    root, path = _split_touched_ref(reference)
    direct = Path(path)
    if root == "musubi" and direct.is_absolute():
        return direct.resolve()
    return _mechanical_registry().resolve(root, path)


def _file_still_exists(path: str) -> bool:
    try:
        return _resolve_touched_ref(path).exists()
    except (ValueError, PermissionError):
        return False


#: A worker's Output Contract opens with a `status:` line; `done` on that line
#: is the worker's own claim that it finished. Matched anywhere at line start so
#: a leading `[mechanical] …` banner or blank lines do not hide it.
_STATUS_DONE_RE = re.compile(r"(?im)^\s*status:\s*done\b")


def _file_nonempty(path: str) -> bool:
    try:
        p = _resolve_touched_ref(path)
        return p.is_file() and p.stat().st_size > 0
    except (OSError, ValueError, PermissionError):
        return False


def surviving_nonempty_files(touched: set[str]) -> list[Any] | None:
    """Sorted mutated files that still exist and are all non-empty, else None.

    Files the worker wrote and then deleted (a generator/scratch script) are
    ignored, mirroring the mechanical gate's G1 filter. No survivors → None
    (nothing was delivered). Any EMPTY survivor → None (truncation evidence
    fails the whole set). Deterministic, zero-LLM — this is the driver-side
    claim that becomes a completion's `artifacts` manifest, which the
    substrate re-verifies itself in `sub_sessions.complete`.
    """
    if not touched:
        return None
    survivors = sorted(p for p in touched if _file_still_exists(p))
    if not survivors:
        return None
    if not all(_file_nonempty(path) for path in survivors):
        return None
    artifacts: list[Any] = []
    for reference in survivors:
        root, path = _split_touched_ref(reference)
        direct = Path(path)
        if root == "musubi" and direct.is_absolute():
            try:
                path = direct.resolve().relative_to(
                    _mechanical_registry().root("musubi").path
                ).as_posix()
            except ValueError:
                return None
        artifacts.append(path if root == "musubi" else {"root": root, "path": path})
    return artifacts


def _forced_final_artifacts(answer: str, touched: set[str]) -> list[Any] | None:
    """Surviving artifact paths when a max-turns worker's deliverable is done.

    On top of `surviving_nonempty_files`, the forced-final answer must
    self-declare `status: done` — a direct worker's done-at-cap acceptance
    changes what the ROOT does next (no recovery), so the worker's own claim
    is required as well. A truncated mutation never reaches here — it is
    caught earlier as a typed `[blocked]`/`[incomplete]` answer.
    """
    if not _STATUS_DONE_RE.search(answer or ""):
        return None
    return surviving_nonempty_files(touched)


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
        registry = _mechanical_registry()
        grouped: dict[str, list[str]] = {}
        for reference in lintable:
            root, _ = _split_touched_ref(reference)
            resolved = _resolve_touched_ref(reference)
            relative = resolved.relative_to(registry.root(root).path).as_posix()
            grouped.setdefault(root, []).append(relative)
        result = "pass"
        for root, lint_paths in sorted(grouped.items()):
            raw = await _call_tool_text(
                session,
                "musubi_run_lint",
                {"files": lint_paths, "root": root},
            )
            res = loads_dict(raw)
            if not isinstance(res, dict):
                result, detail = "error", "validator returned no result"
                break
            if not res.get("passed"):
                root_errors = _lint_errors_preview(res)
                errors.extend(root_errors)
                # ruff ran but produced no structured errors → it could not lint
                # (missing/unparseable) rather than found real problems.
                result = "fail" if root_errors else "error"
                if result == "error":
                    detail = f"validator could not lint root {root!r}"
                break

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
    value = frontmatter_dict(agent_md).get("maxOutputTokens")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _frontmatter_max_turns(agent_md: str) -> int | None:
    """Positive per-role turn cap declared as `maxTurns:` frontmatter.

    None when absent or invalid (non-int, bool, <= 0) — the caller omits any
    model-supplied `max_turns`, leaving the server default as sole owner.
    """
    value = frontmatter_dict(agent_md).get("maxTurns")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def frontmatter_dict(agent_md: str) -> dict[str, Any]:
    """Parse the leading `---`-fenced YAML frontmatter block into a dict.

    Empty dict when there is no frontmatter or it does not parse — every caller
    treats a missing key as "not declared", so a fail-closed empty result never
    silently grants anything.
    """
    text = (agent_md or "").lstrip()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        import yaml  # type: ignore[import-untyped]

        fm = yaml.safe_load(text[3:end]) or {}
    except Exception:
        return {}
    return fm if isinstance(fm, dict) else {}


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


def _agents_root(agents_dir: Path | None) -> Path:
    """The repo root that holds `.github/agents`, from a possibly-None dir."""
    base = agents_dir or _default_agents_dir()
    return base.parent.parent if base.name == "agents" else base


def read_worker_prompt(role: str, agents_dir: Path | None = None) -> str:
    """`workers/<role>.agent.md`, or "" when the role has no worker prompt.

    Public because the pipeline runner needs exactly this lookup as the first
    half of its own two-step resolution, and used to reimplement the three
    lines rather than call them.
    """
    return read_agent_prompt(
        [_agents_root(agents_dir)], role, purpose=AgentPromptPurpose.WORKER,
    )


def _read_agent_md(role: str, agents_dir: Path | None) -> str:
    return read_worker_prompt(role, agents_dir)


def _default_agents_dir() -> Path:
    # musubi/agent/subagent.py → parents[2] is the repo root holding .github/.
    return Path(__file__).resolve().parents[2] / ".github" / "agents"


# ── helpers ─────────────────────────────────────────────────────────────────


async def _safe_complete(session: Any, handle_id: str, *, status: str, summary: str) -> None:
    from agent.run import _call_tool_text

    try:
        await _call_tool_text(
            session, "musubi_complete_subagent",
            {"handle_id": handle_id, "status": status, "summary": summary},
        )
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass
