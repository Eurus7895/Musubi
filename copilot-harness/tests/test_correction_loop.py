"""Tests for correction_loop.py — reviewer→coder retry logic."""

import pytest
from pathlib import Path

import state
import correction_loop
from storage import db as _db


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    _db.init_db(p)
    return p


@pytest.fixture()
def session(db: Path) -> str:
    sid = state.create_session("build a login endpoint", db_path=db)
    state.write_stage(sid, "plan", {"summary": "x", "tasks": []}, db_path=db)
    state.write_stage(sid, "design", {"architecture": "x", "files": []}, db_path=db)
    state.write_stage(sid, "code", {"summary": "impl v1", "files_modified": []}, db_path=db)
    return sid


def _write_review(
    session_id: str,
    db: Path,
    status: str = "fail",
    issues: list | None = None,
) -> dict:
    if issues is None:
        issues = [
            {"severity": "high", "description": "no auth", "fix_instruction": "add JWT"},
            {"severity": "low", "description": "unused var", "fix_instruction": "remove x"},
        ]
    review = {"status": status, "attempt": 1, "issues": issues}
    state.write_stage(session_id, "review", review, db_path=db)
    return review


# ── get_attempt_count ─────────────────────────────────────────────────────────

def test_get_attempt_count_initial(session: str, db: Path) -> None:
    assert correction_loop.get_attempt_count(session, db) == 1


def test_get_attempt_count_after_increment(session: str, db: Path) -> None:
    state.increment_attempt(session, "code", db_path=db)
    assert correction_loop.get_attempt_count(session, db) == 2


# ── build_retry_context ───────────────────────────────────────────────────────

def test_build_retry_context_extracts_fix_instructions(session: str, db: Path) -> None:
    _write_review(session, db)
    fix = correction_loop.build_retry_context(session, db)
    assert "add JWT" in fix
    assert "remove x" in fix


def test_build_retry_context_no_review(session: str, db: Path) -> None:
    fix = correction_loop.build_retry_context(session, db)
    assert fix == []


def test_build_retry_context_issues_without_fix_instruction(session: str, db: Path) -> None:
    review = {"status": "fail", "attempt": 1, "issues": [{"severity": "low", "description": "x"}]}
    state.write_stage(session, "review", review, db_path=db)
    fix = correction_loop.build_retry_context(session, db)
    assert fix == []


# ── escalate ─────────────────────────────────────────────────────────────────

def test_escalate_returns_session_id(session: str, db: Path) -> None:
    _write_review(session, db)
    esc = correction_loop.escalate(session, db)
    assert esc["session_id"] == session


def test_escalate_includes_review_output(session: str, db: Path) -> None:
    _write_review(session, db)
    esc = correction_loop.escalate(session, db)
    assert esc["review_output"] is not None
    assert esc["review_output"]["status"] == "fail"


def test_escalate_includes_fix_instructions(session: str, db: Path) -> None:
    _write_review(session, db)
    esc = correction_loop.escalate(session, db)
    assert "add JWT" in esc["fix_instructions"]


def test_escalate_marked_escalated(session: str, db: Path) -> None:
    _write_review(session, db)
    esc = correction_loop.escalate(session, db)
    assert esc["escalated"] is True


# ── run: pass ─────────────────────────────────────────────────────────────────

def test_run_pass_status(session: str, db: Path) -> None:
    review = {"status": "pass", "attempt": 1, "issues": []}
    result = correction_loop.run(session, review, db)
    assert result.action == "pass"
    assert result.attempt == 1


# ── run: retry ────────────────────────────────────────────────────────────────

def test_run_fail_attempt1_triggers_retry(session: str, db: Path) -> None:
    review = _write_review(session, db)
    result = correction_loop.run(session, review, db)
    assert result.action == "retry"
    assert result.attempt == 2


def test_run_retry_increments_code_attempt(session: str, db: Path) -> None:
    review = _write_review(session, db)
    correction_loop.run(session, review, db)
    assert state.get_attempt(session, "code", db_path=db) == 2


def test_run_retry_returns_fix_instructions(session: str, db: Path) -> None:
    review = _write_review(session, db)
    result = correction_loop.run(session, review, db)
    assert "add JWT" in result.fix_instructions
    assert "remove x" in result.fix_instructions


def test_run_retry_fix_instructions_not_full_review(session: str, db: Path) -> None:
    review = _write_review(session, db)
    result = correction_loop.run(session, review, db)
    assert result.escalation is None
    for item in result.fix_instructions:
        assert isinstance(item, str)


# ── run: max attempts → escalate ─────────────────────────────────────────────

def test_run_attempt3_triggers_escalation(session: str, db: Path) -> None:
    review = _write_review(session, db)
    # Advance code stage to attempt 3.
    state.increment_attempt(session, "code", db_path=db)
    state.increment_attempt(session, "code", db_path=db)
    result = correction_loop.run(session, review, db)
    assert result.action == "escalate"
    assert result.escalation is not None


def test_run_max_attempts_enforced(session: str, db: Path) -> None:
    review = _write_review(session, db)
    # Exhaust retries: attempt 1 → retry, attempt 2 → retry, attempt 3 → escalate.
    r1 = correction_loop.run(session, review, db)
    assert r1.action == "retry"

    # Write new code for attempt 2, then a new review.
    state.write_stage(session, "code", {"summary": "v2", "files_modified": []}, db_path=db)
    state.increment_attempt(session, "review", db_path=db)
    review2 = {"status": "fail", "attempt": 2, "issues": [
        {"severity": "high", "description": "still broken", "fix_instruction": "fix it"},
    ]}
    state.write_stage(session, "review", review2, db_path=db)

    r2 = correction_loop.run(session, review2, db)
    assert r2.action == "retry"

    state.write_stage(session, "code", {"summary": "v3", "files_modified": []}, db_path=db)
    state.increment_attempt(session, "review", db_path=db)
    review3 = {"status": "fail", "attempt": 3, "issues": [
        {"severity": "high", "description": "still broken", "fix_instruction": "fix it"},
    ]}
    state.write_stage(session, "review", review3, db_path=db)

    r3 = correction_loop.run(session, review3, db)
    assert r3.action == "escalate"
    assert r3.escalation["session_id"] == session


def test_run_escalate_status_skips_retry(session: str, db: Path) -> None:
    review = {"status": "escalate", "attempt": 1, "issues": []}
    state.write_stage(session, "review", review, db_path=db)
    result = correction_loop.run(session, review, db)
    assert result.action == "escalate"
    # Code stage attempt should NOT have been incremented.
    assert state.get_attempt(session, "code", db_path=db) == 1


def test_run_wrong_plan_escalates_immediately(session: str, db: Path) -> None:
    review = {"status": "wrong_plan", "attempt": 1, "issues": []}
    state.write_stage(session, "review", review, db_path=db)
    result = correction_loop.run(session, review, db)
    assert result.action == "escalate"


def test_escalation_contains_all_issues(session: str, db: Path) -> None:
    review = _write_review(session, db)
    state.increment_attempt(session, "code", db_path=db)
    state.increment_attempt(session, "code", db_path=db)
    result = correction_loop.run(session, review, db)
    assert result.escalation is not None
    assert result.escalation["reason"] == "Max correction attempts reached"
    assert "add JWT" in result.escalation["fix_instructions"]
