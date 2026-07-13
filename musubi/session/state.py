"""Append-only session state. All persistence goes through storage/db.py.

musubi-tier: substrate
expires-when: never — Session DB ops + status queries.

"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage import db
from validation import schema_migrations, verifier

STAGES: list[str] = ["plan", "design", "code", "review"]


def _root() -> Path:
    """Where the harness's .github/ tree lives.

    MUSUBI_ROOT (set by an installed bundle to its own path) wins so the
    harness finds shipped pipelines/agents when invoked from a workspace
    that has none of its own. Falls back to the source-tree parent when
    running tests or `python -m musubi`.
    """
    env = os.environ.get("MUSUBI_ROOT")
    if env:
        return Path(env)
    # __file__ = musubi/session/state.py → repo root is 3 levels up.
    return Path(__file__).parent.parent.parent


# Resolved as a function so MUSUBI_ROOT changes during tests still take effect.
# Agent files live under the purpose-dir catalog (.github/agents/root|workers|
# meta). lock_agent_versions walks it recursively and locks one version per
# agent; the filename stem (minus the `.agent` suffix) IS the agent name.
def _agents_dirs() -> list[Path]:
    return [_root() / ".github" / "agents"]


# Module-level snapshot for callers that import the list directly.
AGENTS_DIRS: list[Path] = _agents_dirs()
# Back-compat alias — some callers/tests still reference AGENTS_DIR as a Path.
AGENTS_DIR = AGENTS_DIRS[0]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_version(agent_path: Path) -> str:
    # Force UTF-8 — agent .md files contain em dashes / arrows / other
    # non-ASCII; on Windows the default encoding is cp1252 and decoding fails.
    text = agent_path.read_text(encoding="utf-8")
    m = re.search(r"^version:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else "0.0.0"


# ── Session lifecycle ─────────────────────────────────────────────────────────

def create_session(
    request: str,
    db_path: Path | None = None,
    *,
    pipeline_name: str = "feature-dev",
    chat_id: str | None = None,
) -> str:
    """Create a new session and seed all stage rows as pending. Returns session_id.

    `pipeline_name` (Phase G.3) opens a `pipeline_runs` row for the
    observability layer. Defaults to "feature-dev" for back-compat with
    callers (and tests) that don't yet pass it explicitly.
    """
    import time as _time
    session_id = uuid.uuid4().hex[:12]
    now = _now()
    db.init_db(db_path)
    db.insert_session(session_id, request, now, db_path)
    for stage in STAGES:
        # G.2: tag fresh rows with the current schema version so reads
        # don't trigger a spurious v1 → vN migration on data that was
        # written under vN to begin with.
        db.insert_stage(
            session_id, stage, attempt=1, db_path=db_path,
            schema_version=verifier.CURRENT_SCHEMA_VERSION,
        )
    db.set_active_session_id(session_id, now, db_path)
    # G.3: open the pipeline_runs row. ended_at + final_status stay
    # NULL until `finalize_pipeline_run` is called from the runner.
    db.insert_pipeline_run(
        session_id, pipeline_name, _time.time(), db_path, chat_id=chat_id,
    )
    return session_id


def lock_agent_versions(
    session_id: str,
    agents_dir: Path | list[Path] | None = None,
    db_path: Path | None = None,
) -> dict[str, str]:
    """Read version from every *.agent.md frontmatter and persist to DB.

    agents_dir accepts either a single Path (back-compat for tests) or a list
    of Paths. None → use the module-level AGENTS_DIRS (the purpose-dir
    catalog at .github/agents/). When the same agent name appears in more
    than one place, the first occurrence wins.
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
        # rglob: the agent catalog is organised by purpose directory
        # (root/, workers/, meta/); first occurrence of a name wins.
        for agent_file in sorted(base.rglob("*.agent.md")):
            # stem is e.g. "planner.agent"; strip the ".agent" suffix
            name = agent_file.stem.replace(".agent", "")
            if name in versions:
                continue  # earlier base / earlier path wins
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


