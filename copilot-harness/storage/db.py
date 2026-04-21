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
