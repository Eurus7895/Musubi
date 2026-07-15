"""Sub-agent audit log (Phase A.3).

musubi-tier: substrate
expires-when: never — No-silent-sub-agents audit (HI #8).


Every spawn and every terminal completion writes one row to
`storage/audit.db`'s `subagent_audit` table. This is the durable
"no silent sub agents" evidence the agent-pivot invariant
requires — a sub-agent run is provable after the fact even if the
extension's chat marker scrolls off-screen or the user reloads the
window.

Read shape:

    [
      { ts, handle_id, parent_session_id, parent_agent_name, role,
        brief, event: 'spawned' | 'completed',
        # event='spawned' fields:
        allowed_tools (json), max_turns, wall_clock_timeout_s,
        # event='completed' fields:
        final_status, escalated, turns, tools_used (json),
        summary_truncated, verification_errors (json) },
      ...
    ]

The table lives in the same SQLite file as scripts/post_tool_use.py's
`tool_audit` so the harness audit story is one DB to inspect.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

# Mirror scripts/post_tool_use.py's path resolution so both writers
# converge on the same audit.db file.
_DEFAULT_AUDIT_DB: Path = (
    Path(__file__).resolve().parent / "audit.db"
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS subagent_audit (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                   REAL NOT NULL,
    handle_id            TEXT NOT NULL,
    parent_session_id    TEXT NOT NULL,
    parent_agent_name    TEXT NOT NULL,
    role                 TEXT NOT NULL,
    brief                TEXT NOT NULL,
    event                TEXT NOT NULL,            -- 'spawned' | 'completed'
    -- spawn fields
    allowed_tools        TEXT,                     -- JSON array
    max_turns            INTEGER,
    wall_clock_timeout_s INTEGER,
    -- complete fields
    final_status         TEXT,                     -- done | failed | escalated | abandoned
    escalated            INTEGER,                  -- 0/1
    turns                INTEGER,
    tools_used           TEXT,                     -- JSON array
    summary_truncated    INTEGER,                  -- 0/1
    verification_errors  TEXT,                     -- JSON array
    pushed_skill_id      TEXT                      -- root-selected skill pushed at spawn (option 3)
);
CREATE INDEX IF NOT EXISTS idx_subagent_audit_ts
    ON subagent_audit (ts);
CREATE INDEX IF NOT EXISTS idx_subagent_audit_parent
    ON subagent_audit (parent_session_id);
CREATE INDEX IF NOT EXISTS idx_subagent_audit_handle
    ON subagent_audit (handle_id);
"""


def _resolve_db_path() -> Path:
    """audit.db lives next to storage/db.py in dev; respect MUSUBI_ROOT
    in extension binary mode so dev and packaged runs converge."""
    env = os.environ.get("MUSUBI_ROOT")
    if env:
        return Path(env) / "data" / "audit.db"
    return _DEFAULT_AUDIT_DB


@contextmanager
def _connect(
    db_path: Path | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    path = db_path or _resolve_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA_SQL)
        _migrate(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns to a pre-existing table in place. Idempotent.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so a DB
    created before a column was added needs an ALTER. Cheap PRAGMA check.
    """
    have = {row[1] for row in conn.execute("PRAGMA table_info(subagent_audit)")}
    if "pushed_skill_id" not in have:
        conn.execute("ALTER TABLE subagent_audit ADD COLUMN pushed_skill_id TEXT")


def init_db(db_path: Path | None = None) -> None:
    """Create the table + indexes if missing. Idempotent."""
    with _connect(db_path):
        pass  # connect runs the schema script


# ── writers ───────────────────────────────────────────────────────────────────

def record_spawn(
    *,
    handle_id: str,
    parent_session_id: str,
    parent_agent_name: str,
    role: str,
    brief: str,
    allowed_tools: list[str] | None,
    max_turns: int,
    wall_clock_timeout_s: int,
    pushed_skill_id: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Persist one row marking a sub-agent spawn.

    `pushed_skill_id` records the root-selected skill injected into the
    worker's prompt (option 3). It has no tool-call of its own, so the
    spawn row is the only place a Console can prove the worker received it.
    """
    tools_json = json.dumps(allowed_tools) if allowed_tools is not None else None
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO subagent_audit ("
            " ts, handle_id, parent_session_id, parent_agent_name,"
            " role, brief, event,"
            " allowed_tools, max_turns, wall_clock_timeout_s, pushed_skill_id"
            ") VALUES (?, ?, ?, ?, ?, ?, 'spawned', ?, ?, ?, ?)",
            (
                time.time(),
                handle_id, parent_session_id, parent_agent_name,
                role, brief,
                tools_json, max_turns, wall_clock_timeout_s,
                (pushed_skill_id.strip() if pushed_skill_id else None),
            ),
        )


def record_complete(
    *,
    handle_id: str,
    parent_session_id: str,
    parent_agent_name: str,
    role: str,
    brief: str,
    final_status: str,
    escalated: bool,
    turns: int,
    tools_used: list[str] | None,
    summary_truncated: bool,
    verification_errors: list[str] | None,
    db_path: Path | None = None,
) -> None:
    """Persist one row marking a sub-agent's terminal result."""
    tools_json = json.dumps(tools_used) if tools_used is not None else None
    errors_json = (
        json.dumps(verification_errors)
        if verification_errors is not None else None
    )
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO subagent_audit ("
            " ts, handle_id, parent_session_id, parent_agent_name,"
            " role, brief, event,"
            " final_status, escalated, turns, tools_used,"
            " summary_truncated, verification_errors"
            ") VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                handle_id, parent_session_id, parent_agent_name,
                role, brief,
                final_status, 1 if escalated else 0, turns, tools_json,
                1 if summary_truncated else 0, errors_json,
            ),
        )


# ── reader ────────────────────────────────────────────────────────────────────

def query_events(
    *,
    parent_session_id: str | None = None,
    handle_id: str | None = None,
    since_ts: float | None = None,
    limit: int = 200,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return audit rows in ascending ts order.

    Filters are AND-combined; pass None to skip a filter.
    JSON-encoded fields are decoded back to Python types so the caller
    (an MCP tool) can serialise the result without double-encoding.
    """
    if limit <= 0:
        return []
    where: list[str] = []
    params: list[Any] = []
    if parent_session_id is not None:
        where.append("parent_session_id = ?")
        params.append(parent_session_id)
    if handle_id is not None:
        where.append("handle_id = ?")
        params.append(handle_id)
    if since_ts is not None:
        where.append("ts > ?")
        params.append(since_ts)
    sql = "SELECT * FROM subagent_audit"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts ASC LIMIT ?"
    params.append(limit)

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        # Decode JSON-encoded columns if present.
        for col in ("allowed_tools", "tools_used", "verification_errors"):
            if d.get(col):
                try:
                    d[col] = json.loads(d[col])
                except (TypeError, json.JSONDecodeError):
                    pass
        if d.get("escalated") is not None:
            d["escalated"] = bool(d["escalated"])
        if d.get("summary_truncated") is not None:
            d["summary_truncated"] = bool(d["summary_truncated"])
        out.append(d)
    return out
