"""Tests for memory/pattern_detector.py — failure pattern detection."""

from pathlib import Path

import pytest

from session import state
from memory import pattern_detector as pd
from storage import db as _db


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    _db.init_db(p)
    return p


def _make_session(db_path: Path, request: str = "test request") -> str:
    return state.create_session(request, db_path=db_path)


# ── record_failure ────────────────────────────────────────────────────────────

def test_record_failure_stores_to_db(db: Path) -> None:
    sid = _make_session(db)
    pd.record_failure(sid, "coder", "missing error handling", db_path=db)
    rows = _db.get_fail_patterns("coder", db)
    assert len(rows) == 1
    assert rows[0]["issue"] == "missing error handling"
    assert rows[0]["agent_name"] == "coder"
    assert rows[0]["session_id"] == sid


def test_record_failure_multiple_sessions(db: Path) -> None:
    for i in range(3):
        sid = _make_session(db, f"request {i}")
        pd.record_failure(sid, "coder", "same issue", db_path=db)
    rows = _db.get_fail_patterns("coder", db)
    assert len(rows) == 3


def test_record_failure_records_timestamp(db: Path) -> None:
    sid = _make_session(db)
    pd.record_failure(sid, "coder", "issue", db_path=db)
    rows = _db.get_fail_patterns("coder", db)
    assert rows[0]["recorded_at"] is not None


# ── detect_patterns ───────────────────────────────────────────────────────────

def test_detect_patterns_empty_db(db: Path) -> None:
    assert pd.detect_patterns("coder", db) == []


def test_detect_patterns_below_threshold(db: Path) -> None:
    for i in range(pd.PATTERN_THRESHOLD - 1):
        sid = _make_session(db, f"req {i}")
        pd.record_failure(sid, "coder", "missing auth", db_path=db)
    assert pd.detect_patterns("coder", db) == []


def test_detect_patterns_at_threshold(db: Path) -> None:
    for i in range(pd.PATTERN_THRESHOLD):
        sid = _make_session(db, f"req {i}")
        pd.record_failure(sid, "coder", "missing auth", db_path=db)
    patterns = pd.detect_patterns("coder", db)
    assert len(patterns) == 1
    assert patterns[0].agent_name == "coder"
    assert patterns[0].issue == "missing auth"
    assert patterns[0].count == pd.PATTERN_THRESHOLD


def test_detect_patterns_above_threshold(db: Path) -> None:
    for i in range(5):
        sid = _make_session(db, f"req {i}")
        pd.record_failure(sid, "coder", "repeated bug", db_path=db)
    patterns = pd.detect_patterns("coder", db)
    assert patterns[0].count == 5


def test_detect_patterns_filters_by_agent(db: Path) -> None:
    for i in range(pd.PATTERN_THRESHOLD):
        sid = _make_session(db, f"req {i}")
        pd.record_failure(sid, "reviewer", "inconsistent schema", db_path=db)
    assert pd.detect_patterns("coder", db) == []


def test_detect_patterns_no_filter_returns_all_agents(db: Path) -> None:
    for i in range(pd.PATTERN_THRESHOLD):
        sid = _make_session(db, f"req {i}")
        pd.record_failure(sid, "coder", "missing auth", db_path=db)
        pd.record_failure(sid, "designer", "bad schema", db_path=db)
    patterns = pd.detect_patterns(db_path=db)
    agent_names = {p.agent_name for p in patterns}
    assert "coder" in agent_names
    assert "designer" in agent_names


def test_detect_patterns_groups_distinct_issues(db: Path) -> None:
    for i in range(pd.PATTERN_THRESHOLD):
        sid = _make_session(db, f"req {i}")
        pd.record_failure(sid, "coder", "issue A", db_path=db)
        pd.record_failure(sid, "coder", "issue B", db_path=db)
    patterns = pd.detect_patterns("coder", db)
    issues = {p.issue for p in patterns}
    assert "issue A" in issues
    assert "issue B" in issues


def test_detect_patterns_session_ids_populated(db: Path) -> None:
    sids = []
    for i in range(pd.PATTERN_THRESHOLD):
        sid = _make_session(db, f"req {i}")
        sids.append(sid)
        pd.record_failure(sid, "coder", "same issue", db_path=db)
    patterns = pd.detect_patterns("coder", db)
    assert set(patterns[0].session_ids) == set(sids)
