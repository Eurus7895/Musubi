"""Phase J follow-up — agent_turns table + CRUD tests.

Parallel to test_g3_observability.py's stage_metrics tests. The
agent path doesn't fit the stage / chunk / attempt model used
by pipelines, so it gets its own table and CRUD pair.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest

import server
from agent.run import AgentRunStats, _record_agent_turn
from storage import db


@pytest.fixture
def fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    p = tmp_path / "harness.db"
    db.init_db(p)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", p)
    return p


# ── Schema ────────────────────────────────────────────────────────────


def test_init_db_creates_agent_turns_table(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_turns)")}
    expected = {
        "chat_id", "request_id", "parent_session_id",
        "started_at", "ended_at",
        "model_family", "cycles",
        "tokens_in_estimate", "tokens_out_estimate",
        "lm_ms", "total_ms", "schema_version",
    }
    assert expected.issubset(cols)
    assert {"replay_messages", "replay_tokens"}.isdisjoint(cols)


def test_chat_turn_usage_aggregates_conversation_cost(fresh_db: Path) -> None:
    # Per-turn budgets reset on every chat message, so this aggregate is the
    # only thing that can see a conversation spending without delivering.
    for index, delivered in enumerate((False, False, False)):
        db.insert_agent_turn(
            chat_id="chat-a",
            parent_session_id=f"s{index}",
            started_at=float(index),
            ended_at=float(index) + 1,
            model_family="fake",
            cycles=2,
            tokens_in_estimate=100,
            tokens_out_estimate=50,
            lm_ms=10,
            total_ms=20,
            db_path=fresh_db,
            delivered_artifact=delivered,
        )

    usage = db.chat_turn_usage("chat-a", db_path=fresh_db)
    assert usage == {"turns": 3, "tokens": 450, "barren_turns": 3}

    # A delivering turn resets the trailing barren run to zero.
    db.insert_agent_turn(
        chat_id="chat-a",
        parent_session_id="s3",
        started_at=9.0,
        ended_at=10.0,
        model_family="fake",
        cycles=1,
        tokens_in_estimate=10,
        tokens_out_estimate=10,
        lm_ms=1,
        total_ms=2,
        db_path=fresh_db,
        delivered_artifact=True,
    )
    assert db.chat_turn_usage("chat-a", db_path=fresh_db)["barren_turns"] == 0


def test_pending_clarification_survives_one_turn_and_clears_on_the_next(
    fresh_db: Path,
) -> None:
    # The traced loop: "create a website" was answered with a canned question,
    # and the answer ("a weather checking website") classified identically, so
    # the same question came back three times. This row is what lets the next
    # turn know a question is outstanding and act on the answer instead.
    assert db.pending_clarification("chat-c", db_path=fresh_db) is None

    db.insert_agent_turn(
        chat_id="chat-c", parent_session_id="s0", started_at=0.0, ended_at=0.1,
        model_family="deterministic", cycles=0, tokens_in_estimate=0,
        tokens_out_estimate=0, lm_ms=0, total_ms=0, db_path=fresh_db,
        clarification_request="create a website",
    )
    assert db.pending_clarification("chat-c", db_path=fresh_db) == (
        "create a website"
    )
    # Scoped per conversation, and a blank id never claims a pending question.
    assert db.pending_clarification("chat-other", db_path=fresh_db) is None
    assert db.pending_clarification("", db_path=fresh_db) is None

    # A turn that actually ran clears it — no delete, no marker left to leak
    # into an unrelated later request.
    db.insert_agent_turn(
        chat_id="chat-c", parent_session_id="s1", started_at=1.0, ended_at=2.0,
        model_family="fake", cycles=3, tokens_in_estimate=10,
        tokens_out_estimate=10, lm_ms=1, total_ms=1, db_path=fresh_db,
    )
    assert db.pending_clarification("chat-c", db_path=fresh_db) is None


def test_chat_turn_usage_is_scoped_per_conversation(fresh_db: Path) -> None:
    db.insert_agent_turn(
        chat_id="chat-a", parent_session_id="s", started_at=0.0, ended_at=1.0,
        model_family="fake", cycles=1, tokens_in_estimate=5,
        tokens_out_estimate=5, lm_ms=1, total_ms=1, db_path=fresh_db,
    )
    assert db.chat_turn_usage("chat-b", db_path=fresh_db) == {
        "turns": 0, "tokens": 0, "barren_turns": 0,
    }
    assert db.chat_turn_usage("", db_path=fresh_db)["turns"] == 0


def test_init_db_creates_agent_turns_indexes(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        idx = {row[1] for row in conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='agent_turns'"
        )}
    assert "idx_agent_turns_chat" in idx
    assert "idx_agent_turns_started" in idx


# ── CRUD ──────────────────────────────────────────────────────────────


def test_insert_agent_turn_persists_row(fresh_db: Path) -> None:
    db.insert_agent_turn(
        chat_id="chat-1",
        request_id="request-1",
        parent_session_id="psess-1",
        started_at=1000.0,
        ended_at=1010.5,
        model_family="claude-haiku-4.5",
        cycles=2,
        tokens_in_estimate=2200,
        tokens_out_estimate=300,
        lm_ms=4500,
        total_ms=10500,
    )
    rows = db.query_agent_turns("chat-1")
    assert len(rows) == 1
    r = rows[0]
    assert r["chat_id"] == "chat-1"
    assert r["request_id"] == "request-1"
    assert r["parent_session_id"] == "psess-1"
    assert r["model_family"] == "claude-haiku-4.5"
    assert r["cycles"] == 2
    assert r["tokens_in_estimate"] == 2200
    assert r["tokens_out_estimate"] == 300
    assert r["lm_ms"] == 4500
    assert r["total_ms"] == 10500


def test_init_db_migrates_request_id_without_losing_legacy_turns(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy-agent-turns.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute(
            "CREATE TABLE agent_turns ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "chat_id TEXT NOT NULL,"
            "parent_session_id TEXT NOT NULL,"
            "started_at REAL NOT NULL,"
            "ended_at REAL,"
            "model_family TEXT NOT NULL,"
            "cycles INTEGER NOT NULL DEFAULT 0,"
            "tokens_in_estimate INTEGER NOT NULL DEFAULT 0,"
            "tokens_out_estimate INTEGER NOT NULL DEFAULT 0,"
            "lm_ms INTEGER NOT NULL DEFAULT 0,"
            "total_ms INTEGER NOT NULL DEFAULT 0,"
            "schema_version TEXT NOT NULL DEFAULT 'v1'"
            ")"
        )
        conn.execute(
            "INSERT INTO agent_turns"
            " (chat_id,parent_session_id,started_at,model_family)"
            " VALUES ('legacy-chat','legacy-parent',1.0,'deepseek')"
        )

    db.init_db(legacy)

    with sqlite3.connect(legacy) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_turns)")}
        row = conn.execute(
            "SELECT chat_id, parent_session_id, request_id FROM agent_turns"
        ).fetchone()
    assert "request_id" in cols
    assert row == ("legacy-chat", "legacy-parent", None)


def test_driver_turn_recorder_preserves_console_request_identity(
    fresh_db: Path,
) -> None:
    _record_agent_turn(
        chat_id="chat-console",
        request_id="request-console-1",
        parent_session_id="parent-console",
        started_at=10.0,
        ended_at=11.0,
        model_family="deepseek",
        stats=AgentRunStats(cycles=1, tokens_in_estimate=20),
        db_path=fresh_db,
        log=io.StringIO(),
    )

    row = db.query_agent_turns("chat-console", db_path=fresh_db)[0]
    assert row["request_id"] == "request-console-1"


def test_init_db_tolerates_legacy_replay_columns(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        conn.execute(
            "ALTER TABLE agent_turns ADD COLUMN replay_messages "
            "INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            "ALTER TABLE agent_turns ADD COLUMN replay_tokens "
            "INTEGER NOT NULL DEFAULT 0"
        )
    db.init_db(fresh_db)
    db.insert_agent_turn(
        chat_id="legacy", parent_session_id="p", started_at=1.0,
        ended_at=2.0, model_family="m", cycles=1,
        tokens_in_estimate=10, tokens_out_estimate=5, lm_ms=1, total_ms=2,
        db_path=fresh_db,
    )
    with sqlite3.connect(fresh_db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM agent_turns WHERE chat_id='legacy'"
        ).fetchone()[0]
    assert count == 1
    row = db.query_agent_turns("legacy", db_path=fresh_db)[0]
    assert {"replay_messages", "replay_tokens"}.isdisjoint(row)


def test_query_agent_turns_returns_newest_first(fresh_db: Path) -> None:
    for i, ts in enumerate([1000.0, 2000.0, 3000.0]):
        db.insert_agent_turn(
            chat_id="chat-x",
            parent_session_id=f"psess-{i}",
            started_at=ts,
            ended_at=ts + 1,
            model_family="gpt-5-mini",
            cycles=1,
            tokens_in_estimate=100,
            tokens_out_estimate=50,
            lm_ms=1000,
            total_ms=1100,
        )
    rows = db.query_agent_turns("chat-x")
    assert [r["parent_session_id"] for r in rows] == ["psess-2", "psess-1", "psess-0"]


def test_query_agent_turns_isolates_by_chat(fresh_db: Path) -> None:
    db.insert_agent_turn(
        chat_id="chat-a", parent_session_id="p1",
        started_at=1000.0, ended_at=1001.0,
        model_family="haiku", cycles=1,
        tokens_in_estimate=10, tokens_out_estimate=5,
        lm_ms=100, total_ms=110,
    )
    db.insert_agent_turn(
        chat_id="chat-b", parent_session_id="p2",
        started_at=2000.0, ended_at=2001.0,
        model_family="sonnet", cycles=1,
        tokens_in_estimate=20, tokens_out_estimate=10,
        lm_ms=200, total_ms=220,
    )
    assert len(db.query_agent_turns("chat-a")) == 1
    assert len(db.query_agent_turns("chat-b")) == 1
    assert len(db.query_agent_turns("chat-c")) == 0


def test_query_agent_turns_honours_limit(fresh_db: Path) -> None:
    for i in range(10):
        db.insert_agent_turn(
            chat_id="chat-y", parent_session_id=f"p{i}",
            started_at=float(i), ended_at=float(i) + 1,
            model_family="haiku", cycles=1,
            tokens_in_estimate=10, tokens_out_estimate=5,
            lm_ms=100, total_ms=110,
        )
    assert len(db.query_agent_turns("chat-y", limit=3)) == 3
    assert len(db.query_agent_turns("chat-y", limit=100)) == 10


# ── MCP tools ─────────────────────────────────────────────────────────


def test_musubi_record_agent_turn_inserts_row(fresh_db: Path) -> None:
    raw = server.musubi_record_agent_turn(
        chat_id="chat-mcp",
        parent_session_id="psess-mcp",
        started_at=500.0,
        ended_at=510.0,
        model_family="claude-sonnet-4.6",
        cycles=3,
        tokens_in_estimate=5000,
        tokens_out_estimate=800,
        lm_ms=8000,
        total_ms=10000,
    )
    result = json.loads(raw)
    assert result["status"] == "ok"
    rows = db.query_agent_turns("chat-mcp")
    assert len(rows) == 1
    assert rows[0]["model_family"] == "claude-sonnet-4.6"
    assert rows[0]["cycles"] == 3


def test_musubi_query_agent_turns_returns_rows(fresh_db: Path) -> None:
    server.musubi_record_agent_turn(
        chat_id="chat-q", parent_session_id="p1",
        started_at=1.0, ended_at=2.0,
        model_family="haiku", cycles=1,
        tokens_in_estimate=100, tokens_out_estimate=50,
        lm_ms=500, total_ms=600,
    )
    raw = server.musubi_query_agent_turns("chat-q")
    result = json.loads(raw)
    assert result["status"] == "ok"
    assert result["chat_id"] == "chat-q"
    assert len(result["rows"]) == 1


def test_musubi_query_agent_turns_empty_chat(fresh_db: Path) -> None:
    raw = server.musubi_query_agent_turns("never-existed")
    result = json.loads(raw)
    assert result["status"] == "ok"
    assert result["rows"] == []
