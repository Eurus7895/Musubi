CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    request    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',   -- active | complete | escalated
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

-- Append-only: one row per stage per attempt.
-- output is NULL until the agent writes it; then it is write-once.
CREATE TABLE IF NOT EXISTS stage_outputs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    stage      TEXT    NOT NULL,   -- plan | design | code | review
    attempt    INTEGER NOT NULL DEFAULT 1,
    status     TEXT    NOT NULL DEFAULT 'pending',  -- pending | in_progress | complete
    output     TEXT,               -- JSON blob, NULL until written
    written_at TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

-- Single-row pointer to the most recent active session (crash recovery).
-- CHECK constraint enforces at most one row (singleton = 1 always).
CREATE TABLE IF NOT EXISTS active_session (
    singleton  INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton = 1),
    session_id TEXT,
    updated_at TEXT
);

-- Populated by pattern_detector.py (Day 5); schema defined here for FK integrity.
CREATE TABLE IF NOT EXISTS fail_patterns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    agent_name  TEXT NOT NULL,
    issue       TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

-- Sub-agent invocations (Phase A.1). One row per agent-spawned-by-another-agent
-- run; populated by session/sub_sessions.py. Lifecycle is single-shot:
--   running → done | failed | escalated | abandoned
-- The harness records spawn, then the extension-side runner records the
-- terminal result (turns, tools_used, summary, structured). Wall-clock /
-- max-turn breaches coerce the row to 'escalated' with escalated=1.
-- This schema mirrors the embedded `_SCHEMA_SQL` constant in db.py — keep
-- them in sync.
CREATE TABLE IF NOT EXISTS sub_sessions (
    handle_id            TEXT PRIMARY KEY,           -- uuid hex[:12]
    parent_session_id    TEXT NOT NULL,
    parent_agent_name    TEXT NOT NULL,
    role                 TEXT NOT NULL,              -- explorer | investigator | reviewer-aux | …
    brief                TEXT NOT NULL,
    allowed_tools        TEXT,                       -- JSON array of tool names
    max_turns            INTEGER NOT NULL,
    per_turn_timeout_s   INTEGER NOT NULL DEFAULT 60,
    wall_clock_timeout_s INTEGER NOT NULL DEFAULT 300,
    output_schema        TEXT,                       -- optional JSON schema for `result_structured`
    status               TEXT NOT NULL DEFAULT 'running',
    result_summary       TEXT,
    result_structured    TEXT,                       -- JSON
    tools_used           TEXT,                       -- JSON array
    turns                INTEGER NOT NULL DEFAULT 0,
    escalated            INTEGER NOT NULL DEFAULT 0, -- 0/1 boolean
    created_at           TEXT NOT NULL,
    completed_at         TEXT,
    FOREIGN KEY (parent_session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_sub_sessions_parent
    ON sub_sessions (parent_session_id);
CREATE INDEX IF NOT EXISTS idx_sub_sessions_status
    ON sub_sessions (status);
