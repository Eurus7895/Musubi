---
name: database-patterns
description: SQLite schema design, query patterns, and state management for append-only session storage. Use when the user is working with a database, schema, SQL, SQLite, queries, migrations, or state storage.
---

## Purpose

Design correct, performant SQLite schemas and queries for session state management,
append-only logs, and cross-session pattern storage.

## Procedure

1. **Define tables first.** Identify entities, their attributes, and relationships.
   Use `CREATE TABLE IF NOT EXISTS` for idempotent setup.

2. **Set pragmas on connection.**
   ```sql
   PRAGMA journal_mode=WAL;
   PRAGMA foreign_keys=ON;
   ```

3. **Use TEXT for everything non-numeric.** Dates as ISO 8601 text. UUIDs as TEXT.
   Enums as TEXT with CHECK constraints.

4. **Add CHECK constraints for enum columns.**
   ```sql
   status TEXT NOT NULL CHECK(status IN ('pending', 'in_progress', 'complete'))
   ```

5. **Enforce append-only in both SQL and Python.**
   - SQL: unique constraint on `(session_id, stage, attempt)`
   - Python: reject write if `(session_id, stage)` already has `status = 'complete'`

6. **Add indexes for every WHERE column.** See `indexing-strategies.md`.

7. **Use parameterized queries.** No string formatting in SQL. Ever.

8. **Analyze query performance** with `harness_run_asset` when queries run against
   large tables or use multiple JOINs.

## Assets

`query-analyzer.py` — analyzes SQL queries for correctness and performance.
Run via: `harness_run_asset("database-patterns", "query-analyzer.py", {"query": "SELECT ..."})`
Returns: index recommendations, potential N+1 issues, parameterization check.
Use when: writing a new query that JOINs more than 2 tables, or queries
`fail_patterns` with a GROUP BY.

## When to Load References

- Load `indexing-strategies.md` when: designing indexes, writing queries with
  ORDER BY or GROUP BY, or when the `query-analyzer.py` output suggests missing indexes
