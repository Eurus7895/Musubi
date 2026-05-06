"""Tests for session/conversations.py and the harness_append_message /
harness_get_conversation MCP tools (Phase C.1).

Storage seam for orchestrator replay-on-each-turn — covers role validation,
token-budgeted newest-first truncation, multi-chat isolation, deterministic
ordering under timestamp collisions, the UTF-8 invariant from CLAUDE.md, and
schema migration on a fresh DB.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from session import conversations
from storage import db as _db


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    _db.init_db(p)
    return p


# ── append_message ───────────────────────────────────────────────────────────


def test_append_message_returns_id_ts_tokens(db: Path) -> None:
    result = conversations.append_message(
        "chat-A", "user", "hello world", db_path=db
    )
    assert isinstance(result["message_id"], int)
    assert result["message_id"] > 0
    assert isinstance(result["ts"], str) and "T" in result["ts"]
    # 11 chars / 4 = 2 tokens by the verifier heuristic.
    assert result["tokens_estimate"] == 2


def test_append_message_persists_row(db: Path) -> None:
    conversations.append_message(
        "chat-A", "assistant", "hi there", db_path=db
    )
    rows = _db.get_conversation_messages("chat-A", db_path=db)
    assert len(rows) == 1
    assert rows[0]["role"] == "assistant"
    assert rows[0]["content"] == "hi there"


def test_append_rejects_unknown_role(db: Path) -> None:
    with pytest.raises(ValueError, match="role must be one of"):
        conversations.append_message("chat-A", "wizard", "hi", db_path=db)
    # Nothing inserted.
    assert _db.get_conversation_messages("chat-A", db_path=db) == []


def test_append_rejects_empty_chat_id(db: Path) -> None:
    with pytest.raises(ValueError, match="chat_id"):
        conversations.append_message("", "user", "hi", db_path=db)


def test_append_rejects_empty_content(db: Path) -> None:
    with pytest.raises(ValueError, match="content"):
        conversations.append_message("chat-A", "user", "", db_path=db)


# ── get_history: empty + round-trip ──────────────────────────────────────────


def test_get_history_empty_for_unknown_chat(db: Path) -> None:
    h = conversations.get_history("never-seen", db_path=db)
    assert h == {
        "messages": [],
        "total_tokens": 0,
        "truncated": False,
        "dropped_count": 0,
    }


def test_get_history_round_trip_chronological(db: Path) -> None:
    conversations.append_message("chat-A", "user",      "first",  db_path=db)
    conversations.append_message("chat-A", "assistant", "second", db_path=db)
    conversations.append_message("chat-A", "user",      "third",  db_path=db)

    h = conversations.get_history("chat-A", db_path=db)
    assert [m["content"] for m in h["messages"]] == ["first", "second", "third"]
    assert h["truncated"] is False
    assert h["dropped_count"] == 0


# ── multi-chat isolation ─────────────────────────────────────────────────────


def test_multi_chat_isolation(db: Path) -> None:
    conversations.append_message("chat-A", "user", "alpha", db_path=db)
    conversations.append_message("chat-B", "user", "bravo", db_path=db)
    conversations.append_message("chat-A", "user", "alpha2", db_path=db)

    a = conversations.get_history("chat-A", db_path=db)
    b = conversations.get_history("chat-B", db_path=db)
    assert [m["content"] for m in a["messages"]] == ["alpha", "alpha2"]
    assert [m["content"] for m in b["messages"]] == ["bravo"]


# ── token-budgeted truncation ────────────────────────────────────────────────


def test_truncation_drops_oldest_first(db: Path) -> None:
    # Each message ≈ 5 tokens (20 chars / 4). Five messages → ~25 tokens total.
    for i in range(5):
        conversations.append_message(
            "chat-A", "user", f"msg{i}{'x' * 18}", db_path=db
        )
    # Budget for ~3 messages.
    h = conversations.get_history("chat-A", max_tokens=15, db_path=db)
    assert h["truncated"] is True
    assert h["dropped_count"] >= 1
    # Whatever survived must be a contiguous newest-first slice — i.e. the
    # last message ('msg4...') is always present.
    contents = [m["content"] for m in h["messages"]]
    assert contents[-1].startswith("msg4")
    # Returned in chronological order.
    indices = [int(c[3]) for c in contents]
    assert indices == sorted(indices)


def test_truncation_preserves_chronological_order(db: Path) -> None:
    for i in range(4):
        conversations.append_message(
            "chat-A", "user", f"m{i}" + "y" * 20, db_path=db
        )
    h = conversations.get_history("chat-A", max_tokens=10, db_path=db)
    # Whatever survives must be chronologically increasing by id.
    ids = [m["id"] for m in h["messages"]]
    assert ids == sorted(ids)


def test_single_oversized_message_returned_anyway(db: Path) -> None:
    conversations.append_message(
        "chat-A", "user", "x" * 1000, db_path=db
    )  # ~250 tokens
    h = conversations.get_history("chat-A", max_tokens=5, db_path=db)
    assert len(h["messages"]) == 1
    assert h["truncated"] is True


# ── role_filter ──────────────────────────────────────────────────────────────


def test_role_filter_excludes_filtered_roles(db: Path) -> None:
    conversations.append_message("chat-A", "user",      "u1", db_path=db)
    conversations.append_message("chat-A", "tool",      "t1", db_path=db)
    conversations.append_message("chat-A", "assistant", "a1", db_path=db)

    h = conversations.get_history(
        "chat-A", role_filter=["user", "assistant"], db_path=db
    )
    roles = [m["role"] for m in h["messages"]]
    assert "tool" not in roles
    assert roles == ["user", "assistant"]


def test_role_filter_unknown_role_raises(db: Path) -> None:
    with pytest.raises(ValueError, match="unknown roles"):
        conversations.get_history(
            "chat-A", role_filter=["user", "wizard"], db_path=db
        )


# ── ordering / determinism ───────────────────────────────────────────────────


def test_same_ts_orders_by_id(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When multiple appends share a timestamp (fast appends), id ASC keeps
    insertion order deterministic."""
    fixed_ts = "2026-05-05T08:00:00+00:00"
    monkeypatch.setattr(conversations, "_now_iso", lambda: fixed_ts)
    for i in range(3):
        conversations.append_message("chat-A", "user", f"m{i}", db_path=db)

    h = conversations.get_history("chat-A", db_path=db)
    contents = [m["content"] for m in h["messages"]]
    assert contents == ["m0", "m1", "m2"]
    assert all(m["ts"] == fixed_ts for m in h["messages"])


