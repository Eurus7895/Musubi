-- Phase G.1.5 adds six review-gate columns to this table; ALTER-based
-- migration in db.py::init_db handles existing DBs. Keep this CREATE in
-- sync with the embedded `_SCHEMA_SQL` in db.py and with the migration
-- list in `_PAUSE_RESUME_COLUMNS`.
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    request    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',   -- active | complete | escalated
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- Review gate (Phase G.1.5). Set when a stage completes and the gate
    -- is active; the runner reads on entry and either resumes (action
    -- recorded by harness_resume_session) or renders the gate UI.
    paused_at_stage         TEXT,                              -- stage just paused at, NULL when running
    pause_reason            TEXT,                              -- 'stage_review' | 'budget_exhausted' | NULL
    auto_approve_remaining  INTEGER NOT NULL DEFAULT 0,        -- session-scoped escape hatch (per-run)
    pending_action          TEXT,                              -- 'approve' | 'retry' | 'abort' | 'auto_approve_rest' | 'grant' | 'force' | NULL
    pending_user_hint       TEXT,                              -- one-line free text from the retry input box
    pending_extra_budget    INTEGER NOT NULL DEFAULT 0,        -- additional spawns granted on a budget_exhausted resume
    -- Chunked execution (Phase G.1.7). When the gate fires inside a
    -- chunked code/review run, paused_at_chunk records which chunk so
    -- the runner can resume the right one.
    paused_at_chunk         TEXT                               -- e.g. 'T1', NULL for non-chunked stages
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
-- Phase G.1.5 adds `user_hint` so a retry from the gate UI can carry
-- the user's "what was wrong with this attempt" note into the next
-- attempt's read context — populated when `harness_increment_attempt`
-- is called with a non-empty hint.
-- Phase G.1.7 adds `chunk_id` so a single stage can have multiple
-- per-task attempts (e.g. coder runs once for T1, again for T2). NULL
-- means "global" — the row covers the full stage (plan / design / a
-- non-chunked code or review). The composite write-once key becomes
-- (session_id, stage, chunk_id, attempt).
CREATE TABLE IF NOT EXISTS stage_outputs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    stage      TEXT    NOT NULL,   -- plan | design | code | review
    attempt    INTEGER NOT NULL DEFAULT 1,
    status     TEXT    NOT NULL DEFAULT 'pending',  -- pending | in_progress | complete
    output     TEXT,               -- JSON blob, NULL until written
    written_at TEXT,
    user_hint  TEXT,                -- optional: one-line retry hint from the gate UI
    chunk_id   TEXT,                -- optional: per-task chunk identifier (e.g. 'T1')
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

-- Conversation messages (Phase C.1). One row per user / assistant / tool /
-- system turn in an orchestrator chat. The orchestrator runner replays the
-- chronological history on every user turn (locked decision: replay-on-each-
-- turn). Truncation is token-budgeted and newest-first; see
-- session/conversations.py::get_history.
--
-- chat_id is opaque to the harness — the runner mints it (Phase C.2 plugs a
-- stable id; Phase B.2 used a heuristic). Roles are validated against
-- session/conversations.py::VALID_ROLES.
-- This schema mirrors the embedded `_SCHEMA_SQL` constant in db.py — keep
-- them in sync.
CREATE TABLE IF NOT EXISTS conversation_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT    NOT NULL,
    role       TEXT    NOT NULL,    -- 'user' | 'assistant' | 'tool' | 'system'
    content    TEXT    NOT NULL,
    ts         TEXT    NOT NULL     -- ISO8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_conv_chat_ts
    ON conversation_messages (chat_id, ts);
