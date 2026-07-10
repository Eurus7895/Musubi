"""SQLite CRUD layer. No business logic — just data access.

musubi-tier: substrate
expires-when: never — Append-only audit substrate; SQLite + WAL.

"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

# Schema is embedded so it works in both dev and PyInstaller one-file builds.
# Keep in sync with `storage/schema.sql`.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    request    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    paused_at_stage         TEXT,
    pause_reason            TEXT,
    auto_approve_remaining  INTEGER NOT NULL DEFAULT 0,
    pending_action          TEXT,
    pending_user_hint       TEXT,
    pending_extra_budget    INTEGER NOT NULL DEFAULT 0,
    paused_at_chunk         TEXT
);
CREATE TABLE IF NOT EXISTS agent_versions (
    session_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    version    TEXT NOT NULL,
    PRIMARY KEY (session_id, agent_name),
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE TABLE IF NOT EXISTS stage_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    stage           TEXT    NOT NULL,
    attempt         INTEGER NOT NULL DEFAULT 1,
    status          TEXT    NOT NULL DEFAULT 'pending',
    output          TEXT,
    written_at      TEXT,
    user_hint       TEXT,
    chunk_id        TEXT,
    schema_version  TEXT NOT NULL DEFAULT 'v1',
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE TABLE IF NOT EXISTS schema_migrations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    session_id    TEXT NOT NULL,
    stage         TEXT NOT NULL,
    chunk_id      TEXT,
    attempt       INTEGER NOT NULL,
    agent         TEXT NOT NULL,
    from_version  TEXT NOT NULL,
    to_version    TEXT NOT NULL,
    success       INTEGER NOT NULL DEFAULT 1,
    error         TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_schema_migrations_session
    ON schema_migrations (session_id);
CREATE INDEX IF NOT EXISTS idx_schema_migrations_ts
    ON schema_migrations (ts);
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
CREATE TABLE IF NOT EXISTS conversation_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    ts         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_chat_ts
    ON conversation_messages (chat_id, ts);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    session_id              TEXT PRIMARY KEY,
    pipeline_name           TEXT NOT NULL,
    started_at              REAL NOT NULL,
    ended_at                REAL,
    final_status            TEXT,
    total_tokens_estimate   INTEGER NOT NULL DEFAULT 0,
    correction_attempts     INTEGER NOT NULL DEFAULT 0,
    escalated               INTEGER NOT NULL DEFAULT 0,
    chunked                 INTEGER NOT NULL DEFAULT 0,
    chunk_count             INTEGER NOT NULL DEFAULT 0,
    schema_version          TEXT NOT NULL DEFAULT 'v1',
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_pipeline
    ON pipeline_runs (pipeline_name);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started
    ON pipeline_runs (started_at);
CREATE TABLE IF NOT EXISTS stage_metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,
    stage               TEXT NOT NULL,
    chunk_id            TEXT,
    attempt             INTEGER NOT NULL,
    started_at          REAL NOT NULL,
    ended_at            REAL,
    tokens_in_estimate  INTEGER NOT NULL DEFAULT 0,
    tokens_out_estimate INTEGER NOT NULL DEFAULT 0,
    lm_ms               INTEGER NOT NULL DEFAULT 0,
    tool_count          INTEGER NOT NULL DEFAULT 0,
    tool_failures       INTEGER NOT NULL DEFAULT 0,
    credits             REAL NOT NULL DEFAULT 0.0,
    model_family        TEXT,
    schema_version      TEXT NOT NULL DEFAULT 'v1',
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_stage_metrics_session
    ON stage_metrics (session_id);
-- Stage 2 (MVP A.2) — one row per `sendRequest` cycle inside
-- `runAgentLM`. A multi-cycle stage (planner cycle 0, 1, 2 …) gets
-- one row per cycle, where stage_metrics gets one row per agent CALL.
-- The granularity gap is what makes ephemeral-guard fire rates
-- queryable (path-rules preamble, empty-project fallback, bail-out)
-- so dissolution decisions can be data-driven, not guessed.
CREATE TABLE IF NOT EXISTS agent_cycles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    stage           TEXT NOT NULL,
    attempt         INTEGER NOT NULL,
    chunk_id        TEXT,
    cycle_idx       INTEGER NOT NULL,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    lm_ms           INTEGER NOT NULL DEFAULT 0,
    tool_calls_json TEXT,
    text_chars      INTEGER NOT NULL DEFAULT 0,
    credits         REAL NOT NULL DEFAULT 0.0,
    cycle_status    TEXT NOT NULL DEFAULT 'ok',
    schema_version  TEXT NOT NULL DEFAULT 'v1',
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_cycles_session
    ON agent_cycles (session_id);
CREATE TABLE IF NOT EXISTS agent_turns (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id              TEXT NOT NULL,
    parent_session_id    TEXT NOT NULL,
    started_at           REAL NOT NULL,
    ended_at             REAL,
    model_family         TEXT NOT NULL,
    cycles               INTEGER NOT NULL DEFAULT 0,
    tokens_in_estimate   INTEGER NOT NULL DEFAULT 0,
    tokens_out_estimate  INTEGER NOT NULL DEFAULT 0,
    lm_ms                INTEGER NOT NULL DEFAULT 0,
    total_ms             INTEGER NOT NULL DEFAULT 0,
    schema_version       TEXT NOT NULL DEFAULT 'v1'
);
CREATE INDEX IF NOT EXISTS idx_agent_turns_chat
    ON agent_turns (chat_id, started_at);
CREATE INDEX IF NOT EXISTS idx_agent_turns_started
    ON agent_turns (started_at);
"""

