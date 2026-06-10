"""Phase G.3 — observability primitives tests.

Acceptance criteria from the roadmap:
  1. Running feature-dev once populates pipeline_runs + N stage_metrics
     rows (one per stage attempt; chunked stages get one row per
     (chunk, attempt)).
  2. Aggregate query returns sensible numbers on a 5-run history.
  3. Tables migrate cleanly from existing audit.db (no data loss on
     existing sessions).
"""

from __future__ import annotations

import json
import sqlite3
import time as _time
from pathlib import Path

import pytest

import server
from session import state
from storage import db
from validation import observability


@pytest.fixture
def fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    p = tmp_path / "harness.db"
    db.init_db(p)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", p)
    return p


# ── Schema ────────────────────────────────────────────────────────────


def test_init_db_creates_pipeline_runs_table(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_runs)")}
    expected = {
        "session_id", "pipeline_name", "started_at", "ended_at",
        "final_status", "total_tokens_estimate", "correction_attempts",
        "escalated", "chunked", "chunk_count", "schema_version",
    }
    assert expected.issubset(cols)


def test_init_db_creates_stage_metrics_table(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(stage_metrics)")}
    expected = {
        "session_id", "stage", "chunk_id", "attempt",
        "started_at", "ended_at",
        "tokens_in_estimate", "tokens_out_estimate", "lm_ms",
        "tool_count", "tool_failures", "schema_version",
    }
    assert expected.issubset(cols)


def test_pre_g3_db_gets_new_tables_added(tmp_path: Path) -> None:
    """Acceptance #3 — existing DBs without pipeline_runs / stage_metrics
    get them on next init_db() with no data loss to other tables."""
    p = tmp_path / "old.db"
    with sqlite3.connect(p) as conn:
        conn.executescript("""
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY, request TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO sessions VALUES ('s1','do thing','active','now','now')"
        )

    db.init_db(p)

    with sqlite3.connect(p) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        )}
        sess = conn.execute(
            "SELECT request FROM sessions WHERE session_id='s1'",
        ).fetchone()
    assert "pipeline_runs" in tables
    assert "stage_metrics" in tables
    assert sess[0] == "do thing"  # existing data preserved


# ── pipeline_runs lifecycle ────────────────────────────────────────────


def test_create_session_opens_pipeline_runs_row(fresh_db: Path) -> None:
    sid = state.create_session("build a feature", fresh_db)
    row = db.get_pipeline_run(sid, fresh_db)
    assert row is not None
    assert row["pipeline_name"] == "feature-dev"
    assert row["started_at"] is not None
    # Open row — ended_at + final_status NULL until finalize.
    assert row["ended_at"] is None
    assert row["final_status"] is None
    assert row["total_tokens_estimate"] == 0


def test_create_session_with_explicit_pipeline_name(fresh_db: Path) -> None:
    sid = state.create_session("review pr", fresh_db, pipeline_name="code-review")
    row = db.get_pipeline_run(sid, fresh_db)
    assert row is not None
    assert row["pipeline_name"] == "code-review"


def test_finalize_pipeline_run_sets_terminal_fields(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    db.finalize_pipeline_run(
        session_id=sid,
        ended_at=_time.time(),
        final_status="success",
        total_tokens_estimate=4321,
        correction_attempts=2,
        escalated=False,
        chunked=True,
        chunk_count=3,
    )
    row = db.get_pipeline_run(sid, fresh_db)
    assert row is not None
    assert row["final_status"] == "success"
    assert row["total_tokens_estimate"] == 4321
    assert row["correction_attempts"] == 2
    assert row["escalated"] == 0
    assert row["chunked"] == 1
    assert row["chunk_count"] == 3
    assert row["ended_at"] is not None


def test_finalize_pipeline_run_is_idempotent(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    db.finalize_pipeline_run(
        sid, _time.time(), "success", 100, 0, False, False, 0,
    )
    # Second call overwrites without raising.
    db.finalize_pipeline_run(
        sid, _time.time(), "escalated", 200, 1, True, False, 0,
    )
    row = db.get_pipeline_run(sid, fresh_db)
    assert row["final_status"] == "escalated"
    assert row["total_tokens_estimate"] == 200


# ── stage_metrics lifecycle ────────────────────────────────────────────


def test_insert_stage_metric_round_trips(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    db.insert_stage_metric(
        session_id=sid, stage="plan", attempt=1,
        started_at=1.0, ended_at=2.0,
        tokens_in_estimate=500, tokens_out_estimate=200,
        lm_ms=1000,
        db_path=fresh_db,
    )
    rows = db.query_stage_metrics(sid, fresh_db)
    assert len(rows) == 1
    assert rows[0]["stage"] == "plan"
    assert rows[0]["tokens_in_estimate"] == 500
    assert rows[0]["lm_ms"] == 1000


def test_chunked_stage_metrics_distinguish_by_chunk_id(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    for chunk_id in ("T1", "T2", "T3"):
        db.insert_stage_metric(
            sid, "code", attempt=1,
            started_at=1.0, ended_at=2.0,
            tokens_in_estimate=100, tokens_out_estimate=50,
            lm_ms=500,
            db_path=fresh_db, chunk_id=chunk_id,
        )
    rows = db.query_stage_metrics(sid, fresh_db)
    assert len(rows) == 3
    assert sorted(r["chunk_id"] for r in rows) == ["T1", "T2", "T3"]


def test_total_tokens_for_session_sums_in_and_out(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    db.insert_stage_metric(
        sid, "plan", 1, 1.0, 2.0,
        tokens_in_estimate=500, tokens_out_estimate=200, lm_ms=100,
        db_path=fresh_db,
    )
    db.insert_stage_metric(
        sid, "design", 1, 2.0, 3.0,
        tokens_in_estimate=800, tokens_out_estimate=300, lm_ms=200,
        db_path=fresh_db,
    )
    total = db.total_tokens_for_session(sid, fresh_db)
    assert total == 500 + 200 + 800 + 300


# ── Stage 1 (MVP A.4) — credits per stage_metrics row ────────────────


def test_insert_stage_metric_records_credits_and_family(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    db.insert_stage_metric(
        sid, "plan", 1, 1.0, 2.0,
        tokens_in_estimate=500, tokens_out_estimate=200, lm_ms=100,
        db_path=fresh_db, credits=2.5, model_family="claude-sonnet-4.5",
    )
    rows = db.query_stage_metrics(sid, fresh_db)
    assert len(rows) == 1
    assert rows[0]["credits"] == 2.5
    assert rows[0]["model_family"] == "claude-sonnet-4.5"


def test_stage_metric_credits_defaults_to_zero(fresh_db: Path) -> None:
    """Backwards-compat: callers that don't pass credits get 0.0 stored,
    not NULL — which is what total_credits_for_session relies on."""
    sid = state.create_session("do x", fresh_db)
    db.insert_stage_metric(
        sid, "plan", 1, 1.0, 2.0,
        tokens_in_estimate=500, tokens_out_estimate=200, lm_ms=100,
        db_path=fresh_db,
    )
    rows = db.query_stage_metrics(sid, fresh_db)
    assert rows[0]["credits"] == 0.0
    assert rows[0]["model_family"] is None


def test_total_credits_for_session_sums_rows(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    for stage, credits in (("plan", 1.5), ("design", 3.2), ("code", 7.8)):
        db.insert_stage_metric(
            sid, stage, 1, 1.0, 2.0,
            tokens_in_estimate=100, tokens_out_estimate=50, lm_ms=100,
            db_path=fresh_db, credits=credits,
        )
    assert db.total_credits_for_session(sid, fresh_db) == pytest.approx(12.5)


def test_total_credits_for_session_returns_zero_on_no_rows(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    assert db.total_credits_for_session(sid, fresh_db) == 0.0


def test_total_credits_since_aggregates_across_sessions(fresh_db: Path) -> None:
    s1 = state.create_session("task one", fresh_db)
    s2 = state.create_session("task two", fresh_db)
    db.insert_stage_metric(s1, "plan", 1, 100.0, 101.0, 100, 50, 50,
                           db_path=fresh_db, credits=1.0)
    db.insert_stage_metric(s2, "plan", 1, 200.0, 201.0, 100, 50, 50,
                           db_path=fresh_db, credits=2.0)
    # cutoff = 150 → only s2 included
    assert db.total_credits_since(150.0, fresh_db) == pytest.approx(2.0)
    # cutoff = 50  → both included
    assert db.total_credits_since(50.0, fresh_db) == pytest.approx(3.0)


def test_get_status_includes_total_credits(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    db.insert_stage_metric(
        sid, "plan", 1, 1.0, 2.0,
        tokens_in_estimate=100, tokens_out_estimate=50, lm_ms=100,
        db_path=fresh_db, credits=4.2,
    )
    status = state.get_status(sid, fresh_db)
    assert status["total_credits"] == pytest.approx(4.2)


def test_get_status_total_credits_zero_when_no_metrics(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    status = state.get_status(sid, fresh_db)
    assert status["total_credits"] == 0.0


def test_derive_correction_attempts_zero_for_first_attempt(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    # No retries — attempt stays at 1, corrections = 0.
    assert db.derive_correction_attempts(sid, fresh_db) == 0


def test_derive_correction_attempts_counts_retries(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    state.increment_attempt(sid, "code", fresh_db)
    state.increment_attempt(sid, "code", fresh_db)
    # attempt is now 3 ⇒ 2 corrections
    assert db.derive_correction_attempts(sid, fresh_db) == 2


def test_derive_correction_attempts_sums_chunked_attempts(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    state.ensure_chunk_row(sid, "code", "T1", fresh_db)
    state.ensure_chunk_row(sid, "code", "T2", fresh_db)
    # T1: 1 retry (attempt 2). T2: 2 retries (attempt 3).
    state.increment_attempt(sid, "code", fresh_db, chunk_id="T1")
    state.increment_attempt(sid, "code", fresh_db, chunk_id="T2")
    state.increment_attempt(sid, "code", fresh_db, chunk_id="T2")
    # Chunked sum: 1 + 2 = 3. The non-chunked code row is at attempt 1
    # (added by create_session), contributing 0.
    assert db.derive_correction_attempts(sid, fresh_db) == 3


# ── MCP tools end-to-end ───────────────────────────────────────────────


def test_harness_record_stage_metric_writes_a_row(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    raw = server.harness_record_stage_metric(
        session_id=sid, stage="plan", attempt=1,
        started_at=1.0, ended_at=2.0,
        tokens_in_estimate=500, tokens_out_estimate=200,
        lm_ms=1000,
    )
    assert json.loads(raw)["status"] == "ok"
    rows = json.loads(server.harness_query_stage_metrics(sid))["rows"]
    assert len(rows) == 1


def test_harness_finalize_rejects_unknown_status(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    raw = server.harness_finalize_pipeline_run(
        session_id=sid, final_status="exploded",
    )
    assert json.loads(raw)["status"] == "error"


def test_harness_finalize_derives_tokens_and_corrections(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    state.increment_attempt(sid, "code", fresh_db)  # one correction
    server.harness_record_stage_metric(
        session_id=sid, stage="code", attempt=2,
        started_at=1.0, ended_at=2.0,
        tokens_in_estimate=100, tokens_out_estimate=50, lm_ms=300,
    )
    raw = server.harness_finalize_pipeline_run(
        session_id=sid, final_status="success",
        escalated=False, chunked=False, chunk_count=0,
    )
    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert payload["total_tokens_estimate"] == 150
    assert payload["correction_attempts"] == 1


def test_harness_query_pipeline_runs_filters_by_pipeline_name(
    fresh_db: Path,
) -> None:
    sid_a = state.create_session("a", fresh_db, pipeline_name="feature-dev")
    sid_b = state.create_session("b", fresh_db, pipeline_name="code-review")
    server.harness_finalize_pipeline_run(sid_a, "success")
    server.harness_finalize_pipeline_run(sid_b, "success")
    raw = server.harness_query_pipeline_runs(pipeline_name="code-review")
    rows = json.loads(raw)["rows"]
    assert len(rows) == 1
    assert rows[0]["session_id"] == sid_b


# ── Aggregation: 5-run history ────────────────────────────────────────


def test_aggregate_pipeline_stats_empty_returns_zeros() -> None:
    stats = observability.aggregate_pipeline_stats([])
    assert stats["count"] == 0
    assert stats["success_rate"] == 0.0
    assert stats["median_tokens"] == 0


def test_aggregate_pipeline_stats_5_runs(fresh_db: Path) -> None:
    """Acceptance #2 — aggregate query returns sensible numbers on a
    5-run history."""
    sids = []
    for i in range(5):
        sid = state.create_session(f"run {i}", fresh_db)
        sids.append(sid)

    # Outcomes: 3 success, 1 escalated, 1 success-but-escalated=false.
    # Token estimates: 100, 200, 300, 400, 500. Median = 300, p90 ≈ 460.
    finals = [
        ("success",   False, 100),
        ("success",   False, 200),
        ("success",   False, 300),
        ("escalated", True,  400),
        ("success",   False, 500),
    ]
    for sid, (status, esc, tok) in zip(sids, finals, strict=True):
        # Inject token totals via stage_metrics so derive sums correctly.
        db.insert_stage_metric(
            sid, "plan", 1, 1.0, 2.0,
            tokens_in_estimate=tok, tokens_out_estimate=0, lm_ms=100,
            db_path=fresh_db,
        )
        server.harness_finalize_pipeline_run(
            sid, status, escalated=esc, chunked=False, chunk_count=0,
        )

    raw = server.harness_pipeline_stats(pipeline_name="feature-dev")
    stats = json.loads(raw)
    assert stats["status"] == "ok"
    assert stats["count"] == 5
    assert stats["success_rate"] == pytest.approx(4 / 5)
    assert stats["escalate_rate"] == pytest.approx(1 / 5)
    assert stats["median_tokens"] == 300
    assert 400 <= stats["p90_tokens"] <= 500
    assert stats["chunked_run_pct"] == 0.0


def test_pipeline_stats_excludes_in_flight_runs(fresh_db: Path) -> None:
    """An open pipeline_runs row (ended_at IS NULL) must NOT skew
    aggregates — only terminal rows feed the stats query."""
    state.create_session("in-flight", fresh_db)  # never finalized
    sid_done = state.create_session("done", fresh_db)
    server.harness_finalize_pipeline_run(sid_done, "success")

    raw = server.harness_pipeline_stats(pipeline_name="feature-dev")
    stats = json.loads(raw)
    assert stats["count"] == 1, "in-flight session must be excluded"


# ── Integration: harness_new_session opens the row ────────────────────


def test_harness_new_session_opens_pipeline_runs_row(fresh_db: Path) -> None:
    raw = server.harness_new_session("test request", pipeline_name="feature-dev")
    sid = json.loads(raw)["session_id"]
    row = db.get_pipeline_run(sid, fresh_db)
    assert row is not None
    assert row["pipeline_name"] == "feature-dev"
