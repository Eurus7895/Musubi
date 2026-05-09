"""MCP stdio server — exposes all harness_* tools to Copilot agents.

Zero LLM calls. Pure routing: MCP tool call → harness component → structured result.

Tools:
    harness_new_session         → state.py
    harness_read_stage          → context_builder.py (firewall) + skill auto-injection
    harness_write_stage         → verifier.py (schema + secrets + contracts) + state.py
    harness_get_status          → state.py
    harness_get_skill           → skill_loader.py
    harness_get_reference       → skill_loader.py
    harness_run_lint            → executor.py (ruff)
    harness_run_typecheck       → executor.py (mypy)
    harness_run_tests           → executor.py (pytest)
    harness_spawn_subagent      → sub_sessions.spawn (Phase A.1)
    harness_complete_subagent   → sub_sessions.complete (extension-side runner)
    harness_await_subagent      → polls until terminal / wall-clock kill
    harness_list_subagents      → policy_engine spawn allow-list
    harness_append_message      → conversations.append_message (Phase C.1)
    harness_get_conversation    → conversations.get_history (Phase C.1)
    harness_append_failure_pattern → session_distiller.append_pattern (Phase C.2)
    harness_delete_subsessions_for_parent → housekeeping pruner (Phase C.2)

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
import time
from pathlib import Path
from typing import Any

# Ensure the copilot-harness directory is on sys.path when run directly.
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

from execution import executor
from memory import memory_loader, session_distiller
from session import conversations, state, sub_sessions
from skills import skill_loader
from storage import db as _db
from storage import subagent_audit
from session import chunks as session_chunks
from validation import context_builder, subagent_context, verifier
from validation.context_builder import AGENT_SKILL_ALLOWLIST, check_skill_permission


def _add_scripts_to_path() -> None:
    """policy_engine lives in scripts/ at repo root; the extension binary
    sets HARNESS_ROOT to the bundled install dir. Add both candidates so
    the import works in dev (parent.parent) and in the packaged extension.
    """
    candidates: list[Path] = []
    env = os.environ.get("HARNESS_ROOT")
    if env:
        candidates.append(Path(env) / "scripts")
    candidates.append(Path(__file__).parent.parent / "scripts")
    for c in candidates:
        if c.exists() and str(c) not in sys.path:
            sys.path.insert(0, str(c))


_add_scripts_to_path()

import policy_engine as _policy

# Phase G.2 — startup-time policy validation. Catches misconfiguration
# (unknown agents/tools/roles in PIPELINE_POLICIES + SUBAGENT_POLICIES
# + MAIN_SUBAGENT_ALLOWLIST) BEFORE the harness serves any tool calls.
# Raises RuntimeError with a structured error list so the boot failure
# surfaces in the extension's MCP-init log immediately.
_policy.validate_policies_or_raise()

# Ensure DB directory + schema exist before any tool call (critical for first run
# when HARNESS_ROOT points to the extension install dir which has no data/ folder yet).
_db.init_db()

# Startup orphan sweep — any sub-session left in 'running' from a prior crashed
# harness becomes 'abandoned'. Idempotent; runs once at import time.
try:
    sub_sessions.sweep_orphans()
except Exception:
    pass  # Don't block server start on a sweep failure.

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
def harness_clear_active_session() -> str:
    """Clear the active-session pointer without deleting any session data.

    Use to abandon an interrupted pipeline that's stuck pending. Stage
    outputs, audit rows, and the session row are preserved; only the
    pointer that crash-recovery reads is reset. Idempotent.

    Returns:
      { "status": "ok" }
    """
    state.clear_active_session()
    return json.dumps({"status": "ok"})


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
def harness_new_session(request: str, pipeline_name: str = "feature-dev") -> str:
    """Create a new pipeline session and lock agent versions.

    Call this once at the start of every pipeline run.
    Returns session_id — pass it to every subsequent harness tool call.

    `pipeline_name` (Phase G.3) tags the new `pipeline_runs` observability
    row so per-pipeline aggregates stay separable. Default 'feature-dev'
    keeps pre-G.3 callers working unchanged.
    """
    session_id = state.create_session(request, pipeline_name=pipeline_name)
    versions = state.lock_agent_versions(session_id)
    return json.dumps({
        "session_id": session_id,
        "locked_agent_versions": versions,
        "pipeline_name": pipeline_name,
    })


@mcp.tool()
def harness_read_stage(
    session_id: str,
    stage: str,
    agent_name: str,
    chunk_id: str | None = None,
) -> str:
    """Read a stage output, filtered by calling agent's permissions.

    The harness enforces what each agent is allowed to see:
      - planner:       plan only (retry check)
      - designer:      plan only
      - coder:         plan + design; for review → fix_instructions only
      - reviewer:      plan + design + code
      - skill-builder: no stage access

    `chunk_id` (Phase G.1.7) scopes the `user_hint` lookup to a specific
    chunk's output stage so a chunked coder/reviewer retry surfaces the
    hint typed for THAT chunk, not a sibling's. Stage data itself is
    still global (the coder reads the full design — the TS runner
    filters modules per chunk before sending to the LM).

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

    output = context_builder.read_stage_for_agent(
        session_id, stage, agent_name, chunk_id=chunk_id,
    )

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

    # Phase G.1.5 — surface the retry hint set by the gate UI's "Retry
    # this stage" input box. The hint lives on the calling agent's
    # current attempt of its own output stage, so reading any input
    # stage exposes the same hint regardless of which `stage` was asked
    # for. The reviewer doesn't get hints (output-stage='review'; the
    # gate is meant for generator-side correction, not evaluator-side).
    if output_stage and agent_name.lower() != "reviewer":
        try:
            hint = state.read_stage_user_hint(
                session_id, output_stage, chunk_id=chunk_id,
            )
        except ValueError:
            hint = None
        if hint:
            result["user_hint"] = hint
    if chunk_id:
        result["chunk_id"] = chunk_id

    return json.dumps(result)


