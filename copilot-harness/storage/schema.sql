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
