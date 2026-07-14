"""Tests for session/sub_sessions.py — lifecycle + storage helpers + orphan
sweep. All tests use a temp SQLite DB to stay isolated."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from session import state, sub_sessions
from storage import db as _db


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    _db.init_db(p)
    return p


@pytest.fixture()
def parent_session(db: Path) -> str:
    return state.create_session("parent request", db_path=db)


def _spawn(
    db: Path,
    parent: str,
    *,
    role: str = "explorer",
    brief: str = "scan src/ for FooClass",
    max_turns: int = 8,
    wall_clock_timeout_s: int = 60,
    per_turn_timeout_s: int = 30,
    parent_agent_name: str = "agent",
    allowed_tools: list[str] | None = None,
) -> str:
    return sub_sessions.spawn(
        parent_session_id=parent,
        parent_agent_name=parent_agent_name,
        role=role,
        brief=brief,
        allowed_tools=allowed_tools or ["Read", "Grep"],
        max_turns=max_turns,
        per_turn_timeout_s=per_turn_timeout_s,
        wall_clock_timeout_s=wall_clock_timeout_s,
        db_path=db,
    )


# Force the lifecycle module to read/write the test db. The module-level
# functions accept `db_path`, but `_now_dt`/`_parse_iso` don't — we reach
# into them in a couple of tests via monkeypatch.


# ── spawn ────────────────────────────────────────────────────────────────────

def test_spawn_returns_unique_handle_ids(db: Path, parent_session: str) -> None:
    handles = {_spawn(db, parent_session) for _ in range(8)}
    assert len(handles) == 8


def test_spawn_handle_id_format(db: Path, parent_session: str) -> None:
    h = _spawn(db, parent_session)
    # uuid hex truncated to 12 chars
    assert len(h) == 12
    assert all(c in "0123456789abcdef" for c in h)


def test_spawn_persists_all_fields(db: Path, parent_session: str) -> None:
    h = _spawn(
        db,
        parent_session,
        role="investigator",
        brief="run pytest in tests/api",
        allowed_tools=["Read", "Bash"],
        max_turns=5,
        wall_clock_timeout_s=120,
        per_turn_timeout_s=45,
        parent_agent_name="agent",
    )
    row = sub_sessions.get(h, db_path=db)
    assert row is not None
    assert row["handle_id"] == h
    assert row["parent_session_id"] == parent_session
    assert row["parent_agent_name"] == "agent"
    assert row["role"] == "investigator"
    assert row["brief"] == "run pytest in tests/api"
    assert row["allowed_tools"] == ["Read", "Bash"]
    assert row["max_turns"] == 5
    assert row["per_turn_timeout_s"] == 45
    assert row["wall_clock_timeout_s"] == 120
    assert row["status"] == "running"
    assert row["escalated"] is False
    assert row["turns"] == 0
    assert row["completed_at"] is None


def test_spawn_rejects_empty_role(db: Path, parent_session: str) -> None:
    with pytest.raises(ValueError, match="role"):
        sub_sessions.spawn(
            parent_session_id=parent_session,
            parent_agent_name="agent",
            role="   ",
            brief="x",
            db_path=db,
        )


def test_spawn_rejects_empty_brief(db: Path, parent_session: str) -> None:
    with pytest.raises(ValueError, match="brief"):
        sub_sessions.spawn(
            parent_session_id=parent_session,
            parent_agent_name="agent",
            role="explorer",
            brief="",
            db_path=db,
        )


def test_spawn_rejects_zero_max_turns(db: Path, parent_session: str) -> None:
    with pytest.raises(ValueError, match="max_turns"):
        sub_sessions.spawn(
            parent_session_id=parent_session,
            parent_agent_name="agent",
            role="explorer",
            brief="x",
            max_turns=0,
            db_path=db,
        )


def test_spawn_rejects_zero_timeouts(db: Path, parent_session: str) -> None:
    with pytest.raises(ValueError, match="timeouts"):
        sub_sessions.spawn(
            parent_session_id=parent_session,
            parent_agent_name="agent",
            role="explorer",
            brief="x",
            wall_clock_timeout_s=0,
            db_path=db,
        )


# ── complete (happy path) ────────────────────────────────────────────────────

def test_complete_done_records_terminal(db: Path, parent_session: str) -> None:
    h = _spawn(db, parent_session)
    final = sub_sessions.complete(
        h,
        summary="found 14 matches across 9 files",
        structured={"matches": 14, "files": 9},
        tools_used=["Grep", "Read"],
        turns=3,
        status="done",
        db_path=db,
    )
    assert final["status"] == "done"
    assert final["escalated"] is False
    assert final["result_summary"] == "found 14 matches across 9 files"
    assert final["result_structured"] == {"matches": 14, "files": 9}
    assert final["tools_used"] == ["Grep", "Read"]
    assert final["turns"] == 3
    assert final["completed_at"] is not None


def test_complete_failed_records_failed(db: Path, parent_session: str) -> None:
    h = _spawn(db, parent_session)
    final = sub_sessions.complete(
        h, summary="parse error", turns=1, status="failed", db_path=db
    )
    assert final["status"] == "failed"
    assert final["escalated"] is False  # explicit failed != escalated


def test_complete_twice_rejected(db: Path, parent_session: str) -> None:
    h = _spawn(db, parent_session)
    sub_sessions.complete(h, summary="ok", turns=1, db_path=db)
    with pytest.raises(ValueError, match="already terminal"):
        sub_sessions.complete(h, summary="again", turns=1, db_path=db)


def test_complete_unknown_handle_rejected(db: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        sub_sessions.complete(
            "nope", summary="x", turns=1, db_path=db
        )


def test_complete_invalid_status_rejected(
    db: Path, parent_session: str
) -> None:
    h = _spawn(db, parent_session)
    with pytest.raises(ValueError, match="status"):
        sub_sessions.complete(h, summary="x", turns=1, status="bogus", db_path=db)


def test_complete_negative_turns_rejected(
    db: Path, parent_session: str
) -> None:
    h = _spawn(db, parent_session)
    with pytest.raises(ValueError, match="turns"):
        sub_sessions.complete(h, summary="x", turns=-1, db_path=db)


# ── timeout enforcement ─────────────────────────────────────────────────────

def test_complete_max_turns_kills_to_escalated(
    db: Path, parent_session: str
) -> None:
    h = _spawn(db, parent_session, max_turns=4)
    # Runner reports done with turns at the cap → harness coerces to escalated.
    final = sub_sessions.complete(
        h, summary="finished", turns=4, status="done", db_path=db
    )
    assert final["status"] == "escalated"
    assert final["escalated"] is True
    assert "max_turns=4" in (final["result_summary"] or "")


def test_complete_wall_clock_kills_to_escalated(
    db: Path, parent_session: str
) -> None:
    """1s wall clock + 1.1s sleep → harness escalates even on done."""
    h = _spawn(db, parent_session, wall_clock_timeout_s=1, max_turns=10)
    time.sleep(1.1)
    final = sub_sessions.complete(
        h, summary="took too long", turns=2, status="done", db_path=db
    )
    assert final["status"] == "escalated"
    assert final["escalated"] is True
    assert "wall_clock_timeout_s=1" in (final["result_summary"] or "")


def test_complete_under_caps_stays_done(db: Path, parent_session: str) -> None:
    h = _spawn(db, parent_session, max_turns=5, wall_clock_timeout_s=60)
    final = sub_sessions.complete(
        h, summary="quick", turns=2, status="done", db_path=db
    )
    assert final["status"] == "done"
    assert final["escalated"] is False


# ── abandon (force) ─────────────────────────────────────────────────────────

def test_abandon_running_sets_abandoned(
    db: Path, parent_session: str
) -> None:
    h = _spawn(db, parent_session)
    final = sub_sessions.abandon(h, reason="user closed chat", db_path=db)
    assert final is not None
    assert final["status"] == "abandoned"
    assert final["escalated"] is True
    assert "user closed chat" in (final["result_summary"] or "")


def test_abandon_terminal_is_noop(db: Path, parent_session: str) -> None:
    h = _spawn(db, parent_session)
    sub_sessions.complete(h, summary="ok", turns=1, db_path=db)
    final = sub_sessions.abandon(h, db_path=db)
    assert final is not None
    # status unchanged
    assert final["status"] == "done"


def test_abandon_unknown_handle_returns_none(db: Path) -> None:
    assert sub_sessions.abandon("does-not-exist", db_path=db) is None


# ── cascade on parent end ──────────────────────────────────────────────────

def test_cascade_marks_running_children_abandoned(
    db: Path, parent_session: str
) -> None:
    h1 = _spawn(db, parent_session)
    h2 = _spawn(db, parent_session)
    h3 = _spawn(db, parent_session)
    # Complete one; the other two stay running.
    sub_sessions.complete(h2, summary="ok", turns=1, db_path=db)

    n = sub_sessions.cascade_abandon_for_parent(parent_session, db_path=db)
    assert n == 2

    # Re-read state
    assert sub_sessions.get(h1, db_path=db)["status"] == "abandoned"
    assert sub_sessions.get(h2, db_path=db)["status"] == "done"
    assert sub_sessions.get(h3, db_path=db)["status"] == "abandoned"


def test_cascade_with_no_running_children_returns_zero(
    db: Path, parent_session: str
) -> None:
    assert sub_sessions.cascade_abandon_for_parent(parent_session, db_path=db) == 0


# ── orphan sweep ────────────────────────────────────────────────────────────

def test_sweep_orphans_marks_running_for_inactive_parents(
    db: Path,
) -> None:
    # Parent A is active; parent B is closed.
    pa = state.create_session("a", db_path=db)
    pb = state.create_session("b", db_path=db)

    # Manually flip parent B's status to 'complete' to simulate a finished
    # session whose runner crashed before cascading.
    with _db._connect(db) as conn:
        conn.execute(
            "UPDATE sessions SET status = 'complete' WHERE session_id = ?",
            (pb,),
        )

    ha = _spawn(db, pa)  # should stay running
    hb1 = _spawn(db, pb)
    hb2 = _spawn(db, pb)
    # And one already terminal under B — should NOT be touched.
    hb3 = _spawn(db, pb)
    sub_sessions.complete(hb3, summary="ok", turns=1, db_path=db)

    n = sub_sessions.sweep_orphans(db_path=db)
    assert n == 2

    assert sub_sessions.get(ha, db_path=db)["status"] == "running"
    assert sub_sessions.get(hb1, db_path=db)["status"] == "abandoned"
    assert sub_sessions.get(hb2, db_path=db)["status"] == "abandoned"
    assert sub_sessions.get(hb3, db_path=db)["status"] == "done"


def test_sweep_orphans_idempotent(db: Path) -> None:
    parent = state.create_session("p", db_path=db)
    with _db._connect(db) as conn:
        conn.execute(
            "UPDATE sessions SET status = 'complete' WHERE session_id = ?",
            (parent,),
        )
    _spawn(db, parent)
    assert sub_sessions.sweep_orphans(db_path=db) == 1
    # Second call has nothing left to mark.
    assert sub_sessions.sweep_orphans(db_path=db) == 0


# ── list_for_parent ─────────────────────────────────────────────────────────

def test_list_for_parent_orders_by_created(
    db: Path, parent_session: str
) -> None:
    h1 = _spawn(db, parent_session, brief="first")
    h2 = _spawn(db, parent_session, brief="second")
    rows = sub_sessions.list_for_parent(parent_session, db_path=db)
    assert [r["handle_id"] for r in rows] == [h1, h2]


def test_list_for_parent_includes_terminal(
    db: Path, parent_session: str
) -> None:
    h = _spawn(db, parent_session)
    sub_sessions.complete(h, summary="ok", turns=1, db_path=db)
    rows = sub_sessions.list_for_parent(parent_session, db_path=db)
    assert len(rows) == 1
    assert rows[0]["status"] == "done"


def test_list_for_unknown_parent_is_empty(db: Path) -> None:
    assert sub_sessions.list_for_parent("nope", db_path=db) == []


# ── get for unknown handle ──────────────────────────────────────────────────

def test_get_unknown_handle_returns_none(db: Path) -> None:
    assert sub_sessions.get("nope", db_path=db) is None


# ── MCP tool layer (server.py) ──────────────────────────────────────────────
#
# The MCP tools don't accept db_path. We swap _db.DEFAULT_DB_PATH for an
# isolated tmp file per test so the musubi_* functions read/write there.

import json  # noqa: E402

import server  # noqa: E402


@pytest.fixture()
def mcp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    p = tmp_path / "mcp.db"
    _db.init_db(p)
    monkeypatch.setattr(_db, "DEFAULT_DB_PATH", p)
    # Fast polling so musubi_await_subagent doesn't drag the suite.
    monkeypatch.setattr(server, "_AWAIT_POLL_S", 0.02)
    return p


def test_mcp_spawn_unknown_role_rejected(mcp_db: Path) -> None:
    parent = state.create_session("p")
    raw = server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="ghost",
        brief="x",
    )
    payload = json.loads(raw)
    assert payload["status"] == "error"
    assert "ghost" in payload["error"]


def test_mcp_spawn_disallowed_main_rejected(mcp_db: Path) -> None:
    """planner / designer remain spawn-locked (Phase G.1.6 only opted
    coder + reviewer in). The earlier coder→explorer denial moved to
    test_mcp_spawn_coder_explorer_allowed below."""
    parent = state.create_session("p")
    raw = server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="planner",
        role="explorer",
        brief="x",
    )
    payload = json.loads(raw)
    assert payload["status"] == "error"
    assert "planner" in payload["error"]


def test_mcp_spawn_coder_explorer_allowed(mcp_db: Path) -> None:
    """Phase G.1.6 — coder may spawn explorer. The pre-coder dispatcher
    in the TS pipeline runner relies on this MCP path succeeding."""
    parent = state.create_session("p")
    raw = server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="coder",
        role="explorer",
        brief="find callers of FooClass",
    )
    payload = json.loads(raw)
    assert payload["status"] == "spawned", payload


def test_mcp_spawn_unknown_parent_rejected(mcp_db: Path) -> None:
    raw = server.musubi_spawn_subagent(
        parent_session_id="no-such-sess",
        parent_agent_name="agent",
        role="explorer",
        brief="x",
    )
    payload = json.loads(raw)
    assert payload["status"] == "error"
    assert "parent session" in payload["error"]


def test_mcp_spawn_intersection_with_caller_tools(mcp_db: Path) -> None:
    parent = state.create_session("p")
    raw = server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="investigator",
        brief="run pytest",
        allowed_tools=["Read", "Bash"],  # narrower than role default
    )
    payload = json.loads(raw)
    assert payload["status"] == "spawned"
    assert sorted(payload["effective_tools"]) == ["Bash", "Read"]


def test_mcp_spawn_disjoint_caller_tools_rejected(mcp_db: Path) -> None:
    parent = state.create_session("p")
    raw = server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="explorer",
        brief="x",
        allowed_tools=["Write", "Edit"],  # disjoint from explorer's read-only set
    )
    payload = json.loads(raw)
    assert payload["status"] == "error"
    assert "No tools available" in payload["error"]


def test_mcp_spawn_then_complete_then_await_returns_summary(
    mcp_db: Path,
) -> None:
    parent = state.create_session("p")
    spawn_raw = server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="explorer",
        brief="scan src/",
    )
    spawn = json.loads(spawn_raw)
    h = spawn["handle_id"]

    server.musubi_complete_subagent(
        handle_id=h,
        summary="14 matches in 9 files",
        structured={"matches": 14},
        tools_used=["Grep", "Read"],
        turns=3,
        status="done",
    )

    await_raw = server.musubi_await_subagent(handle_id=h, max_wait_s=2)
    payload = json.loads(await_raw)
    assert payload["status"] == "recorded"
    assert payload["final_status"] == "done"
    assert payload["summary"] == "14 matches in 9 files"
    assert payload["structured"] == {"matches": 14}
    assert payload["turns"] == 3
    assert payload["escalated"] is False


def test_mcp_await_unknown_handle_errors(mcp_db: Path) -> None:
    raw = server.musubi_await_subagent(handle_id="nope", max_wait_s=1)
    payload = json.loads(raw)
    assert payload["status"] == "error"
    assert "not found" in payload["error"]


def test_mcp_await_pending_returns_snapshot_after_max_wait(
    mcp_db: Path,
) -> None:
    parent = state.create_session("p")
    spawn = json.loads(
        server.musubi_spawn_subagent(
            parent_session_id=parent,
            parent_agent_name="agent",
            role="explorer",
            brief="x",
            wall_clock_timeout_s=300,
        )
    )
    raw = server.musubi_await_subagent(handle_id=spawn["handle_id"], max_wait_s=0)
    payload = json.loads(raw)
    assert payload["status"] == "pending"
    assert payload["still_running"] is True
    assert payload["snapshot"]["role"] == "explorer"


def test_mcp_await_wall_clock_kill_escalates(
    mcp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1s wall_clock + a sleep past it → await coerces to escalated."""
    parent = state.create_session("p")
    spawn = json.loads(
        server.musubi_spawn_subagent(
            parent_session_id=parent,
            parent_agent_name="agent",
            role="explorer",
            brief="x",
            wall_clock_timeout_s=1,
        )
    )
    time.sleep(1.1)
    raw = server.musubi_await_subagent(handle_id=spawn["handle_id"], max_wait_s=2)
    payload = json.loads(raw)
    assert payload["status"] == "recorded"
    assert payload["final_status"] == "escalated"
    assert payload["escalated"] is True