@mcp.tool()
def harness_write_stage(
    session_id: str,
    stage: str,
    output: Any,
    agent_name: str,
    chunk_id: str | None = None,
) -> str:
    """Write stage output after validation.

    output may be a JSON string or a JSON-serialisable object — both are accepted.
    The harness runs (in order):
      1. Normalise to parsed dict (parse if string, use directly if already a dict/list)
      2. Injection scan (rejects immediately if found)
      3. Schema + secrets + cross-stage contract validation (verifier.py)
      4. Append-only store in session state

    `chunk_id` (Phase G.1.7) targets a per-task chunk row when set so a
    chunked code/review run writes alongside its sibling rows under the
    same session.
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

        # Reviewer severity-rubric enforcement: if the reviewer returned a
        # fail that isn't backed by a critical or high severity issue, the
        # harness rewrites it to pass. Prevents the checklist-opinion
        # correction loop (see CLAUDE.md failure-patterns).
        coerced = False
        if agent_name.lower() == "reviewer" and isinstance(parsed, dict):
            parsed, coerced = verifier.normalize_reviewer_status(parsed)

        try:
            state.write_stage(session_id, stage, parsed, chunk_id=chunk_id)
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})

        response: dict[str, Any] = {
            "status": "stored",
            "session_id": session_id,
            "stage": stage,
        }
        if chunk_id:
            response["chunk_id"] = chunk_id
        if coerced:
            response["status_coerced"] = True
            response["coercion_note"] = parsed.get("status_coercion_reason")
        return json.dumps(response)

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
def harness_increment_attempt(
    session_id: str,
    stage: str,
    user_hint: str | None = None,
    chunk_id: str | None = None,
) -> str:
    """Increment the attempt counter for a stage, enabling a retry write.

    Used by the Phase 2 VS Code extension correction loop and the Phase
    G.1.5 review-gate's "Retry this stage" path. `user_hint` (Phase G.1.5)
    is the optional one-line note from the gate UI's input box; persisted
    on the new attempt row so `harness_read_stage` surfaces it to the
    retrying agent.

    `chunk_id` (Phase G.1.7) scopes the increment to a per-task chunk so
    T1's retries don't bump T2's attempt counter on a chunked code/review
    run.

    state.py enforces write-once per attempt; incrementing creates a new
    attempt row so the next harness_write_stage call succeeds without
    overwriting history.
    """
    try:
        state.increment_attempt(
            session_id, stage,
            user_hint=user_hint, chunk_id=chunk_id,
        )
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    attempt = state.get_attempt(session_id, stage, chunk_id=chunk_id)
    return json.dumps({
        "status": "incremented",
        "session_id": session_id,
        "stage": stage,
        "chunk_id": chunk_id,
        "attempt": attempt,
    })


# ── Phase G.1.5: review-gate pause / resume tools ─────────────────────────

@mcp.tool()
def harness_pause_session(
    session_id: str,
    stage: str,
    reason: str,
    chunk_id: str | None = None,
) -> str:
    """Mark a session paused at `stage` for `reason`.

    Called by the pipeline runner when a review gate fires (after a stage
    completes) or when a sub-agent budget is exhausted mid-stage. The
    pause survives a VS Code restart — `@harness continue` resumes from
    `paused_at_stage`.

    `reason` must be one of: 'stage_review' | 'budget_exhausted'.
    `chunk_id` (Phase G.1.7) records which chunk run the pause belongs
    to so the resume command targets the right chunk.
    """
    try:
        state.pause_session(session_id, stage, reason, chunk_id=chunk_id)
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    return json.dumps({
        "status": "paused",
        "session_id": session_id,
        "paused_at_stage": stage,
        "paused_at_chunk": chunk_id,
        "pause_reason": reason,
    })


@mcp.tool()
def harness_resume_session(
    session_id: str,
    action: str,
    user_hint: str | None = None,
    extra_budget: int = 0,
) -> str:
    """Record a user resume decision for a paused session.

    Valid actions per pause_reason:
      - stage_review:     'approve' | 'retry' | 'abort' | 'auto_approve_rest'
      - budget_exhausted: 'grant' | 'force' | 'abort'

    `user_hint` is the inline retry-box text (only used when action='retry').
    `extra_budget` is the additional spawn count granted on a 'grant' action
    (Phase G.1.5 ships +3 fixed; pipeline.yaml may parameterise it later).

    Clears `paused_at_stage`/`pause_reason` and stages the action under
    `pending_*` columns. The runner reads-and-clears via
    `consume_pending_action` on its next entry.
    """
    try:
        sess = state.resume_session(
            session_id,
            action,
            user_hint=user_hint,
            extra_budget=extra_budget,
        )
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    return json.dumps({
        "status": "resumed",
        "session_id": session_id,
        "action": action,
        "auto_approve_remaining": bool(sess.get("auto_approve_remaining") or 0),
    })


@mcp.tool()
def harness_get_pause_state(session_id: str) -> str:
    """Return the session's pause flags. Runner calls on entry.

    `{paused_at_stage, paused_at_chunk, pause_reason, auto_approve_remaining,
       pending_action, pending_user_hint, pending_extra_budget}`

    `pending_*` are returned read-only here — to consume them (and clear
    in one shot) call `harness_consume_pending_action`.
    """
    sess = state.get_session(session_id)
    if sess is None:
        return json.dumps({"status": "error", "error": f"session {session_id!r} not found"})
    return json.dumps({
        "status": "ok",
        "session_id": session_id,
        "paused_at_stage":         sess.get("paused_at_stage"),
        "paused_at_chunk":         sess.get("paused_at_chunk"),
        "pause_reason":            sess.get("pause_reason"),
        "auto_approve_remaining":  bool(sess.get("auto_approve_remaining") or 0),
        "pending_action":          sess.get("pending_action"),
        "pending_user_hint":       sess.get("pending_user_hint"),
        "pending_extra_budget":    sess.get("pending_extra_budget") or 0,
    })


@mcp.tool()
def harness_compute_chunks(session_id: str) -> str:
    """Phase G.1.7 — compute per-task chunks from the design.

    Reads the latest plan + design for `session_id` and returns a list of
    chunks the runner can iterate over for the coder + reviewer stages.

    Response shape:
      { "status": "ok",
        "chunks": [{ "chunk_id": "T1", "task_label": "T1 — …",
                     "file_paths": ["a.py", "b.py"] }, …] }

    `chunks` is empty when the design fits a single coder run (zero or
    one task with modules) — the runner falls back to today's
    non-chunked path.
    """
    try:
        plan = state.read_stage(session_id, "plan")
        design = state.read_stage(session_id, "design")
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    if plan is None or design is None:
        return json.dumps({
            "status": "ok",
            "session_id": session_id,
            "chunks": [],
            "reason": "plan or design not yet written",
        })
    computed = session_chunks.compute_chunks(plan, design)
    return json.dumps({
        "status": "ok",
        "session_id": session_id,
        "chunks": [
            {
                "chunk_id":   c.chunk_id,
                "task_label": c.task_label,
                "file_paths": list(c.file_paths),
            }
            for c in computed
        ],
    })


@mcp.tool()
def harness_ensure_chunk_row(
    session_id: str,
    stage: str,
    chunk_id: str,
) -> str:
    """Phase G.1.7 — ensure an `attempt=1` row exists for a chunked stage.

    The runner calls this on entry to a new chunk's coder/reviewer pair
    so subsequent `harness_write_stage` / `harness_increment_attempt`
    calls have a row to update. Idempotent: returns the current attempt
    if the row already exists.
    """
    try:
        attempt = state.ensure_chunk_row(session_id, stage, chunk_id)
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    return json.dumps({
        "status": "ok",
        "session_id": session_id,
        "stage": stage,
        "chunk_id": chunk_id,
        "attempt": attempt,
    })


@mcp.tool()
def harness_consume_pending_action(session_id: str) -> str:
    """Atomically read-and-clear the session's `pending_*` payload.

    Returns `{status: "ok", action, user_hint, extra_budget}` when an
    action was pending, or `{status: "ok", action: null}` when none.
    Calling twice in a row returns `null` on the second call — the
    runner relies on this single-consume invariant to avoid double-
    applying a resume decision.
    """
    payload = state.consume_pending_action(session_id)
    if payload is None:
        return json.dumps({"status": "ok", "action": None})
    return json.dumps({
        "status": "ok",
        "action":       payload["action"],
        "user_hint":    payload.get("user_hint"),
        "extra_budget": payload.get("extra_budget", 0),
    })


# ── Phase G.2: pipeline correction-rule loading ──────────────────────────

@mcp.tool()
def harness_get_correction_rules(pipeline_name: str) -> str:
    """Return the parsed `correction.escalate_on_*` rules for a pipeline.

    The TS runner consults this once per pipeline to know whether to
    coerce a reviewer's status to 'escalate' on critical/category
    matches instead of running the correction loop.

    Returns defaults when the pipeline.yaml file is missing, malformed,
    or omits the `correction` block:
      - escalate_on_critical: true
      - escalate_on_categories: []
    """
    import yaml  # type: ignore[import-untyped]
    defaults = {"escalate_on_critical": True, "escalate_on_categories": []}
    safe_name = (pipeline_name or "").strip()
    if not safe_name or "/" in safe_name or ".." in safe_name:
        return json.dumps({"status": "ok", "rules": defaults, "source": "default-invalid-name"})
    candidate = (
        Path(__file__).resolve().parent.parent
        / ".github" / "pipelines" / safe_name / "pipeline.yaml"
    )
    if not candidate.exists():
        return json.dumps({"status": "ok", "rules": defaults, "source": "default-no-file"})
    try:
        with candidate.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:
        return json.dumps({
            "status": "ok",
            "rules": defaults,
            "source": "default-parse-error",
            "error": f"{type(exc).__name__}: {exc}",
        })
    correction = data.get("correction") if isinstance(data, dict) else None
    if not isinstance(correction, dict):
        return json.dumps({"status": "ok", "rules": defaults, "source": "default-no-correction"})
    on_critical = correction.get("escalate_on_critical")
    on_categories = correction.get("escalate_on_categories")
    rules: dict[str, Any] = {
        "escalate_on_critical":
            bool(on_critical) if isinstance(on_critical, bool) else True,
        "escalate_on_categories":
            [c for c in on_categories if isinstance(c, str) and c]
            if isinstance(on_categories, list) else [],
    }
    return json.dumps({"status": "ok", "rules": rules, "source": str(candidate)})


# ── Phase G.3: observability primitives ───────────────────────────────────

@mcp.tool()
def harness_record_stage_metric(
    session_id: str,
    stage: str,
    attempt: int,
    started_at: float,
    ended_at: float,
    tokens_in_estimate: int,
    tokens_out_estimate: int,
    lm_ms: int,
    chunk_id: str | None = None,
    tool_count: int = 0,
    tool_failures: int = 0,
) -> str:
    """Append one row to `stage_metrics` after a stage's LM round-trip.

    Called by the TS runner immediately after `vscode.lm.sendRequest`
    completes — the wall-clock ms + token estimates are already on hand
    there. Token counts are estimates (chars/4 heuristic from
    `runners/orchestratorCore.estimateTokens`), not billed amounts.

    Failures are non-fatal — observability writes must never abort a
    pipeline run.
    """
    try:
        _db.insert_stage_metric(
            session_id, stage, attempt, started_at, ended_at,
            tokens_in_estimate, tokens_out_estimate, lm_ms,
            chunk_id=chunk_id, tool_count=tool_count, tool_failures=tool_failures,
        )
    except Exception as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return json.dumps({"status": "ok"})


@mcp.tool()
def harness_finalize_pipeline_run(
    session_id: str,
    final_status: str,
    escalated: bool = False,
    chunked: bool = False,
    chunk_count: int = 0,
) -> str:
    """Close out the `pipeline_runs` row for `session_id` when the
    runner finishes.

    Auto-derives:
      - `total_tokens_estimate` from accumulated `stage_metrics` rows
      - `correction_attempts` from the highest 'code' stage attempt
        per chunk in `stage_outputs` (chunked runs sum across chunks)

    `final_status` ∈ {'success', 'escalated', 'aborted'}. Idempotent
    via UPDATE — calling twice on a retry simply overwrites with the
    latest known state.
    """
    if final_status not in {"success", "escalated", "aborted"}:
        return json.dumps({
            "status": "error",
            "error": f"final_status must be one of success|escalated|aborted, got {final_status!r}",
        })
    import time as _time
    try:
        total_tokens = _db.total_tokens_for_session(session_id)
        correction_attempts = _db.derive_correction_attempts(session_id)
        _db.finalize_pipeline_run(
            session_id=session_id,
            ended_at=_time.time(),
            final_status=final_status,
            total_tokens_estimate=total_tokens,
            correction_attempts=correction_attempts,
            escalated=escalated,
            chunked=chunked,
            chunk_count=chunk_count,
        )
    except Exception as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return json.dumps({
        "status": "ok",
        "total_tokens_estimate": total_tokens,
        "correction_attempts": correction_attempts,
    })


@mcp.tool()
def harness_query_pipeline_runs(
    pipeline_name: str | None = None,
    limit: int = 50,
    since_ts: float | None = None,
) -> str:
    """Read `pipeline_runs` rows, newest first. Optional filters:
    pipeline_name (exact match), since_ts (started_at >= ts).

    Includes in-flight rows (ended_at IS NULL) so a UI can show
    "running now" alongside historical runs. `harness_pipeline_stats`
    excludes them from aggregates.
    """
    try:
        rows = _db.query_pipeline_runs(
            pipeline_name=pipeline_name, limit=limit, since_ts=since_ts,
        )
    except Exception as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return json.dumps({"status": "ok", "rows": rows})


@mcp.tool()
def harness_query_stage_metrics(session_id: str) -> str:
    """Per-session breakdown — every stage_metrics row for this session,
    chronological. Useful for debugging "why did this run cost so many
    tokens" by walking the per-stage estimates."""
    try:
        rows = _db.query_stage_metrics(session_id)
    except Exception as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return json.dumps({"status": "ok", "session_id": session_id, "rows": rows})