def _default_db_path() -> Path:
    # When running as the VS Code extension binary MUSUBI_ROOT points to the
    # extension install dir — a stable, writable location across binary runs.
    # Fall back to alongside db.py for dev / test usage.
    root = os.environ.get("MUSUBI_ROOT")
    if root:
        return Path(root) / "data" / "musubi.db"
    return Path(__file__).parent / "musubi.db"

DEFAULT_DB_PATH = _default_db_path()


# Phase G.1.5 — review-gate columns to add to the `sessions` table on
# existing DBs. CREATE TABLE IF NOT EXISTS is idempotent for a fresh DB
# but does not alter existing tables, so we run targeted ADD COLUMN
# migrations on every init_db. SQLite DEFAULTs only work with constants,
# which all of these are.
_PAUSE_RESUME_COLUMNS: tuple[tuple[str, str], ...] = (
    ("paused_at_stage",        "TEXT"),
    ("pause_reason",           "TEXT"),
    ("auto_approve_remaining", "INTEGER NOT NULL DEFAULT 0"),
    ("pending_action",         "TEXT"),
    ("pending_user_hint",      "TEXT"),
    ("pending_extra_budget",   "INTEGER NOT NULL DEFAULT 0"),
    # G.1.7 — paused_at_chunk surfaces the chunk a stage_review pause
    # belongs to so resume targets the correct chunk run.
    ("paused_at_chunk",        "TEXT"),
)

# Same shape for stage_outputs columns added after the original schema.
_STAGE_OUTPUT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("user_hint", "TEXT"),
    # G.1.7 — chunk_id makes (session_id, stage, chunk_id, attempt) the
    # composite write-once key so per-task code/review runs are sibling
    # rows under one session.
    ("chunk_id",  "TEXT"),
    # G.2 — schema_version tags each row with the schema generation it
    # was written under. Default 'v1' covers all pre-G.2 rows; reads
    # upgrade through `validation/schema_migrations` when the stored
    # version differs from CURRENT_SCHEMA_VERSION.
    ("schema_version", "TEXT NOT NULL DEFAULT 'v1'"),
)

# Stage 1 (MVP, A.4) — per-row credits + model_family. Computed at
# runAgentLM write time so the sidebar / /status / /credits can sum
# without needing a live BudgetEnforcer.
_STAGE_METRICS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("credits",       "REAL NOT NULL DEFAULT 0.0"),
    ("model_family",  "TEXT"),
)

