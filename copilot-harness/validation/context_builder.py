"""Context firewall — each agent receives only what its role requires.

Two public APIs:
    build_context(session_id, agent_name) → dict
        Used by server.py to build the full allowed context for an agent
        in one call (e.g. coder gets plan + design together).

    read_stage_for_agent(session_id, stage, agent_name) → dict | None
        Used by harness_read_stage MCP tool — per-stage access with
        per-agent permission checks. Returns None if agent is not
        permitted to read that stage.

Per-agent firewall rules:
    planner       → request only  (no stage outputs)
    designer      → plan only
    coder         → plan + design; reading "review" → fix_instructions only
    reviewer      → code only  (evaluator firewall — no request, plan, design)
    skill-builder → fail patterns only  (no session state, no user code)
    orchestrator  → memory_tier1 only  (no pipeline state of any kind;
                    user_message + conversation_history are runner-supplied
                    in Phase B.2 + Phase C, not built here)
"""

import os
import re
from pathlib import Path
from typing import Any

from memory import memory_loader
from session import state
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


# ── Skill access control ─────────────────────────────────────────────────────

# Explicit allowlist per agent. Deny by default — unknown agents get nothing.
# Mirrors the stage firewall: agents only load skills relevant to their role.
AGENT_SKILL_ALLOWLIST: dict[str, set[str]] = {
    "planner":       set(),
    "designer":      {"api-design", "database-patterns", "documentation", "docs-writing"},
    "coder":         {"python", "testing", "database-patterns", "api-design"},
    "reviewer":      {"code-review", "testing"},
    "skill-builder": set(),
    # Phase B.1 — orchestrator. Routing skill is pushed via inject_skills
    # frontmatter; the allowlist entry exists so harness_get_skill /
    # harness_list_skills resolve without policy denial when the runner
    # asks for it by id. Generator-side skills (python, api-design, etc.)
    # reach the orchestrator only through spawned sub-agents whose
    # allowlists already cover them — see MAIN_SUBAGENT_ALLOWLIST in
    # scripts/policy_engine.py.
    #
    # MVP item 9 — first non-coding skills (docs-writing, research) added
    # to the orchestrator allowlist so the butler can pull them on demand
    # when a user asks "write a design doc" or "how does X work?".
    "orchestrator": {"orchestrator-routing", "docs-writing", "research"},
    # Phase H.1 — /code-review pipeline roles.
    # scoper        triages files; needs the scope-detection skill only.
    # finder        cross-cutting pass; needs per-file-review (its checklist)
    #               and code-review (for severity rubric + categories).
    # synthesizer   aggregator; reads code-review for the rubric, per-file-
    #               review for context on what reviewer-aux was told to do.
    "scoper":      {"pr-scope-detection"},
    "finder":      {"per-file-review", "code-review"},
    "synthesizer": {"code-review", "per-file-review"},
}


