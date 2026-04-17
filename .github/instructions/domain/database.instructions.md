---
applyTo: "**/models/**,**/storage/**,**/memory/**"
priority: P3
---

# Database Instructions — Domain Standard (P3)

## SQLite Rules

- Use parameterized queries always. No f-strings or `.format()` in SQL.
- Use `sqlite3.Row` as row factory for dict-like access.
- Enable WAL mode for concurrent reads: `PRAGMA journal_mode=WAL`.
- Enable foreign keys: `PRAGMA foreign_keys=ON`.

```python
# correct
cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))

# wrong — SQL injection risk
cursor.execute(f"SELECT * FROM sessions WHERE session_id = '{session_id}'")
```

## Schema

- Define schema in `storage/schema.sql`. Apply via `db.py` on startup.
- Use `CREATE TABLE IF NOT EXISTS` — idempotent migrations.
- All tables have a `created_at TEXT` column storing ISO 8601 UTC timestamps.
- Primary keys: use UUIDs stored as TEXT, not auto-increment integers.
- Enum-like columns: use `CHECK` constraints to enforce allowed values.

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    request TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'complete', 'escalated')),
    created_at TEXT NOT NULL
);
```

## State Transitions

- Session stages follow: `pending → in_progress → complete`. No backward transitions.
- Enforce in both SQL (`CHECK` constraint) and Python (`state.py` logic).
- Completed stage outputs are write-once. Reject overwrites at the `db.py` layer.

## Connection Management

- Use a single connection per session/request. Do not pool in SQLite.
- Always close connections in `finally` blocks or use context managers.
- Use `connection.commit()` explicitly. Do not rely on auto-commit.

## Append-Only Log

Session stage outputs are append-only. When a Coder retries, the attempt N output
is stored as a new row, not an update. Schema must reflect this:

```sql
CREATE TABLE IF NOT EXISTS stage_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    output TEXT NOT NULL,  -- JSON
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
```

## Indexes

Add indexes for columns used in `WHERE` clauses:

```sql
CREATE INDEX IF NOT EXISTS idx_stage_outputs_session ON stage_outputs(session_id, stage);
CREATE INDEX IF NOT EXISTS idx_fail_patterns_agent ON fail_patterns(agent, issue_type);
```

## Memory / Pattern Storage

The `memory/cross_session.db` tracks failure patterns across sessions. Schema:

```sql
CREATE TABLE IF NOT EXISTS fail_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

Query count before triggering Skill-Builder — threshold is 3 occurrences.