# Replay accounting — how much prior conversation the turn replayed as its
# seed. Surfaces the seed cost that dominates stateful (GUI) turns.
_AGENT_TURNS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("replay_messages", "INTEGER NOT NULL DEFAULT 0"),
    ("replay_tokens",   "INTEGER NOT NULL DEFAULT 0"),
)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_columns(
    conn: sqlite3.Connection,
    table: str,
    spec: tuple[tuple[str, str], ...],
) -> None:
    have = _existing_columns(conn, table)
    for name, ddl in spec:
        if name in have:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db(db_path: Path | None = None) -> None:
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA_SQL)
        # Migrate pre-G.1.5 DBs in place. Idempotent — second call is a
        # no-op because all columns are present.
        _migrate_columns(conn, "sessions", _PAUSE_RESUME_COLUMNS)
        _migrate_columns(conn, "stage_outputs", _STAGE_OUTPUT_COLUMNS)
        _migrate_columns(conn, "stage_metrics", _STAGE_METRICS_COLUMNS)
        _migrate_columns(conn, "agent_turns", _AGENT_TURNS_COLUMNS)


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


# ── Phase G.1.5: review-gate pause / resume ───────────────────────────────

def set_session_paused(
    session_id: str,
    stage: str,
    reason: str,
    now: str,
    db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> None:
    """Mark a session as paused at `stage` for `reason`.

    Clears any prior pending_action / pending_user_hint / pending_extra_budget
    so a stale resume payload from a previous pause doesn't auto-resolve
    the new one. Invariant: at most one pause is active per session.

    `chunk_id` (Phase G.1.7) records which chunk run the pause belongs
    to so the resume command + UI can target the right one.
    """
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE sessions SET"
            "   paused_at_stage = ?,"
            "   paused_at_chunk = ?,"
            "   pause_reason = ?,"
            "   pending_action = NULL,"
            "   pending_user_hint = NULL,"
            "   pending_extra_budget = 0,"
            "   updated_at = ?"
            " WHERE session_id = ?",
            (stage, chunk_id, reason, now, session_id),
        )


def set_session_resumed(
    session_id: str,
    action: str,
    now: str,
    db_path: Path | None = None,
    *,
    user_hint: str | None = None,
    extra_budget: int = 0,
    set_auto_approve_remaining: bool | None = None,
) -> None:
    """Record a user resume decision and clear the pause flags.

    The runner's next entry checks `pending_action` to decide what to do:
      - 'approve' → next stage runs
      - 'retry' → same stage re-runs (with `user_hint` carried to attempt+1)
      - 'abort' → session closes
      - 'auto_approve_rest' → set auto_approve_remaining=1 + 'approve' semantics
      - 'grant' → re-run paused stage with extra_budget more spawns allowed
      - 'force' → re-run paused stage with explicit "no more spawns" signal
    """
    set_flag = (
        1 if set_auto_approve_remaining is True else
        0 if set_auto_approve_remaining is False else
        None
    )
    with _connect(db_path) as conn:
        if set_flag is None:
            conn.execute(
                "UPDATE sessions SET"
                "   paused_at_stage = NULL,"
                "   paused_at_chunk = NULL,"
                "   pause_reason = NULL,"
                "   pending_action = ?,"
                "   pending_user_hint = ?,"
                "   pending_extra_budget = ?,"
                "   updated_at = ?"
                " WHERE session_id = ?",
                (action, user_hint, extra_budget, now, session_id),
            )
        else:
            conn.execute(
                "UPDATE sessions SET"
                "   paused_at_stage = NULL,"
                "   paused_at_chunk = NULL,"
                "   pause_reason = NULL,"
                "   pending_action = ?,"
                "   pending_user_hint = ?,"
                "   pending_extra_budget = ?,"
                "   auto_approve_remaining = ?,"
                "   updated_at = ?"
                " WHERE session_id = ?",
                (action, user_hint, extra_budget, set_flag, now, session_id),
            )


