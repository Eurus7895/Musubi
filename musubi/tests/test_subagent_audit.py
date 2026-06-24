"""Tests for storage/subagent_audit.py and the audit wiring in server.py
(Phase A.3).

The "no silent sub agents" invariant requires that every spawn and
every terminal completion writes a durable row. These tests prove the
audit module behaves correctly on its own and that
`musubi_spawn_subagent` / `musubi_complete_subagent` actually call it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session import state, sub_sessions
from storage import db as _db
from storage import subagent_audit

import server


@pytest.fixture()
def audit_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    p = tmp_path / "audit.db"
    subagent_audit.init_db(p)
    monkeypatch.setattr(subagent_audit, "_DEFAULT_AUDIT_DB", p)
    # Also patch the path-resolver so MUSUBI_ROOT doesn't sneak in.
    monkeypatch.setattr(subagent_audit, "_resolve_db_path", lambda: p)
    return p


@pytest.fixture()
def state_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    p = tmp_path / "state.db"
    _db.init_db(p)
    monkeypatch.setattr(_db, "DEFAULT_DB_PATH", p)
    monkeypatch.setattr(server, "_AWAIT_POLL_S", 0.02)
    return p


# ── unit: writers ───────────────────────────────────────────────────────────

def test_record_spawn_persists_all_fields(audit_db: Path) -> None:
    subagent_audit.record_spawn(
        handle_id="h1",
        parent_session_id="p1",
        parent_agent_name="agent",
        role="explorer",
        brief="scan src/",
        allowed_tools=["Read", "Grep"],
        max_turns=8,
        wall_clock_timeout_s=300,
    )
    rows = subagent_audit.query_events()
    assert len(rows) == 1
    r = rows[0]
    assert r["handle_id"] == "h1"
    assert r["event"] == "spawned"
    assert r["allowed_tools"] == ["Read", "Grep"]
    assert r["max_turns"] == 8
    assert r["wall_clock_timeout_s"] == 300
    # complete-only fields are null on a spawn row
    assert r["final_status"] is None
    assert r["turns"] is None


def test_record_complete_persists_all_fields(audit_db: Path) -> None:
    subagent_audit.record_complete(
        handle_id="h1",
        parent_session_id="p1",
        parent_agent_name="agent",
        role="investigator",
        brief="run pytest",
        final_status="done",
        escalated=False,
        turns=3,
        tools_used=["Bash", "Read"],
        summary_truncated=True,
        verification_errors=None,
    )
    rows = subagent_audit.query_events()
    assert len(rows) == 1
    r = rows[0]
    assert r["event"] == "completed"
    assert r["final_status"] == "done"
    assert r["escalated"] is False
    assert r["turns"] == 3
    assert r["tools_used"] == ["Bash", "Read"]
    assert r["summary_truncated"] is True
    assert r["verification_errors"] is None


def test_record_complete_persists_verification_errors(
    audit_db: Path,
) -> None:
    subagent_audit.record_complete(
        handle_id="h2",
        parent_session_id="p1",
        parent_agent_name="agent",
        role="explorer",
        brief="x",
        final_status="failed",
        escalated=False,
        turns=1,
        tools_used=None,
        summary_truncated=False,
        verification_errors=["sub-agent summary contains potential secret: AWS access key"],
    )
    rows = subagent_audit.query_events()
    assert len(rows) == 1
    assert rows[0]["verification_errors"] == [
        "sub-agent summary contains potential secret: AWS access key"
    ]


# ── unit: query filters ─────────────────────────────────────────────────────

def test_query_filters_by_parent_session_id(audit_db: Path) -> None:
    subagent_audit.record_spawn(
        handle_id="h1", parent_session_id="A", parent_agent_name="agent",
        role="explorer", brief="x",
        allowed_tools=None, max_turns=4, wall_clock_timeout_s=60,
    )
    subagent_audit.record_spawn(
        handle_id="h2", parent_session_id="B", parent_agent_name="agent",
        role="explorer", brief="x",
        allowed_tools=None, max_turns=4, wall_clock_timeout_s=60,
    )
    a_rows = subagent_audit.query_events(parent_session_id="A")
    b_rows = subagent_audit.query_events(parent_session_id="B")
    assert {r["handle_id"] for r in a_rows} == {"h1"}
    assert {r["handle_id"] for r in b_rows} == {"h2"}


def test_query_filters_by_handle_id(audit_db: Path) -> None:
    for h in ("h1", "h2", "h3"):
        subagent_audit.record_spawn(
            handle_id=h, parent_session_id="p", parent_agent_name="agent",
            role="explorer", brief="x",
            allowed_tools=None, max_turns=4, wall_clock_timeout_s=60,
        )
    rows = subagent_audit.query_events(handle_id="h2")
    assert [r["handle_id"] for r in rows] == ["h2"]


def test_query_orders_by_ts_ascending(audit_db: Path) -> None:
    subagent_audit.record_spawn(
        handle_id="h1", parent_session_id="p", parent_agent_name="agent",
        role="explorer", brief="first",
        allowed_tools=None, max_turns=4, wall_clock_timeout_s=60,
    )
    subagent_audit.record_complete(
        handle_id="h1", parent_session_id="p", parent_agent_name="agent",
        role="explorer", brief="first", final_status="done", escalated=False,
        turns=1, tools_used=None, summary_truncated=False,
        verification_errors=None,
    )
    rows = subagent_audit.query_events()
    assert [r["event"] for r in rows] == ["spawned", "completed"]


def test_query_respects_limit(audit_db: Path) -> None:
    for i in range(5):
        subagent_audit.record_spawn(
            handle_id=f"h{i}", parent_session_id="p",
            parent_agent_name="agent", role="explorer", brief="x",
            allowed_tools=None, max_turns=4, wall_clock_timeout_s=60,
        )
    rows = subagent_audit.query_events(limit=3)
    assert len(rows) == 3


def test_query_limit_zero_returns_empty(audit_db: Path) -> None:
    subagent_audit.record_spawn(
        handle_id="h1", parent_session_id="p", parent_agent_name="agent",
        role="explorer", brief="x",
        allowed_tools=None, max_turns=4, wall_clock_timeout_s=60,
    )
    assert subagent_audit.query_events(limit=0) == []


def test_query_filters_by_since_ts(audit_db: Path) -> None:
    subagent_audit.record_spawn(
        handle_id="h1", parent_session_id="p", parent_agent_name="agent",
        role="explorer", brief="early",
        allowed_tools=None, max_turns=4, wall_clock_timeout_s=60,
    )
    early_rows = subagent_audit.query_events()
    cutoff = early_rows[0]["ts"]
    subagent_audit.record_spawn(
        handle_id="h2", parent_session_id="p", parent_agent_name="agent",
        role="explorer", brief="late",
        allowed_tools=None, max_turns=4, wall_clock_timeout_s=60,
    )
    after = subagent_audit.query_events(since_ts=cutoff)
    assert [r["handle_id"] for r in after] == ["h2"]


# ── integration: server.py wires audit on spawn + complete ─────────────────

def test_server_spawn_writes_audit_row(
    audit_db: Path, state_db: Path
) -> None:
    parent = state.create_session("p")
    raw = server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="explorer",
        brief="scan src/",
    )
    spawn = json.loads(raw)
    assert spawn["status"] == "spawned"

    rows = subagent_audit.query_events(parent_session_id=parent)
    assert len(rows) == 1
    r = rows[0]
    assert r["event"] == "spawned"
    assert r["handle_id"] == spawn["handle_id"]
    assert r["role"] == "explorer"
    assert r["brief"] == "scan src/"
    assert "Read" in r["allowed_tools"]


def test_server_complete_writes_audit_row(
    audit_db: Path, state_db: Path
) -> None:
    parent = state.create_session("p")
    spawn_raw = server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="explorer",
        brief="scan src/",
    )
    h = json.loads(spawn_raw)["handle_id"]

    server.musubi_complete_subagent(
        handle_id=h, summary="found 3", turns=2, status="done",
    )

    rows = subagent_audit.query_events(handle_id=h)
    events = [r["event"] for r in rows]
    assert events == ["spawned", "completed"]
    completed = rows[-1]
    assert completed["final_status"] == "done"
    assert completed["turns"] == 2
    assert completed["escalated"] is False


def test_server_records_escalation_in_audit(
    audit_db: Path, state_db: Path
) -> None:
    parent = state.create_session("p")
    spawn_raw = server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="explorer",
        brief="x",
        max_turns=3,  # the cap
    )
    h = json.loads(spawn_raw)["handle_id"]
    # turns=3 >= max_turns → harness coerces to escalated.
    server.musubi_complete_subagent(
        handle_id=h, summary="ok", turns=3, status="done",
    )
    rows = subagent_audit.query_events(handle_id=h)
    completed = rows[-1]
    assert completed["final_status"] == "escalated"
    assert completed["escalated"] is True


def test_server_records_verification_failure(
    audit_db: Path, state_db: Path
) -> None:
    parent = state.create_session("p")
    spawn_raw = server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="explorer",
        brief="x",
    )
    h = json.loads(spawn_raw)["handle_id"]
    server.musubi_complete_subagent(
        handle_id=h,
        summary="leaked: AKIAIOSFODNN7EXAMPLE",
        turns=1, status="done",
    )
    rows = subagent_audit.query_events(handle_id=h)
    completed = rows[-1]
    assert completed["final_status"] == "failed"
    assert completed["verification_errors"]
    assert any("AWS" in e for e in completed["verification_errors"])


def test_server_records_truncation(audit_db: Path, state_db: Path) -> None:
    parent = state.create_session("p")
    spawn_raw = server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="explorer",
        brief="x",
    )
    h = json.loads(spawn_raw)["handle_id"]
    server.musubi_complete_subagent(
        handle_id=h,
        summary="a" * 100_000,
        turns=1, status="done",
        max_summary_tokens=200,  # ~800-char cap
    )
    rows = subagent_audit.query_events(handle_id=h)
    completed = rows[-1]
    assert completed["summary_truncated"] is True


# ── invariant: no silent sub agents ────────────────────────────────────────

def test_no_silent_sub_agents_full_lifecycle(
    audit_db: Path, state_db: Path
) -> None:
    """Drive the full spawn → complete cycle three times across two
    different sub-agent roles and assert that the audit log captures
    every transition with no gaps."""
    parent = state.create_session("p")
    handles: list[str] = []
    for role, brief in [
        ("explorer", "scan src/"),
        ("investigator", "run pytest"),
        ("reviewer-aux", "review src/x.py"),
    ]:
        spawn = json.loads(server.musubi_spawn_subagent(
            parent_session_id=parent,
            parent_agent_name="agent",
            role=role, brief=brief,
        ))
        handles.append(spawn["handle_id"])
        server.musubi_complete_subagent(
            handle_id=spawn["handle_id"],
            summary=f"{role} done", turns=1, status="done",
        )

    rows = subagent_audit.query_events(parent_session_id=parent)
    # 3 spawns + 3 completions = 6 rows, no fewer.
    assert len(rows) == 6
    # Pair up: every handle has both a spawned and a completed row.
    for h in handles:
        h_rows = [r for r in rows if r["handle_id"] == h]
        events = {r["event"] for r in h_rows}
        assert events == {"spawned", "completed"}, (
            f"handle {h} missing one of the audit events: got {events}"
        )


# ── MCP tool: musubi_query_subagent_events ────────────────────────────────

def test_query_tool_returns_events_for_parent(
    audit_db: Path, state_db: Path
) -> None:
    parent = state.create_session("p")
    json.loads(server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="explorer", brief="x",
    ))
    raw = server.musubi_query_subagent_events(parent_session_id=parent)
    payload = json.loads(raw)
    assert payload["count"] >= 1
    assert payload["events"][0]["event"] == "spawned"


def test_query_tool_filters_by_handle(
    audit_db: Path, state_db: Path
) -> None:
    parent = state.create_session("p")
    h1 = json.loads(server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="explorer", brief="a",
    ))["handle_id"]
    json.loads(server.musubi_spawn_subagent(
        parent_session_id=parent,
        parent_agent_name="agent",
        role="investigator", brief="b",
    ))
    raw = server.musubi_query_subagent_events(handle_id=h1)
    payload = json.loads(raw)
    assert payload["count"] == 1
    assert payload["events"][0]["handle_id"] == h1


def test_query_tool_returns_empty_for_unknown_parent(
    audit_db: Path, state_db: Path
) -> None:
    raw = server.musubi_query_subagent_events(
        parent_session_id="no-such-session"
    )
    payload = json.loads(raw)
    assert payload["events"] == []
    assert payload["count"] == 0


# ── role file presence (the agents shipped in Phase A.3) ───────────────────

def test_role_agent_md_files_exist() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    for role in ("explorer", "investigator", "reviewer-aux"):
        path = repo_root / ".github" / "agents" / f"{role}.agent.md"
        assert path.exists(), f"missing role file: {path}"
        text = path.read_text(encoding="utf-8")
        # Frontmatter check — the harness's lock_agent_versions parses
        # version: ... here.
        assert "version:" in text
        assert "tools:" in text


def test_role_skill_files_exist_and_load() -> None:
    """SKILL.md files for each role must exist and be loadable through
    skill_loader (the same path build_subagent_context uses)."""
    from skills import skill_loader
    for role in ("explorer", "investigator", "reviewer-aux"):
        content = skill_loader.get_skill(role)
        assert content is not None, f"SKILL.md missing for role {role}"
        assert "## Procedure" in content or "## Purpose" in content
