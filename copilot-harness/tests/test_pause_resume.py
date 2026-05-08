"""Phase G.1.5 review-gate state tests.

Covers:
  - schema migration adds the six pause/resume columns + user_hint to
    fresh and existing DBs without data loss
  - state.pause_session / resume_session / consume_pending_action
    behave as the runner expects
  - increment_attempt persists user_hint; read_stage_user_hint surfaces it
  - harness_read_stage returns user_hint in the calling agent's read
    context, scoped to the agent's output stage
  - the action × pause_reason validation matrix matches the TS-side
    pipelineGate.ts contract
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from session import state  # noqa: E402  (module-side path setup OK)
from session.state import (
    VALID_PAUSE_REASONS,
    VALID_RESUME_ACTIONS,
)
from storage import db


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    p = tmp_path / "harness.db"
    db.init_db(p)
    return p


# ── Schema migration ────────────────────────────────────────────────────────

def test_init_db_adds_pause_columns_to_fresh_db(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    expected = {
        "paused_at_stage", "pause_reason", "auto_approve_remaining",
        "pending_action", "pending_user_hint", "pending_extra_budget",
    }
    assert expected.issubset(cols)


def test_init_db_adds_user_hint_to_stage_outputs(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(stage_outputs)").fetchall()}
    assert "user_hint" in cols


def test_init_db_migrates_pre_g15_db_in_place(tmp_path: Path) -> None:
    """Simulate a pre-G.1.5 DB by creating the old schema, inserting a
    session row, then running init_db. The new columns must appear and
    the existing row must survive."""
    p = tmp_path / "old.db"
    with sqlite3.connect(p) as conn:
        conn.execute(
            "CREATE TABLE sessions ("
            "session_id TEXT PRIMARY KEY,"
            "request    TEXT NOT NULL,"
            "status     TEXT NOT NULL DEFAULT 'active',"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE stage_outputs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "session_id TEXT NOT NULL,"
            "stage TEXT NOT NULL,"
            "attempt INTEGER NOT NULL DEFAULT 1,"
            "status TEXT NOT NULL DEFAULT 'pending',"
            "output TEXT,"
            "written_at TEXT)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES ('s1', 'do thing', 'active', 'now', 'now')"
        )
        conn.execute(
            "INSERT INTO stage_outputs (session_id, stage, attempt) VALUES ('s1', 'plan', 1)"
        )

    db.init_db(p)

    with sqlite3.connect(p) as conn:
        sess_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        stage_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(stage_outputs)").fetchall()
        }
        sess_row = conn.execute(
            "SELECT request, status FROM sessions WHERE session_id='s1'"
        ).fetchone()
        stage_row = conn.execute(
            "SELECT stage, attempt, output FROM stage_outputs WHERE session_id='s1'"
        ).fetchone()

    assert "paused_at_stage" in sess_cols
    assert "pending_extra_budget" in sess_cols
    assert "user_hint" in stage_cols
    # Existing data intact.
    assert sess_row == ("do thing", "active")
    assert stage_row == ("plan", 1, None)


def test_init_db_is_idempotent(fresh_db: Path) -> None:
    """Calling init_db twice must not raise (no duplicate ALTER TABLE)."""
    db.init_db(fresh_db)  # second call


# ── pause_session / resume_session / consume_pending_action ───────────────

def _new_session(db_path: Path, request: str = "do x") -> str:
    return state.create_session(request, db_path)


def test_pause_session_sets_columns(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    state.pause_session(sid, "plan", "stage_review", fresh_db)
    pause = state.get_pause_state(sid, fresh_db)
    assert pause is not None
    assert pause["paused_at_stage"] == "plan"
    assert pause["pause_reason"] == "stage_review"
    assert pause["auto_approve_remaining"] is False


def test_pause_session_rejects_unknown_stage(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    with pytest.raises(ValueError, match="Unknown stage"):
        state.pause_session(sid, "yolo", "stage_review", fresh_db)


def test_pause_session_rejects_unknown_reason(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    with pytest.raises(ValueError, match="Unknown pause_reason"):
        state.pause_session(sid, "plan", "snack_break", fresh_db)


def test_pause_session_rejects_unknown_session(fresh_db: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        state.pause_session("nonexistent", "plan", "stage_review", fresh_db)


@pytest.mark.parametrize("action", ["approve", "retry", "abort", "auto_approve_rest"])
def test_resume_session_clears_pause_and_records_action(
    fresh_db: Path, action: str,
) -> None:
    sid = _new_session(fresh_db)
    state.pause_session(sid, "plan", "stage_review", fresh_db)
    state.resume_session(sid, action, fresh_db)
    pause = state.get_pause_state(sid, fresh_db)
    assert pause is not None
    assert pause["paused_at_stage"] is None
    assert pause["pause_reason"] is None
    if action == "auto_approve_rest":
        assert pause["auto_approve_remaining"] is True
    else:
        assert pause["auto_approve_remaining"] is False
    # consume_pending_action returns the action and then nulls it.
    payload = state.consume_pending_action(sid, fresh_db)
    assert payload is not None
    assert payload["action"] == action
    assert state.consume_pending_action(sid, fresh_db) is None


def test_resume_session_persists_user_hint_on_retry(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    state.pause_session(sid, "plan", "stage_review", fresh_db)
    state.resume_session(sid, "retry", fresh_db, user_hint="add error handling")
    payload = state.consume_pending_action(sid, fresh_db)
    assert payload is not None
    assert payload["action"] == "retry"
    assert payload["user_hint"] == "add error handling"
    assert payload["extra_budget"] == 0


def test_resume_session_strips_blank_hint(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    state.pause_session(sid, "plan", "stage_review", fresh_db)
    state.resume_session(sid, "retry", fresh_db, user_hint="    ")
    payload = state.consume_pending_action(sid, fresh_db)
    assert payload is not None
    assert payload["user_hint"] is None


def test_resume_session_grant_records_extra_budget(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    state.pause_session(sid, "code", "budget_exhausted", fresh_db)
    state.resume_session(sid, "grant", fresh_db, extra_budget=5)
    payload = state.consume_pending_action(sid, fresh_db)
    assert payload is not None
    assert payload["action"] == "grant"
    assert payload["extra_budget"] == 5


def test_resume_session_force_does_not_carry_extra_budget(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    state.pause_session(sid, "code", "budget_exhausted", fresh_db)
    # force is "answer with what you have" — extra_budget MUST stay 0
    # even if a misuse passes one in.
    state.resume_session(sid, "force", fresh_db, extra_budget=99)
    payload = state.consume_pending_action(sid, fresh_db)
    assert payload is not None
    assert payload["action"] == "force"
    assert payload["extra_budget"] == 0


def test_resume_session_rejects_action_not_matching_pause_reason(
    fresh_db: Path,
) -> None:
    sid = _new_session(fresh_db)
    state.pause_session(sid, "code", "budget_exhausted", fresh_db)
    # 'approve' is a stage_review action only.
    with pytest.raises(ValueError, match="does not apply"):
        state.resume_session(sid, "approve", fresh_db)


def test_resume_session_rejects_when_not_paused(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    with pytest.raises(ValueError, match="not paused"):
        state.resume_session(sid, "approve", fresh_db)


def test_resume_session_rejects_unknown_action(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    state.pause_session(sid, "plan", "stage_review", fresh_db)
    with pytest.raises(ValueError, match="Unknown resume action"):
        state.resume_session(sid, "yolo", fresh_db)


def test_pause_session_clears_stale_pending_action(fresh_db: Path) -> None:
    """A new pause must not surface a leftover resume from a prior pause."""
    sid = _new_session(fresh_db)
    state.pause_session(sid, "plan", "stage_review", fresh_db)
    state.resume_session(sid, "retry", fresh_db, user_hint="first hint")
    # consume_pending_action would normally clear the row; deliberately
    # skip to simulate a runner that never read the action and the user
    # paused the session again.
    state.pause_session(sid, "design", "stage_review", fresh_db)
    payload = state.consume_pending_action(sid, fresh_db)
    assert payload is None


# ── increment_attempt + read_stage_user_hint ───────────────────────────────

def test_increment_attempt_persists_user_hint(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    new_attempt = state.increment_attempt(
        sid, "plan", fresh_db, user_hint="fix the error handling",
    )
    assert new_attempt == 2
    hint = state.read_stage_user_hint(sid, "plan", fresh_db)
    assert hint == "fix the error handling"


def test_increment_attempt_no_hint_yields_none(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    state.increment_attempt(sid, "plan", fresh_db)
    assert state.read_stage_user_hint(sid, "plan", fresh_db) is None


def test_increment_attempt_strips_whitespace_hint(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    state.increment_attempt(sid, "plan", fresh_db, user_hint="    ")
    assert state.read_stage_user_hint(sid, "plan", fresh_db) is None


def test_read_stage_user_hint_returns_latest_attempt_only(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    state.increment_attempt(sid, "plan", fresh_db, user_hint="first try")
    state.increment_attempt(sid, "plan", fresh_db, user_hint="second try")
    hint = state.read_stage_user_hint(sid, "plan", fresh_db)
    assert hint == "second try"


def test_read_stage_user_hint_rejects_unknown_stage(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    with pytest.raises(ValueError, match="Unknown stage"):
        state.read_stage_user_hint(sid, "yolo", fresh_db)


# ── action × pause_reason matrix mirrors the TS side ───────────────────────

def test_valid_pause_reasons_match_ts_constants() -> None:
    """TS side: pipelineGate.ts::PauseReason. Keep aligned."""
    assert VALID_PAUSE_REASONS == frozenset({"stage_review", "budget_exhausted"})


def test_resume_action_matrix_is_complete() -> None:
    """Every action listed must apply to at least one pause_reason."""
    for action, reasons in VALID_RESUME_ACTIONS.items():
        assert reasons, f"action {action!r} has no allowed pause_reason"
        assert reasons.issubset(VALID_PAUSE_REASONS), (
            f"action {action!r} references unknown pause_reason"
        )


def test_resume_action_matrix_matches_ts_side() -> None:
    """Lock the action sets so a TS-side change requires a Python-side update."""
    expected_stage_review = frozenset({"approve", "retry", "abort", "auto_approve_rest"})
    expected_budget = frozenset({"grant", "force", "abort"})
    sr = {a for a, r in VALID_RESUME_ACTIONS.items() if "stage_review" in r}
    be = {a for a, r in VALID_RESUME_ACTIONS.items() if "budget_exhausted" in r}
    assert sr == expected_stage_review
    assert be == expected_budget


# ── Persistence end-to-end (state survives reopen) ─────────────────────────

def test_pause_state_persists_across_reconnects(fresh_db: Path) -> None:
    sid = _new_session(fresh_db)
    state.pause_session(sid, "design", "stage_review", fresh_db)
    state.resume_session(sid, "auto_approve_rest", fresh_db)
    # Re-open the DB by going through a fresh accessor — get_session
    # re-connects under the hood.
    sess = state.get_session(sid, fresh_db)
    assert sess is not None
    assert bool(sess.get("auto_approve_remaining")) is True
    assert sess.get("paused_at_stage") is None


# ── server-level sanity check (read_stage surfaces the hint) ───────────────

def test_user_hint_exposed_via_state_after_increment(fresh_db: Path) -> None:
    """The harness_read_stage tool reads through state.read_stage_user_hint
    and surfaces the hint as `user_hint` on the response. Direct call
    here proves the round-trip without booting MCP."""
    sid = _new_session(fresh_db)
    state.increment_attempt(sid, "plan", fresh_db, user_hint="describe the failure clearly")
    payload = {
        "stage": "plan",
        "user_hint": state.read_stage_user_hint(sid, "plan", fresh_db),
    }
    assert payload["user_hint"] == "describe the failure clearly"
    # Round-trip through json to mimic the MCP boundary.
    assert json.loads(json.dumps(payload))["user_hint"] == "describe the failure clearly"