def consume_pending_action(
    session_id: str, db_path: Path | None = None,
) -> dict | None:
    """Read and clear the `pending_*` columns atomically.

    Used by the runner on entry — it gets the user's decision exactly
    once, then the row is reset to a clean state so a subsequent runner
    entry doesn't double-apply the same action.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT pending_action, pending_user_hint, pending_extra_budget"
            " FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None or row["pending_action"] is None:
            return None
        result = {
            "action": row["pending_action"],
            "user_hint": row["pending_user_hint"],
            "extra_budget": row["pending_extra_budget"] or 0,
        }
        conn.execute(
            "UPDATE sessions SET"
            "   pending_action = NULL,"
            "   pending_user_hint = NULL,"
            "   pending_extra_budget = 0"
            " WHERE session_id = ?",
            (session_id,),
        )
    return result


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
    session_id: str,
    stage: str,
    attempt: int,
    db_path: Path | None = None,
    *,
    user_hint: str | None = None,
    chunk_id: str | None = None,
    schema_version: str | None = None,
) -> None:
    """Create a new attempt row.

    `user_hint` is set when the gate's "Retry this stage" button passes a
    one-line note — `musubi_read_stage` surfaces it in the next attempt's
    context so the agent knows what to fix.

    `chunk_id` (Phase G.1.7) tags the row with a per-task chunk identifier
    so chunked code/review runs are sibling rows under one session. NULL
    means a non-chunked stage (plan / design / single-chunk feature).

    `schema_version` (Phase G.2) tags the row with the schema generation
    its eventual output is expected to match. None ⇒ rely on the column
    default ('v1'); callers writing under a newer schema must pass it
    explicitly.
    """
    with _connect(db_path) as conn:
        if schema_version is None:
            conn.execute(
                "INSERT INTO stage_outputs"
                " (session_id, stage, attempt, status, user_hint, chunk_id)"
                " VALUES (?, ?, ?, 'pending', ?, ?)",
                (session_id, stage, attempt, user_hint, chunk_id),
            )
        else:
            conn.execute(
                "INSERT INTO stage_outputs"
                " (session_id, stage, attempt, status, user_hint, chunk_id, schema_version)"
                " VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                (session_id, stage, attempt, user_hint, chunk_id, schema_version),
            )


# ── Phase G.2: schema-migration audit ─────────────────────────────────────

def record_schema_migration(
    session_id: str,
    stage: str,
    attempt: int,
    agent: str,
    from_version: str,
    to_version: str,
    db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
    success: bool = True,
    error: str | None = None,
) -> None:
    """Insert one row in the schema_migrations audit table.

    Called by `validation/schema_migrations.migrate()` after each
    migration step (whether successful or not). Failures are still
    audited so a misbehaving migration is post-mortem-able.
    """
    import time as _time
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO schema_migrations"
            " (ts, session_id, stage, chunk_id, attempt, agent,"
            "  from_version, to_version, success, error)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _time.time(), session_id, stage, chunk_id, attempt, agent,
                from_version, to_version, 1 if success else 0, error,
            ),
        )


def query_schema_migrations(
    session_id: str | None = None,
    db_path: Path | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return migration audit rows, newest first. Optionally scoped to
    one session."""
    with _connect(db_path) as conn:
        if session_id is None:
            rows = conn.execute(
                "SELECT * FROM schema_migrations ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM schema_migrations WHERE session_id = ?"
                " ORDER BY ts DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
    return [dict(r) for r in rows]


def update_stage_schema_version(
    session_id: str,
    stage: str,
    attempt: int,
    new_version: str,
    db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> None:
    """Persist the post-migration schema_version on a row so re-reads
    don't run the migration again. Used by the migrate-on-read path.
    """
    chunk_sql, chunk_params = _chunk_clause(chunk_id)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE stage_outputs SET schema_version = ?"
            f" WHERE session_id = ? AND stage = ? AND {chunk_sql} AND attempt = ?",
            (new_version, session_id, stage, *chunk_params, attempt),
        )


def _chunk_clause(chunk_id: str | None) -> tuple[str, tuple]:
    """Build `chunk_id IS ?` (NULL-safe) plus its bound parameter.

    SQLite's `=` doesn't match NULL, so callers that filter on a
    nullable column must use `IS`. Centralised here so every chunked
    query stays NULL-safe in lockstep.
    """
    return ("chunk_id IS ?", (chunk_id,))


def get_stage_row(
    session_id: str,
    stage: str,
    attempt: int | None = None,
    db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> dict | None:
    """Return the latest attempt row (regardless of output) or a specific attempt.

    `chunk_id=None` selects rows where chunk_id IS NULL — the non-chunked
    stages. Pass an explicit task ID (e.g. 'T1') to scope to one chunk.
    """
    chunk_sql, chunk_params = _chunk_clause(chunk_id)
    with _connect(db_path) as conn:
        if attempt is None:
            row = conn.execute(
                "SELECT * FROM stage_outputs"
                f" WHERE session_id = ? AND stage = ? AND {chunk_sql}"
                " ORDER BY attempt DESC LIMIT 1",
                (session_id, stage, *chunk_params),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM stage_outputs"
                f" WHERE session_id = ? AND stage = ? AND {chunk_sql} AND attempt = ?",
                (session_id, stage, *chunk_params, attempt),
            ).fetchone()
    return dict(row) if row else None


def get_latest_written_stage_row(
    session_id: str,
    stage: str,
    db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> dict | None:
    """Return the highest-attempt row that has a non-null output."""
    chunk_sql, chunk_params = _chunk_clause(chunk_id)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM stage_outputs"
            f" WHERE session_id = ? AND stage = ? AND {chunk_sql} AND output IS NOT NULL"
            " ORDER BY attempt DESC LIMIT 1",
            (session_id, stage, *chunk_params),
        ).fetchone()
    return dict(row) if row else None


def get_all_stage_rows(
    session_id: str, db_path: Path | None = None
) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM stage_outputs WHERE session_id = ?"
            " ORDER BY stage, COALESCE(chunk_id, ''), attempt",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_stage_in_progress(
    session_id: str, stage: str, attempt: int, db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> None:
    chunk_sql, chunk_params = _chunk_clause(chunk_id)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE stage_outputs SET status = 'in_progress'"
            f" WHERE session_id = ? AND stage = ? AND {chunk_sql} AND attempt = ?",
            (session_id, stage, *chunk_params, attempt),
        )


def write_stage_output(
    session_id: str,
    stage: str,
    attempt: int,
    output: Any,
    now: str,
    db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
) -> None:
    output_json = json.dumps(output)
    chunk_sql, chunk_params = _chunk_clause(chunk_id)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE stage_outputs"
            " SET output = ?, status = 'complete', written_at = ?"
            f" WHERE session_id = ? AND stage = ? AND {chunk_sql} AND attempt = ?",
            (output_json, now, session_id, stage, *chunk_params, attempt),
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

    Called at Musubi startup to recover from crashes. Any sub-session in
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


# ── conversation_messages (Phase C.1) ─────────────────────────────────────
#
# Per-chat append-only message log driving agent replay-on-each-turn.
# Role validation lives in session/conversations.py; this layer is pure SQL.

def insert_conversation_message(
    chat_id: str,
    role: str,
    content: str,
    ts: str,
    db_path: Path | None = None,
) -> int:
    """Insert a message and return its row id."""
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO conversation_messages (chat_id, role, content, ts)"
            " VALUES (?, ?, ?, ?)",
            (chat_id, role, content, ts),
        )
        return int(cursor.lastrowid or 0)


def get_conversation_messages(
    chat_id: str,
    db_path: Path | None = None,
) -> list[dict]:
    """Return every message for `chat_id` ordered chronologically.

    Secondary sort by id keeps ordering deterministic when timestamps collide
    under fast appends (sqlite's TEXT comparison handles ISO8601 lexically).
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, chat_id, role, content, ts FROM conversation_messages"
            " WHERE chat_id = ? ORDER BY ts ASC, id ASC",
            (chat_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── sub-session housekeeping (Phase C.2) ──────────────────────────────────

def delete_terminal_sub_sessions_for_parent(
    parent_session_id: str,
    *,
    older_than_iso: str | None = None,
    db_path: Path | None = None,
) -> int:
    """Delete terminal sub-sessions for a parent. Audit-safe pruner.

    Only rows whose `status` is one of {'done','failed','escalated','abandoned'}
    are eligible — never `running`. When `older_than_iso` is provided, only
    rows whose `completed_at` is strictly less than that ISO8601 timestamp
    are deleted.

    Returns the row count removed. The mirror rows in `subagent_audit`
    are NOT touched — the audit log stays intact.
    """
    sql = (
        "DELETE FROM sub_sessions"
        " WHERE parent_session_id = ?"
        "   AND status IN ('done','failed','escalated','abandoned')"
    )
    params: list[Any] = [parent_session_id]
    if older_than_iso is not None:
        sql += " AND completed_at IS NOT NULL AND completed_at < ?"
        params.append(older_than_iso)
    with _connect(db_path) as conn:
        cursor = conn.execute(sql, tuple(params))
        return cursor.rowcount


# ── Phase G.3: pipeline_runs CRUD ─────────────────────────────────────────

def insert_pipeline_run(
    session_id: str,
    pipeline_name: str,
    started_at: float,
    db_path: Path | None = None,
) -> None:
    """Open a `pipeline_runs` row at session creation. ended_at and
    final_status stay NULL until `finalize_pipeline_run` is called."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO pipeline_runs"
            " (session_id, pipeline_name, started_at)"
            " VALUES (?, ?, ?)",
            (session_id, pipeline_name, started_at),
        )


