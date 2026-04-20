"""Tests for memory/pattern_detector.py — failure pattern detection."""

from pathlib import Path

import pytest

import state
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


# ── trigger_skill_builder ─────────────────────────────────────────────────────

def test_trigger_creates_patch_in_proposed(db: Path, tmp_path: Path) -> None:
    for i in range(pd.PATTERN_THRESHOLD):
        sid = _make_session(db, f"req {i}")
        pd.record_failure(sid, "coder", "always forgets error handling", db_path=db)
    patterns = pd.detect_patterns("coder", db)
    patch_path = pd.trigger_skill_builder(patterns[0], repo_root=tmp_path)
    assert patch_path.exists()
    assert patch_path.name == "coder.patch.md"
    proposed = tmp_path / ".github" / "agents" / "proposed"
    assert patch_path.parent == proposed


def test_trigger_patch_contains_required_sections(db: Path, tmp_path: Path) -> None:
    for i in range(pd.PATTERN_THRESHOLD):
        sid = _make_session(db, f"req {i}")
        pd.record_failure(sid, "coder", "missing error handling", db_path=db)
    patterns = pd.detect_patterns("coder", db)
    patch_path = pd.trigger_skill_builder(patterns[0], repo_root=tmp_path)
    content = patch_path.read_text()
    assert "# Proposed Patch: coder" in content
    assert "## Proposed Behavior-Rules Addition" in content
    assert "missing error handling" in content
    assert "## Review Instructions" in content


def test_trigger_overwrites_existing_patch(db: Path, tmp_path: Path) -> None:
    proposed = tmp_path / ".github" / "agents" / "proposed"
    proposed.mkdir(parents=True)
    (proposed / "coder.patch.md").write_text("old content")

    for i in range(pd.PATTERN_THRESHOLD):
        sid = _make_session(db, f"req {i}")
        pd.record_failure(sid, "coder", "new issue", db_path=db)
    patterns = pd.detect_patterns("coder", db)
    pd.trigger_skill_builder(patterns[0], repo_root=tmp_path)
    assert "old content" not in (proposed / "coder.patch.md").read_text()


# ── integration: 3 sessions → patch created ──────────────────────────────────

def test_three_sessions_same_failure_creates_patch(db: Path, tmp_path: Path) -> None:
    """Core Day 5 requirement: 3 sessions same coder failure → patch created."""
    issue = "always missing error handling on DB calls"
    for i in range(3):
        sid = _make_session(db, f"session {i}")
        pd.record_failure(sid, "coder", issue, db_path=db)

    patterns = pd.detect_patterns("coder", db)
    assert len(patterns) == 1
    assert patterns[0].count == 3

    patch_path = pd.trigger_skill_builder(patterns[0], repo_root=tmp_path)
    assert patch_path.exists()
    content = patch_path.read_text()
    assert issue in content
    assert "Behavior-Rules Addition" in content


def test_two_sessions_below_threshold_no_patch(db: Path, tmp_path: Path) -> None:
    issue = "missing error handling"
    for i in range(2):
        sid = _make_session(db, f"session {i}")
        pd.record_failure(sid, "coder", issue, db_path=db)

    patterns = pd.detect_patterns("coder", db)
    assert patterns == []


# ── integration: correction_loop records failures ─────────────────────────────

def test_correction_loop_records_failures_to_pattern_detector(db: Path) -> None:
    """correction_loop.run() records issue descriptions for pattern detection."""
    import correction_loop

    issue_desc = "missing authentication check"
    for i in range(pd.PATTERN_THRESHOLD):
        sid = _make_session(db, f"req {i}")
        state.write_stage(sid, "plan", {"summary": "x", "tasks": []}, db_path=db)
        state.write_stage(sid, "design", {"architecture": "x", "files": []}, db_path=db)
        state.write_stage(sid, "code", {"summary": "v1", "files_modified": []}, db_path=db)
        review = {
            "status": "fail",
            "attempt": 1,
            "issues": [{"severity": "high", "description": issue_desc,
                        "fix_instruction": "add auth middleware"}],
        }
        state.write_stage(sid, "review", review, db_path=db)
        correction_loop.run(sid, review, db)

    patterns = pd.detect_patterns("coder", db)
    assert len(patterns) == 1
    assert patterns[0].issue == issue_desc
    assert patterns[0].count == pd.PATTERN_THRESHOLD


def test_correction_loop_triggers_patch_when_repo_root_given(
    db: Path, tmp_path: Path
) -> None:
    """correction_loop.run() writes patch file when repo_root provided and threshold met."""
    import correction_loop

    for i in range(pd.PATTERN_THRESHOLD):
        sid = _make_session(db, f"req {i}")
        state.write_stage(sid, "plan", {"summary": "x", "tasks": []}, db_path=db)
        state.write_stage(sid, "design", {"architecture": "x", "files": []}, db_path=db)
        state.write_stage(sid, "code", {"summary": "v1", "files_modified": []}, db_path=db)
        review = {
            "status": "fail",
            "attempt": 1,
            "issues": [{"severity": "high", "description": "no input validation",
                        "fix_instruction": "validate all inputs"}],
        }
        state.write_stage(sid, "review", review, db_path=db)
        result = correction_loop.run(sid, review, db, repo_root=tmp_path)

    assert result.triggered_patches
    patch_path = Path(result.triggered_patches[0])
    assert patch_path.exists()
    assert "coder" in patch_path.name