@mcp.tool()
def harness_pipeline_stats(
    pipeline_name: str,
    since_ts: float | None = None,
) -> str:
    """Aggregate stats over TERMINAL `pipeline_runs` for `pipeline_name`.

    Returns success-rate, escalate-rate, median + p90 token estimates,
    median wall-clock ms, median correction attempts, percentage of
    chunked runs. Empty input ⇒ a zero-valued summary so UI callers
    don't crash on "haven't run anything yet."
    """
    try:
        from validation import observability
        rows = _db.query_pipeline_runs_for_stats(
            pipeline_name=pipeline_name, since_ts=since_ts,
        )
        stats = observability.aggregate_pipeline_stats(rows)
    except Exception as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return json.dumps({
        "status": "ok",
        "pipeline_name": pipeline_name,
        **stats,
    })


# ── Phase G.2: schema-migration audit query ───────────────────────────────

@mcp.tool()
def harness_query_schema_migrations(
    session_id: str | None = None,
    limit: int = 100,
) -> str:
    """Return rows from the `schema_migrations` audit table, newest first.

    Optional `session_id` filters to one session's migrations. `limit`
    caps the result set (default 100). Hard Invariant #8 ("no silent
    migrations") — same discipline as `harness_query_subagent_events`.
    """
    try:
        rows = _db.query_schema_migrations(session_id=session_id, limit=limit)
    except Exception as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return json.dumps({
        "status": "ok",
        "session_id": session_id,
        "rows": rows,
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
def harness_list_skills(agent_name: str) -> str:
    """Return the catalog of skills the calling agent may load.

    Week 4 Day 3 — enables direct-mode pull-on-demand. The extension injects
    the catalog into the system prompt so the LLM knows which skill_ids it
    may request via harness_get_skill / harness_get_reference mid-response.

    Returns JSON { "skills": [{"skill_id", "title"}, ...], "agent_name": ... }.
    Skills outside the caller's allowlist are filtered out — the catalog is
    the only route by which an agent discovers skills it can pull.
    """
    key = agent_name.lower().strip()
    allowed = AGENT_SKILL_ALLOWLIST.get(key, set())
    catalog: list[dict[str, str]] = []
    for meta in skill_loader.list_skills():
        if meta.skill_id in allowed:
            catalog.append({"skill_id": meta.skill_id, "title": meta.title})
    return json.dumps({"agent_name": key, "skills": catalog})


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
def harness_get_memory_context() -> str:
    """Return the Tier 1 memory index + list of Tier 2 entries available.

    Wraps memory_loader.get_memory_context so direct mode (and any other
    caller that does not go through harness_read_stage) can inject project
    memory into its prompt. Pipeline agents already receive this via the
    stage-firewalled read, so they should NOT call this tool directly.

    Result shape:
        { "tier1_index": "...MEMORY.md content...",
          "tier2_available": ["architecture.md", "failure-patterns.md"] }
    Empty object when no MEMORY.md exists.
    """
    return json.dumps(memory_loader.get_memory_context())


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
def harness_query_sessions(query: str, limit: int = 20) -> str:
    """Search prior sessions for requests or review outputs matching `query`.

    Week 4 Day 4 — cross-session memory query. Case-insensitive substring
    match against session requests and stored review output. Returns session
    IDs with short excerpts (never full transcripts) so a caller can decide
    whether to pull a specific session for deeper inspection.

    Result shape:
        { "query": str, "results": [
            { "session_id", "request", "created_at", "match_source",
              "review_snippets"? } ] }
    """
    try:
        results = memory_loader.query_sessions(query, limit=limit)
    except Exception as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return json.dumps({"query": query, "results": results})


@mcp.tool()
def harness_compact_memory() -> str:
    """Rewrite .github/memory/failure-patterns.md if it has grown past 5 KB.

    Week 4 Day 4 — keeps only the most-frequent + most-recent entries so the
    file stays compact enough for Tier 2 injection without losing signal.

    Returns { "compacted": bool, "before_bytes", "after_bytes", "kept", "dropped" }.
    Safe to call as an idempotent operation — below the threshold it no-ops.
    """
    try:
        result = session_distiller.compact_failure_patterns()
    except Exception as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return json.dumps(result)


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


# ── Sub-agent tools (Phase A.1) ───────────────────────────────────────────────
#
# A *sub-session* is the row for an agent-spawned-by-another-agent invocation.
# The harness validates the spawn (policy intersection), records the row, and
# tracks lifecycle. The actual sub-agent loop runs in the VS Code extension —
# the harness never calls an LLM. The extension calls `harness_complete_*`
# when the sub-agent finishes; the parent agent calls `harness_await_*` to
# block until that happens (or until the wall-clock cap fires).

# Polling cadence for harness_await_subagent. Tests can override via the
# HARNESS_SUBAGENT_POLL_S env var to keep the suite fast.
_AWAIT_POLL_S: float = float(os.environ.get("HARNESS_SUBAGENT_POLL_S", "0.25"))


@mcp.tool()
def harness_spawn_subagent(
    parent_session_id: str,
    parent_agent_name: str,
    role: str,
    brief: str,
    allowed_tools: list[str] | None = None,
    max_turns: int = sub_sessions.DEFAULT_MAX_TURNS,
    per_turn_timeout_s: int = sub_sessions.DEFAULT_PER_TURN_TIMEOUT_S,
    wall_clock_timeout_s: int = sub_sessions.DEFAULT_WALL_CLOCK_TIMEOUT_S,
    output_schema: str | None = None,
) -> str:
    """Spawn a sub-agent run. Returns a handle_id the parent can await.

    Validation (fail-closed):
      1. role must exist in SUBAGENT_POLICIES.
      2. parent_agent_name must list role in MAIN_SUBAGENT_ALLOWLIST.
      3. effective_tools = SUBAGENT_POLICIES[role] ∩ allowed_tools.
         Empty intersection → reject; the sub-agent would have nothing to do.

    Four-layer timeouts are recorded on the row:
      - max_turns                 (caller arg)
      - per_turn_timeout_s        (default 60)
      - wall_clock_timeout_s      (default 300)
      - await max_wait_s          (default 300, harness_await_subagent arg)

    Returns: { handle_id, role, parent_session_id, effective_tools,
               max_turns, per_turn_timeout_s, wall_clock_timeout_s }.
    """
    # 1. Role + main allow-list intersection.
    if not _policy.check_subagent_allowed(parent_agent_name, role):
        return json.dumps({
            "status": "error",
            "error": _policy.subagent_deny_reason(parent_agent_name, role),
        })

    # 2. Parent session must exist (foreign-key safety + clearer error).
    if state.get_session(parent_session_id) is None:
        return json.dumps({
            "status": "error",
            "error": f"parent session {parent_session_id!r} not found",
        })

    # 3. Effective tools = role ∩ requested. Caller's main-tool list is the
    #    cap that pre_tool_use.py enforces at run-time via PIPELINE_POLICIES;
    #    we intersect with `allowed_tools` here so the sub-agent's recorded
    #    set never exceeds what the caller passes in.
    requested = list(allowed_tools) if allowed_tools is not None else None
    role_tools = _policy.get_subagent_tools(role)
    if requested is None:
        effective_tools = role_tools
    else:
        effective_tools = [t for t in role_tools if t in requested]
    if not effective_tools:
        return json.dumps({
            "status": "error",
            "error": (
                f"No tools available for sub-agent role {role!r} after "
                f"intersecting with caller's allow-list. "
                f"Role tools: {role_tools}; requested: {requested}."
            ),
        })

    try:
        handle_id = sub_sessions.spawn(
            parent_session_id=parent_session_id,
            parent_agent_name=parent_agent_name,
            role=role,
            brief=brief,
            allowed_tools=effective_tools,
            max_turns=max_turns,
            per_turn_timeout_s=per_turn_timeout_s,
            wall_clock_timeout_s=wall_clock_timeout_s,
            output_schema=output_schema,
        )
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})

    # Phase A.3 — durable spawn audit row. The chat-marker side is
    # extension-side (Phase A.3 TS work); this guarantees the spawn is
    # provable post-hoc even if the marker scrolls off-screen.
    try:
        subagent_audit.record_spawn(
            handle_id=handle_id,
            parent_session_id=parent_session_id,
            parent_agent_name=parent_agent_name,
            role=role,
            brief=brief,
            allowed_tools=effective_tools,
            max_turns=max_turns,
            wall_clock_timeout_s=wall_clock_timeout_s,
        )
    except Exception:
        # Audit failure must not silently drop a spawn — but it also must
        # not block the spawn itself. We swallow here and rely on the
        # extension's own pre-spawn marker for visibility; durable audit
        # for this run is lost only if the audit DB is unwritable.
        pass

    return json.dumps({
        "status": "spawned",
        "handle_id": handle_id,
        "role": role,
        "parent_session_id": parent_session_id,
        "parent_agent_name": parent_agent_name,
        "effective_tools": effective_tools,
        "max_turns": max_turns,
        "per_turn_timeout_s": per_turn_timeout_s,
        "wall_clock_timeout_s": wall_clock_timeout_s,
    })