def finalize_pipeline_run(
    session_id: str,
    ended_at: float,
    final_status: str,
    total_tokens_estimate: int,
    correction_attempts: int,
    escalated: bool,
    chunked: bool,
    chunk_count: int,
    db_path: Path | None = None,
) -> None:
    """Close out a `pipeline_runs` row when the runner finishes. Idempotent
    via UPDATE (caller may finalize twice on a retry; second write
    overwrites with the latest known state)."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE pipeline_runs SET"
            "   ended_at = ?,"
            "   final_status = ?,"
            "   total_tokens_estimate = ?,"
            "   correction_attempts = ?,"
            "   escalated = ?,"
            "   chunked = ?,"
            "   chunk_count = ?"
            " WHERE session_id = ?",
            (
                ended_at, final_status, total_tokens_estimate,
                correction_attempts, 1 if escalated else 0,
                1 if chunked else 0, chunk_count, session_id,
            ),
        )


def get_pipeline_run(
    session_id: str, db_path: Path | None = None,
) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_runs WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def query_pipeline_runs(
    pipeline_name: str | None = None,
    limit: int = 50,
    since_ts: float | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """Return pipeline_runs rows, newest first. Optional filters: by
    pipeline_name, by started_at >= since_ts."""
    sql = "SELECT * FROM pipeline_runs WHERE 1=1"
    params: list = []
    if pipeline_name is not None:
        sql += " AND pipeline_name = ?"
        params.append(pipeline_name)
    if since_ts is not None:
        sql += " AND started_at >= ?"
        params.append(since_ts)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(int(limit))
    with _connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


# ── Phase G.3: stage_metrics CRUD ─────────────────────────────────────────

def insert_stage_metric(
    session_id: str,
    stage: str,
    attempt: int,
    started_at: float,
    ended_at: float,
    tokens_in_estimate: int,
    tokens_out_estimate: int,
    lm_ms: int,
    db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
    tool_count: int = 0,
    tool_failures: int = 0,
    credits: float = 0.0,
    model_family: str | None = None,
) -> None:
    """One row per stage attempt (chunked or not). Caller passes the
    pre-measured wall-clock + token estimates collected at the
    `vscode.lm.sendRequest` call site.

    Stage 1 (MVP A.4): `credits` (the estimateCallCredits result) and
    `model_family` (model.family) are stored per row so paused / historic
    sessions can compute their cumulative spend without a live
    BudgetEnforcer. Defaults are 0.0 / None for backwards compat with
    callers that haven't been updated."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stage_metrics"
            " (session_id, stage, chunk_id, attempt,"
            "  started_at, ended_at,"
            "  tokens_in_estimate, tokens_out_estimate,"
            "  lm_ms, tool_count, tool_failures,"
            "  credits, model_family)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, stage, chunk_id, attempt,
                started_at, ended_at,
                tokens_in_estimate, tokens_out_estimate,
                lm_ms, tool_count, tool_failures,
                float(credits), model_family,
            ),
        )