# ── unicode / UTF-8 invariant (CLAUDE.md hard rule) ──────────────────────────


def test_unicode_content_round_trip(db: Path) -> None:
    payload = "em—dash, → arrow, 日本語, code: `ls -la` 🎉"
    conversations.append_message("chat-A", "user", payload, db_path=db)
    h = conversations.get_history("chat-A", db_path=db)
    assert h["messages"][0]["content"] == payload


# ── schema migration ─────────────────────────────────────────────────────────


def test_init_db_creates_table_and_index(tmp_path: Path) -> None:
    p = tmp_path / "fresh.db"
    _db.init_db(p)
    with sqlite3.connect(p) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table', 'index')"
            )
        }
    assert "conversation_messages" in names
    assert "idx_conv_chat_ts" in names


# ── MCP-tool integration ─────────────────────────────────────────────────────


def test_mcp_append_then_get_round_trip(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the server module to use the test DB.
    monkeypatch.setattr(_db, "DEFAULT_DB_PATH", db)
    import server

    appended = json.loads(
        server.harness_append_message("chat-X", "user", "ping")
    )
    assert appended["status"] == "ok"
    assert appended["tokens_estimate"] >= 1

    fetched = json.loads(server.harness_get_conversation("chat-X"))
    assert fetched["status"] == "ok"
    assert len(fetched["messages"]) == 1
    assert fetched["messages"][0]["content"] == "ping"


def test_mcp_append_rejects_bad_role(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_db, "DEFAULT_DB_PATH", db)
    import server

    out = json.loads(server.harness_append_message("chat-X", "wizard", "hi"))
    assert out["status"] == "error"
    assert "role" in out["error"]


def test_mcp_append_coerces_dict_content_to_json_string(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When FastMCP/Pydantic delivers content as a dict (the orchestrator
    tool-result path used to hit this), the entrypoint json.dumps it
    rather than rejecting. The stored row reads back as the JSON string."""
    monkeypatch.setattr(_db, "DEFAULT_DB_PATH", db)
    import server

    appended = json.loads(server.harness_append_message(
        "chat-X", "tool",
        {"tool": "harness_spawn_subagent", "result": "{\"ok\": true}"},
    ))
    assert appended["status"] == "ok"

    fetched = json.loads(server.harness_get_conversation("chat-X"))
    assert fetched["status"] == "ok"
    stored = fetched["messages"][0]["content"]
    # Round-trip — the dict became a JSON string, parseable back to the original.
    assert json.loads(stored) == {
        "tool": "harness_spawn_subagent",
        "result": "{\"ok\": true}",
    }


def test_mcp_append_rejects_unserializable_content(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_db, "DEFAULT_DB_PATH", db)
    import server

    # A set isn't JSON-serializable. Should fail closed with a clear error.
    out = json.loads(server.harness_append_message("chat-X", "user", {1, 2, 3}))
    assert out["status"] == "error"
    assert "not serializable" in out["error"]


# ── token estimator parity with verifier ─────────────────────────────────────


def test_estimate_tokens_matches_verifier_heuristic() -> None:
    from validation.verifier import _CHARS_PER_TOKEN
    text = "x" * 80
    assert conversations.estimate_tokens(text) == 80 // _CHARS_PER_TOKEN
    assert conversations.estimate_tokens("") == 0
    # Single-character message still costs 1 token (max(1, len // 4)).
    assert conversations.estimate_tokens("x") == 1
