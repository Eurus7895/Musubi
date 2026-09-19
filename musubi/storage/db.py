"""SQLite CRUD layer. No business logic — just data access.

musubi-tier: substrate
expires-when: never — Append-only audit substrate; SQLite + WAL.

"""

import json
import os
import sqlite3
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    phase           TEXT NOT NULL DEFAULT 'pending',
    contract_json   TEXT,
    contract_hash   TEXT,
    selected_skill_id TEXT,
    selected_skill_version TEXT,
    selected_skill_hash TEXT,
    worker_handle_id TEXT,
    artifact_manifest_json TEXT,
    gate_result_json TEXT,
    gate_written_at TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE TABLE IF NOT EXISTS stage_attempt_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    chunk_id TEXT,
    attempt INTEGER NOT NULL,
    event TEXT NOT NULL,
    worker_handle_id TEXT,
    contract_hash TEXT,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stage_attempt_events_identity
    ON stage_attempt_events(session_id, stage, chunk_id, attempt, id);
CREATE TABLE IF NOT EXISTS stage_command_results (
    execution_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    command_id TEXT NOT NULL,
    status TEXT NOT NULL,
    exit_code INTEGER,
    stdout TEXT NOT NULL,
    stderr TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    recorded_at REAL NOT NULL
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
    pushed_skill_id      TEXT,
    status               TEXT NOT NULL DEFAULT 'running',
    result_summary       TEXT,
    result_structured    TEXT,
    tools_used           TEXT,
    turns                INTEGER NOT NULL DEFAULT 0,
    escalated            INTEGER NOT NULL DEFAULT 0,
    turn_cap_accepted    INTEGER NOT NULL DEFAULT 0,
    turn_cap_acceptance  TEXT,
    goal_id              TEXT,
    work_package_id      TEXT,
    attempt_id           TEXT,
    contract_hash        TEXT,
    created_at           TEXT NOT NULL,
    completed_at         TEXT,
    FOREIGN KEY (parent_session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_sub_sessions_parent
    ON sub_sessions (parent_session_id);
CREATE INDEX IF NOT EXISTS idx_sub_sessions_status
    ON sub_sessions (status);
CREATE TABLE IF NOT EXISTS audit_obligations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    handle_id    TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    delivered_at TEXT,
    error        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_obligations_kind_handle
    ON audit_obligations (kind, handle_id);
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
    chat_id                 TEXT,
    request_id              TEXT,
    profile                 TEXT,
    task                    TEXT,
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
    worker_id       TEXT NOT NULL DEFAULT 'root',
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    token_source    TEXT NOT NULL DEFAULT 'estimated',
    cycle_status    TEXT NOT NULL DEFAULT 'ok',
    schema_version  TEXT NOT NULL DEFAULT 'v1',
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_cycles_session
    ON agent_cycles (session_id);
CREATE TABLE IF NOT EXISTS agent_turns (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id              TEXT NOT NULL,
    request_id           TEXT,
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
CREATE TABLE IF NOT EXISTS session_folder_grants (
    chat_id        TEXT NOT NULL,
    grant_id       TEXT NOT NULL,
    alias          TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    ordinal        INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (chat_id, grant_id),
    UNIQUE (chat_id, alias),
    UNIQUE (chat_id, canonical_path)
);
CREATE INDEX IF NOT EXISTS idx_session_folder_grants_chat_order
    ON session_folder_grants (chat_id, ordinal, grant_id);
CREATE TABLE IF NOT EXISTS request_folder_grants (
    request_id     TEXT NOT NULL,
    chat_id        TEXT NOT NULL,
    grant_id       TEXT NOT NULL,
    alias          TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    ordinal        INTEGER NOT NULL,
    captured_at    TEXT NOT NULL,
    PRIMARY KEY (request_id, grant_id),
    UNIQUE (request_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_request_folder_grants_chat
    ON request_folder_grants (chat_id, request_id, ordinal);
CREATE TABLE IF NOT EXISTS goal_contract_versions (
    contract_hash TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    canonical_json TEXT NOT NULL,
    supersedes_hash TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (session_id, goal_id, version),
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE TABLE IF NOT EXISTS goal_criterion_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    goal_contract_hash TEXT NOT NULL,
    criterion_id TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    work_package_id TEXT,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_goal_criterion_events
    ON goal_criterion_events (session_id, goal_id, criterion_id, id);
CREATE TABLE IF NOT EXISTS work_package_versions (
    contract_hash TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    work_package_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    goal_contract_hash TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    supersedes_hash TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (session_id, work_package_id, version),
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE TABLE IF NOT EXISTS work_package_attempts (
    attempt_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    work_package_id TEXT NOT NULL,
    contract_hash TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    failure_class TEXT,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    turns_used INTEGER NOT NULL DEFAULT 0,
    criterion_delta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (session_id, work_package_id, contract_hash, attempt),
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE TABLE IF NOT EXISTS verification_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id TEXT NOT NULL,
    criterion_id TEXT NOT NULL,
    verifier_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (attempt_id) REFERENCES work_package_attempts (attempt_id)
);
CREATE TABLE IF NOT EXISTS budget_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    work_package_id TEXT,
    attempt_id TEXT,
    event TEXT NOT NULL,
    tokens INTEGER NOT NULL DEFAULT 0,
    turns INTEGER NOT NULL DEFAULT 0,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE TABLE IF NOT EXISTS rollback_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    work_package_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    root_alias TEXT NOT NULL,
    path TEXT NOT NULL,
    original_exists INTEGER NOT NULL,
    original_bytes BLOB,
    before_sha256 TEXT,
    after_sha256 TEXT,
    status TEXT NOT NULL DEFAULT 'captured',
    created_at TEXT NOT NULL,
    rolled_back_at TEXT,
    UNIQUE (attempt_id, root_alias, path),
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
"""

def _default_db_path() -> Path:
    configured = os.environ.get("MUSUBI_STATE_DB")
    if configured:
        return Path(configured)
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
    ("phase", "TEXT NOT NULL DEFAULT 'pending'"),
    ("contract_json", "TEXT"),
    ("contract_hash", "TEXT"),
    ("selected_skill_id", "TEXT"),
    ("selected_skill_version", "TEXT"),
    ("selected_skill_hash", "TEXT"),
    ("worker_handle_id", "TEXT"),
    ("artifact_manifest_json", "TEXT"),
    ("gate_result_json", "TEXT"),
    ("gate_written_at", "TEXT"),
)

# Provider identity added after the original stage_metrics schema.
_STAGE_METRICS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("model_family",  "TEXT"),
)

_AGENT_CYCLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("worker_id", "TEXT NOT NULL DEFAULT 'root'"),
    ("tokens_in", "INTEGER NOT NULL DEFAULT 0"),
    ("cached_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("tokens_out", "INTEGER NOT NULL DEFAULT 0"),
    ("token_source", "TEXT NOT NULL DEFAULT 'estimated'"),
    ("tool_calls_json", "TEXT"),
    ("lm_ms", "INTEGER NOT NULL DEFAULT 0"),
    ("text_chars", "INTEGER NOT NULL DEFAULT 0"),
    ("cycle_status", "TEXT NOT NULL DEFAULT 'ok'"),
    ("schema_version", "TEXT NOT NULL DEFAULT 'v1'"),
)

_PIPELINE_RUNS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("chat_id", "TEXT"),
    ("request_id", "TEXT"),
    ("profile", "TEXT"),
    ("task", "TEXT"),
)

# `request_id` groups the turns of one Orchestrator launch.
# `delivered_artifact` records whether a turn ended with files on disk;
# pre-existing rows default to 0, which reads as "delivered nothing" — safe,
# because the flag only ever makes the root MORE conservative about planning.
# The pre-run `clarification_request` column is retired: the lexical layer that
# halted a turn on a guessed ambiguity is gone, and the only clarification left
# is the planner's, which is answered within the turn rather than stored for the
# next one. Existing databases keep the column; nothing reads or writes it.
_AGENT_TURNS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("request_id", "TEXT"),
    ("delivered_artifact", "INTEGER NOT NULL DEFAULT 0"),
    # One-time destructive-approval tokens this turn is waiting on, as JSON
    # `[{token, keys}]`. The NEXT turn matches the token literally against the
    # user's own message — a model cannot author a user turn, so a match is
    # proof a human approved exactly these paths.
    ("pending_destructive", "TEXT"),
    # What the ROOT said this turn was, in its own words: "work: dashboard.html
    # exists and the change is one file". Recorded, never checked — the harness
    # cannot know whether a triage was right, and inferring one from behaviour
    # would make a guess indistinguishable from a declaration in the only
    # record that makes an overridden routing hint reviewable.
    ("root_triage", "TEXT"),
)

# Root-selected skill injection (option 3): the root may name a catalog
# skill for a spawned worker, stored per-row so it is provable post-hoc.
_SUB_SESSIONS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("pushed_skill_id", "TEXT"),
    ("turn_cap_accepted", "INTEGER NOT NULL DEFAULT 0"),
    ("turn_cap_acceptance", "TEXT"),
    ("goal_id", "TEXT"),
    ("work_package_id", "TEXT"),
    ("attempt_id", "TEXT"),
    ("contract_hash", "TEXT"),
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
        _migrate_columns(conn, "agent_cycles", _AGENT_CYCLE_COLUMNS)
        _migrate_columns(conn, "agent_turns", _AGENT_TURNS_COLUMNS)
        _migrate_columns(conn, "pipeline_runs", _PIPELINE_RUNS_COLUMNS)
        _migrate_columns(conn, "sub_sessions", _SUB_SESSIONS_COLUMNS)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_stage_outputs_without_chunk "
            "ON stage_outputs(session_id, stage, attempt) WHERE chunk_id IS NULL"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_stage_outputs_with_chunk "
            "ON stage_outputs(session_id, stage, chunk_id, attempt) "
            "WHERE chunk_id IS NOT NULL"
        )


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

_STAGE_PHASES: tuple[str, ...] = (
    "pending", "preflight_running", "contract_frozen", "worker_running",
    "worker_complete", "gate_running", "passed", "retryable_failed",
    "gate_error", "exhausted", "escalated",
)
_WRITE_ONCE_ATTEMPT_FIELDS = frozenset({
    "contract_json", "contract_hash", "selected_skill_id",
    "selected_skill_version", "selected_skill_hash", "worker_handle_id",
    "output", "artifact_manifest_json", "gate_result_json", "gate_written_at",
})


@dataclass(frozen=True)
class StageAttemptIdentity:
    session_id: str
    stage: str
    attempt: int
    chunk_id: str | None = None


def _identity_where(identity: StageAttemptIdentity) -> tuple[str, tuple[Any, ...]]:
    return (
        "session_id = ? AND stage = ? AND chunk_id IS ? AND attempt = ?",
        (identity.session_id, identity.stage, identity.chunk_id, identity.attempt),
    )


def transition_stage_attempt(
    identity: StageAttemptIdentity,
    expected_phase: str,
    next_phase: str,
    event: str,
    detail: dict[str, Any],
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """CAS one phase and append its event in the same transaction."""
    if expected_phase not in _STAGE_PHASES or next_phase not in _STAGE_PHASES:
        raise ValueError("unknown stage attempt phase")
    if _STAGE_PHASES.index(next_phase) <= _STAGE_PHASES.index(expected_phase):
        raise ValueError("stage attempt phase must move forward")
    where, params = _identity_where(identity)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT * FROM stage_outputs WHERE {where}", params,
        ).fetchone()
        if row is None:
            raise ValueError(f"stage attempt not found: {identity}")
        if row["phase"] != expected_phase:
            raise ValueError(
                f"expected phase {expected_phase!r}, found {row['phase']!r}"
            )
        updated = conn.execute(
            f"UPDATE stage_outputs SET phase = ? WHERE {where} AND phase = ?",
            (next_phase, *params, expected_phase),
        )
        if updated.rowcount != 1:
            raise ValueError("stage attempt transition lost a concurrent race")
        conn.execute(
            "INSERT INTO stage_attempt_events "
            "(ts, session_id, stage, chunk_id, attempt, event, "
            "worker_handle_id, contract_hash, detail_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(), identity.session_id, identity.stage,
                identity.chunk_id, identity.attempt, event,
                row["worker_handle_id"], row["contract_hash"],
                json.dumps(detail, sort_keys=True),
            ),
        )
        result = conn.execute(
            f"SELECT * FROM stage_outputs WHERE {where}", params,
        ).fetchone()
    return dict(result) if result is not None else {}


def write_stage_attempt_once(
    identity: StageAttemptIdentity,
    field: str,
    value: Any,
    *,
    db_path: Path | None = None,
) -> None:
    if field not in _WRITE_ONCE_ATTEMPT_FIELDS:
        raise ValueError(f"field {field!r} is not an attempt write-once field")
    where, params = _identity_where(identity)
    encoded = (
        json.dumps(value, sort_keys=True)
        if field in {"artifact_manifest_json", "gate_result_json"}
        and not isinstance(value, str)
        else value
    )
    with _connect(db_path) as conn:
        cursor = conn.execute(
            f"UPDATE stage_outputs SET {field} = ? "
            f"WHERE {where} AND {field} IS NULL",
            (encoded, *params),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"{field} is write-once or attempt does not exist")


def create_next_stage_attempt(
    identity: StageAttemptIdentity,
    expected_attempt: int,
    detail: dict[str, Any],
    *,
    db_path: Path | None = None,
) -> int:
    """Atomically create the next append-only attempt from a known latest."""
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT MAX(attempt) AS latest FROM stage_outputs "
            "WHERE session_id = ? AND stage = ? AND chunk_id IS ?",
            (identity.session_id, identity.stage, identity.chunk_id),
        ).fetchone()
        latest = int(row["latest"] or 0)
        if latest != expected_attempt or identity.attempt != expected_attempt:
            raise ValueError(
                f"stale attempt writer: expected {expected_attempt}, latest {latest}"
            )
        new_attempt = latest + 1
        conn.execute(
            "INSERT INTO stage_outputs "
            "(session_id, stage, attempt, status, phase, chunk_id) "
            "VALUES (?, ?, ?, 'pending', 'pending', ?)",
            (identity.session_id, identity.stage, new_attempt, identity.chunk_id),
        )
        conn.execute(
            "INSERT INTO stage_attempt_events "
            "(ts, session_id, stage, chunk_id, attempt, event, detail_json) "
            "VALUES (?, ?, ?, ?, ?, 'retry_created', ?)",
            (
                time.time(), identity.session_id, identity.stage,
                identity.chunk_id, new_attempt, json.dumps(detail, sort_keys=True),
            ),
        )
    return new_attempt


def get_stage_attempt_events(
    identity: StageAttemptIdentity, *, db_path: Path | None = None,
) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM stage_attempt_events WHERE session_id = ? "
            "AND stage = ? AND chunk_id IS ? AND attempt = ? ORDER BY id",
            (identity.session_id, identity.stage, identity.chunk_id, identity.attempt),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["detail"] = json.loads(item.pop("detail_json"))
        result.append(item)
    return result


def get_stage_command_result(
    execution_id: str, db_path: Path | None = None,
) -> dict[str, Any] | None:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM stage_command_results WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
    return dict(row) if row else None


def record_stage_command_result(
    result: dict[str, Any], db_path: Path | None = None,
) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO stage_command_results "
            "(execution_id, session_id, stage, attempt, command_id, status, "
            "exit_code, stdout, stderr, duration_ms, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result["execution_id"], result["session_id"], result["stage"],
                result["attempt"], result["command_id"], result["status"],
                result.get("exit_code"), result.get("stdout", ""),
                result.get("stderr", ""), result.get("duration_ms", 0),
                result.get("recorded_at", time.time()),
            ),
        )

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


def get_stage_names(
    session_id: str, db_path: Path | None = None,
) -> list[str]:
    """Return seeded stage names in recipe insertion order."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT stage, MIN(id) AS first_id FROM stage_outputs "
            "WHERE session_id = ? AND chunk_id IS NULL "
            "GROUP BY stage ORDER BY first_id",
            (session_id,),
        ).fetchall()
    return [str(row["stage"]) for row in rows]


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
    pushed_skill_id: str | None = None,
    goal_id: str | None = None,
    work_package_id: str | None = None,
    attempt_id: str | None = None,
    contract_hash: str | None = None,
    db_path: Path | None = None,
) -> None:
    tools_json = json.dumps(allowed_tools) if allowed_tools is not None else None
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sub_sessions ("
            " handle_id, parent_session_id, parent_agent_name, role, brief,"
            " allowed_tools, max_turns, per_turn_timeout_s, wall_clock_timeout_s,"
            " output_schema, pushed_skill_id, goal_id, work_package_id,"
            " attempt_id, contract_hash, status, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
            (
                handle_id, parent_session_id, parent_agent_name, role, brief,
                tools_json, max_turns, per_turn_timeout_s, wall_clock_timeout_s,
                output_schema, pushed_skill_id, goal_id, work_package_id,
                attempt_id, contract_hash, now,
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
    result["turn_cap_accepted"] = bool(result.get("turn_cap_accepted"))
    return result


def record_audit_obligation(
    *, kind: str, handle_id: str, payload: dict[str, Any], created_at: str,
    db_path: Path | None = None,
) -> int:
    """Persist one idempotent audit outbox item and return its id."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO audit_obligations "
            "(created_at, kind, handle_id, payload_json, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (created_at, kind, handle_id, json.dumps(payload, sort_keys=True)),
        )
        row = conn.execute(
            "SELECT id FROM audit_obligations WHERE kind = ? AND handle_id = ?",
            (kind, handle_id),
        ).fetchone()
    if row is None:
        raise RuntimeError("audit obligation was not persisted")
    return int(row["id"])


def mark_audit_obligation_delivered(
    obligation_id: int, delivered_at: str, db_path: Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE audit_obligations SET status = 'delivered', "
            "delivered_at = ?, error = NULL WHERE id = ?",
            (delivered_at, obligation_id),
        )


def mark_audit_obligation_failed(
    obligation_id: int, error: str, db_path: Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE audit_obligations SET status = 'pending', error = ? "
            "WHERE id = ?",
            (error[:2000], obligation_id),
        )


def get_audit_obligations(
    *, status: str | None = None, db_path: Path | None = None,
) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        if status is None:
            rows = conn.execute(
                "SELECT * FROM audit_obligations ORDER BY id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_obligations WHERE status = ? ORDER BY id",
                (status,),
            ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        result.append(item)
    return result


def abandon_sub_session(
    handle_id: str, completed_at: str, db_path: Path | None = None,
) -> None:
    """Make an undeliverable reserved worker permanently non-runnable."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE sub_sessions SET status = 'abandoned', completed_at = ? "
            "WHERE handle_id = ? AND status = 'running'",
            (completed_at, handle_id),
        )


def update_sub_session_result(
    handle_id: str,
    status: str,
    summary: str | None,
    structured: Any | None,
    tools_used: list[str] | None,
    turns: int,
    escalated: bool,
    completed_at: str,
    turn_cap_accepted: bool = False,
    turn_cap_acceptance: str | None = None,
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
            "     tools_used = ?, turns = ?, escalated = ?,"
            "     turn_cap_accepted = ?, turn_cap_acceptance = ?, completed_at = ?"
            " WHERE handle_id = ?",
            (
                status, summary, structured_json, tools_json,
                turns, 1 if escalated else 0,
                1 if turn_cap_accepted else 0, turn_cap_acceptance,
                completed_at, handle_id,
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
        d["turn_cap_accepted"] = bool(d.get("turn_cap_accepted"))
        results.append(d)
    return results


def get_sub_session_by_attempt(
    attempt_id: str, db_path: Path | None = None,
) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM sub_sessions WHERE attempt_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (attempt_id,),
        ).fetchone()
    return dict(row) if row else None


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


def count_conversation_messages(
    chat_id: str,
    db_path: Path | None = None,
) -> int:
    """Return how many messages `chat_id` has on record.

    A COUNT rather than a fetch: the scope classifier only needs to know
    whether a conversation already exists, and must not pay a full replay
    read to learn it.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM conversation_messages WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return int(row["n"] if row else 0)


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
    *,
    chat_id: str | None = None,
    request_id: str | None = None,
    profile: str | None = None,
    task: str | None = None,
) -> None:
    """Open a `pipeline_runs` row at session creation. ended_at and
    final_status stay NULL until `finalize_pipeline_run` is called."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO pipeline_runs"
            " (session_id, pipeline_name, chat_id, request_id, profile, task, started_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, pipeline_name, chat_id, request_id,
                profile, task, started_at,
            ),
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
    model_family: str | None = None,
) -> None:
    """One row per stage attempt (chunked or not). Caller passes the
    pre-measured wall-clock + token estimates collected at the
    LM call site.

    `model_family` records the provider model identity used for the call."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stage_metrics"
            " (session_id, stage, chunk_id, attempt,"
            "  started_at, ended_at,"
            "  tokens_in_estimate, tokens_out_estimate,"
            "  lm_ms, tool_count, tool_failures, model_family)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, stage, chunk_id, attempt,
                started_at, ended_at,
                tokens_in_estimate, tokens_out_estimate,
                lm_ms, tool_count, tool_failures, model_family,
            ),
        )


def query_stage_metrics(
    session_id: str, db_path: Path | None = None,
) -> list[dict]:
    """Return all stage_metrics rows for a session, ordered chronologically."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, session_id, stage, chunk_id, attempt, started_at,"
            " ended_at, tokens_in_estimate, tokens_out_estimate, lm_ms,"
            " tool_count, tool_failures, model_family, schema_version"
            " FROM stage_metrics WHERE session_id = ?"
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
    worker_id: str = "root",
    tokens_in: int = 0,
    cached_input_tokens: int = 0,
    tokens_out: int = 0,
    token_source: str = "estimated",
    cycle_status: str = "ok",
) -> None:
    """One row per `sendRequest` cycle inside `runAgentLM`.

    Stage 2 (MVP A.2) — `stage_metrics` records one row per agent
    CALL; this table records one row per CYCLE within that call.
    The granularity gap is what makes ephemeral-guard fire rates
    queryable (path-rules preamble, empty-project fallback, the
    bail-out counter) so dissolution decisions can be data-driven.

    `cycle_status` is one of
    {'ok', 'final', 'truncated', 'budget_halt', 'recovery_halt'}:
      - 'final' = cycle emitted zero tool calls and broke out
        (model is done; finalText == this cycle's text)
      - 'ok'    = cycle dispatched ≥ 1 tool call (intermediate)
      - 'truncated' = requested tool calls were dropped because the response
        hit its output limit and arguments may be incomplete
      - 'budget_halt' = usage was measured, but postflight enforcement stopped
        the run before any requested tool was dispatched
      - 'recovery_halt' = bounded root recovery ended the run, either before a
        disallowed third analysis dispatch or after the last worker failed

    `tool_calls_json` is a JSON-encoded array of tool names (one per tool call
    dispatched in this cycle). It may be populated for `ok` and
    `recovery_halt` cycles.

    Best-effort: caller wraps in a fire-and-forget catch so a row
    write failure never aborts the run.
    """
    if token_source not in {"provider", "estimated"}:
        raise ValueError("token_source must be 'provider' or 'estimated'")
    tokens_in = max(0, int(tokens_in))
    tokens_out = max(0, int(tokens_out))
    cached_input_tokens = max(0, min(tokens_in, int(cached_input_tokens)))
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO agent_cycles"
            " (session_id, stage, chunk_id, attempt, cycle_idx,"
            "  started_at, ended_at,"
            "  lm_ms, tool_calls_json, text_chars, worker_id, tokens_in,"
            "  cached_input_tokens, tokens_out, token_source, cycle_status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, stage, chunk_id, attempt, cycle_idx,
                started_at, ended_at,
                lm_ms, tool_calls_json, text_chars, worker_id, tokens_in,
                cached_input_tokens, tokens_out, token_source, cycle_status,
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
        "SELECT id, session_id, stage, attempt, chunk_id, cycle_idx,"
        " started_at, ended_at, lm_ms, tool_calls_json, text_chars,"
        " worker_id, tokens_in, cached_input_tokens, tokens_out,"
        " token_source, cycle_status, schema_version"
        " FROM agent_cycles WHERE " + " AND ".join(clauses)
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
    request_id: str | None = None,
    db_path: Path | None = None,
    delivered_artifact: bool = False,
    pending_destructive: str | None = None,
    root_triage: str | None = None,
) -> None:
    """One row per agent turn. Parallel to insert_stage_metric.
    Caller (TS runner via the musubi_record_agent_turn MCP
    tool) passes the pre-measured wall-clock + token estimates
    collected over all sendRequest cycles of the turn.

    `delivered_artifact` records whether the turn ended with files on disk,
    which is what a later turn in the same conversation reads to notice that
    spend is accumulating without progress.

    `root_triage` records the turn shape the ROOT declared for itself, from
    its first cycle only. Never inferred: an absent declaration stays absent,
    or a shape the harness invented would be indistinguishable from a stated
    one in the only record that makes a routing choice reviewable."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO agent_turns"
            " (chat_id, request_id, parent_session_id, started_at, ended_at,"
            "  model_family, cycles,"
            "  tokens_in_estimate, tokens_out_estimate, lm_ms, total_ms,"
            "  delivered_artifact, pending_destructive, root_triage)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id, request_id, parent_session_id, started_at, ended_at,
                model_family, cycles,
                tokens_in_estimate, tokens_out_estimate, lm_ms, total_ms,
                1 if delivered_artifact else 0,
                pending_destructive or None,
                root_triage or None,
            ),
        )


def chat_turn_usage(chat_id: str, db_path: Path | None = None) -> dict:
    """Aggregate what one CONVERSATION has spent across its agent turns.

    Per-turn budgets are process-scoped — every chat message runs in a fresh
    agent process with a fresh allowance — so without this aggregate nothing
    in the substrate can observe a conversation that has spent six turns and
    delivered nothing. `barren_turns` is the TRAILING run of turns that ended
    without writing a file; it resets to 0 the moment one delivers.

    Returns ``{turns, tokens, barren_turns}``.
    """
    if not chat_id:
        return {"turns": 0, "tokens": 0, "barren_turns": 0}
    with _connect(db_path) as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS turns,"
            " COALESCE(SUM(tokens_in_estimate), 0) AS tokens_in,"
            " COALESCE(SUM(tokens_out_estimate), 0) AS tokens_out"
            " FROM agent_turns WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        recent = conn.execute(
            "SELECT delivered_artifact FROM agent_turns"
            " WHERE chat_id = ? ORDER BY started_at DESC, id DESC",
            (chat_id,),
        ).fetchall()
    barren = 0
    for row in recent:
        if int(row["delivered_artifact"] or 0):
            break
        barren += 1
    tokens_in = int(totals["tokens_in"]) if totals else 0
    tokens_out = int(totals["tokens_out"]) if totals else 0
    return {
        "turns": int(totals["turns"]) if totals else 0,
        "tokens": tokens_in + tokens_out,
        "barren_turns": barren,
    }


def pending_destructive(chat_id: str, db_path: Path | None = None) -> str | None:
    """Approval tokens the latest turn of `chat_id` is waiting on, or None.

    Latest turn only: a turn that ran
    without being gated writes NULL, so an approval cannot be replayed against
    a later, different set of files.
    """
    if not chat_id:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT pending_destructive FROM agent_turns"
            " WHERE chat_id = ? ORDER BY started_at DESC, id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
    if row is None:
        return None
    pending = row["pending_destructive"]
    return str(pending) if pending else None


def query_agent_turns(
    chat_id: str, db_path: Path | None = None, limit: int = 100,
) -> list[dict]:
    """Return agent_turns rows for a chat_id, newest first.
    Limit defaults to 100 — sufficient for sidebar surfacing without
    pulling the entire chat history."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, chat_id, request_id, parent_session_id, started_at, ended_at,"
            " model_family, cycles, tokens_in_estimate, tokens_out_estimate,"
            " lm_ms, total_ms, schema_version FROM agent_turns WHERE chat_id = ?"
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


# ── Root Goal Contract / Work Package ledger ─────────────────────────────

def insert_goal_contract_version(
    *, session_id: str, goal_id: str, version: int, canonical_json: str,
    contract_hash: str, supersedes_hash: str | None, created_at: str,
    db_path: Path | None = None,
) -> None:
    """Append one frozen Goal Contract version; never update in place."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO goal_contract_versions "
            "(contract_hash, session_id, goal_id, version, canonical_json, "
            " supersedes_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (contract_hash, session_id, goal_id, version, canonical_json,
             supersedes_hash, created_at),
        )


def get_goal_contract_version(
    contract_hash: str, db_path: Path | None = None,
) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM goal_contract_versions WHERE contract_hash = ?",
            (contract_hash,),
        ).fetchone()
    return dict(row) if row else None


def latest_goal_contract(
    session_id: str, goal_id: str, db_path: Path | None = None,
) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM goal_contract_versions WHERE session_id = ? AND goal_id = ? "
            "ORDER BY version DESC LIMIT 1", (session_id, goal_id),
        ).fetchone()
    return dict(row) if row else None


def append_criterion_event(
    *, session_id: str, goal_id: str, goal_contract_hash: str,
    criterion_id: str, status: str, evidence_refs: list[str],
    work_package_id: str | None, reason: str, created_at: str,
    db_path: Path | None = None,
) -> int:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO goal_criterion_events "
            "(session_id, goal_id, goal_contract_hash, criterion_id, status, "
            " evidence_refs_json, work_package_id, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, goal_id, goal_contract_hash, criterion_id, status,
             json.dumps(evidence_refs, sort_keys=True), work_package_id, reason,
            created_at),
        )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)


def fold_criterion_states(
    session_id: str, goal_id: str, db_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Fold append-only events into the latest state per criterion."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM goal_criterion_events WHERE session_id = ? AND goal_id = ? "
            "ORDER BY id", (session_id, goal_id),
        ).fetchall()
    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = dict(row)
        value["evidence_refs"] = json.loads(value.pop("evidence_refs_json"))
        states[str(value["criterion_id"])] = value
    return states


def insert_work_package_version(
    *, session_id: str, work_package_id: str, version: int,
    goal_contract_hash: str, canonical_json: str, contract_hash: str,
    supersedes_hash: str | None, created_at: str, db_path: Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO work_package_versions "
            "(contract_hash, session_id, work_package_id, version, "
            " goal_contract_hash, canonical_json, supersedes_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (contract_hash, session_id, work_package_id, version,
             goal_contract_hash, canonical_json, supersedes_hash, created_at),
        )


def get_work_package_version(
    contract_hash: str, db_path: Path | None = None,
) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM work_package_versions WHERE contract_hash = ?",
            (contract_hash,),
        ).fetchone()
    return dict(row) if row else None


def latest_work_package_version(
    session_id: str, work_package_id: str, db_path: Path | None = None,
) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM work_package_versions WHERE session_id = ? "
            "AND work_package_id = ? ORDER BY version DESC LIMIT 1",
            (session_id, work_package_id),
        ).fetchone()
    return dict(row) if row else None


def latest_work_packages_for_goal(
    session_id: str, goal_contract_hash: str, db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the latest immutable version of each WP under one Goal Contract."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT wp.* FROM work_package_versions AS wp JOIN ("
            " SELECT work_package_id, MAX(version) AS version"
            " FROM work_package_versions WHERE session_id = ? AND goal_contract_hash = ?"
            " GROUP BY work_package_id"
            ") AS latest ON latest.work_package_id = wp.work_package_id"
            " AND latest.version = wp.version WHERE wp.session_id = ?"
            " AND wp.goal_contract_hash = ? ORDER BY wp.created_at, wp.work_package_id",
            (session_id, goal_contract_hash, session_id, goal_contract_hash),
        ).fetchall()
    return [dict(row) for row in rows]


def insert_work_package_attempt(
    *, attempt_id: str, session_id: str, goal_id: str, work_package_id: str,
    contract_hash: str, attempt: int, status: str, created_at: str,
    db_path: Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO work_package_attempts "
            "(attempt_id, session_id, goal_id, work_package_id, contract_hash, "
            " attempt, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (attempt_id, session_id, goal_id, work_package_id, contract_hash,
             attempt, status, created_at),
        )


def finish_work_package_attempt(
    *, attempt_id: str, status: str, failure_class: str | None,
    tokens_used: int, turns_used: int, criterion_delta: Mapping[str, str],
    completed_at: str, db_path: Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE work_package_attempts SET status = ?, failure_class = ?, "
            "tokens_used = ?, turns_used = ?, criterion_delta_json = ?, "
            "completed_at = ? WHERE attempt_id = ? AND status = 'running'",
            (status, failure_class, tokens_used, turns_used,
             json.dumps(dict(criterion_delta), sort_keys=True), completed_at,
             attempt_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("work package attempt is missing or already terminal")


def get_work_package_attempts(
    session_id: str, work_package_id: str, db_path: Path | None = None,
) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM work_package_attempts WHERE session_id = ? "
            "AND work_package_id = ? ORDER BY created_at, attempt",
            (session_id, work_package_id),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        value = dict(row)
        value["criterion_delta"] = json.loads(value.pop("criterion_delta_json"))
        result.append(value)
    return result


def goal_attempt_usage(
    session_id: str, goal_id: str, db_path: Path | None = None,
) -> dict[str, int]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS attempts, COALESCE(SUM(tokens_used), 0) AS tokens,"
            " COALESCE(SUM(turns_used), 0) AS turns FROM work_package_attempts"
            " WHERE session_id = ? AND goal_id = ?",
            (session_id, goal_id),
        ).fetchone()
    return {
        "attempts": int(row["attempts"]),
        "tokens": int(row["tokens"]),
        "turns": int(row["turns"]),
    }


def append_verification_evidence(
    *, attempt_id: str, criterion_id: str, verifier_ref: str, status: str,
    evidence: Mapping[str, Any], created_at: str, db_path: Path | None = None,
) -> int:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO verification_evidence "
            "(attempt_id, criterion_id, verifier_ref, status, evidence_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (attempt_id, criterion_id, verifier_ref, status,
             json.dumps(dict(evidence), sort_keys=True), created_at),
        )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)


def append_budget_event(
    *, session_id: str, goal_id: str, work_package_id: str | None,
    attempt_id: str | None, event: str, tokens: int, turns: int,
    detail: Mapping[str, Any], created_at: str, db_path: Path | None = None,
) -> int:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO budget_events "
            "(session_id, goal_id, work_package_id, attempt_id, event, tokens, "
            " turns, detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, goal_id, work_package_id, attempt_id, event, tokens,
             turns, json.dumps(dict(detail), sort_keys=True), created_at),
        )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)


def capture_rollback_file(
    *, session_id: str, work_package_id: str, attempt_id: str,
    root_alias: str, path: str, original_exists: bool,
    original_bytes: bytes | None, before_sha256: str | None, created_at: str,
    db_path: Path | None = None,
) -> None:
    """Capture original bytes once; repeated edits keep the first baseline."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO rollback_journal "
            "(session_id, work_package_id, attempt_id, root_alias, path, "
            " original_exists, original_bytes, before_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, work_package_id, attempt_id, root_alias, path,
             1 if original_exists else 0, original_bytes, before_sha256, created_at),
        )


def mark_rollback_file_after(
    *, attempt_id: str, root_alias: str, path: str, after_sha256: str | None,
    db_path: Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE rollback_journal SET after_sha256 = ? "
            "WHERE attempt_id = ? AND root_alias = ? AND path = ?",
            (after_sha256, attempt_id, root_alias, path),
        )


def get_rollback_files(
    attempt_id: str, db_path: Path | None = None,
) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM rollback_journal WHERE attempt_id = ? ORDER BY id DESC",
            (attempt_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_rollback_file_status(
    journal_id: int, status: str, rolled_back_at: str,
    db_path: Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE rollback_journal SET status = ?, rolled_back_at = ? WHERE id = ?",
            (status, rolled_back_at, journal_id),
        )


def goal_execution_snapshot(
    session_id: str, goal_id: str, db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Materialize Goal → WP → Attempt → Evidence for replay/Console use."""
    goal = latest_goal_contract(session_id, goal_id, db_path)
    if goal is None:
        return None
    goal_value = dict(goal)
    goal_value["contract"] = json.loads(goal_value.pop("canonical_json"))
    criteria = list(fold_criterion_states(session_id, goal_id, db_path).values())
    packages = latest_work_packages_for_goal(
        session_id, str(goal["contract_hash"]), db_path,
    )
    with _connect(db_path) as conn:
        for package in packages:
            package["contract"] = json.loads(package.pop("canonical_json"))
            attempts = conn.execute(
                "SELECT * FROM work_package_attempts WHERE session_id = ? "
                "AND work_package_id = ? ORDER BY attempt",
                (session_id, package["work_package_id"]),
            ).fetchall()
            package["attempts"] = []
            for raw_attempt in attempts:
                attempt = dict(raw_attempt)
                attempt["criterion_delta"] = json.loads(
                    attempt.pop("criterion_delta_json")
                )
                evidence = conn.execute(
                    "SELECT * FROM verification_evidence WHERE attempt_id = ? ORDER BY id",
                    (attempt["attempt_id"],),
                ).fetchall()
                attempt["evidence"] = []
                for raw_evidence in evidence:
                    evidence_value = dict(raw_evidence)
                    evidence_value["evidence"] = json.loads(
                        evidence_value.pop("evidence_json")
                    )
                    attempt["evidence"].append(evidence_value)
                attempt["budget_events"] = [
                    {**dict(event), "detail": json.loads(event["detail_json"])}
                    for event in conn.execute(
                        "SELECT * FROM budget_events WHERE attempt_id = ? ORDER BY id",
                        (attempt["attempt_id"],),
                    ).fetchall()
                ]
                attempt["rollback"] = [
                    {
                        key: value for key, value in dict(journal).items()
                        if key != "original_bytes"
                    }
                    for journal in conn.execute(
                        "SELECT * FROM rollback_journal WHERE attempt_id = ? ORDER BY id",
                        (attempt["attempt_id"],),
                    ).fetchall()
                ]
                package["attempts"].append(attempt)
    return {"goal": goal_value, "criteria": criteria, "work_packages": packages}