def query_stage_metrics(
    session_id: str, db_path: Path | None = None,
) -> list[dict]:
    """Return all stage_metrics rows for a session, ordered chronologically."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM stage_metrics WHERE session_id = ?"
            " ORDER BY started_at ASC, id ASC",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Stage 2 (MVP A.2): agent_cycles CRUD ───────────────────────────────────


def insert_agent_cycle(
    session_id: str,
    stage: str,
    attempt: int,
    cycle_idx: int,
    started_at: float,
    ended_at: float,
    db_path: Path | None = None,
    *,
    chunk_id: str | None = None,
    lm_ms: int = 0,
    tool_calls_json: str | None = None,
    text_chars: int = 0,
    credits: float = 0.0,
    cycle_status: str = "ok",
) -> None:
    """One row per `sendRequest` cycle inside `runAgentLM`.

    Stage 2 (MVP A.2) — `stage_metrics` records one row per agent
    CALL; this table records one row per CYCLE within that call.
    The granularity gap is what makes ephemeral-guard fire rates
    queryable (path-rules preamble, empty-project fallback, the
    bail-out counter) so dissolution decisions can be data-driven.

    `cycle_status` is one of {'ok', 'final'}:
      - 'final' = cycle emitted zero tool calls and broke out
        (model is done; finalText == this cycle's text)
      - 'ok'    = cycle dispatched ≥ 1 tool call (intermediate)
    Other states ('interrupted', 'budget_halt') don't get rows
    because both throw before recording reaches us.

    `tool_calls_json` is a JSON-encoded array of `{name, ok}` objects
    (one per tool call dispatched in this cycle). Empty / null for
    `cycle_status='final'` cycles.

    Best-effort: caller wraps in a fire-and-forget catch so a row
    write failure never aborts the run.
    """
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO agent_cycles"
            " (session_id, stage, chunk_id, attempt, cycle_idx,"
            "  started_at, ended_at,"
            "  lm_ms, tool_calls_json, text_chars, credits, cycle_status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, stage, chunk_id, attempt, cycle_idx,
                started_at, ended_at,
                lm_ms, tool_calls_json, text_chars,
                float(credits), cycle_status,
            ),
        )


def query_agent_cycles(
    session_id: str,
    stage: str | None = None,
    attempt: int | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """Return agent_cycles rows for a session, optionally narrowed by
    stage / attempt. Ordered chronologically (started_at ASC, id ASC).

    Used by the forthcoming dissolution-candidates SQL ("what's the
    fire rate of guard X?") and by Stage 5's eval-suite reporter.
    """
    clauses = ["session_id = ?"]
    params: list[object] = [session_id]
    if stage is not None:
        clauses.append("stage = ?")
        params.append(stage)
    if attempt is not None:
        clauses.append("attempt = ?")
        params.append(attempt)
    sql = (
        "SELECT * FROM agent_cycles WHERE " + " AND ".join(clauses)
        + " ORDER BY started_at ASC, id ASC"
    )
    with _connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


# ── Phase J follow-up: agent_turns CRUD ─────────────────────────────

def insert_agent_turn(
    chat_id: str,
    parent_session_id: str,
    started_at: float,
    ended_at: float,
    model_family: str,
    cycles: int,
    tokens_in_estimate: int,
    tokens_out_estimate: int,
    lm_ms: int,
    total_ms: int,
    replay_messages: int = 0,
    replay_tokens: int = 0,
    db_path: Path | None = None,
) -> None:
    """One row per agent turn. Parallel to insert_stage_metric.
    Caller (TS runner via the musubi_record_agent_turn MCP
    tool) passes the pre-measured wall-clock + token estimates
    collected over all sendRequest cycles of the turn.
    `replay_messages` / `replay_tokens` record how much prior
    conversation the turn replayed as its seed (0 for a stateless turn)."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO agent_turns"
            " (chat_id, parent_session_id, started_at, ended_at,"
            "  model_family, cycles,"
            "  tokens_in_estimate, tokens_out_estimate,"
            "  lm_ms, total_ms, replay_messages, replay_tokens)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id, parent_session_id, started_at, ended_at,
                model_family, cycles,
                tokens_in_estimate, tokens_out_estimate,
                lm_ms, total_ms, replay_messages, replay_tokens,
            ),
        )


