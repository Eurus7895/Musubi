"""Append-only session state. All persistence goes through storage/db.py."""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage import db

STAGES: list[str] = ["plan", "design", "code", "review"]


def _root() -> Path:
    """Where the harness's .github/ tree lives.

    HARNESS_ROOT (set by the VS Code extension to its installed bundle path)
    wins so the harness finds shipped pipelines/agents when invoked from a
    workspace that has none of its own. Falls back to the source-tree parent
    when running tests or `python -m copilot-harness`.
    """
    env = os.environ.get("HARNESS_ROOT")
    if env:
        return Path(env)
    return Path(__file__).parent.parent


# Resolved as functions so HARNESS_ROOT changes during tests still take effect.
def _agents_dirs() -> list[Path]:
    root = _root()
    return [
        root / ".github" / "pipelines" / "feature-dev" / "agents",
        root / ".github" / "agents",
    ]


# Module-level snapshot for callers that import the list directly.
AGENTS_DIRS: list[Path] = _agents_dirs()
# Back-compat alias — some callers/tests still reference AGENTS_DIR as a Path.
# Points at the primary (new) location.
AGENTS_DIR = AGENTS_DIRS[0]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_version(agent_path: Path) -> str:
    text = agent_path.read_text()
    m = re.search(r"^version:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else "0.0.0"


# ── Session lifecycle ─────────────────────────────────────────────────────────

def create_session(request: str, db_path: Path | None = None) -> str:
    """Create a new session and seed all stage rows as pending. Returns session_id."""
    session_id = uuid.uuid4().hex[:12]
    now = _now()
    db.init_db(db_path)
    db.insert_session(session_id, request, now, db_path)
    for stage in STAGES:
        db.insert_stage(session_id, stage, attempt=1, db_path=db_path)
    db.set_active_session_id(session_id, now, db_path)
    return session_id


def lock_agent_versions(
    session_id: str,
    agents_dir: Path | list[Path] | None = None,
    db_path: Path | None = None,
) -> dict[str, str]:
    """Read version from every *.agent.md frontmatter and persist to DB.

    agents_dir accepts either a single Path (back-compat for tests) or a list
    of Paths. None → use the module-level AGENTS_DIRS (pipeline dir + legacy
    .github/agents/). When the same agent name appears in more than one dir,
    the first occurrence wins (pipeline dir takes precedence over legacy).
    """
    if agents_dir is None:
        bases: list[Path] = _agents_dirs()
    elif isinstance(agents_dir, Path):
        bases = [agents_dir]
    else:
        bases = list(agents_dir)
    versions: dict[str, str] = {}
    for base in bases:
        if not base.exists():
            continue
        for agent_file in sorted(base.glob("*.agent.md")):
            # stem is e.g. "planner.agent"; strip the ".agent" suffix
            name = agent_file.stem.replace(".agent", "")
            if name in versions:
                continue  # earlier base (pipeline dir) wins
            version = _parse_version(agent_file)
            versions[name] = version
            db.upsert_agent_version(session_id, name, version, db_path)
    return versions


def get_session(session_id: str, db_path: Path | None = None) -> dict | None:
    return db.get_session(session_id, db_path)


def get_agent_versions(
    session_id: str, db_path: Path | None = None
) -> dict[str, str]:
    return db.get_agent_versions(session_id, db_path)


# ── Active session (crash recovery) ──────────────────────────────────────────

def set_active_session(session_id: str, db_path: Path | None = None) -> None:
    """Update the active-session pointer (called automatically by create_session)."""
    db.set_active_session_id(session_id, _now(), db_path)


def get_active_session(db_path: Path | None = None) -> dict | None:
    """Return crash-recovery info for the current active session, or None.

    Returns None if there is no active session, if the tracked session is
    already complete/escalated, or if all pipeline stages are written
    (pipeline fully finished — auto-clears the pointer so the next run
    starts a fresh session instead of silently skipping everything).
    """
    session_id = db.get_active_session_id(db_path)
    if not session_id:
        return None
    session = db.get_session(session_id, db_path)
    if session is None or session["status"] != "active":
        return None
    resume_stage = resume(session_id, db_path)
    if resume_stage is None:
        # All stages have written output — pipeline is done.
        # Clear the pointer so the next @harness call starts fresh.
        db.set_active_session_id(None, _now(), db_path)
        return None
    attempt = get_attempt(session_id, resume_stage, db_path)
    return {
        "session_id": session_id,
        "request": session["request"],
        "resume_stage": resume_stage,
        "attempt": attempt,
    }


# ── Stage I/O ─────────────────────────────────────────────────────────────────

def write_stage(
    session_id: str,
    stage: str,
    output: Any,
    db_path: Path | None = None,
) -> None:
    """Store output for the current attempt. Write-once — raises if already set."""
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Valid stages: {STAGES}")
    row = db.get_stage_row(session_id, stage, db_path=db_path)
    if row is None:
        raise ValueError(f"Stage {stage!r} row missing for session {session_id!r}")
    if row["output"] is not None:
        raise ValueError(
            f"Stage {stage!r} attempt {row['attempt']} already has output (write-once)"
        )
    now = _now()
    db.write_stage_output(session_id, stage, row["attempt"], output, now, db_path)
    db.touch_session(session_id, now, db_path)


def read_stage(
    session_id: str, stage: str, db_path: Path | None = None
) -> Any | None:
    """Return the latest *written* output for a stage, or None if nothing written yet.

    Uses the highest-attempt row with a non-null output so that reading after
    increment_attempt still returns the previous attempt's output.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Valid stages: {STAGES}")
    row = db.get_latest_written_stage_row(session_id, stage, db_path=db_path)
    if row is None:
        return None
    return json.loads(row["output"])


def get_attempt(
    session_id: str, stage: str, db_path: Path | None = None
) -> int:
    """Return the current (highest) attempt number for a stage."""
    row = db.get_stage_row(session_id, stage, db_path=db_path)
    return row["attempt"] if row else 1


def increment_attempt(
    session_id: str, stage: str, db_path: Path | None = None
) -> int:
    """Insert a new attempt row and return the new attempt number."""
    row = db.get_stage_row(session_id, stage, db_path=db_path)
    if row is None:
        raise ValueError(f"Stage {stage!r} not found for session {session_id!r}")
    new_attempt = row["attempt"] + 1
    db.insert_stage(session_id, stage, new_attempt, db_path)
    return new_attempt


def mark_in_progress(
    session_id: str, stage: str, db_path: Path | None = None
) -> None:
    """Transition the current attempt to in_progress (idempotent signal for crash recovery)."""
    row = db.get_stage_row(session_id, stage, db_path=db_path)
    if row and row["status"] == "pending":
        db.set_stage_in_progress(session_id, stage, row["attempt"], db_path)


def resume(session_id: str, db_path: Path | None = None) -> str | None:
    """Return the first stage that has no written output (in pipeline order), or None."""
    rows = db.get_all_stage_rows(session_id, db_path)
    # Build a map: stage → latest row
    latest: dict[str, dict] = {}
    for row in rows:
        s = row["stage"]
        if s not in latest or row["attempt"] > latest[s]["attempt"]:
            latest[s] = row
    for stage in STAGES:
        row = latest.get(stage)
        if row and row["output"] is None:
            return stage
    return None


def get_status(session_id: str, db_path: Path | None = None) -> dict:
    """Return a summary of session state suitable for orchestration."""
    session = db.get_session(session_id, db_path)
    if session is None:
        raise ValueError(f"Session {session_id!r} not found")
    rows = db.get_all_stage_rows(session_id, db_path)
    stages: dict[str, Any] = {}
    for row in rows:
        s = row["stage"]
        existing = stages.get(s)
        if existing is None or row["attempt"] > existing["attempt"]:
            stages[s] = {
                "status": row["status"],
                "attempt": row["attempt"],
                "has_output": row["output"] is not None,
            }
    return {
        "session_id": session_id,
        "status": session["status"],
        "stages": stages,
        "next_stage": resume(session_id, db_path),
    }