@mcp.tool()
def harness_complete_subagent(
    handle_id: str,
    summary: str | None = None,
    structured: Any | None = None,
    tools_used: list[str] | None = None,
    turns: int = 0,
    status: str = "done",
    max_summary_tokens: int = verifier.DEFAULT_SUBAGENT_MAX_TOKENS,
) -> str:
    """Record the terminal result of a sub-agent run.

    Called by the VS Code extension's sub-agent runner after the sub-agent
    produces its summary. The harness applies four-layer timeout checks
    here — even if the runner reports `status='done'`, exceeding max_turns
    or wall_clock_timeout_s coerces the row to status='escalated' with an
    explanatory note appended to the summary.

    Phase A.2 firewall — the recorded summary is also passed through
    `verifier.verify_subagent_summary`:
      - over-cap text is truncated with a marker (cap = max_summary_tokens
        ≈ chars/4),
      - secrets / instruction-injection in the summary force status='failed'
        with a structured error so the parent never sees the offending text,
      - if `output_schema` was set at spawn time and `structured` is given,
        the structured payload is validated against that schema.

    `status` ∈ {'done', 'failed', 'escalated', 'abandoned'}.
    `structured` may be any JSON-serialisable value (or null).
    """
    # Pull the recorded output_schema (set at spawn time) so the runner
    # cannot dodge the schema check by omitting it on completion.
    existing_row = sub_sessions.get(handle_id)
    schema_dict: dict[str, Any] | None = None
    if existing_row is not None and existing_row.get("output_schema"):
        try:
            schema_dict = json.loads(existing_row["output_schema"])
        except (TypeError, json.JSONDecodeError):
            schema_dict = None

    verify = verifier.verify_subagent_summary(
        summary,
        structured=structured,
        max_tokens=max_summary_tokens,
        schema=schema_dict,
    )

    final_status = status
    safe_summary = verify.summary
    if not verify.valid:
        # Treat as hard fail — but persist it so the audit trail records
        # the rejection. We replace the summary with a structured error
        # so the parent never sees the rejected content.
        final_status = "failed"
        safe_summary = (
            "[harness] sub-agent result rejected by verify_subagent_summary: "
            + "; ".join(verify.errors)
        )
        structured = None  # don't propagate a malformed structured payload

    try:
        final = sub_sessions.complete(
            handle_id,
            summary=safe_summary,
            structured=structured,
            tools_used=tools_used,
            turns=turns,
            status=final_status,
        )
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})

    # Phase A.3 — durable completion audit row, mirror of the spawn row.
    try:
        subagent_audit.record_complete(
            handle_id=handle_id,
            parent_session_id=final["parent_session_id"],
            parent_agent_name=final["parent_agent_name"],
            role=final["role"],
            brief=final["brief"],
            final_status=final["status"],
            escalated=bool(final["escalated"]),
            turns=int(final.get("turns", 0) or 0),
            tools_used=final.get("tools_used"),
            summary_truncated=verify.truncated,
            verification_errors=verify.errors if verify.errors else None,
        )
    except Exception:
        pass

    response: dict[str, Any] = {
        "status": "recorded",
        "handle_id": handle_id,
        "final_status": final["status"],
        "escalated": bool(final["escalated"]),
        "summary": final.get("result_summary"),
        "structured": final.get("result_structured"),
        "tools_used": final.get("tools_used"),
        "turns": final.get("turns", 0),
    }
    if verify.truncated:
        response["summary_truncated"] = True
    if not verify.valid:
        response["verification_errors"] = verify.errors
    return json.dumps(response)


