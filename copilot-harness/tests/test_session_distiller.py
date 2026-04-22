"""Tests for session_distiller.py — Tier 2 memory population from sessions."""

import json
import pytest
from pathlib import Path

import state
import session_distiller
from storage import db


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_session_with_review(
    db_path: Path,
    agents_dir: Path,
    review_output: dict,
) -> str:
    session_id = state.create_session("test request", db_path=db_path)
    state.lock_agent_versions(session_id, agents_dir=agents_dir, db_path=db_path)
    # Write plan, design, code stubs so review can be written
    for stage in ["plan", "design", "code"]:
        state.write_stage(session_id, stage, {"stub": True}, db_path=db_path)
    state.write_stage(session_id, "review", review_output, db_path=db_path)
    return session_id


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


@pytest.fixture()
def agents_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agents"
    d.mkdir()
    return d


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    mem_dir = tmp_path / ".github" / "memory"
    mem_dir.mkdir(parents=True)
    fp = mem_dir / "failure-patterns.md"
    fp.write_text(
        "# Failure Patterns — Distilled from Sessions\n\n---\n\n## Known Patterns\n\n"
    )
    return tmp_path


# ── distill_session — pass review (no issues appended) ────────────────────────

def test_distill_pass_review_appends_nothing(
    db_path: Path, agents_dir: Path, repo_root: Path
) -> None:
    review = {"status": "pass", "attempt": 1, "issues": [], "checklist_results": []}
    sid = _make_session_with_review(db_path, agents_dir, review)
    appended = session_distiller.distill_session(sid, db_path, repo_root)
    assert appended == []


def test_distill_no_review_written_appends_nothing(
    db_path: Path, agents_dir: Path, repo_root: Path
) -> None:
    sid = state.create_session("no review", db_path=db_path)
    appended = session_distiller.distill_session(sid, db_path, repo_root)
    assert appended == []


# ── distill_session — fail review with issues ─────────────────────────────────

def test_distill_fail_review_critical_issue_appended(
    db_path: Path, agents_dir: Path, repo_root: Path
) -> None:
    review = {
        "status": "fail",
        "attempt": 1,
        "issues": [
            {
                "severity": "critical",
                "description": "Missing error handling on DB calls",
                "fix_instruction": "Wrap all DB calls in try/except",
            }
        ],
        "checklist_results": [],
    }
    sid = _make_session_with_review(db_path, agents_dir, review)
    appended = session_distiller.distill_session(sid, db_path, repo_root)
    assert len(appended) == 1
    assert "Missing error handling on DB calls" in appended[0]


def test_distill_fail_review_high_issue_appended(
    db_path: Path, agents_dir: Path, repo_root: Path
) -> None:
    review = {
        "status": "fail",
        "attempt": 1,
        "issues": [
            {
                "severity": "high",
                "description": "No input validation on request body",
                "fix_instruction": "Add validation",
            }
        ],
        "checklist_results": [],
    }
    sid = _make_session_with_review(db_path, agents_dir, review)
    appended = session_distiller.distill_session(sid, db_path, repo_root)
    assert len(appended) == 1


def test_distill_low_severity_not_appended(
    db_path: Path, agents_dir: Path, repo_root: Path
) -> None:
    review = {
        "status": "fail",
        "attempt": 1,
        "issues": [
            {
                "severity": "low",
                "description": "Missing docstring",
                "fix_instruction": "Add docstring",
            }
        ],
        "checklist_results": [],
    }
    sid = _make_session_with_review(db_path, agents_dir, review)
    appended = session_distiller.distill_session(sid, db_path, repo_root)
    assert appended == []


