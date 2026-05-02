"""SQLite CRUD layer. No business logic — just data access."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

# Schema is embedded so it works in both dev and PyInstaller one-file builds.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    request    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_versions (
    session_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    version    TEXT NOT NULL,
    PRIMARY KEY (session_id, agent_name),
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE TABLE IF NOT EXISTS stage_outputs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    stage      TEXT    NOT NULL,
    attempt    INTEGER NOT NULL DEFAULT 1,
    status     TEXT    NOT NULL DEFAULT 'pending',
    output     TEXT,
    written_at TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE TABLE IF NOT EXISTS active_session (
    singleton  INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton = 1),
    session_id TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS fail_patterns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    agent_name  TEXT NOT NULL,
    issue       TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE TABLE IF NOT EXISTS sub_sessions (
    handle_id            TEXT PRIMARY KEY,
    parent_session_id    TEXT NOT NULL,
    parent_agent_name    TEXT NOT NULL,
    role                 TEXT NOT NULL,
    brief                TEXT NOT NULL,
    allowed_tools        TEXT,
    max_turns            INTEGER NOT NULL,
    per_turn_timeout_s   INTEGER NOT NULL DEFAULT 60,
    wall_clock_timeout_s INTEGER NOT NULL DEFAULT 300,
    output_schema        TEXT,
    status               TEXT NOT NULL DEFAULT 'running',
    result_summary       TEXT,
    result_structured    TEXT,
    tools_used           TEXT,
    turns                INTEGER NOT NULL DEFAULT 0,
    escalated            INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    completed_at         TEXT,
    FOREIGN KEY (parent_session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_sub_sessions_parent
    ON sub_sessions (parent_session_id);
CREATE INDEX IF NOT EXISTS idx_sub_sessions_status
    ON sub_sessions (status);
"""

def _default_db_path() -> Path:
    # When running as the VS Code extension binary HARNESS_ROOT points to the
    # extension install dir — a stable, writable location across binary runs.
    # Fall back to alongside db.py for dev / test usage.
    root = os.environ.get("HARNESS_ROOT")
    if root:
        return Path(root) / "data" / "copilot_harness.db"
    return Path(__file__).parent / "copilot_harness.db"

DEFAULT_DB_PATH = _default_db_path()


def init_db(db_path: Path | None = None) -> None:
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA_SQL)


@contextmanager
def _connect(db_path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── sessions ──────────────────────────────────────────────────────────────────

def insert_session(
    session_id: str, request: str, now: str, db_path: Path | None = None
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, request, status, created_at, updated_at)"
            " VALUES (?, ?, 'active', ?, ?)",
            (session_id, request, now, now),
        )


def get_session(session_id: str, db_path: Path | None = None) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def touch_session(session_id: str, now: str, db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )


# ── agent_versions ─────────────────────────────────────────────────────────

def upsert_agent_version(
    session_id: str, agent_name: str, version: str, db_path: Path | None = None
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO agent_versions (session_id, agent_name, version)"
            " VALUES (?, ?, ?)",
            (session_id, agent_name, version),
        )


def get_agent_versions(
    session_id: str, db_path: Path | None = None
) -> dict[str, str]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT agent_name, version FROM agent_versions WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    return {row["agent_name"]: row["version"] for row in rows}


# ── stage_outputs ─────────────────────────────────────────────────────────

def insert_stage(
    session_id: str, stage: str, attempt: int, db_path: Path | None = None
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stage_outputs (session_id, stage, attempt, status)"
            " VALUES (?, ?, ?, 'pending')",
            (session_id, stage, attempt),
        )


def get_stage_row(
    session_id: str,
    stage: str,
    attempt: int | None = None,
    db_path: Path | None = None,
) -> dict | None:
    """Return the latest attempt row (regardless of output) or a specific attempt."""
    with _connect(db_path) as conn:
        if attempt is None:
            row = conn.execute(
                "SELECT * FROM stage_outputs"
                " WHERE session_id = ? AND stage = ?"
                " ORDER BY attempt DESC LIMIT 1",
                (session_id, stage),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM stage_outputs"
                " WHERE session_id = ? AND stage = ? AND attempt = ?",
                (session_id, stage, attempt),
            ).fetchone()
    return dict(row) if row else None


def get_latest_written_stage_row(
    session_id: str,
    stage: str,
    db_path: Path | None = None,
) -> dict | None:
    """Return the highest-attempt row that has a non-null output."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM stage_outputs"
            " WHERE session_id = ? AND stage = ? AND output IS NOT NULL"
            " ORDER BY attempt DESC LIMIT 1",
            (session_id, stage),
        ).fetchone()
    return dict(row) if row else None


def get_all_stage_rows(
    session_id: str, db_path: Path | None = None
) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM stage_outputs WHERE session_id = ? ORDER BY stage, attempt",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_stage_in_progress(
    session_id: str, stage: str, attempt: int, db_path: Path | None = None
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE stage_outputs SET status = 'in_progress'"
            " WHERE session_id = ? AND stage = ? AND attempt = ?",
            (session_id, stage, attempt),
        )


def write_stage_output(
    session_id: str,
    stage: str,
    attempt: int,
    output: Any,
    now: str,
    db_path: Path | None = None,
) -> None:
    output_json = json.dumps(output)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE stage_outputs"
            " SET output = ?, status = 'complete', written_at = ?"
            " WHERE session_id = ? AND stage = ? AND attempt = ?",
            (output_json, now, session_id, stage, attempt),
        )


# ── active_session ────────────────────────────────────────────────────────

def get_active_session_id(db_path: Path | None = None) -> str | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT session_id FROM active_session WHERE singleton = 1"
        ).fetchone()
    return row["session_id"] if row else None


def set_active_session_id(
    session_id: str | None, now: str, db_path: Path | None = None
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO active_session (singleton, session_id, updated_at) VALUES (1, ?, ?)"
            " ON CONFLICT(singleton) DO UPDATE SET"
            "  session_id = excluded.session_id,"
            "  updated_at = excluded.updated_at",
            (session_id, now),
        )


# ── fail_patterns ─────────────────────────────────────────────────────────

def insert_fail_pattern(
    session_id: str,
    agent_name: str,
    issue: str,
    now: str,
    db_path: Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO fail_patterns (session_id, agent_name, issue, recorded_at)"
            " VALUES (?, ?, ?, ?)",
            (session_id, agent_name, issue, now),
        )


def get_fail_patterns(
    agent_name: str | None = None, db_path: Path | None = None
) -> list[dict]:
    with _connect(db_path) as conn:
        if agent_name:
            rows = conn.execute(
                "SELECT * FROM fail_patterns WHERE agent_name = ? ORDER BY recorded_at DESC",
                (agent_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM fail_patterns ORDER BY recorded_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


# ── sessions (bulk) ────────────────────────────────────────────────────────

def get_all_sessions(db_path: Path | None = None) -> list[dict]:
    """Return all session rows ordered by creation time."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── sub_sessions ──────────────────────────────────────────────────────────