@mcp.tool()
def harness_await_subagent(
    handle_id: str, max_wait_s: int = sub_sessions.DEFAULT_AWAIT_MAX_WAIT_S
) -> str:
    """Block until the sub-session is terminal or the wall-clock cap fires.

    Polls the row in-process. If the sub-session is still running after
    `max_wait_s`, returns the current row with `still_running: true` so the
    parent can retry. If the row's `wall_clock_timeout_s` has elapsed since
    creation while we were waiting, the harness coerces the row to
    `status='escalated'` and returns the escalated final state — this is
    the wall-clock kill the design.md spec requires.

    Result on terminal:
      { status: 'recorded', final_status, escalated, summary, structured,
        tools_used, turns }

    Result on still-running after max_wait_s:
      { status: 'pending', still_running: true, handle_id, snapshot: {...} }
    """
    if max_wait_s < 0:
        return json.dumps({
            "status": "error",
            "error": "max_wait_s must be >= 0",
        })

    deadline = time.monotonic() + float(max_wait_s)
    poll = max(0.05, _AWAIT_POLL_S)

    while True:
        row = sub_sessions.get(handle_id)
        if row is None:
            return json.dumps({
                "status": "error",
                "error": f"handle {handle_id!r} not found",
            })

        if row["status"] != "running":
            return json.dumps({
                "status": "recorded",
                "handle_id": handle_id,
                "final_status": row["status"],
                "escalated": bool(row["escalated"]),
                "summary": row.get("result_summary"),
                "structured": row.get("result_structured"),
                "tools_used": row.get("tools_used"),
                "turns": row.get("turns", 0),
            })

        # Wall-clock kill: harness escalates a long-running sub-session even
        # if the runner never reports completion. Computed by complete()
        # when called with the row's existing turn count.
        created = sub_sessions._parse_iso(row["created_at"])
        elapsed = (sub_sessions._now_dt() - created).total_seconds()
        if elapsed > row["wall_clock_timeout_s"]:
            try:
                final = sub_sessions.complete(
                    handle_id,
                    summary=row.get("result_summary"),
                    structured=row.get("result_structured"),
                    tools_used=row.get("tools_used"),
                    turns=row.get("turns", 0) or 0,
                    status="escalated",
                )
            except ValueError:
                # Already terminal — re-read.
                final = sub_sessions.get(handle_id) or row
            return json.dumps({
                "status": "recorded",
                "handle_id": handle_id,
                "final_status": final["status"],
                "escalated": bool(final["escalated"]),
                "summary": final.get("result_summary"),
                "structured": final.get("result_structured"),
                "tools_used": final.get("tools_used"),
                "turns": final.get("turns", 0),
            })

        if time.monotonic() >= deadline:
            return json.dumps({
                "status": "pending",
                "still_running": True,
                "handle_id": handle_id,
                "snapshot": {
                    "role": row["role"],
                    "parent_agent_name": row["parent_agent_name"],
                    "turns": row.get("turns", 0),
                    "elapsed_s": int(elapsed),
                    "wall_clock_timeout_s": row["wall_clock_timeout_s"],
                },
            })

        time.sleep(poll)


