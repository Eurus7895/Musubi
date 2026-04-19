"""Tests for state.py — all use a temp SQLite DB to stay isolated."""

import pytest
from pathlib import Path

import state


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    from storage import db as _db
    _db.init_db(p)
    return p


@pytest.fixture()
def session_id(db: Path) -> str:
    return state.create_session("build a login endpoint", db_path=db)


# ── create_session ────────────────────────────────────────────────────────────

def test_create_session_returns_id(db: Path) -> None:
    sid = state.create_session("test request", db_path=db)
    assert sid and isinstance(sid, str)


def test_create_session_seeds_all_stages(db: Path) -> None:
    sid = state.create_session("test", db_path=db)
    for stage in state.STAGES:
        row = state.read_stage(sid, stage, db_path=db)
        assert row is None  # pending, no output yet


def test_create_session_unique_ids(db: Path) -> None:
    ids = {state.create_session("req", db_path=db) for _ in range(5)}
    assert len(ids) == 5


# ── lock_agent_versions ───────────────────────────────────────────────────────

def test_lock_agent_versions(tmp_path: Path, db: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "planner.agent.md").write_text("---\nname: Planner\nversion: 1.2.3\n---\n")
    (agents_dir / "coder.agent.md").write_text("---\nname: Coder\nversion: 2.0.0\n---\n")

    sid = state.create_session("req", db_path=db)
    versions = state.lock_agent_versions(sid, agents_dir=agents_dir, db_path=db)

    assert versions["planner"] == "1.2.3"
    assert versions["coder"] == "2.0.0"


def test_lock_agent_versions_persisted(tmp_path: Path, db: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "reviewer.agent.md").write_text("---\nversion: 0.9.1\n---\n")

    sid = state.create_session("req", db_path=db)
    state.lock_agent_versions(sid, agents_dir=agents_dir, db_path=db)

    persisted = state.get_agent_versions(sid, db_path=db)
    assert persisted["reviewer"] == "0.9.1"


def test_lock_agent_versions_missing_version(tmp_path: Path, db: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "designer.agent.md").write_text("---\nname: Designer\n---\n")

    sid = state.create_session("req", db_path=db)
    versions = state.lock_agent_versions(sid, agents_dir=agents_dir, db_path=db)
    assert versions["designer"] == "0.0.0"


# ── write_stage / read_stage ──────────────────────────────────────────────────

def test_write_and_read_stage(session_id: str, db: Path) -> None:
    payload = {"summary": "done", "tasks": []}
    state.write_stage(session_id, "plan", payload, db_path=db)
    assert state.read_stage(session_id, "plan", db_path=db) == payload


def test_write_stage_write_once(session_id: str, db: Path) -> None:
    state.write_stage(session_id, "plan", {"v": 1}, db_path=db)
    with pytest.raises(ValueError, match="write-once"):
        state.write_stage(session_id, "plan", {"v": 2}, db_path=db)


def test_read_stage_returns_none_before_write(session_id: str, db: Path) -> None:
    assert state.read_stage(session_id, "plan", db_path=db) is None


def test_write_stage_unknown_stage(session_id: str, db: Path) -> None:
    with pytest.raises(ValueError, match="Unknown stage"):
        state.write_stage(session_id, "nonexistent", {}, db_path=db)


def test_completed_stage_cannot_be_overwritten(session_id: str, db: Path) -> None:
    state.write_stage(session_id, "design", {"arch": "x"}, db_path=db)
    with pytest.raises(ValueError):
        state.write_stage(session_id, "design", {"arch": "y"}, db_path=db)


# ── increment_attempt ─────────────────────────────────────────────────────────

def test_increment_attempt(session_id: str, db: Path) -> None:
    assert state.get_attempt(session_id, "code", db_path=db) == 1
    new = state.increment_attempt(session_id, "code", db_path=db)
    assert new == 2
    assert state.get_attempt(session_id, "code", db_path=db) == 2


def test_increment_attempt_preserves_previous_output(session_id: str, db: Path) -> None:
    state.write_stage(session_id, "code", {"summary": "attempt1"}, db_path=db)
    state.increment_attempt(session_id, "code", db_path=db)
    # Previous attempt is still readable via direct DB query
    from storage import db as _db
    row = _db.get_stage_row(session_id, "code", attempt=1, db_path=db)
    assert row is not None
    assert row["output"] is not None


