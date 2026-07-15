"""Tests for root-selected skill injection into workers (option 3).

musubi-tier: substrate test — pins the option-3 contract:
  - the root can rank a *worker role's* skills via `for_role`;
  - a validated `pushed_skill_id` threads spawn → DB row → subagent context
    and lands as the worker's `role_skill`;
  - the spawn firewall (HI #3/#5) rejects a skill outside the worker role's
    allowlist and an unknown skill, fail-closed;
  - skill *selection* is available to the root in every scope, including
    simple artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import server
from agent.goal_state import GoalState, root_decision_tools
from skills import skill_loader
from validation import subagent_context
from validation.context_builder import AGENT_SKILL_ALLOWLIST

# ── recommend_skills honours for_role ──────────────────────────────────────


def test_recommend_for_coder_role_surfaces_coder_skills() -> None:
    """The root (agent) asks for coder skills; the ranked set is drawn from
    the coder allowlist, not the agent's own."""
    payload = json.loads(server.musubi_recommend_skills(
        "write a typescript react dashboard component",
        "agent",
        for_role="coder",
    ))
    assert payload["for_role"] == "coder"
    ids = {r["skill_id"] for r in payload["recommended"]}
    # typescript is coder-only; the agent's own allowlist could never surface it.
    assert ids <= AGENT_SKILL_ALLOWLIST["coder"]
    assert "typescript" in ids


def test_recommend_unknown_role_returns_nothing() -> None:
    payload = json.loads(server.musubi_recommend_skills(
        "anything", "agent", for_role="nonexistent-role",
    ))
    assert payload["recommended"] == []


def test_recommend_without_for_role_uses_caller_allowlist() -> None:
    """Back-compat: omitting for_role ranks the caller's own skills."""
    payload = json.loads(server.musubi_recommend_skills(
        "why does this traceback fail at root cause", "agent",
    ))
    ids = {r["skill_id"] for r in payload["recommended"]}
    assert ids <= AGENT_SKILL_ALLOWLIST["agent"]


# ── spawn validation (fail-closed) ─────────────────────────────────────────


@pytest.fixture
def parent_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real parent session + isolated DB so spawn's FK check passes."""
    from session import state
    from storage import db as storage_db

    db_path = tmp_path / "audit.db"
    storage_db.init_db(db_path)
    # Route every default-db call in this test through the tmp DB.
    monkeypatch.setattr(storage_db, "DEFAULT_DB_PATH", db_path)
    sid = state.create_session("build a dashboard", db_path=db_path)
    return sid, db_path


def test_spawn_rejects_skill_outside_role_allowlist(parent_session) -> None:
    sid, _ = parent_session
    out = json.loads(server.musubi_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="agent",
        role="coder",
        brief="build the page",
        pushed_skill_id="agent-routing",  # not in coder's allowlist
    ))
    assert out["status"] == "error"
    assert "not permitted" in out["error"]


def test_spawn_rejects_unknown_skill(parent_session) -> None:
    sid, _ = parent_session
    out = json.loads(server.musubi_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="agent",
        role="coder",
        brief="build the page",
        pushed_skill_id="typescript-but-typoed",
    ))
    assert out["status"] == "error"
    # Rejected at the allowlist gate before catalog lookup.
    assert "not permitted" in out["error"] or "not found" in out["error"]


def test_spawn_accepts_valid_pushed_skill_and_stores_it(parent_session) -> None:
    sid, db_path = parent_session
    out = json.loads(server.musubi_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="agent",
        role="coder",
        brief="build the typescript page",
        pushed_skill_id="typescript",
    ))
    assert out["status"] == "spawned"
    assert out["pushed_skill_id"] == "typescript"

    from session import sub_sessions
    row = sub_sessions.get(out["handle_id"], db_path=db_path)
    assert row["pushed_skill_id"] == "typescript"


# ── subagent context threads the pushed skill as role_skill ────────────────


def test_context_builder_pushes_root_selected_skill() -> None:
    ctx = subagent_context.build_subagent_context(
        brief="build the page",
        role="coder",
        pushed_skill_id="typescript",
    )
    assert ctx.role_skill is not None
    assert ctx.role_skill == skill_loader.get_skill("typescript")


def test_context_builder_defaults_to_native_role_skill() -> None:
    """No pushed skill → the role's native SUBAGENT_ROLE_SKILLS push (or None
    for coder, which has no native skill)."""
    coder = subagent_context.build_subagent_context(brief="x", role="coder")
    assert coder.role_skill is None
    explorer = subagent_context.build_subagent_context(brief="x", role="explorer")
    assert explorer.role_skill == skill_loader.get_skill("explorer")


def test_context_builder_pushed_skill_overrides_native() -> None:
    """When both exist, the root's explicit choice wins."""
    ctx = subagent_context.build_subagent_context(
        brief="x", role="reviewer-aux", pushed_skill_id="reviewer-aux",
    )
    assert ctx.role_skill == skill_loader.get_skill("reviewer-aux")


def test_get_subagent_context_tool_surfaces_pushed_skill(parent_session) -> None:
    sid, _ = parent_session
    spawn = json.loads(server.musubi_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="agent",
        role="coder",
        brief="build the typescript page",
        pushed_skill_id="typescript",
    ))
    ctx = json.loads(server.musubi_get_subagent_context(spawn["handle_id"]))
    assert ctx["status"] == "ok"
    assert ctx["role_skill"] == skill_loader.get_skill("typescript")


# ── skill selection is available to the root in every scope ────────────────


def _tools(names: list[str]) -> list[dict]:
    return [{"name": n} for n in names]


def test_simple_scope_root_sees_spawn_and_recommend() -> None:
    """The headline fix: a simple_artifact root can still select a skill."""
    state = GoalState.create("build dashboard", "simple_artifact", "single_coder")
    tools = _tools([
        "musubi_spawn_subagent",
        "musubi_recommend_skills",
        "musubi_get_skill",
        "musubi_get_reference",
        "musubi_write_file",
    ])
    visible = {t["name"] for t in root_decision_tools(tools, state)}
    assert "musubi_spawn_subagent" in visible
    assert "musubi_recommend_skills" in visible
    # Content-loading skill tools stay out of a simple-scope root turn.
    assert "musubi_get_skill" not in visible
    # And non-skill mutation tools never reach the root decision surface.
    assert "musubi_write_file" not in visible


def test_broad_scope_root_also_sees_skill_read_tools() -> None:
    state = GoalState.create("multi-surface change", "medium_change", "planner_then_coder_check")
    tools = _tools([
        "musubi_spawn_subagent",
        "musubi_recommend_skills",
        "musubi_get_skill",
        "musubi_get_reference",
    ])
    visible = {t["name"] for t in root_decision_tools(tools, state)}
    assert {"musubi_recommend_skills", "musubi_get_skill", "musubi_get_reference"} <= visible
