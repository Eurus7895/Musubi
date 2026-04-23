# Indexing Strategies — Database Patterns Reference

Use this reference when designing indexes for the harness SQLite schema or
when `query-analyzer.py` reports index hints.

---

## When to Add an Index

Add an index when a column appears in:
- `WHERE` clause of a query that runs more than once per session
- `ORDER BY` or `GROUP BY`
- A foreign key column (SQLite does not auto-index FK columns)
- A column used in a JOIN condition

Do NOT index every column. Each index adds write overhead.

---

## Harness Schema — Recommended Indexes

```sql
-- sessions table
CREATE INDEX IF NOT EXISTS idx_sessions_status
    ON sessions(status);

-- stage_outputs table (most frequently queried)
CREATE INDEX IF NOT EXISTS idx_stage_outputs_session_stage
    ON stage_outputs(session_id, stage);

CREATE INDEX IF NOT EXISTS idx_stage_outputs_session_attempt
    ON stage_outputs(session_id, stage, attempt);

-- fail_patterns table (queried by agent + issue_type for threshold check)
CREATE INDEX IF NOT EXISTS idx_fail_patterns_agent_issue
    ON fail_patterns(agent, issue_type);

CREATE INDEX IF NOT EXISTS idx_fail_patterns_session
    ON fail_patterns(session_id);
```

---

## Composite Indexes

Use composite indexes when queries filter on two columns together:

```sql
-- correct: composite covers WHERE session_id = ? AND stage = ?
CREATE INDEX idx_stage_outputs_session_stage ON stage_outputs(session_id, stage);

-- less efficient: two separate indexes, query planner may only use one
CREATE INDEX idx_session ON stage_outputs(session_id);
CREATE INDEX idx_stage ON stage_outputs(stage);
```

Column order in composite index matters: put the higher-selectivity column first
(session_id before stage, since session_id has more unique values).

---

## EXPLAIN QUERY PLAN

To verify an index is being used:

```python
cursor.execute("EXPLAIN QUERY PLAN SELECT * FROM stage_outputs WHERE session_id = ?", (sid,))
plan = cursor.fetchall()
# look for "USING INDEX" in the plan output
```

If the plan shows "SCAN" instead of "SEARCH USING INDEX", add or adjust the index.

---

## Covering Indexes

A covering index includes all columns needed by a query, so SQLite never reads
the table itself:

```sql
-- query: SELECT stage, attempt, output FROM stage_outputs WHERE session_id = ?
-- covering index
CREATE INDEX idx_stage_outputs_covering
    ON stage_outputs(session_id, stage, attempt, output);
```

Only use covering indexes for very hot queries (called on every read).

---

## Append-Only Uniqueness

To enforce append-only semantics in SQL:

```sql
-- prevents duplicate (session_id, stage, attempt) rows at DB level
CREATE UNIQUE INDEX IF NOT EXISTS idx_stage_outputs_unique
    ON stage_outputs(session_id, stage, attempt);
```

This means an INSERT with a duplicate `(session_id, stage, attempt)` raises
`sqlite3.IntegrityError`, which `db.py` catches and returns as a conflict error.

---

## WAL Mode and Reads

With `PRAGMA journal_mode=WAL`, multiple readers can proceed concurrently with
one writer. Index reads are non-blocking. For the harness use case (one writer
at a time, multiple agents reading), WAL mode is optimal.

---

## When NOT to Index

- Columns with very low cardinality and no selectivity (e.g., a `type` column
  with only 2 values in a small table) — full scan is often faster
- Columns only used in INSERT/UPDATE, never SELECT
- Tables with fewer than 1000 rows — full scan is negligible
