"""Context firewall — each agent receives only what its role requires.

Per-agent rules (from CLAUDE.md):
    planner       → request only  (no stage outputs)
    designer      → plan output only  (no request text)
    coder         → plan + design; on retry: + fix_instructions only (not full review)
    reviewer      → request + all stage outputs
    skill-builder → fail patterns only  (no session state, no user code)
"""

import os
import re
from pathlib import Path
from typing import Any

import state
from storage import db

# ── Injection detection ───────────────────────────────────────────────────────

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(?:your|all|previous|prior)(?:\s+(?:your|all|previous|prior))?\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"forget\s+(previous|prior|all|your)\b", re.IGNORECASE),
    re.compile(r"disregard\s+(all|previous|prior|your)\s+instructions?", re.IGNORECASE),
    re.compile(r"override\s+(all|previous|prior|your)\s+instructions?", re.IGNORECASE),
    re.compile(r"new\s+system\s+prompt\b", re.IGNORECASE),
    re.compile(r"act\s+as\s+(if|though)\s+you\s+(are|were)\b", re.IGNORECASE),
    re.compile(r"pretend\s+(you\s+are|to\s+be)\b", re.IGNORECASE),
    re.compile(r"</?(system|user|assistant)>", re.IGNORECASE),
    re.compile(r"\[\s*system\s*\]", re.IGNORECASE),
]


def scan_injection(text: str) -> bool:
    """Return True if text contains a prompt-injection attempt."""
    return any(p.search(text) for p in _INJECTION_PATTERNS)


# ── Skill-Builder path guard ──────────────────────────────────────────────────

_PROPOSED_PATTERN = re.compile(
    r"(^|[/\\])\.github[/\\]agents[/\\]proposed[/\\]", re.IGNORECASE
)


def validate_skill_builder_write(path: str | Path) -> bool:
    """Return True only if path is inside .github/agents/proposed/.

    Normalises the path first so traversal tricks like proposed/../ are blocked.
    """
    normalized = os.path.normpath(str(path))
    return bool(_PROPOSED_PATTERN.search(normalized))


# ── Context builder ───────────────────────────────────────────────────────────

def build_context(
    session_id: str,
    agent_name: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Return a filtered context dict for the given agent.

    Raises ValueError for unknown agent names.
    """
    agent = agent_name.lower().strip()

    if agent == "planner":
        return _context_planner(session_id, db_path)
    if agent == "designer":
        return _context_designer(session_id, db_path)
    if agent == "coder":
        return _context_coder(session_id, db_path)
    if agent == "reviewer":
        return _context_reviewer(session_id, db_path)
    if agent == "skill-builder":
        return _context_skill_builder(db_path)

    raise ValueError(
        f"Unknown agent {agent_name!r}. "
        "Valid agents: planner, designer, coder, reviewer, skill-builder"
    )


# ── Per-agent builders ────────────────────────────────────────────────────────

def _context_planner(session_id: str, db_path: Path | None) -> dict[str, Any]:
    session = state.get_session(session_id, db_path)
    return {"request": session["request"] if session else None}


def _context_designer(session_id: str, db_path: Path | None) -> dict[str, Any]:
    return {"plan": state.read_stage(session_id, "plan", db_path)}


def _context_coder(session_id: str, db_path: Path | None) -> dict[str, Any]:
    attempt = state.get_attempt(session_id, "code", db_path)
    ctx: dict[str, Any] = {
        "plan": state.read_stage(session_id, "plan", db_path),
        "design": state.read_stage(session_id, "design", db_path),
    }
    if attempt > 1:
        # Retry: inject fix_instructions extracted from the last review, not the full review.
        review = state.read_stage(session_id, "review", db_path)
        fix_instructions: list[str] = []
        if isinstance(review, dict):
            for issue in review.get("issues", []):
                instr = issue.get("fix_instruction")
                if instr:
                    fix_instructions.append(instr)
        ctx["fix_instructions"] = fix_instructions
    return ctx


def _context_reviewer(session_id: str, db_path: Path | None) -> dict[str, Any]:
    session = state.get_session(session_id, db_path)
    return {
        "request": session["request"] if session else None,
        "plan":    state.read_stage(session_id, "plan",   db_path),
        "design":  state.read_stage(session_id, "design", db_path),
        "code":    state.read_stage(session_id, "code",   db_path),
    }


def _context_skill_builder(db_path: Path | None) -> dict[str, Any]:
    # No session state, no user code — only aggregated fail patterns.
    patterns = db.get_fail_patterns(db_path=db_path)
    return {"fail_patterns": patterns}
