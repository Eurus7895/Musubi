"""Reviewer → Coder correction loop.

Max 3 attempts before escalation. Zero LLM calls.

Public API:
    run(session_id, review_output, db_path?) → LoopResult
    get_attempt_count(session_id, db_path?) → int
    build_retry_context(session_id, db_path?) → list[str]
    escalate(session_id, db_path?) → dict
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import state

MAX_ATTEMPTS = 3


# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class LoopResult:
    action: str                              # "pass" | "retry" | "escalate"
    attempt: int                             # code stage attempt after this action
    fix_instructions: list[str] = field(default_factory=list)
    escalation: dict[str, Any] | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def get_attempt_count(session_id: str, db_path: Path | None = None) -> int:
    """Return current attempt number for the code stage."""
    return state.get_attempt(session_id, "code", db_path)


def build_retry_context(session_id: str, db_path: Path | None = None) -> list[str]:
    """Extract fix_instructions from the latest review output."""
    review = state.read_stage(session_id, "review", db_path)
    if not isinstance(review, dict):
        return []
    return [
        issue["fix_instruction"]
        for issue in review.get("issues", [])
        if isinstance(issue, dict) and issue.get("fix_instruction")
    ]


def escalate(session_id: str, db_path: Path | None = None) -> dict[str, Any]:
    """Build an escalation payload with full context for human review."""
    review = state.read_stage(session_id, "review", db_path)
    attempt = get_attempt_count(session_id, db_path)
    return {
        "escalated": True,
        "session_id": session_id,
        "attempt": attempt,
        "reason": "Max correction attempts reached",
        "review_output": review,
        "fix_instructions": build_retry_context(session_id, db_path),
    }


# ── Main loop ─────────────────────────────────────────────────────────────────


def run(
    session_id: str,
    review_output: dict[str, Any],
    db_path: Path | None = None,
) -> LoopResult:
    """Process a review output and decide next action.

    - status == "pass"                         → LoopResult(action="pass")
    - status == "fail" and attempt < MAX       → increment attempt, return retry
    - status == "fail" and attempt >= MAX      → escalate
    - status == "escalate" or "wrong_plan"     → escalate immediately
    """
    status = review_output.get("status", "fail")
    attempt = get_attempt_count(session_id, db_path)

    if status == "pass":
        return LoopResult(action="pass", attempt=attempt)

    if status in ("escalate", "wrong_plan") or attempt >= MAX_ATTEMPTS:
        esc = escalate(session_id, db_path)
        return LoopResult(action="escalate", attempt=attempt, escalation=esc)

    new_attempt = state.increment_attempt(session_id, "code", db_path)
    fix = build_retry_context(session_id, db_path)
    return LoopResult(action="retry", attempt=new_attempt, fix_instructions=fix)
