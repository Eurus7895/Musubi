"""Phase G.1.6 — pre-spawn dispatcher audit-trail integration test.

The G.1 acceptance criterion: "feature-dev's `coder` stage spawns an
`explorer` sub-agent for a 'find callers of X' lookup; the summary
lands in the coder's next prompt; the audit DB shows the spawn +
completion rows."

This test pins the audit-DB part end-to-end via the MCP tools the
TS-side `subagentDispatcherRun.runPreSpawns` calls. The "summary
lands in coder's next prompt" half is covered by the TS-side
`subagentDispatcher.test.ts::spliceResultsIntoContext` tests.

Mirrors the sequence the dispatcher fires for a chunked coder run:
  1. coder pre-spawn → harness_spawn_subagent(role='explorer',
     parent_agent_name='coder')
  2. extension-side runner completes → harness_complete_subagent(
     status='done', summary, tools_used, turns)
  3. parent reads back → harness_query_subagent_events(
     parent_session_id) returns 2 rows: spawned + completed.

Plus: the policy table now allows coder→explorer / coder→investigator
/ reviewer→reviewer-aux. Pin those entries so a future refactor can't
silently revoke them and break the dispatcher.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Import validation first — its module-level _ensure_scripts_on_path
# call puts scripts/ on sys.path so policy_engine resolves below.
# isort: split  (block separator so ruff keeps the order)
from validation.subagent_context import build_subagent_context  # noqa: F401, E402  (side effect)

# isort: split
import server  # noqa: E402, I001
from policy_engine import MAIN_SUBAGENT_ALLOWLIST, SUBAGENT_POLICIES  # noqa: E402, I001
from session import state  # noqa: E402, I001
from storage import subagent_audit  # noqa: E402, I001


@pytest.fixture()
def audit_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolate the audit DB to a tmp file for each test."""
    p = tmp_path / "audit.db"
    subagent_audit.init_db(p)
    monkeypatch.setattr(subagent_audit, "_DEFAULT_AUDIT_DB", p)
    monkeypatch.setattr(subagent_audit, "_resolve_db_path", lambda: p)
    return p