def test_distill_medium_severity_not_appended(
    db_path: Path, agents_dir: Path, repo_root: Path
) -> None:
    review = {
        "status": "fail",
        "attempt": 1,
        "issues": [{"severity": "medium", "description": "Minor naming issue", "fix_instruction": ""}],
        "checklist_results": [],
    }
    sid = _make_session_with_review(db_path, agents_dir, review)
    appended = session_distiller.distill_session(sid, db_path, repo_root)
    assert appended == []


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_distill_deduplicates_same_issue_across_sessions(
    db_path: Path, agents_dir: Path, repo_root: Path
) -> None:
    review = {
        "status": "fail",
        "attempt": 1,
        "issues": [
            {"severity": "critical", "description": "SQL injection risk", "fix_instruction": "Use params"}
        ],
        "checklist_results": [],
    }
    sid1 = _make_session_with_review(db_path, agents_dir, review)
    sid2 = _make_session_with_review(db_path, agents_dir, review)

    appended1 = session_distiller.distill_session(sid1, db_path, repo_root)
    appended2 = session_distiller.distill_session(sid2, db_path, repo_root)

    assert len(appended1) == 1
    assert len(appended2) == 0  # already in file


# ── File content verification ─────────────────────────────────────────────────

def test_distill_writes_entry_to_failure_patterns_file(
    db_path: Path, agents_dir: Path, repo_root: Path
) -> None:
    review = {
        "status": "fail",
        "attempt": 1,
        "issues": [
            {"severity": "critical", "description": "Unhandled exception in login", "fix_instruction": "Add try/except"}
        ],
        "checklist_results": [],
    }
    sid = _make_session_with_review(db_path, agents_dir, review)
    session_distiller.distill_session(sid, db_path, repo_root)

    content = (repo_root / ".github" / "memory" / "failure-patterns.md").read_text()
    assert "coder" in content
    assert "Unhandled exception in login" in content


def test_distill_truncates_long_descriptions(
    db_path: Path, agents_dir: Path, repo_root: Path
) -> None:
    long_desc = "A" * 500
    review = {
        "status": "fail",
        "attempt": 1,
        "issues": [{"severity": "critical", "description": long_desc, "fix_instruction": ""}],
        "checklist_results": [],
    }
    sid = _make_session_with_review(db_path, agents_dir, review)
    appended = session_distiller.distill_session(sid, db_path, repo_root)
    assert len(appended) == 1
    # Entry in file should be truncated
    content = (repo_root / ".github" / "memory" / "failure-patterns.md").read_text()
    assert "A" * 301 not in content  # truncated at 300


# ── Creates memory dir if missing ─────────────────────────────────────────────

def test_distill_creates_memory_dir_if_missing(
    db_path: Path, agents_dir: Path, tmp_path: Path
) -> None:
    """No .github/memory/ directory → distiller creates it."""
    repo_root = tmp_path / "newrepo"
    repo_root.mkdir()
    review = {
        "status": "fail",
        "attempt": 1,
        "issues": [{"severity": "critical", "description": "Missing auth check", "fix_instruction": ""}],
        "checklist_results": [],
    }
    sid = _make_session_with_review(db_path, agents_dir, review)
    appended = session_distiller.distill_session(sid, db_path, repo_root)
    assert len(appended) == 1
    assert (repo_root / ".github" / "memory" / "failure-patterns.md").exists()


# ── distill_all_completed ─────────────────────────────────────────────────────

def test_distill_all_completed_processes_multiple_sessions(
    db_path: Path, agents_dir: Path, repo_root: Path
) -> None:
    r1 = {
        "status": "fail", "attempt": 1,
        "issues": [{"severity": "critical", "description": "Issue alpha", "fix_instruction": ""}],
        "checklist_results": [],
    }
    r2 = {
        "status": "fail", "attempt": 1,
        "issues": [{"severity": "high", "description": "Issue beta", "fix_instruction": ""}],
        "checklist_results": [],
    }
    sid1 = _make_session_with_review(db_path, agents_dir, r1)
    sid2 = _make_session_with_review(db_path, agents_dir, r2)

    results = session_distiller.distill_all_completed(db_path, repo_root)
    assert sid1 in results
    assert sid2 in results
    assert "Issue alpha" in results[sid1][0]
    assert "Issue beta" in results[sid2][0]


def test_distill_all_skips_pass_sessions(
    db_path: Path, agents_dir: Path, repo_root: Path
) -> None:
    review = {"status": "pass", "attempt": 1, "issues": [], "checklist_results": []}
    sid = _make_session_with_review(db_path, agents_dir, review)
    results = session_distiller.distill_all_completed(db_path, repo_root)
    assert sid not in results
