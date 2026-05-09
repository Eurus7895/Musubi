"""Phase G.2 — reviewer firewall runtime-assertion tests.

`_STAGE_PERMISSIONS["reviewer"] = {"code"}` is the documented
evaluator firewall. The G.2 runtime assertion in
`context_builder.read_stage_for_agent` is defense-in-depth: even if
a future refactor accidentally widens what the reviewer reads, the
assertion catches it loudly instead of returning a poisoned context.

These tests pin: legit reviewer reads pass through, smuggled
generator-side keys raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from session import state
from storage import db
from validation.context_builder import (
    _REVIEWER_FORBIDDEN_TOP_LEVEL_KEYS,
    _assert_reviewer_firewall_payload,
    read_stage_for_agent,
)


@pytest.fixture
def fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    p = tmp_path / "harness.db"
    db.init_db(p)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", p)
    return p


# ── Direct unit tests of the assertion helper ─────────────────────────


@pytest.mark.parametrize("forbidden_key", sorted(_REVIEWER_FORBIDDEN_TOP_LEVEL_KEYS))
def test_assertion_raises_for_each_forbidden_key(forbidden_key: str) -> None:
    payload = {"code": {"summary": "ok"}, forbidden_key: "leaked!"}
    with pytest.raises(RuntimeError, match="Reviewer firewall breach"):
        _assert_reviewer_firewall_payload(payload, "code")


def test_assertion_passes_for_clean_reviewer_payload() -> None:
    """A normal `code`-only payload is fine."""
    payload = {
        "summary": "fix login bug",
        "files_modified": ["src/auth.py"],
        "file_contents": {"src/auth.py": "..."},
    }
    # Should not raise.
    _assert_reviewer_firewall_payload(payload, "code")


def test_assertion_ignores_non_dict_payload() -> None:
    """Some stages might return non-dict outputs (e.g. lists). The
    firewall scans top-level dict keys; non-dict short-circuits."""
    _assert_reviewer_firewall_payload(["not a dict"], "code")
    _assert_reviewer_firewall_payload(None, "code")
    _assert_reviewer_firewall_payload("string", "code")


def test_assertion_message_lists_leaked_keys() -> None:
    payload = {"code": {}, "plan": "leak", "design": "also leak"}
    with pytest.raises(RuntimeError) as excinfo:
        _assert_reviewer_firewall_payload(payload, "code")
    msg = str(excinfo.value)
    assert "plan" in msg
    assert "design" in msg


# ── End-to-end through read_stage_for_agent ───────────────────────────


def _seed_review_code(fresh_db: Path) -> str:
    """Helper: create a session and write a clean code stage output."""
    sid = state.create_session("do x", fresh_db)
    state.write_stage(
        sid, "code",
        {"summary": "ok", "files_modified": ["a.py"], "file_contents": {"a.py": "x"}},
        fresh_db,
    )
    return sid


def test_reviewer_reads_code_normally(fresh_db: Path) -> None:
    sid = _seed_review_code(fresh_db)
    out = read_stage_for_agent(sid, "code", "reviewer", fresh_db)
    assert out is not None
    assert out["summary"] == "ok"


def test_reviewer_cannot_read_plan_via_permissions(fresh_db: Path) -> None:
    """_STAGE_PERMISSIONS already blocks reviewer from reading plan;
    verify that path is still tight (the new assertion is a second
    line, not the only line)."""
    sid = state.create_session("do x", fresh_db)
    state.write_stage(sid, "plan", {"summary": "p", "tasks": []}, fresh_db)
    out = read_stage_for_agent(sid, "plan", "reviewer", fresh_db)
    assert out is None  # firewall via _STAGE_PERMISSIONS


def test_reviewer_assertion_fires_when_permissions_silently_widened(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate a future bug: someone adds `plan` to reviewer's read
    permissions AND state.read_stage returns a payload with forbidden
    top-level keys. The assertion must catch it.

    We monkeypatch _STAGE_PERMISSIONS so reviewer can read 'code', and
    monkeypatch state.read_stage to return a tampered payload that
    contains a top-level `plan` key (mimicking a botched join).
    """
    sid = _seed_review_code(fresh_db)
    monkeypatch.setattr(
        state, "read_stage",
        lambda *a, **k: {"summary": "ok", "plan": {"tasks": ["leak"]}},
    )
    with pytest.raises(RuntimeError, match="Reviewer firewall breach"):
        read_stage_for_agent(sid, "code", "reviewer", fresh_db)


def test_other_agents_unaffected_by_assertion(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The firewall assertion is reviewer-only. Coder reading a
    payload with 'plan' is normal (coder reads plan + design)."""
    sid = state.create_session("do x", fresh_db)
    state.write_stage(sid, "plan", {"summary": "p", "tasks": []}, fresh_db)
    # Coder reading 'plan' returns the plan output; must not raise.
    out = read_stage_for_agent(sid, "plan", "coder", fresh_db)
    assert out is not None


def test_reviewer_aux_role_unaffected(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime assertion is keyed on agent_name='reviewer' exactly.
    Sub-agent role 'reviewer-aux' has its own firewall via
    SubagentContext and shouldn't trip this assertion (which is
    pipeline-stage-reviewer-only)."""
    sid = state.create_session("do x", fresh_db)
    # reviewer-aux isn't in _STAGE_PERMISSIONS at all → returns None.
    out = read_stage_for_agent(sid, "code", "reviewer-aux", fresh_db)
    assert out is None
