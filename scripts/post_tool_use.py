#!/usr/bin/env python3
"""PostToolUse hook — append an audit entry for each tool call.

Invocation (stdin is JSON):
    python scripts/post_tool_use.py
    stdin: {
      "session_id": "abc123",
      "pipeline": "feature-dev",
      "agent": "coder",
      "tool": "Write",
      "args": { ... },        # optional
      "result_hash": "sha256:...",  # optional
      "status": "ok" | "error"
    }

Writes to copilot-harness/storage/audit.db. Schema is created on first
run if absent. Exit code 0 on success, 1 on database error.

Never send an LLM to do a linter's job: this is pure I/O.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

_DEFAULT_DB = (
    Path(__file__).resolve().parent.parent
    / "copilot-harness" / "storage" / "audit.db"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL NOT NULL,
    session_id   TEXT,
    pipeline     TEXT,
    agent        TEXT,
    tool         TEXT NOT NULL,
    args_json    TEXT,
    result_hash  TEXT,
    status       TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_audit_session
    ON tool_audit(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_audit_ts
    ON tool_audit(ts);
"""


def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    return conn


def record(payload: dict, db_path: Path = _DEFAULT_DB) -> None:
    """Append a single audit row. Used by tests as well as the CLI path."""
    conn = _open_db(db_path)
    try:
        conn.execute(
            "INSERT INTO tool_audit "
            "(ts, session_id, pipeline, agent, tool, args_json, result_hash, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                payload.get("session_id"),
                payload.get("pipeline"),
                payload.get("agent"),
                payload.get("tool", ""),
                json.dumps(payload.get("args")) if payload.get("args") is not None else None,
                payload.get("result_hash"),
                payload.get("status"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(f"post_tool_use: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2

    if not payload.get("tool"):
        print("post_tool_use: 'tool' is required", file=sys.stderr)
        return 2

    try:
        record(payload)
    except sqlite3.Error as exc:
        print(f"post_tool_use: sqlite error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