#
# A sub-session is the row for an agent-spawned-by-another-agent invocation.
# Lifecycle: insert (status='running') → update_result (status='done' /
# 'failed' / 'escalated') → mark_abandoned (cleanup on parent end / startup).
# Sub-sessions are firewalled: they cannot read parent state or other subs.

def insert_sub_session(
    handle_id: str,
    parent_session_id: str,
    parent_agent_name: str,
    role: str,
    brief: str,
    allowed_tools: list[str] | None,
    max_turns: int,
    per_turn_timeout_s: int,
    wall_clock_timeout_s: int,
    output_schema: str | None,
    now: str,
    db_path: Path | None = None,
) -> None:
    tools_json = json.dumps(allowed_tools) if allowed_tools is not None else None
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sub_sessions ("
            " handle_id, parent_session_id, parent_agent_name, role, brief,"
            " allowed_tools, max_turns, per_turn_timeout_s, wall_clock_timeout_s,"
            " output_schema, status, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
            (
                handle_id, parent_session_id, parent_agent_name, role, brief,
                tools_json, max_turns, per_turn_timeout_s, wall_clock_timeout_s,
                output_schema, now,
            ),
        )


def get_sub_session(
    handle_id: str, db_path: Path | None = None
) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM sub_sessions WHERE handle_id = ?", (handle_id,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    # Decode JSON fields back into Python types for callers.
    if result.get("allowed_tools"):
        result["allowed_tools"] = json.loads(result["allowed_tools"])
    if result.get("tools_used"):
        result["tools_used"] = json.loads(result["tools_used"])
    if result.get("result_structured"):
        result["result_structured"] = json.loads(result["result_structured"])
    result["escalated"] = bool(result["escalated"])
    return result


def update_sub_session_result(
    handle_id: str,
    status: str,
    summary: str | None,
    structured: Any | None,
    tools_used: list[str] | None,
    turns: int,
    escalated: bool,
    completed_at: str,
    db_path: Path | None = None,
) -> None:
    """Persist the terminal result of a sub-session.

    `status` must be one of: 'done', 'failed', 'escalated', 'abandoned'.
    """
    structured_json = json.dumps(structured) if structured is not None else None
    tools_json = json.dumps(tools_used) if tools_used is not None else None
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE sub_sessions"
            " SET status = ?, result_summary = ?, result_structured = ?,"
            "     tools_used = ?, turns = ?, escalated = ?, completed_at = ?"
            " WHERE handle_id = ?",
            (
                status, summary, structured_json, tools_json,
                turns, 1 if escalated else 0, completed_at, handle_id,
            ),
        )


def get_sub_sessions_by_parent(
    parent_session_id: str, db_path: Path | None = None
) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM sub_sessions WHERE parent_session_id = ?"
            " ORDER BY created_at ASC",
            (parent_session_id,),
        ).fetchall()
    results = []
    for row in rows:
        d = dict(row)
        if d.get("allowed_tools"):
            d["allowed_tools"] = json.loads(d["allowed_tools"])
        if d.get("tools_used"):
            d["tools_used"] = json.loads(d["tools_used"])
        if d.get("result_structured"):
            d["result_structured"] = json.loads(d["result_structured"])
        d["escalated"] = bool(d["escalated"])
        results.append(d)
    return results


def mark_sub_sessions_abandoned_for_parent(
    parent_session_id: str, now: str, db_path: Path | None = None
) -> int:
    """Mark all `running` sub-sessions for a parent as `abandoned`.

    Returns the number of rows updated. Called when the parent session ends
    so orphan rows don't linger.
    """
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE sub_sessions"
            " SET status = 'abandoned', completed_at = ?"
            " WHERE parent_session_id = ? AND status = 'running'",
            (now, parent_session_id),
        )
        return cursor.rowcount


def mark_orphan_running_sub_sessions_abandoned(
    now: str, db_path: Path | None = None
) -> int:
    """Startup sweep: mark `running` sub-sessions whose parent is not active.

    Called at harness startup to recover from crashes. Any sub-session in
    `running` whose parent session is no longer `active` becomes `abandoned`.
    Returns the number of rows updated.
    """
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE sub_sessions"
            " SET status = 'abandoned', completed_at = ?"
            " WHERE status = 'running'"
            "   AND parent_session_id NOT IN ("
            "     SELECT session_id FROM sessions WHERE status = 'active'"
            "   )",
            (now,),
        )
        return cursor.rowcount