def test_mcp_complete_unknown_handle_errors(mcp_db: Path) -> None:
    raw = server.musubi_complete_subagent(
        handle_id="nope", summary="x", turns=1
    )
    payload = json.loads(raw)
    assert payload["status"] == "error"


def test_mcp_complete_max_turns_kill(mcp_db: Path) -> None:
    parent = state.create_session("p")
    spawn = json.loads(
        server.musubi_spawn_subagent(
            parent_session_id=parent,
            parent_agent_name="agent",
            role="explorer",
            brief="x",
            max_turns=3,
        )
    )
    raw = server.musubi_complete_subagent(
        handle_id=spawn["handle_id"],
        summary="ok",
        turns=3,  # at the cap
        status="done",
    )
    payload = json.loads(raw)
    assert payload["final_status"] == "escalated"
    assert payload["escalated"] is True


def _spawn_coder_at_cap(max_turns: int = 3) -> str:
    parent = state.create_session("p")
    spawn = json.loads(
        server.musubi_spawn_subagent(
            parent_session_id=parent,
            parent_agent_name="agent",
            role="coder",
            brief="write dashboard",
            max_turns=max_turns,
        )
    )
    return spawn["handle_id"]


def test_mcp_complete_max_turns_done_with_verified_artifacts_stays_done(
    mcp_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """'done' at exactly the turn cap + an artifacts manifest the harness
    verifies on disk is a completion, not a timeout violation. This is the
    layer that used to coerce a finished artifact back to escalated and
    push the root into a pointless recovery."""
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    artifact = tmp_path / "artifacts" / "nyc-dashboard.html"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("<!DOCTYPE html><html></html>", encoding="utf-8")

    raw = server.musubi_complete_subagent(
        handle_id=_spawn_coder_at_cap(),
        summary="status: done",
        turns=3,  # at the cap
        status="done",
        artifacts=["artifacts/nyc-dashboard.html"],
    )
    payload = json.loads(raw)
    assert payload["final_status"] == "done"
    assert payload["escalated"] is False
    # The audit trail still records that the cap was reached — and why the
    # result was accepted anyway.
    assert "max_turns=3 reached" in payload["summary"]
    assert "verified non-empty on disk" in payload["summary"]


def test_mcp_complete_max_turns_done_with_missing_artifact_coerces(
    mcp_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    raw = server.musubi_complete_subagent(
        handle_id=_spawn_coder_at_cap(),
        summary="status: done",
        turns=3,
        status="done",
        artifacts=["artifacts/never-written.html"],
    )
    payload = json.loads(raw)
    assert payload["final_status"] == "escalated"
    assert payload["escalated"] is True


def test_mcp_complete_max_turns_artifact_escape_coerces(
    mcp_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A manifest path escaping the workspace root fails verification —
    the runner cannot point at an arbitrary host file to dodge the cap."""
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("MUSUBI_ROOT", str(root))
    outside = tmp_path / "outside.html"
    outside.write_text("<html></html>", encoding="utf-8")

    raw = server.musubi_complete_subagent(
        handle_id=_spawn_coder_at_cap(),
        summary="status: done",
        turns=3,
        status="done",
        artifacts=["../outside.html"],
    )
    payload = json.loads(raw)
    assert payload["final_status"] == "escalated"
    assert payload["escalated"] is True


def test_mcp_complete_wall_clock_never_waived_by_artifacts(
    mcp_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    artifact = tmp_path / "out.html"
    artifact.write_text("<html></html>", encoding="utf-8")

    parent = state.create_session("p")
    spawn = json.loads(
        server.musubi_spawn_subagent(
            parent_session_id=parent,
            parent_agent_name="agent",
            role="coder",
            brief="x",
            max_turns=3,
            wall_clock_timeout_s=1,
        )
    )
    time.sleep(1.1)
    raw = server.musubi_complete_subagent(
        handle_id=spawn["handle_id"],
        summary="status: done",
        turns=3,
        status="done",
        artifacts=["out.html"],
    )
    payload = json.loads(raw)
    assert payload["final_status"] == "escalated"
    assert payload["escalated"] is True


# ── musubi_list_subagents (MCP tool) ───────────────────────────────────────

def test_mcp_list_subagents_for_agent(mcp_db: Path) -> None:
    raw = server.musubi_list_subagents(main_agent_name="agent")
    payload = json.loads(raw)
    assert payload["main_agent"] == "agent"
    role_names = {r["role"] for r in payload["roles"]}
    # Phase A.1 roles plus Phase B.1 ad-hoc-spawnable pipeline roles.
    assert role_names >= {
        "explorer", "investigator", "reviewer-aux",
        "planner", "coder", "reviewer",
    }
    # Each entry exposes the role's tool allow-list as a list. Phase A/B
    # roles each get at least Read; Phase C.2 introduces the text-only
    # summarizer with an empty tools list.
    for r in payload["roles"]:
        assert isinstance(r["allowed_tools"], list)
        if r["role"] == "summarizer":
            assert r["allowed_tools"] == []
        else:
            assert r["allowed_tools"]  # non-empty for tool-using roles


def test_mcp_list_subagents_for_pipeline_stage_phase_g16(mcp_db: Path) -> None:
    """Phase G.1.6 — coder + reviewer opted into specific roles. The
    MCP tool reflects MAIN_SUBAGENT_ALLOWLIST, so it now returns the
    G.1.6 role lists for these stages."""
    coder_payload = json.loads(server.musubi_list_subagents(main_agent_name="coder"))
    reviewer_payload = json.loads(server.musubi_list_subagents(main_agent_name="reviewer"))
    coder_roles = {r["role"] for r in coder_payload["roles"]}
    reviewer_roles = {r["role"] for r in reviewer_payload["roles"]}
    assert coder_roles == {"explorer", "investigator"}
    assert reviewer_roles == {"reviewer-aux"}
    # planner / designer remain empty.
    planner_payload = json.loads(server.musubi_list_subagents(main_agent_name="planner"))
    designer_payload = json.loads(server.musubi_list_subagents(main_agent_name="designer"))
    assert planner_payload["roles"] == []
    assert designer_payload["roles"] == []


def test_mcp_list_subagents_for_unknown_main_is_empty(mcp_db: Path) -> None:
    raw = server.musubi_list_subagents(main_agent_name="nobody")
    payload = json.loads(raw)
    assert payload["roles"] == []