@pytest.fixture()
def harness_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolate the harness state DB to a tmp file."""
    p = tmp_path / "harness.db"
    from storage import db as _db
    _db.init_db(p)
    monkeypatch.setattr(_db, "DEFAULT_DB_PATH", p)
    return p


# ── policy-table pins ─────────────────────────────────────────────────


def test_coder_can_spawn_explorer_and_investigator() -> None:
    """G.1.6 wiring: coder must be allowed to spawn the read-only
    explorer + investigator roles. The dispatcher fires explorer
    pre-coder; investigator is reserved for future diagnostic flows.
    """
    allowed = MAIN_SUBAGENT_ALLOWLIST.get("coder", [])
    assert "explorer" in allowed
    assert "investigator" in allowed


def test_reviewer_can_spawn_reviewer_aux() -> None:
    """G.1.6 wiring: reviewer must be allowed to spawn reviewer-aux
    for per-file checks on chunks with > 2 modules."""
    allowed = MAIN_SUBAGENT_ALLOWLIST.get("reviewer", [])
    assert "reviewer-aux" in allowed


def test_planner_and_designer_still_cannot_spawn() -> None:
    """Tightness check — planner / designer remain spawn-locked. The
    dispatcher's stage-routing already returns [] for these stages,
    but the policy table is the authoritative second line of defence."""
    assert MAIN_SUBAGENT_ALLOWLIST.get("planner") == []
    assert MAIN_SUBAGENT_ALLOWLIST.get("designer") == []


def test_subagent_roles_have_tool_policies() -> None:
    """Every role in the dispatcher's vocabulary must declare a tool
    allow-list. SUBAGENT_POLICIES is the firewall for what each role
    can actually do once spawned."""
    for role in ("explorer", "investigator", "reviewer-aux"):
        assert role in SUBAGENT_POLICIES, f"missing tool policy for {role}"
        assert isinstance(SUBAGENT_POLICIES[role], list)


# ── audit-trail integration ───────────────────────────────────────────


def test_coder_explorer_spawn_writes_audit_row(
    audit_db: Path, harness_db: Path,
) -> None:
    """When a coder stage pre-spawns explorer (the heuristic case
    from the dispatcher), the audit DB must show a `spawned` row
    with parent_agent_name='coder' and role='explorer'."""
    sid = state.create_session("build a feature", harness_db)
    # The TS dispatcher's spawnSubAgent helper ultimately calls this
    # MCP tool. We exercise the same path here without booting MCP.
    spawn_raw = server.harness_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="coder",
        role="explorer",
        brief="find callers of FooClass in src/",
    )
    spawn = json.loads(spawn_raw)
    assert spawn["status"] == "spawned", spawn
    handle_id = spawn["handle_id"]

    events = subagent_audit.query_events(parent_session_id=sid)
    assert len(events) == 1
    row = events[0]
    assert row["event"] == "spawned"
    assert row["parent_agent_name"] == "coder"
    assert row["role"] == "explorer"
    assert row["handle_id"] == handle_id


def test_coder_explorer_round_trip_writes_spawn_and_complete(
    audit_db: Path, harness_db: Path,
) -> None:
    """The full lifecycle the dispatcher drives: spawn, then complete
    with the verified summary. The audit DB must show BOTH rows so
    'no silent sub-agents' (Hard Invariant #8) holds for the new
    pipeline-side spawn path too."""
    sid = state.create_session("build a feature", harness_db)
    spawn_raw = server.harness_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="coder",
        role="explorer",
        brief="find callers of FooClass in src/",
    )
    handle_id = json.loads(spawn_raw)["handle_id"]
    complete_raw = server.harness_complete_subagent(
        handle_id=handle_id,
        summary="Found 3 callers in src/foo.py:42, src/bar.py:7, src/baz.py:99.",
        status="done",
        turns=2,
        tools_used=["copilot_searchWorkspace", "copilot_readFile"],
    )
    complete = json.loads(complete_raw)
    assert complete["status"] == "recorded"
    assert complete["final_status"] == "done"

    events = subagent_audit.query_events(parent_session_id=sid)
    assert len(events) == 2
    spawn_row, complete_row = events  # ordered by ts ascending
    assert spawn_row["event"] == "spawned"
    assert complete_row["event"] == "completed"
    assert complete_row["final_status"] == "done"
    # tools_used is already deserialised to a list by query_events.
    tools_used = complete_row["tools_used"] or []
    if isinstance(tools_used, str):  # tolerate raw JSON for forward-compat
        tools_used = json.loads(tools_used)
    assert "copilot_searchWorkspace" in tools_used


def test_reviewer_aux_per_file_round_trip(
    audit_db: Path, harness_db: Path,
) -> None:
    """The reviewer pre-spawn dispatcher fires one reviewer-aux per
    file when a chunk has > 2 modules. Pin that the policy table
    permits the spawn AND the audit trail captures both events."""
    sid = state.create_session("build a feature", harness_db)
    spawn_raw = server.harness_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="reviewer",
        role="reviewer-aux",
        brief="apply the code-review checklist to src/foo.py",
    )
    handle_id = json.loads(spawn_raw)["handle_id"]
    server.harness_complete_subagent(
        handle_id=handle_id,
        summary="src/foo.py: 1 medium issue (missing docstring on main()), no critical findings.",
        status="done",
        turns=1,
    )

    events = subagent_audit.query_events(parent_session_id=sid)
    assert [(e["event"], e["role"]) for e in events] == [
        ("spawned", "reviewer-aux"),
        ("completed", "reviewer-aux"),
    ]


def test_planner_explorer_spawn_denied(
    audit_db: Path, harness_db: Path,
) -> None:
    """planner is NOT on the explorer allow-list (and the dispatcher's
    stage-routing wouldn't fire a spawn from a planner anyway). The
    policy engine must fail-closed if a hypothetical caller tries."""
    sid = state.create_session("build a feature", harness_db)
    raw = server.harness_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="planner",
        role="explorer",
        brief="find callers",
    )
    parsed = json.loads(raw)
    assert parsed["status"] == "error", parsed
