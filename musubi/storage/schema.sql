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
    -- recorded by musubi_resume_session) or renders the gate UI.
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
-- attempt's read context — populated when `musubi_increment_attempt`
-- is called with a non-empty hint.
-- Phase G.1.7 adds `chunk_id` so a single stage can have multiple
-- per-task attempts (e.g. coder runs once for T1, again for T2). NULL
-- means "global" — the row covers the full stage (plan / design / a
-- non-chunked code or review). The composite write-once key becomes
-- (session_id, stage, chunk_id, attempt).
-- Phase G.2 adds `schema_version` so older rows can be migrated to a
-- newer schema shape on read. Default 'v1' covers all pre-G.2 rows;
-- writes go in at `validation/verifier.CURRENT_SCHEMA_VERSION`.
CREATE TABLE IF NOT EXISTS stage_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    stage           TEXT    NOT NULL,   -- plan | design | code | review
    attempt         INTEGER NOT NULL DEFAULT 1,
    status          TEXT    NOT NULL DEFAULT 'pending',  -- pending | in_progress | complete
    output          TEXT,               -- JSON blob, NULL until written
    written_at      TEXT,
    user_hint       TEXT,               -- optional: one-line retry hint from the gate UI
    chunk_id        TEXT,               -- optional: per-task chunk identifier (e.g. 'T1')
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
CREATE UNIQUE INDEX IF NOT EXISTS uq_stage_outputs_without_chunk
    ON stage_outputs(session_id, stage, attempt) WHERE chunk_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_stage_outputs_with_chunk
    ON stage_outputs(session_id, stage, chunk_id, attempt) WHERE chunk_id IS NOT NULL;
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

-- Worker invocations (Phase A.1). One row per worker (an agent spawned by
-- another); the table keeps the "sub_sessions" name for stability — "worker"
-- and "sub-agent" are the same concept. Populated by session/sub_sessions.py.
-- Lifecycle is single-shot:
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
    pushed_skill_id      TEXT,                       -- root's per-spawn skill override; the role's native push is resolved by build_subagent_context
    status               TEXT NOT NULL DEFAULT 'running',
    result_summary       TEXT,
    result_structured    TEXT,                       -- JSON
    tools_used           TEXT,                       -- JSON array
    turns                INTEGER NOT NULL DEFAULT 0,
    escalated            INTEGER NOT NULL DEFAULT 0, -- 0/1 boolean
    turn_cap_accepted    INTEGER NOT NULL DEFAULT 0, -- 0/1: accepted exactly at max_turns
    turn_cap_acceptance  TEXT,                       -- verified_artifacts | verified_readonly_response
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

-- Durable outbox for audit evidence that must exist before a worker becomes
-- runnable (HI #8). A failed delivery remains pending and the reserved worker
-- is abandoned; recovery may deliver evidence but never resurrect the run.
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

-- Conversation messages (Phase C.1). One row per user / assistant / tool /
-- system turn in an agent chat. The agent runner replays the
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

-- Schema-migration audit (Phase G.2). One row per migration applied
-- to a `stage_outputs` row. `musubi_query_schema_migrations` exposes
-- this for debugging when a migration silently shape-shifts data.
-- Hard Invariant #8 ("no silent migrations") — same discipline the
-- subagent_audit table enforces for sub-agent runs.
CREATE TABLE IF NOT EXISTS schema_migrations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    session_id    TEXT NOT NULL,
    stage         TEXT NOT NULL,
    chunk_id      TEXT,                              -- NULL for non-chunked stages
    attempt       INTEGER NOT NULL,
    agent         TEXT NOT NULL,                     -- planner | designer | coder | reviewer
    from_version  TEXT NOT NULL,
    to_version    TEXT NOT NULL,
    success       INTEGER NOT NULL DEFAULT 1,        -- 0 when migration raised
    error         TEXT,                              -- non-null when success=0
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_schema_migrations_session
    ON schema_migrations (session_id);
CREATE INDEX IF NOT EXISTS idx_schema_migrations_ts
    ON schema_migrations (ts);

-- Phase G.3 — observability primitives. One row per musubi_new_session
-- (pipeline_runs) and one row per stage attempt (stage_metrics). The
-- `musubi_pipeline_stats` MCP tool aggregates these into success-rate /
-- median-tokens / median-wall-clock dashboards.
--
-- Composite key for stage_metrics: (session_id, stage, chunk_id, attempt).
-- chunk_id is NULL for non-chunked stages (G.1.7); chunked stages get
-- one stage_metrics row per (chunk, attempt). schema_version follows the
-- G.2 versioning discipline so a future stats-row schema bump rides the
-- existing migration registry.
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
    schema_version      TEXT NOT NULL DEFAULT 'v1',
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_stage_metrics_session
    ON stage_metrics (session_id);

CREATE TABLE IF NOT EXISTS agent_cycles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,
    stage               TEXT NOT NULL,
    attempt             INTEGER NOT NULL,
    chunk_id            TEXT,
    cycle_idx           INTEGER NOT NULL,
    started_at          REAL NOT NULL,
    ended_at            REAL,
    lm_ms               INTEGER NOT NULL DEFAULT 0,
    tool_calls_json     TEXT,
    text_chars          INTEGER NOT NULL DEFAULT 0,
    worker_id           TEXT NOT NULL DEFAULT 'root',
    tokens_in           INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    tokens_out          INTEGER NOT NULL DEFAULT 0,
    token_source        TEXT NOT NULL DEFAULT 'estimated',
    cycle_status        TEXT NOT NULL DEFAULT 'ok',
    schema_version      TEXT NOT NULL DEFAULT 'v1',
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_cycles_session
    ON agent_cycles (session_id);

-- Phase J follow-up — agent-turn observability. Parallel to
-- `stage_metrics` (which is pipeline-only) so the Tasks view and any
-- current cross-session token dashboard can show agent usage
-- alongside pipeline usage instead of having to load
-- `conversation_messages` (raw chat log without metrics).
-- One row per agent turn. parent_session_id is the
-- per-turn synthetic session the runner mints in createAgentSession.
-- chat_id stays stable across all turns of the same chat panel.
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
    -- 1 when some worker in this turn finished with files on disk. Per-turn
    -- budgets are process-scoped, so this is what lets a LATER turn see that
    -- the conversation has been spending without delivering anything.
    delivered_artifact   INTEGER NOT NULL DEFAULT 0,
    -- One-time destructive-approval tokens this turn is waiting on, JSON
    -- `[{token, keys}]`. Matched literally against the NEXT user message; a
    -- model cannot author a user turn, so a match proves a human approved.
    pending_destructive   TEXT,
    -- What the ROOT declared this turn to be, as `shape: reason`. Recorded,
    -- never checked: the harness cannot know whether a triage was right, and a
    -- shape inferred from behaviour would be indistinguishable from one the
    -- model stated — which would ruin the only record that makes an overridden
    -- routing hint reviewable afterwards. NULL when nothing was declared.
    root_triage           TEXT,
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

-- Append-only authority snapshot for one Orchestrator request.
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
    contract_hash TEXT NOT NULL,
    session_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    canonical_json TEXT NOT NULL,
    supersedes_hash TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, contract_hash),
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
    contract_hash TEXT NOT NULL,
    session_id TEXT NOT NULL,
    work_package_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    goal_contract_hash TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    supersedes_hash TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, contract_hash),
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
