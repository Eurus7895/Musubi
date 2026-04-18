"""MCP stdio server — exposes all harness_* tools to Copilot agents.

Zero LLM calls. Pure routing: MCP tool call → harness component → structured result.

Tools implemented now (Day 2 + Day 3):
    harness_new_session       → state.py
    harness_read_stage        → context_builder.py (firewall) + skill auto-injection
    harness_write_stage       → verifier.py (schema + secrets + contracts) + state.py
    harness_get_status        → state.py
    harness_get_skill         → skill_loader.py
    harness_get_reference     → skill_loader.py

Stub tools (wired in Day 4 when executor.py is built):
    harness_run_lint          → executor.py
    harness_run_typecheck     → executor.py
    harness_run_tests         → executor.py

Skill auto-injection:
    harness_read_stage automatically appends relevant SKILL.md content
    based on STAGE_SKILL_MAP. Agents cannot opt out — skill content
    is part of the tool response.
"""

import json
import sys
from pathlib import Path

# Ensure the copilot-harness directory is on sys.path when run directly.
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

import context_builder
import skill_loader
import state
import verifier

# ── Skill auto-injection map ──────────────────────────────────────────────────
# (stage, agent_name) → list of skill IDs whose SKILL.md is injected into
# the harness_read_stage response. Agent cannot opt out.

_STAGE_SKILL_MAP: dict[tuple[str, str], list[str]] = {
    ("plan",   "designer"):  ["api-design"],
    ("design", "coder"):     ["python"],
    ("code",   "reviewer"):  ["code-review"],   # always — reviewer must use it
}

# Agent → the stage that agent writes to.
# Used to auto-mark that stage in_progress when the agent calls harness_read_stage,
# so crash recovery can identify where the pipeline was interrupted.
_AGENT_OUTPUT_STAGE: dict[str, str] = {
    "planner":  "plan",
    "designer": "design",
    "coder":    "code",
    "reviewer": "review",
}

# ── MCP server ────────────────────────────────────────────────────────────────

mcp = FastMCP("copilot-harness")


# ── State tools ───────────────────────────────────────────────────────────────

@mcp.tool()
def harness_get_active_session() -> str:
    """Check for an active session that needs resuming after a crash or restart.

    Call this BEFORE harness_new_session() at the start of every agent invocation.

    If session_id is non-null, resume from resume_stage instead of starting fresh:
      - Skip any stages before resume_stage (their outputs are already stored).
      - Start work at resume_stage with the given attempt number.

    Returns:
      { "session_id": null }                            → no active session, call harness_new_session()
      { "session_id": "...", "request": "...",          → resume this session
        "resume_stage": "code", "attempt": 2 }
    """
    active = state.get_active_session()
    if active is None:
        return json.dumps({
            "session_id": None,
            "message": "No active session. Call harness_new_session() to start.",
        })
    return json.dumps(active)


@mcp.tool()
def harness_new_session(request: str) -> str:
    """Create a new pipeline session and lock agent versions.

    Call this once at the start of every pipeline run.
    Returns session_id — pass it to every subsequent harness tool call.
    """
    session_id = state.create_session(request)
    versions = state.lock_agent_versions(session_id)
    return json.dumps({
        "session_id": session_id,
        "locked_agent_versions": versions,
    })


@mcp.tool()
def harness_read_stage(session_id: str, stage: str, agent_name: str) -> str:
    """Read a stage output, filtered by calling agent's permissions.

    The harness enforces what each agent is allowed to see:
      - planner:       plan only (retry check)
      - designer:      plan only
      - coder:         plan + design; for review → fix_instructions only
      - reviewer:      plan + design + code
      - skill-builder: no stage access

    Relevant skills are automatically injected into the response based on
    the (stage, agent_name) pair — agents cannot opt out of skill content.
    """
    # Mark the calling agent's output stage as in_progress for crash recovery.
    # Planner reading "plan" → marks plan in_progress before writing.
    output_stage = _AGENT_OUTPUT_STAGE.get(agent_name.lower())
    if output_stage:
        try:
            state.mark_in_progress(session_id, output_stage)
        except Exception:
            pass  # session may not exist yet — don't fail the read

    output = context_builder.read_stage_for_agent(session_id, stage, agent_name)

    result: dict = {}

    if output is None:
        result["data"] = None
        result["note"] = (
            f"Agent '{agent_name}' is not permitted to read stage '{stage}', "
            "or stage has no output yet."
        )
    else:
        result["data"] = output

    # Auto-inject skills based on (stage, agent_name) — pushed, not pulled.
    injected: dict[str, str] = {}
    for skill_id in _STAGE_SKILL_MAP.get((stage, agent_name.lower()), []):
        content = skill_loader.get_skill(skill_id)
        if content:
            injected[skill_id] = content
    if injected:
        result["injected_skills"] = injected

    return json.dumps(result)