def clear_active_session(db_path: Path | None = None) -> None:
    """Clear the active-session pointer without deleting any session data.

    Use to abandon an interrupted pipeline that's stuck pending and the
    user does not want to resume. Stage outputs, audit rows, and the
    session row in `sessions` are preserved; only the pointer is reset.
    Idempotent — clearing an already-empty pointer is a no-op.
    """
    db.set_active_session_id(None, _now(), db_path)


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
        # Clear the pointer so the next driver call starts fresh.
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
    *,
    chunk_id: str | None = None,
) -> None:
    """Store output for the current attempt. Write-once — raises if already set.

    `chunk_id` (Phase G.1.7) scopes to a per-task chunk row when set.
    Write-once is enforced per `(session_id, stage, chunk_id, attempt)`,
    so a chunked code stage can have one row per task and each row is
    still write-once within its (chunk, attempt) tuple.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Valid stages: {STAGES}")
    row = db.get_stage_row(session_id, stage, db_path=db_path, chunk_id=chunk_id)
    if row is None:
        raise ValueError(
            f"Stage {stage!r} row missing for session {session_id!r}"
            + (f" chunk {chunk_id!r}" if chunk_id else "")
        )
    if row["output"] is not None:
        raise ValueError(
            f"Stage {stage!r}"
            + (f" chunk {chunk_id!r}" if chunk_id else "")
            + f" attempt {row['attempt']} already has output (write-once)"
        )
    now = _now()
    db.write_stage_output(
        session_id, stage, row["attempt"], output, now, db_path,
        chunk_id=chunk_id,
    )
    db.touch_session(session_id, now, db_path)


# Phase G.2: which agent's schema each stage belongs to. Used by
# read_stage to choose the right migration when stored schema_version
# differs from CURRENT_SCHEMA_VERSION. Always single-valued — one
# agent writes each stage, by construction.
_STAGE_TO_AGENT: dict[str, str] = {
    "plan":   "planner",
    "design": "designer",
    "code":   "coder",
    "review": "reviewer",
}


def read_stage(
    session_id: str, stage: str, db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> Any | None:
    """Return the latest *written* output for a stage, or None if nothing written yet.

    Uses the highest-attempt row with a non-null output so that reading after
    increment_attempt still returns the previous attempt's output.

    `chunk_id` (Phase G.1.7) scopes to a per-task chunk; default reads
    the non-chunked row.

    Phase G.2: when the stored row's schema_version differs from
    `verifier.CURRENT_SCHEMA_VERSION`, run the migration chain via
    `validation/schema_migrations.migrate()` and persist the upgraded
    version on the row so future reads skip the migration. Each
    migration step writes one audit row. Failure raises (the migration
    itself decides if data is recoverable).
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Valid stages: {STAGES}")
    row = db.get_latest_written_stage_row(
        session_id, stage, db_path=db_path, chunk_id=chunk_id,
    )
    if row is None:
        return None
    data = json.loads(row["output"])
    stored_version = row.get("schema_version") or "v1"
    current_version = verifier.CURRENT_SCHEMA_VERSION
    if stored_version != current_version:
        agent = _STAGE_TO_AGENT.get(stage)
        if agent is None:
            return data  # unknown stage — pass through
        data = schema_migrations.migrate(
            agent, data, stored_version, current_version,
            session_id=session_id, stage=stage,
            attempt=row["attempt"], chunk_id=chunk_id,
            db_path=db_path,
        )
        # Persist so re-reads don't migrate again. Idempotent — second
        # call would no-op (chain is empty when stored == current).
        db.update_stage_schema_version(
            session_id, stage, row["attempt"], current_version,
            db_path, chunk_id=chunk_id,
        )
    return data