@mcp.tool()
def harness_get_subagent_context(handle_id: str) -> str:
    """Return the firewalled pre-prompt payload for a spawned sub-session.

    Phase A.2 firewall — sub-agents see exactly two things: the spawn
    `brief` and the role's SKILL.md (when registered). They never see the
    parent's session state, memory, or sibling sub-agents. The runner
    (Phase A.3) calls this once per spawn and uses the result verbatim.

    Result on success:
      { status: 'ok', brief, role, role_skill, allowed_tools }
    Result on missing handle:
      { status: 'error', error: 'handle … not found' }
    """
    row = sub_sessions.get(handle_id)
    if row is None:
        return json.dumps({
            "status": "error",
            "error": f"handle {handle_id!r} not found",
        })
    try:
        ctx = subagent_context.build_subagent_context(
            brief=row["brief"], role=row["role"]
        )
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})

    return json.dumps({
        "status": "ok",
        "handle_id": handle_id,
        "brief": ctx.brief,
        "role": ctx.role,
        "role_skill": ctx.role_skill,
        "allowed_tools": list(ctx.allowed_tools),
    })


@mcp.tool()
def harness_query_subagent_events(
    parent_session_id: str | None = None,
    handle_id: str | None = None,
    since_ts: float | None = None,
    limit: int = 200,
) -> str:
    """Return the durable audit log for sub-agent spawns + completions.

    Phase A.3 — the extension polls this tool to render chat markers
    ("explorer spawned with brief X", "investigator done in 4 turns").
    The audit is also evidence-of-record for the "no silent sub agents"
    invariant: a spawn that produces no chat marker still leaves a row
    here.

    Filters are AND-combined; pass None / omit to skip a filter.

    Result: { events: [...], count: N }. Each event has:
      ts, handle_id, parent_session_id, parent_agent_name, role, brief,
      event ('spawned' | 'completed'),
      + spawn-only: allowed_tools, max_turns, wall_clock_timeout_s,
      + complete-only: final_status, escalated, turns, tools_used,
                       summary_truncated, verification_errors.
    """
    try:
        events = subagent_audit.query_events(
            parent_session_id=parent_session_id,
            handle_id=handle_id,
            since_ts=since_ts,
            limit=limit,
        )
    except Exception as exc:
        return json.dumps({
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        })
    return json.dumps({"events": events, "count": len(events)})


