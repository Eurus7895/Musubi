"""Phase J follow-up — agent_turns table + CRUD tests.

Parallel to test_g3_observability.py's stage_metrics tests. The
agent path doesn't fit the stage / chunk / attempt model used
by pipelines, so it gets its own table and CRUD pair.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import server
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
        "chat_id", "parent_session_id",
        "started_at", "ended_at",
        "model_family", "cycles",
        "tokens_in_estimate", "tokens_out_estimate",
        "lm_ms", "total_ms", "schema_version",
    }
    assert expected.issubset(cols)


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
    assert r["parent_session_id"] == "psess-1"
    assert r["model_family"] == "claude-haiku-4.5"
    assert r["cycles"] == 2
    assert r["tokens_in_estimate"] == 2200
    assert r["tokens_out_estimate"] == 300
    assert r["lm_ms"] == 4500
    assert r["total_ms"] == 10500


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