def test_attempt_counter_increments_multiple_times(session_id: str, db: Path) -> None:
    state.increment_attempt(session_id, "code", db_path=db)
    state.increment_attempt(session_id, "code", db_path=db)
    assert state.get_attempt(session_id, "code", db_path=db) == 3


# ── resume ────────────────────────────────────────────────────────────────────

def test_resume_returns_first_pending_stage(session_id: str, db: Path) -> None:
    assert state.resume(session_id, db_path=db) == "plan"


def test_resume_advances_after_write(session_id: str, db: Path) -> None:
    state.write_stage(session_id, "plan", {"ok": True}, db_path=db)
    assert state.resume(session_id, db_path=db) == "design"


def test_resume_returns_none_when_all_complete(session_id: str, db: Path) -> None:
    for stage in state.STAGES:
        state.write_stage(session_id, stage, {"stage": stage}, db_path=db)
    assert state.resume(session_id, db_path=db) is None


def test_resume_after_retry_points_to_retried_stage(session_id: str, db: Path) -> None:
    state.write_stage(session_id, "plan", {"ok": True}, db_path=db)
    state.write_stage(session_id, "design", {"ok": True}, db_path=db)
    state.write_stage(session_id, "code", {"ok": True}, db_path=db)
    state.increment_attempt(session_id, "code", db_path=db)
    # code has a new attempt with no output — should resume there
    assert state.resume(session_id, db_path=db) == "code"


# ── crash recovery (active session) ──────────────────────────────────────────

def test_create_session_sets_active_session(db: Path) -> None:
    sid = state.create_session("build something", db_path=db)
    active = state.get_active_session(db_path=db)
    assert active is not None
    assert active["session_id"] == sid


def test_get_active_session_returns_request(db: Path) -> None:
    state.create_session("my request", db_path=db)
    active = state.get_active_session(db_path=db)
    assert active is not None
    assert active["request"] == "my request"


def test_get_active_session_returns_resume_stage(session_id: str, db: Path) -> None:
    state.write_stage(session_id, "plan", {"ok": True}, db_path=db)
    active = state.get_active_session(db_path=db)
    assert active is not None
    assert active["resume_stage"] == "design"


def test_get_active_session_returns_none_when_no_session(db: Path) -> None:
    assert state.get_active_session(db_path=db) is None


def test_get_active_session_latest_session_wins(db: Path) -> None:
    state.create_session("first", db_path=db)
    sid2 = state.create_session("second", db_path=db)
    active = state.get_active_session(db_path=db)
    assert active is not None
    assert active["session_id"] == sid2


def test_get_active_session_returns_attempt(session_id: str, db: Path) -> None:
    state.write_stage(session_id, "plan", {"ok": True}, db_path=db)
    state.write_stage(session_id, "design", {"ok": True}, db_path=db)
    state.write_stage(session_id, "code", {"ok": True}, db_path=db)
    state.increment_attempt(session_id, "code", db_path=db)
    active = state.get_active_session(db_path=db)
    assert active is not None
    assert active["resume_stage"] == "code"
    assert active["attempt"] == 2


def test_get_active_session_none_when_all_complete(session_id: str, db: Path) -> None:
    for stage in state.STAGES:
        state.write_stage(session_id, stage, {"stage": stage}, db_path=db)
    # All stages complete → resume returns None → get_active_session still returns
    # the session (status is still 'active'), but resume_stage is None
    active = state.get_active_session(db_path=db)
    assert active is not None
    assert active["resume_stage"] is None
    assert active["attempt"] is None


def test_mark_in_progress_transitions_status(session_id: str, db: Path) -> None:
    from storage import db as _db
    row_before = _db.get_stage_row(session_id, "plan", db_path=db)
    assert row_before is not None
    assert row_before["status"] == "pending"
    state.mark_in_progress(session_id, "plan", db_path=db)
    row_after = _db.get_stage_row(session_id, "plan", db_path=db)
    assert row_after is not None
    assert row_after["status"] == "in_progress"


def test_mark_in_progress_idempotent(session_id: str, db: Path) -> None:
    state.mark_in_progress(session_id, "plan", db_path=db)
    state.mark_in_progress(session_id, "plan", db_path=db)
    from storage import db as _db
    row = _db.get_stage_row(session_id, "plan", db_path=db)
    assert row is not None
    assert row["status"] == "in_progress"