@mcp.tool()
def harness_list_subagents(main_agent_name: str) -> str:
    """Return the spawn allow-list for a main agent.

    The VS Code extension's runner injects this into the main agent's
    tool catalog so the LLM only sees roles it is permitted to spawn.

    Result: { main_agent, roles: [ {role, allowed_tools}, ... ] }.
    Unknown / un-allow-listed mains return an empty roles array (fail-closed).
    """
    roles = _policy.list_subagent_roles(main_agent_name)
    catalog = [
        {"role": r, "allowed_tools": _policy.get_subagent_tools(r)}
        for r in roles
    ]
    return json.dumps({"main_agent": main_agent_name, "roles": catalog})


# ── Conversation continuity (Phase C.1) ───────────────────────────────────────
# Storage seam for orchestrator replay-on-each-turn. Roles are validated
# fail-closed against `conversations.VALID_ROLES`. `chat_id` is opaque to the
# harness — the runner mints it.

@mcp.tool()
def harness_append_message(chat_id: str, role: str, content: Any) -> str:
    """Append a message to an orchestrator conversation.

    Roles: 'user' | 'assistant' | 'tool' | 'system'. Anything else
    rejects fail-closed. `chat_id` is opaque — the runner is responsible
    for mint stability across turns.

    `content` is annotated `Any` rather than `str` because FastMCP's
    Pydantic layer was rejecting JSON-object-shaped string content
    ("Input should be a valid string ... input_type=dict") for the
    orchestrator's tool-result rows. Whatever path turns the wire
    string back into a dict, this entrypoint accepts both shapes and
    coerces dict → JSON-string before storage.

    Result on success:
      { status: 'ok', message_id, ts, tokens_estimate }
    Result on bad input:
      { status: 'error', error: '...' }
    """
    if not isinstance(content, str):
        try:
            content = json.dumps(content)
        except (TypeError, ValueError) as exc:
            return json.dumps({"status": "error", "error": f"content not serializable: {exc}"})
    try:
        result = conversations.append_message(
            chat_id=chat_id, role=role, content=content
        )
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    return json.dumps({"status": "ok", **result})