def query_agent_turns(
    chat_id: str, db_path: Path | None = None, limit: int = 100,
) -> list[dict]:
    """Return agent_turns rows for a chat_id, newest first.
    Limit defaults to 100 — sufficient for sidebar surfacing without
    pulling the entire chat history."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM agent_turns WHERE chat_id = ?"
            " ORDER BY started_at DESC, id DESC LIMIT ?",
            (chat_id, int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def total_tokens_for_session(
    session_id: str, db_path: Path | None = None,
) -> int:
    """Sum tokens_in + tokens_out across all stage_metrics for a session.
    Used by `finalize_pipeline_run` to populate `total_tokens_estimate`."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(tokens_in_estimate + tokens_out_estimate), 0)"
            " FROM stage_metrics WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row[0]) if row else 0


def total_credits_for_session(
    session_id: str, db_path: Path | None = None,
) -> float:
    """Sum `credits` across all stage_metrics rows for a session.

    Stage 1 (MVP A.4): consumed by `get_status` and the sidebar so
    paused / historic sessions show their cumulative spend.
    Returns 0.0 when the session has no rows OR the rows pre-date the
    credits column (the column's DEFAULT is 0.0)."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(credits), 0.0) FROM stage_metrics"
            " WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return float(row[0]) if row else 0.0


def total_credits_since(
    cutoff_ts: float, db_path: Path | None = None,
) -> float:
    """Sum `credits` across all stage_metrics rows with started_at >= cutoff_ts.

    Used by the `/credits` slash command's today / this-week / this-month
    aggregates."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(credits), 0.0) FROM stage_metrics"
            " WHERE started_at >= ?",
            (float(cutoff_ts),),
        ).fetchone()
    return float(row[0]) if row else 0.0


