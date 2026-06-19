"""Reviewer → Coder correction loop.

harness-tier: ephemeral
expires-when: models pass verifier checks on first try at 95th-percentile rate
cost-lever: deletes the retry orchestrator + validation_feedback pipeline
(what: Validation-retry loop.)


Max 3 attempts before escalation. Zero LLM calls.

Public API:
    run(session_id, review_output, db_path?, repo_root?) → LoopResult
    get_attempt_count(session_id, db_path?) → int
    build_retry_context(session_id, db_path?) → list[str]
    escalate(session_id, db_path?) → dict
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from memory import pattern_detector as _pd

from . import state

MAX_ATTEMPTS = 3


# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class LoopResult:
    action: str                              # "pass" | "retry" | "escalate"
    attempt: int                             # code stage attempt after this action
    fix_instructions: list[str] = field(default_factory=list)
    escalation: dict[str, Any] | None = None
    triggered_patches: list[str] = field(default_factory=list)  # proposed patch paths


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
    repo_root: Path | None = None,
) -> LoopResult:
    """Process a review output and decide next action.

    - status == "pass"                         → LoopResult(action="pass")
    - status == "fail" and attempt < MAX       → increment attempt, return retry
    - status == "fail" and attempt >= MAX      → escalate
    - status == "escalate" or "wrong_plan"     → escalate immediately

    Failures are recorded to pattern_detector on every non-pass review.
    When a pattern reaches PATTERN_THRESHOLD, a proposed patch is written to
    .github/agents/proposed/ (only when repo_root is provided).
    """
    status = review_output.get("status", "fail")
    attempt = get_attempt_count(session_id, db_path)

    triggered_patches: list[str] = []

    # Record each failure issue and check for recurring patterns.
    if status != "pass":
        for issue in review_output.get("issues", []):
            desc = issue.get("description", "")
            if desc:
                _pd.record_failure(session_id, "coder", desc, db_path)

        if repo_root is not None:
            for pattern in _pd.detect_patterns("coder", db_path):
                patch_path = _pd.trigger_skill_builder(pattern, repo_root)
                triggered_patches.append(str(patch_path))

    if status == "pass":
        return LoopResult(action="pass", attempt=attempt)

    if status in ("escalate", "wrong_plan") or attempt >= MAX_ATTEMPTS:
        esc = escalate(session_id, db_path)
        return LoopResult(
            action="escalate", attempt=attempt, escalation=esc,
            triggered_patches=triggered_patches,
        )

    new_attempt = state.increment_attempt(session_id, "code", db_path)
    fix = build_retry_context(session_id, db_path)
    return LoopResult(
        action="retry", attempt=new_attempt, fix_instructions=fix,
        triggered_patches=triggered_patches,
    )