@mcp.tool()
def harness_append_failure_pattern(
    agent: str,
    issue: str,
    source: str = "orchestrator",
) -> str:
    """Record a failure pattern from an orchestrator distillation trigger.

    Phase C.2 — orchestrator-driven entry point. Mirrors
    `session_distiller.append_pattern`: dedupes by (agent, issue prefix)
    against the existing `.github/memory/failure-patterns.md` and appends
    a new row when the pair is new. `source` is recorded in place of a
    session id so the audit trail still names the trigger
    ('reviewer-fail' / 'frustration:<label>' / etc.).

    Result on success:
      { status: 'ok', appended: bool, issue?: '...' }   # appended=false → deduped
    Result on bad input:
      { status: 'error', error: '...' }
    """
    if not isinstance(agent, str) or not agent.strip():
        return json.dumps({"status": "error", "error": "agent must be a non-empty string"})
    if not isinstance(issue, str) or not issue.strip():
        return json.dumps({"status": "error", "error": "issue must be a non-empty string"})
    appended = session_distiller.append_pattern(
        agent.strip(), issue.strip(), source=source,
    )
    if appended is None:
        return json.dumps({"status": "ok", "appended": False})
    return json.dumps({"status": "ok", "appended": True, "issue": appended})


@mcp.tool()
def harness_delete_subsessions_for_parent(
    parent_session_id: str,
    older_than_iso: str | None = None,
) -> str:
    """Housekeeping pruner — delete terminal sub-sessions for a parent.

    Phase C.2 — only rows in {'done','failed','escalated','abandoned'} are
    eligible; `running` rows are never touched. When `older_than_iso` is
    provided, only rows whose `completed_at` predates it are deleted.
    The mirror rows in `subagent_audit` are preserved so forensic queries
    still work.

    Result:
      { status: 'ok', deleted: N }
    """
    try:
        deleted = _db.delete_terminal_sub_sessions_for_parent(
            parent_session_id, older_than_iso=older_than_iso,
        )
    except Exception as exc:
        return json.dumps({
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        })
    return json.dumps({"status": "ok", "deleted": deleted})


@mcp.tool()
def harness_get_conversation(
    chat_id: str,
    max_tokens: int = conversations.DEFAULT_MAX_TOKENS,
    role_filter: list[str] | None = None,
) -> str:
    """Return a token-budgeted, chronological history for `chat_id`.

    Newest-first truncation: when the running token total would exceed
    `max_tokens`, older messages are dropped first. The returned list is
    chronological so the runner can splice it into `LanguageModelChatMessage[]`
    directly. A single oversized message is still returned (with
    `truncated=true`) so the runner always has at least one prior turn.

    Result:
      { status: 'ok', messages: [...], total_tokens, truncated, dropped_count }
    """
    try:
        history = conversations.get_history(
            chat_id=chat_id,
            max_tokens=max_tokens,
            role_filter=role_filter,
        )
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    return json.dumps({"status": "ok", **history})


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
