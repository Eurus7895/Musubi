"""MCP stdio server — exposes all harness_* tools to Copilot agents.

Zero LLM calls. Pure routing: MCP tool call → harness component → structured result.

Tools:
    harness_new_session       → state.py
    harness_read_stage        → context_builder.py (firewall) + skill auto-injection
    harness_write_stage       → verifier.py (schema + secrets + contracts) + state.py
    harness_get_status        → state.py
    harness_get_skill         → skill_loader.py
    harness_get_reference     → skill_loader.py
    harness_run_lint          → executor.py (ruff)
    harness_run_typecheck     → executor.py (mypy)
    harness_run_tests         → executor.py (pytest)

Skill auto-injection:
    harness_read_stage automatically appends relevant SKILL.md content
    based on STAGE_SKILL_MAP. Agents cannot opt out — skill content
    is part of the tool response.
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

# Ensure the copilot-harness directory is on sys.path when run directly.
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

import context_builder
import executor
import memory_loader
import session_distiller
import skill_loader
import state
import verifier
from context_builder import AGENT_SKILL_ALLOWLIST, check_skill_permission
from storage import db as _db

# Ensure DB directory + schema exist before any tool call (critical for first run
# when HARNESS_ROOT points to the extension install dir which has no data/ folder yet).
_db.init_db()

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

    # Auto-inject skills — static map floor + plan-declared required_skills.
    # Static map: always injected regardless of task (e.g. reviewer always gets code-review).
    # required_skills: declared by Planner in plan output, filtered through agent's allowlist
    #   so a wrong or irrelevant skill cannot reach an agent that shouldn't see it.
    skill_ids: set[str] = set(_STAGE_SKILL_MAP.get((stage, agent_name.lower()), []))

    # Reviewer is an evaluator — plan-declared required_skills are a generator
    # hint, not relevant to judging the artifact. Skip dynamic injection for
    # the reviewer; only the static code-review skill is injected.
    if agent_name.lower() != "reviewer":
        allowed = AGENT_SKILL_ALLOWLIST.get(agent_name.lower(), set())
        try:
            plan = state.read_stage(session_id, "plan")
            if isinstance(plan, dict):
                for sid in plan.get("required_skills", []):
                    if sid in allowed:
                        skill_ids.add(sid)
        except Exception:
            pass  # plan not yet written — skip dynamic injection

    injected: dict[str, str] = {}
    for skill_id in skill_ids:
        content = skill_loader.get_skill(skill_id)
        if content:
            injected[skill_id] = content
    if injected:
        result["injected_skills"] = injected

    # Inject Tier 1 memory index so agents know what decisions were made and
    # where Tier 2 knowledge lives. Agents can load Tier 2 entries on demand
    # via harness_get_memory_entry().
    # Reviewer is skipped: memory is generator-side learning about producing
    # better outputs. The evaluator must judge against the checklist, not the
    # team's prior preferences.
    if agent_name.lower() != "reviewer":
        mem = memory_loader.get_memory_context()
        if mem:
            result["memory"] = mem

    return json.dumps(result)


@mcp.tool()
def harness_write_stage(
    session_id: str, stage: str, output: Any, agent_name: str
) -> str:
    """Write stage output after validation.

    output may be a JSON string or a JSON-serialisable object — both are accepted.
    The harness runs (in order):
      1. Normalise to parsed dict (parse if string, use directly if already a dict/list)
      2. Injection scan (rejects immediately if found)
      3. Schema + secrets + cross-stage contract validation (verifier.py)
      4. Append-only store in session state
    """
    try:
        # Accept both JSON-string and native JSON object from MCP clients.
        if isinstance(output, str):
            stripped = output.strip()
            if not stripped:
                return json.dumps({
                    "status": "error",
                    "error": "Output rejected: agent returned an empty response.",
                })
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                return json.dumps({"status": "error", "error": f"Invalid JSON: {exc}"})
        else:
            parsed = output

        if parsed is None:
            return json.dumps({
                "status": "error",
                "error": "Output rejected: agent returned null — expected a JSON object.",
            })

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

    except Exception as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})


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
def harness_get_skill(skill_id: str, agent_name: str) -> str:
    """Return the SKILL.md content for a skill by ID.

    Each agent has an explicit allowlist — only skills relevant to its role
    can be loaded on demand. This prevents wrong-domain knowledge (e.g. C++
    or devops skills) from contaminating an agent's reasoning context.

    Skills in STAGE_SKILL_MAP are auto-injected via harness_read_stage.
    Use this only for additional skills the Planner did not declare or
    that were not auto-injected for this stage.
    """
    if not check_skill_permission(agent_name, skill_id):
        allowed = sorted(AGENT_SKILL_ALLOWLIST.get(agent_name.lower(), set()))
        return json.dumps({
            "error": f"Agent '{agent_name}' is not permitted to load skill '{skill_id}'.",
            "allowed_skills": allowed,
        })
    content = skill_loader.get_skill(skill_id)
    if content is None:
        available = [s.skill_id for s in skill_loader.list_skills()]
        return json.dumps({
            "error": f"Skill '{skill_id}' not found.",
            "available_skills": available,
        })
    return content


@mcp.tool()
def harness_get_reference(skill_id: str, reference_name: str, agent_name: str) -> str:
    """Return a reference document from a skill's references/ folder.

    Subject to the same agent allowlist as harness_get_skill — an agent
    cannot access references for a skill it is not permitted to load.

    Load references only when needed — they are not auto-injected.
    Example: harness_get_reference("python", "async-patterns.md", agent_name="coder")
    """
    if not check_skill_permission(agent_name, skill_id):
        allowed = sorted(AGENT_SKILL_ALLOWLIST.get(agent_name.lower(), set()))
        return json.dumps({
            "error": f"Agent '{agent_name}' is not permitted to access skill '{skill_id}'.",
            "allowed_skills": allowed,
        })
    content = skill_loader.get_reference(skill_id, reference_name)
    if content is None:
        available = skill_loader.list_references(skill_id)
        return json.dumps({
            "error": f"Reference '{reference_name}' not found in skill '{skill_id}'.",
            "available_references": available,
        })
    return content


# ── Execution tools ───────────────────────────────────────────────────────────

@mcp.tool()
def harness_run_lint(files: list[str]) -> str:
    """Run ruff check on the specified files. Returns structured lint errors."""
    if not files:
        return json.dumps({"passed": True, "errors": [], "note": "No files specified."})
    result = executor.run_lint(files)
    payload: dict = {
        "passed": result.passed,
        "errors": [
            {"file": e.file, "line": e.line, "col": e.col,
             "code": e.code, "message": e.message}
            for e in result.errors
        ],
    }
    if result.raw and not result.passed and not result.errors:
        payload["raw_output"] = result.raw
    return json.dumps(payload)


@mcp.tool()
def harness_run_typecheck(files: list[str]) -> str:
    """Run mypy type checking on the specified files. Returns structured type errors."""
    if not files:
        return json.dumps({"passed": True, "errors": [], "note": "No files specified."})
    result = executor.run_typecheck(files)
    payload: dict = {
        "passed": result.passed,
        "errors": [
            {"file": e.file, "line": e.line, "message": e.message}
            for e in result.errors
        ],
    }
    if result.raw and not result.passed and not result.errors:
        payload["raw_output"] = result.raw
    return json.dumps(payload)


@mcp.tool()
def harness_run_tests(test_dir: str) -> str:
    """Run pytest in the specified directory. Returns structured test failures."""
    if not test_dir or not test_dir.strip():
        return json.dumps({"passed": False, "failures": [],
                           "error": "test_dir must not be empty."})
    result = executor.run_tests(test_dir)
    payload: dict = {
        "passed": result.passed,
        "failures": [
            {"test_name": f.test_name, "reason": f.reason}
            for f in result.failures
        ],
    }
    if result.raw and not result.passed and not result.failures:
        payload["raw_output"] = result.raw
    return json.dumps(payload)


# ── Memory tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def harness_get_memory_entry(name: str) -> str:
    """Return a Tier 2 memory file from .github/memory/.

    Tier 1 (MEMORY.md index) is always injected automatically by harness_read_stage.
    Use this to load a specific Tier 2 entry on demand (e.g. "architecture.md",
    "failure-patterns.md").

    name must be a plain filename — path traversal is rejected.
    Returns the file content or an error dict.
    """
    content = memory_loader.get_tier2_entry(name)
    if content is None:
        available = memory_loader.list_tier2_entries()
        return json.dumps({
            "error": f"Memory entry '{name}' not found.",
            "available": available,
        })
    return content


@mcp.tool()
def harness_distill_session(session_id: str) -> str:
    """Distill a completed session's review output into Tier 2 failure-patterns.md.

    Extracts critical/high severity issues from the review stage and appends
    any new (deduplicated) patterns to .github/memory/failure-patterns.md.

    Call this after a pipeline run completes (or after escalation) to keep
    the memory layer current.

    Returns { "appended": [...] } listing newly added issue strings.
    """
    try:
        appended = session_distiller.distill_session(session_id)
    except Exception as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return json.dumps({"status": "ok", "appended": appended})


# ── Hook loader (Week 3c) ─────────────────────────────────────────────────────
# hooks.json lives at repo root. If HARNESS_ROOT is set (extension bundle),
# look there first; otherwise look next to this file's parent.

def _resolve_hooks_path() -> Path:
    harness_root = os.environ.get("HARNESS_ROOT")
    if harness_root:
        candidate = Path(harness_root) / "hooks.json"
        if candidate.exists():
            return candidate
    return Path(__file__).parent.parent / "hooks.json"


def _load_hooks() -> dict:
    path = _resolve_hooks_path()
    if not path.exists():
        return {"version": "1.0", "hooks": {}}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"version": "1.0", "hooks": {}}


_HOOKS = _load_hooks()
_REPO_ROOT = _resolve_hooks_path().parent


@mcp.tool()
def harness_run_hook(event: str, payload: str = "") -> str:
    """Execute the hook(s) registered for a lifecycle event.

    event — one of "SessionStart", "PreToolUse", "PostToolUse",
            "on-eval-fail", "on-escalate" (anything listed in hooks.json).
    payload — JSON string piped to each hook on stdin (optional; empty ok).

    Returns a JSON object describing each hook's exit code, stdout, and stderr.
    The harness never substitutes an LLM for a deterministic hook — this
    just shells out and reports what happened.
    """
    configured = _HOOKS.get("hooks", {}).get(event, [])
    if not configured:
        return json.dumps({"event": event, "results": [], "note": "no hooks configured"})

    results: list[dict] = []
    for spec in configured:
        if spec.get("type") != "command":
            results.append({
                "type": spec.get("type"),
                "skipped": f"unsupported hook type {spec.get('type')!r}",
            })
            continue
        cmd_str = spec.get("command", "").strip()
        if not cmd_str:
            continue
        argv = shlex.split(cmd_str)
        try:
            proc = subprocess.run(
                argv,
                input=payload,
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
                timeout=30,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            results.append({
                "command": cmd_str,
                "error": f"{type(exc).__name__}: {exc}",
                "exit_code": None,
            })
            continue
        results.append({
            "command": cmd_str,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        })

    return json.dumps({"event": event, "results": results})


# ── Entry point ───────────────────────────────────────────────────────────────

def serve() -> None:
    """Start the MCP stdio server. Called by cli.py."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    serve()
