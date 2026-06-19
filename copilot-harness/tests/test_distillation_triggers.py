"""Tests for the Phase C.2 distillation triggers + frustration regex bank
+ housekeeping pruner.

Covered:
  - memory/pattern_detector.detect_frustration: positive matches across
    each shipped pattern, negative on neutral text, hot-reload via mtime.
  - memory/session_distiller.append_pattern: dedup, formatting, source label.
  - server.harness_append_failure_pattern: MCP envelope, dedup signal,
    bad-input rejection.
  - server.harness_delete_subsessions_for_parent: prunes terminal rows,
    spares running rows, age gating.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memory import pattern_detector, session_distiller
from session import state, sub_sessions
from storage import db as _db


# ── detect_frustration ──────────────────────────────────────────────────────


@pytest.fixture()
def patterns_path() -> Path:
    return (
        Path(__file__).parent.parent.parent
        / ".github" / "memory" / "sentiment-patterns.json"
    )


@pytest.mark.parametrize("text,label", [
    ("That's wrong",                 "wrong/broken assertion"),
    ("This isn't working again",     "still not working"),
    ("Stop doing that",              "stop doing X"),
    ("No, I told you twice",         "repeated correction"),
    ("I give up",                    "give up"),
    ("Never mind",                   "never mind"),
    ("Forget it",                    "forget it"),
    ("ugh",                          "ugh"),
])
def test_detect_frustration_matches_each_pattern(
    text: str, label: str, patterns_path: Path,
) -> None:
    assert pattern_detector.detect_frustration(text, patterns_path) == label


@pytest.mark.parametrize("text", [
    "Please add a unit test for parseCommand.",
    "Could you explain what /clear does?",
    "Run the tests and let me know.",
    "",
    "   ",
])
def test_detect_frustration_no_match_on_neutral_text(
    text: str, patterns_path: Path,
) -> None:
    assert pattern_detector.detect_frustration(text, patterns_path) is None


def test_detect_frustration_returns_none_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    assert pattern_detector.detect_frustration("that's wrong", missing) is None


def test_detect_frustration_hot_reload_picks_up_edits(tmp_path: Path) -> None:
    p = tmp_path / "patterns.json"
    p.write_text(json.dumps({
        "patterns": [{"label": "v1", "regex": r"foo"}],
    }), encoding="utf-8")
    assert pattern_detector.detect_frustration("foo", p) == "v1"

    # Bump mtime + rewrite — the lru_cache key includes mtime so the next
    # call must recompile.
    import os
    later = p.stat().st_mtime + 5
    p.write_text(json.dumps({
        "patterns": [{"label": "v2", "regex": r"bar"}],
    }), encoding="utf-8")
    os.utime(p, (later, later))
    assert pattern_detector.detect_frustration("bar", p) == "v2"
    assert pattern_detector.detect_frustration("foo", p) is None


# ── session_distiller.append_pattern ────────────────────────────────────────


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    """Pretend tmp_path is the repo root so failure-patterns.md is isolated."""
    (tmp_path / ".github" / "memory").mkdir(parents=True)
    return tmp_path


def test_append_pattern_writes_new_row(repo_root: Path) -> None:
    out = session_distiller.append_pattern(
        "coder", "missing tests for parseCommand",
        source="reviewer-fail", repo_root=repo_root,
    )
    assert out == "missing tests for parseCommand"
    body = (repo_root / ".github" / "memory" / "failure-patterns.md").read_text(
        encoding="utf-8"
    )
    assert "coder — missing tests for parseCommand" in body
    assert "Sessions: reviewer-fail" in body


def test_append_pattern_dedups_repeats(repo_root: Path) -> None:
    first = session_distiller.append_pattern(
        "coder", "missing tests", source="A", repo_root=repo_root,
    )
    second = session_distiller.append_pattern(
        "coder", "missing tests", source="B", repo_root=repo_root,
    )
    assert first == "missing tests"
    assert second is None  # deduped


def test_append_pattern_rejects_empty_input(repo_root: Path) -> None:
    assert session_distiller.append_pattern("", "issue", repo_root=repo_root) is None
    assert session_distiller.append_pattern("agent", "", repo_root=repo_root) is None
    assert session_distiller.append_pattern("agent", "   ", repo_root=repo_root) is None


# ── server.harness_append_failure_pattern ────────────────────────────────────


def test_mcp_append_failure_pattern_round_trip(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_distiller, "_DEFAULT_REPO_ROOT", repo_root)
    import server
    raw = server.harness_append_failure_pattern(
        "coder", "schema drift between planner and coder", "reviewer-fail",
    )
    payload = json.loads(raw)
    assert payload == {
        "status": "ok",
        "appended": True,
        "issue": "schema drift between planner and coder",
    }
    # Second call deduplicates.
    raw2 = server.harness_append_failure_pattern(
        "coder", "schema drift between planner and coder", "reviewer-fail",
    )
    assert json.loads(raw2) == {"status": "ok", "appended": False}


def test_mcp_append_failure_pattern_rejects_empty(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_distiller, "_DEFAULT_REPO_ROOT", repo_root)
    import server
    raw = server.harness_append_failure_pattern("", "issue")
    payload = json.loads(raw)
    assert payload["status"] == "error"
    raw = server.harness_append_failure_pattern("agent", "")
    payload = json.loads(raw)
    assert payload["status"] == "error"


# ── server.harness_delete_subsessions_for_parent ────────────────────────────


@pytest.fixture()
def state_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    p = tmp_path / "state.db"
    _db.init_db(p)
    monkeypatch.setattr(_db, "DEFAULT_DB_PATH", p)
    return p


def test_delete_subsessions_prunes_terminal_only(state_db: Path) -> None:
    parent = state.create_session("parent")
    # One running, one done, one abandoned.
    h_running = sub_sessions.spawn(
        parent_session_id=parent, parent_agent_name="agent",
        role="explorer", brief="b1", allowed_tools=["Read"],
        max_turns=4, per_turn_timeout_s=10, wall_clock_timeout_s=60,
    )
    h_done = sub_sessions.spawn(
        parent_session_id=parent, parent_agent_name="agent",
        role="explorer", brief="b2", allowed_tools=["Read"],
        max_turns=4, per_turn_timeout_s=10, wall_clock_timeout_s=60,
    )
    sub_sessions.complete(h_done, summary="done summary", status="done", turns=1)
    h_abandoned = sub_sessions.spawn(
        parent_session_id=parent, parent_agent_name="agent",
        role="explorer", brief="b3", allowed_tools=["Read"],
        max_turns=4, per_turn_timeout_s=10, wall_clock_timeout_s=60,
    )
    sub_sessions.abandon(h_abandoned, reason="test")

    import server
    raw = server.harness_delete_subsessions_for_parent(parent)
    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert payload["deleted"] == 2
    # Running row survives.
    surviving = sub_sessions.list_for_parent(parent)
    assert {r["handle_id"] for r in surviving} == {h_running}


def test_delete_subsessions_age_gates(state_db: Path) -> None:
    parent = state.create_session("parent")
    h = sub_sessions.spawn(
        parent_session_id=parent, parent_agent_name="agent",
        role="explorer", brief="b", allowed_tools=["Read"],
        max_turns=4, per_turn_timeout_s=10, wall_clock_timeout_s=60,
    )
    sub_sessions.complete(h, summary="s", status="done", turns=1)

    # Cutoff in the past — nothing to delete (the row is newer).
    past_cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    import server
    raw = server.harness_delete_subsessions_for_parent(parent, past_cutoff)
    assert json.loads(raw)["deleted"] == 0
    # Cutoff in the future — row is eligible.
    future_cutoff = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    raw = server.harness_delete_subsessions_for_parent(parent, future_cutoff)
    assert json.loads(raw)["deleted"] == 1