def query_pipeline_runs_for_stats(
    pipeline_name: str,
    since_ts: float | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """Return TERMINAL pipeline_runs rows (ended_at IS NOT NULL) for
    `pipeline_name` so the stats query can ignore in-flight sessions
    that would skew aggregates."""
    sql = (
        "SELECT * FROM pipeline_runs"
        " WHERE pipeline_name = ? AND ended_at IS NOT NULL"
    )
    params: list = [pipeline_name]
    if since_ts is not None:
        sql += " AND started_at >= ?"
        params.append(since_ts)
    sql += " ORDER BY started_at DESC"
    with _connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def derive_correction_attempts(
    session_id: str, db_path: Path | None = None,
) -> int:
    """G.3 helper: derive `correction_attempts` for finalize_pipeline_run
    from the highest 'code' attempt count (per-chunk MAX summed) on
    `stage_outputs`. Each retry past attempt 1 counts as one correction.

    Chunked sessions: sums the (max_code_attempt - 1) per chunk_id so a
    multi-chunk run with one retry in T1 and two in T2 reports 3.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT chunk_id, MAX(attempt) FROM stage_outputs"
            " WHERE session_id = ? AND stage = 'code'"
            " GROUP BY chunk_id",
            (session_id,),
        ).fetchall()
    if not rows:
        return 0
    return sum(max(0, int(r[1]) - 1) for r in rows if r[1] is not None)