def get_attempt(
    session_id: str, stage: str, db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> int:
    """Return the current (highest) attempt number for a stage."""
    row = db.get_stage_row(session_id, stage, db_path=db_path, chunk_id=chunk_id)
    return row["attempt"] if row else 1


def increment_attempt(
    session_id: str,
    stage: str,
    db_path: Path | None = None,
    *,
    user_hint: str | None = None,
    chunk_id: str | None = None,
) -> int:
    """Insert a new attempt row and return the new attempt number.

    `user_hint` (Phase G.1.5) is the optional one-line note the gate UI's
    "Retry this stage" input box collects. Persisted to the new attempt
    row so `read_stage_user_hint` can surface it on the next read.

    `chunk_id` (Phase G.1.7) scopes the attempt counter to a per-task
    chunk so T1's retries don't bump T2's attempt counter.
    """
    row = db.get_stage_row(session_id, stage, db_path=db_path, chunk_id=chunk_id)
    if row is None:
        raise ValueError(
            f"Stage {stage!r}"
            + (f" chunk {chunk_id!r}" if chunk_id else "")
            + f" not found for session {session_id!r}"
        )
    new_attempt = row["attempt"] + 1
    cleaned = user_hint.strip() if isinstance(user_hint, str) and user_hint.strip() else None
    db.insert_stage(
        session_id, stage, new_attempt, db_path,
        user_hint=cleaned, chunk_id=chunk_id,
        schema_version=verifier.CURRENT_SCHEMA_VERSION,
    )
    return new_attempt


def read_stage_user_hint(
    session_id: str, stage: str, db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> str | None:
    """Return the `user_hint` on the latest attempt of `stage`, or None.

    Used by `musubi_read_stage` to surface a retry hint into the
    calling agent's context — so a coder retrying after the user typed
    "the previous attempt skipped error handling" sees that note in its
    next read.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Valid stages: {STAGES}")
    row = db.get_stage_row(session_id, stage, db_path=db_path, chunk_id=chunk_id)
    if row is None:
        return None
    hint = row.get("user_hint")
    return hint if isinstance(hint, str) and hint.strip() else None


def ensure_chunk_row(
    session_id: str,
    stage: str,
    chunk_id: str,
    db_path: Path | None = None,
) -> int:
    """Create the first attempt row for a chunk if it doesn't exist; return
    the current attempt number.

    Phase G.1.7. The runner calls this when starting a new chunk's
    coder/review pair so an `attempt=1, chunk_id=<id>` row exists for
    `mark_in_progress` / `write_stage` to update.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Valid stages: {STAGES}")
    if not chunk_id or not chunk_id.strip():
        raise ValueError("ensure_chunk_row requires a non-empty chunk_id")
    chunk_id = chunk_id.strip()
    row = db.get_stage_row(session_id, stage, db_path=db_path, chunk_id=chunk_id)
    if row is None:
        db.insert_stage(
            session_id, stage, 1, db_path,
            chunk_id=chunk_id,
            schema_version=verifier.CURRENT_SCHEMA_VERSION,
        )
        return 1
    return row["attempt"]


def mark_in_progress(
    session_id: str, stage: str, db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> None:
    """Transition the current attempt to in_progress (idempotent signal for crash recovery)."""
    row = db.get_stage_row(session_id, stage, db_path=db_path, chunk_id=chunk_id)
    if row and row["status"] == "pending":
        db.set_stage_in_progress(
            session_id, stage, row["attempt"], db_path, chunk_id=chunk_id,
        )


# ── Phase G.1.5: review-gate pause / resume ───────────────────────────────

VALID_PAUSE_REASONS: frozenset[str] = frozenset({"stage_review", "budget_exhausted"})

# Resume actions and which pause_reason they apply to. The runner uses
# this table to validate before persisting.
VALID_RESUME_ACTIONS: dict[str, frozenset[str]] = {
    "approve":            frozenset({"stage_review"}),
    "retry":              frozenset({"stage_review"}),
    "abort":              frozenset({"stage_review", "budget_exhausted"}),
    "auto_approve_rest":  frozenset({"stage_review"}),
    "grant":              frozenset({"budget_exhausted"}),
    "force":              frozenset({"budget_exhausted"}),
}


def pause_session(
    session_id: str, stage: str, reason: str, db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> None:
    """Mark the session paused at `stage`. Stage must be a known pipeline
    stage; reason must be one of `VALID_PAUSE_REASONS`.

    `chunk_id` (Phase G.1.7) records which chunk a chunked-stage pause
    belongs to so the resume command targets the right chunk run.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Valid stages: {STAGES}")
    if reason not in VALID_PAUSE_REASONS:
        raise ValueError(
            f"Unknown pause_reason {reason!r}. Valid: {sorted(VALID_PAUSE_REASONS)}"
        )
    if get_session(session_id, db_path) is None:
        raise ValueError(f"Session {session_id!r} not found")
    cleaned_chunk = (
        chunk_id.strip()
        if isinstance(chunk_id, str) and chunk_id.strip()
        else None
    )
    db.set_session_paused(
        session_id, stage, reason, _now(), db_path,
        chunk_id=cleaned_chunk,
    )


def resume_session(
    session_id: str,
    action: str,
    db_path: Path | None = None,
    *,
    user_hint: str | None = None,
    extra_budget: int = 0,
) -> dict:
    """Record the user's resume decision. Returns the post-update session row.

    The runner picks up the action via `consume_pending_action` on its
    next entry and dispatches: approve→next stage, retry→same stage new
    attempt with hint, abort→close session, auto_approve_rest→approve+set
    flag, grant→same stage with extra budget, force→same stage with
    explicit no-spawns signal.
    """
    sess = get_session(session_id, db_path)
    if sess is None:
        raise ValueError(f"Session {session_id!r} not found")

    if action not in VALID_RESUME_ACTIONS:
        raise ValueError(
            f"Unknown resume action {action!r}. Valid: {sorted(VALID_RESUME_ACTIONS)}"
        )
    pause_reason = sess.get("pause_reason")
    if pause_reason is None:
        raise ValueError(f"Session {session_id!r} is not paused")
    if pause_reason not in VALID_RESUME_ACTIONS[action]:
        raise ValueError(
            f"Action {action!r} does not apply to pause_reason {pause_reason!r}"
        )

    set_auto: bool | None = None
    if action == "auto_approve_rest":
        set_auto = True

    cleaned_hint = (
        user_hint.strip()
        if isinstance(user_hint, str) and user_hint.strip()
        else None
    )
    eb = max(0, int(extra_budget)) if action in {"grant"} else 0

    db.set_session_resumed(
        session_id,
        action,
        _now(),
        db_path,
        user_hint=cleaned_hint,
        extra_budget=eb,
        set_auto_approve_remaining=set_auto,
    )
    return get_session(session_id, db_path) or {}


def consume_pending_action(
    session_id: str, db_path: Path | None = None,
) -> dict | None:
    """Return + clear the `pending_*` payload (read-once)."""
    return db.consume_pending_action(session_id, db_path)


def get_pause_state(
    session_id: str, db_path: Path | None = None,
) -> dict | None:
    """Return the pause-state record for `session_id`, or None if missing."""
    sess = get_session(session_id, db_path)
    if sess is None:
        return None
    return {
        "paused_at_stage":         sess.get("paused_at_stage"),
        "paused_at_chunk":         sess.get("paused_at_chunk"),
        "pause_reason":            sess.get("pause_reason"),
        "auto_approve_remaining":  bool(sess.get("auto_approve_remaining") or 0),
    }


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