def check_skill_permission(agent_name: str, skill_id: str) -> bool:
    """Return True only if the agent is permitted to access the given skill."""
    allowed = AGENT_SKILL_ALLOWLIST.get(agent_name.lower().strip(), set())
    return skill_id in allowed


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
    if agent == "orchestrator":
        return _context_orchestrator()

    raise ValueError(
        f"Unknown agent {agent_name!r}. "
        "Valid agents: planner, designer, coder, reviewer, skill-builder, "
        "orchestrator"
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
    # Evaluator firewall: reviewer sees only the artifact being judged.
    # No request, no plan, no design — those would bias the review toward
    # intent rather than the code-review checklist. The review schema is
    # injected by the extension's system prompt (AGENT_OUTPUT_HINTS) and
    # by the code-review skill, not here.
    return {"code": state.read_stage(session_id, "code", db_path)}


def _context_skill_builder(db_path: Path | None) -> dict[str, Any]:
    # No session state, no user code — only aggregated fail patterns.
    patterns = db.get_fail_patterns(db_path=db_path)
    return {"fail_patterns": patterns}


def _context_orchestrator() -> dict[str, Any]:
    # The orchestrator holds the user-facing chat across turns. The harness
    # owns Tier-1 memory; user_message + conversation_history are passed by
    # the extension runner at request-build time (Phase B.2 + Phase C) and
    # are deliberately not synthesised here. Pipeline session state
    # (request, plan, design, code, review, fail_patterns) is firewalled —
    # the orchestrator never spawns a pipeline and must not peek at one.
    return {"memory_tier1": memory_loader.get_memory_context()}


# ── Per-stage MCP access (used by harness_read_stage tool) ───────────────────

# Which stages each agent is permitted to read via harness_read_stage.
_STAGE_PERMISSIONS: dict[str, set[str]] = {
    "planner":       {"plan"},
    "designer":      {"plan"},
    "coder":         {"plan", "design", "review"},
    "reviewer":      {"code"},
    "skill-builder": set(),
    # Orchestrator never reads pipeline stages. Pipelines are user-invoked
    # and run in their own session; the orchestrator must not peek at
    # in-flight or completed pipeline state via harness_read_stage.
    "orchestrator":  set(),
    # Phase H.1 — /code-review pipeline roles.
    # scoper        reads the raw request (diff). No prior stage exists.
    # finder        reads scope (and request) for cross-cutting analysis.
    # synthesizer   evaluator: locked to findings only. Reviewer-aux outputs
    #               reach it via the runner's sub_agent_outputs injection,
    #               not via stage reads — same evaluator-firewall pattern
    #               as feature-dev's reviewer.
    "scoper":       set(),
    "finder":       {"scope"},
    "synthesizer":  {"findings"},
}


def read_stage_for_agent(
    session_id: str,
    stage: str,
    agent_name: str,
    db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> dict[str, Any] | None:
    """Return stage output filtered by the calling agent's permissions.

    Returns None if the agent is not permitted to read this stage,
    or if the stage has no output yet.

    Special case: coder reading 'review' receives only fix_instructions,
    not the full review JSON (context firewall on retry path).

    `chunk_id` (Phase G.1.7) scopes the read to a per-task chunk row.
    Plan and design stay non-chunked (read with chunk_id=None) because
    they're pipeline-global; only code and review actually have chunked
    rows. Without this, a chunked reviewer reads the empty non-chunked
    code row and escalates with "no code artifact present".
    """
    agent = agent_name.lower().strip()
    permitted = _STAGE_PERMISSIONS.get(agent, set())

    if stage not in permitted:
        return None

    # Plan and design are always pipeline-global; chunk_id only applies
    # to code/review which fan out per task.
    effective_chunk_id = chunk_id if stage in {"code", "review"} else None
    output = state.read_stage(session_id, stage, db_path, chunk_id=effective_chunk_id)
    if output is None:
        return None

    if agent == "coder" and stage == "review":
        return {
            "fix_instructions": [
                issue["fix_instruction"]
                for issue in output.get("issues", [])
                if issue.get("fix_instruction")
            ]
        }

    # Phase G.2: defense-in-depth runtime assertion that the reviewer
    # firewall stays tight. _STAGE_PERMISSIONS already restricts what
    # stages a reviewer may read; this catches the case where a future
    # refactor accidentally widens the resulting payload to include
    # generator-side fields (plan / design / request / memory / etc.).
    if agent == "reviewer":
        _assert_reviewer_firewall_payload(output, stage)

    return output


# ── Phase G.2: reviewer firewall runtime assertion ─────────────────────

# Fields that must NEVER appear at the top level of a reviewer's read
# payload. Mirrors the closed set the evaluator firewall is supposed
# to enforce. If any of these slip in, raise loudly — silently serving
# them is exactly the failure mode the firewall exists to prevent.
_REVIEWER_FORBIDDEN_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "plan", "design", "request", "memory",
    "fix_instructions", "fail_patterns",
    "session_id", "agent_versions",
})


def _assert_reviewer_firewall_payload(payload: Any, stage: str) -> None:
    """Defense-in-depth: a reviewer's read payload must not carry
    generator-side fields. Called from `read_stage_for_agent` after
    `_STAGE_PERMISSIONS` already filtered the stage.

    Raises RuntimeError with a clear message naming the leaked keys
    so the failure surfaces the bug in CI instead of returning a
    silently-poisoned context.
    """
    if not isinstance(payload, dict):
        return
    leaked = _REVIEWER_FORBIDDEN_TOP_LEVEL_KEYS & set(payload.keys())
    if leaked:
        raise RuntimeError(
            f"Reviewer firewall breach: read of stage {stage!r} surfaced "
            f"forbidden top-level keys {sorted(leaked)}. The reviewer "
            f"must see only the artifact under review, not the "
            f"generator-side context. Check the recent changes in "
            f"validation/context_builder.py and state.read_stage."
        )