@mcp.tool()
def harness_write_stage(
    session_id: str, stage: str, output: str, agent_name: str
) -> str:
    """Write stage output after validation.

    output must be a JSON string. The harness runs (in order):
      1. JSON parse
      2. Injection scan (rejects immediately if found)
      3. Schema + secrets + cross-stage contract validation (verifier.py)
      4. Append-only store in session state
    """
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        return json.dumps({"status": "error", "error": f"Invalid JSON: {exc}"})

    if context_builder.scan_injection(json.dumps(parsed)):
        return json.dumps({
            "status": "error",
            "error": "Output rejected: contains instruction-injection patterns.",
        })

    result = verifier.validate(parsed, agent_name, session_id=session_id)
    if not result.valid:
        return json.dumps({
            "status": "error",
            "error": "Output rejected: validation failed.",
            "validation_errors": result.errors,
        })

    try:
        state.write_stage(session_id, stage, parsed)
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})

    return json.dumps({"status": "stored", "session_id": session_id, "stage": stage})


@mcp.tool()
def harness_get_status(session_id: str) -> str:
    """Return current pipeline status — which stages are complete, pending, or in progress."""
    try:
        status = state.get_status(session_id)
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    return json.dumps(status)


@mcp.tool()
def harness_increment_attempt(session_id: str, stage: str) -> str:
    """Increment the attempt counter for a stage, enabling a retry write.

    Used by the Phase 2 VS Code extension correction loop:
      after a failed review, the extension calls this for both "code" and "review"
      before re-running the coder and reviewer agents.

    state.py enforces write-once per attempt; incrementing creates a new attempt
    row so the next harness_write_stage call succeeds without overwriting history.
    """
    try:
        state.increment_attempt(session_id, stage)
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    attempt = state.get_attempt(session_id, stage)
    return json.dumps({
        "status": "incremented",
        "session_id": session_id,
        "stage": stage,
        "attempt": attempt,
    })


# ── Skill tools ───────────────────────────────────────────────────────────────

@mcp.tool()
def harness_get_skill(skill_id: str) -> str:
    """Return the SKILL.md content for a skill by ID.

    Use this to load a skill on demand. Skills listed in STAGE_SKILL_MAP
    are auto-injected into harness_read_stage — you only need this for
    skills not automatically provided.
    """
    content = skill_loader.get_skill(skill_id)
    if content is None:
        available = [s.skill_id for s in skill_loader.list_skills()]
        return json.dumps({
            "error": f"Skill '{skill_id}' not found.",
            "available_skills": available,
        })
    return content


@mcp.tool()
def harness_get_reference(skill_id: str, reference_name: str) -> str:
    """Return a reference document from a skill's references/ folder.

    Load references only when needed — they are not auto-injected.
    Example: harness_get_reference("code-review", "owasp-top10.md")
    """
    content = skill_loader.get_reference(skill_id, reference_name)
    if content is None:
        available = skill_loader.list_references(skill_id)
        return json.dumps({
            "error": f"Reference '{reference_name}' not found in skill '{skill_id}'.",
            "available_references": available,
        })
    return content


# ── Execution tools — stubs until executor.py is built (Day 3/4) ─────────────

@mcp.tool()
def harness_run_lint(files: list[str]) -> str:
    """Run ruff lint on the specified files. (executor.py — Day 4)"""
    return json.dumps({
        "status": "not_implemented",
        "message": "executor.py not yet built. Coming in Day 4.",
    })


@mcp.tool()
def harness_run_typecheck(files: list[str]) -> str:
    """Run mypy type checking on the specified files. (executor.py — Day 4)"""
    return json.dumps({
        "status": "not_implemented",
        "message": "executor.py not yet built. Coming in Day 4.",
    })


@mcp.tool()
def harness_run_tests(test_dir: str) -> str:
    """Run pytest in the specified directory. (executor.py — Day 4)"""
    return json.dumps({
        "status": "not_implemented",
        "message": "executor.py not yet built. Coming in Day 4.",
    })


# ── Entry point ───────────────────────────────────────────────────────────────

def serve() -> None:
    """Start the MCP stdio server. Called by cli.py."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    serve()
