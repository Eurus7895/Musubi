"""Tests for root-selected skill injection into workers (option 3).

musubi-tier: substrate test — pins the option-3 contract:
  - the root can LIST a *worker role's* skills via `for_role`;
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

# ── list_skills honours for_role ───────────────────────────────────────────


def test_list_for_coder_role_surfaces_coder_skills() -> None:
    """The root asks for the coder's catalog; the set is drawn from the coder
    allowlist, not the root's own."""
    payload = json.loads(server.musubi_list_skills("agent", for_role="coder"))
    assert payload["for_role"] == "coder"
    ids = {r["skill_id"] for r in payload["skills"]}
    # typescript is coder-only; the root's own allowlist could never surface it.
    assert ids <= AGENT_SKILL_ALLOWLIST["coder"]
    assert "typescript" in ids

    web_ui = next(row for row in payload["skills"] if row["skill_id"] == "web-ui")
    assert web_ui["version"] == "1.0.0"
    assert web_ui["content_hash"].startswith("sha256:")
    assert web_ui["completion_contract"]["required_check_types"] == [
        "file_created_or_modified",
    ]


def test_list_unknown_role_returns_nothing() -> None:
    """Fail-closed: an unknown role has no allowlist entry, so it gets no
    catalog rather than the caller's."""
    payload = json.loads(server.musubi_list_skills(
        "agent", for_role="nonexistent-role",
    ))
    assert payload["skills"] == []


def test_list_without_for_role_uses_caller_allowlist() -> None:
    payload = json.loads(server.musubi_list_skills("agent"))
    ids = {r["skill_id"] for r in payload["skills"]}
    assert ids <= AGENT_SKILL_ALLOWLIST["root"]


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
    assert "not permitted" in out["error"] or "not a candidate" in out["error"]


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
    assert "not permitted" in out["error"] or "not a candidate" in out["error"]


def test_spawn_accepts_a_permitted_skill_without_any_ticket(
    parent_session,
) -> None:
    """The recommendation ticket is gone. It constrained WHERE the root got a
    name, never WHICH names are legal — the allowlist and catalog checks below
    answer that on their own, and the ticket cost a turn when its id was
    confused with the skill id."""
    sid, db_path = parent_session
    out = json.loads(server.musubi_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="root",
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


def test_context_builder_rejects_an_omitted_model_choice() -> None:
    with pytest.raises(ValueError, match="model-selected skill"):
        subagent_context.build_subagent_context(brief="x", role="explorer")


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
    assert ctx["role_skill_id"] == "typescript"


# ── the push is auditable, override or not (HI #2 push, HI #8 no silence) ──


def test_context_names_the_skill_it_pushed_not_only_its_text() -> None:
    """`role_skill` is prose; nothing downstream could say WHICH skill it is.

    Every consumer — the runtime log, the audit ledger, the console's Skills
    panel — needs the id. Without it a role-default push was invisible, and a
    session that pushed a skill to every worker still read "No successful
    skill calls recorded".
    """
    explorer = subagent_context.build_subagent_context(
        brief="x", role="explorer", pushed_skill_id="explorer",
    )
    assert explorer.role_skill_id == "explorer"


def _audited_push(handle_id: str, audit_db: Path) -> str | None:
    from storage import subagent_audit

    rows = [
        row for row in subagent_audit.query_events(
            handle_id=handle_id, db_path=audit_db,
        )
        if row["event"] == "spawned"
    ]
    assert len(rows) == 1
    return rows[0]["pushed_skill_id"]


def test_spawn_requires_the_models_explicit_skill_choice(
    parent_session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, _ = parent_session
    audit_db = tmp_path / "spawn-audit.db"
    monkeypatch.setenv("MUSUBI_AUDIT_DB", str(audit_db))

    out = json.loads(server.musubi_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="agent",
        role="explorer",
        brief="find where the loop dispatches tools",
    ))
    assert out["status"] == "error"
    assert out["error_kind"] == "policy_denied"
    assert "model-selected skill" in out["error"]


def test_spawn_audits_the_exact_model_selected_skill(
    parent_session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, _ = parent_session
    audit_db = tmp_path / "spawn-audit.db"
    monkeypatch.setenv("MUSUBI_AUDIT_DB", str(audit_db))

    out = json.loads(server.musubi_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="agent",
        role="coder",
        brief="write the page",
        pushed_skill_id="web-ui",
    ))
    assert out["status"] == "spawned"
    assert _audited_push(out["handle_id"], audit_db) == "web-ui"


def test_spawn_audit_failure_abandons_worker_and_keeps_outbox(
    parent_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, db_path = parent_session

    def fail_delivery(*args, **kwargs):  # noqa: ANN002, ANN003
        raise OSError("audit disk unavailable")

    monkeypatch.setattr(
        server.subagent_audit, "deliver_spawn_obligation", fail_delivery,
    )
    out = json.loads(server.musubi_spawn_subagent(
        parent_session_id=sid,
        parent_agent_name="agent",
        role="coder",
        brief="write the page",
        pushed_skill_id="web-ui",
    ))

    assert out["status"] == "error"
    assert out["error_kind"] == "audit_unavailable"
    from storage import db
    worker = db.get_sub_session(out["handle_id"], db_path)
    assert worker is not None and worker["status"] == "abandoned"
    pending = db.get_audit_obligations(status="pending", db_path=db_path)
    assert [row["handle_id"] for row in pending] == [out["handle_id"]]


# ── skill selection is available to the root in every scope ────────────────


def _tools(names: list[str]) -> list[dict]:
    return [{"name": n} for n in names]


def test_simple_scope_root_sees_spawn_and_listing() -> None:
    """The headline fix: a simple_artifact root can still select a skill."""
    state = GoalState.create("build dashboard", "simple_artifact", "single_coder")
    state.begin_direct(
        target_intent="create",
        target_path="dashboard.html",
        target_exists=False,
        worker_role="coder",
    )
    tools = _tools([
        "musubi_spawn_subagent",
        "musubi_list_skills",
        "musubi_get_skill",
        "musubi_get_reference",
        "musubi_write_file",
    ])
    visible = {t["name"] for t in root_decision_tools(tools, state)}
    assert "musubi_spawn_subagent" in visible
    assert "musubi_list_skills" in visible
    # Content-loading skill tools stay out of a simple-scope root turn.
    assert "musubi_get_skill" not in visible
    # And non-skill mutation tools never reach the root decision surface.
    assert "musubi_write_file" not in visible


def test_broad_scope_root_also_sees_skill_read_tools() -> None:
    state = GoalState.create("multi-surface change", "medium_change", "planner_then_coder_check")
    state.begin_plan()
    from agent.manifest import parse_change_manifest_object
    manifest = parse_change_manifest_object({
        "files_expected": 4,
        "subsystems": ["agent", "storage"],
    })
    assert manifest is not None
    state.commit_root_plan(
        manifest=manifest,
        change_size="medium",
        worker_chain=("coder", "reviewer"),
        planning_artifacts=("plan.md", "manifest.json"),
    )
    tools = _tools([
        "musubi_spawn_subagent",
        "musubi_list_skills",
        "musubi_get_skill",
        "musubi_get_reference",
    ])
    visible = {t["name"] for t in root_decision_tools(tools, state)}
    assert {"musubi_list_skills", "musubi_get_skill", "musubi_get_reference"} <= visible


# ── the worker can say the pushed skill does not fit (HI #2 stays intact) ──


def test_report_skill_mismatch_records_the_pushed_skill_and_suggestion(
    parent_session,
) -> None:
    """A worker states the mismatch; the harness decides the statement is
    well-formed and echoes back WHAT was pushed. The worker never names the
    pushed skill itself — that fact comes from the spawn row."""
    sid, _ = parent_session
    spawn = json.loads(server.musubi_spawn_subagent(
        parent_session_id=sid, parent_agent_name="root", role="coder",
        brief="build a weather app", pushed_skill_id="web-ui",
    ))
    out = json.loads(server.musubi_report_skill_mismatch(
        handle_id=spawn["handle_id"],
        reason="the skill forbids external requests; the app must fetch live data",
        suggested_skill_id="typescript",
    ))
    assert out["status"] == "recorded"
    assert out["role"] == "coder"
    assert out["pushed_skill_id"] == "web-ui"
    assert out["suggested_skill_id"] == "typescript"
    assert "external requests" in out["reason"]


def test_report_skill_mismatch_is_not_a_self_service_skill_swap(
    parent_session,
) -> None:
    """The suggestion passes the SAME firewall a spawn does. A worker that
    could name any skill here would have found a way to widen its own
    contract from inside the sandbox."""
    sid, _ = parent_session
    spawn = json.loads(server.musubi_spawn_subagent(
        parent_session_id=sid, parent_agent_name="root", role="coder",
        brief="build a weather app", pushed_skill_id="web-ui",
    ))
    out = json.loads(server.musubi_report_skill_mismatch(
        handle_id=spawn["handle_id"], reason="wrong fit",
        suggested_skill_id="pr-scope-detection",  # reviewer-side, not coder's
    ))
    assert out["status"] == "error"
    assert out["error_kind"] == "policy_denied"
    assert "pr-scope-detection" not in out["allowed_skills"]

    unknown = json.loads(server.musubi_report_skill_mismatch(
        handle_id=spawn["handle_id"], reason="wrong fit",
        suggested_skill_id="weather-api",
    ))
    assert unknown["status"] == "error"


def test_report_skill_mismatch_requires_a_running_worker_and_a_reason(
    parent_session,
) -> None:
    """Fail closed on both ends: an unknown handle, and an empty reason that
    would put an unexplained mismatch in front of the root."""
    sid, _ = parent_session
    spawn = json.loads(server.musubi_spawn_subagent(
        parent_session_id=sid, parent_agent_name="root", role="coder",
        brief="build a weather app", pushed_skill_id="web-ui",
    ))
    assert json.loads(
        server.musubi_report_skill_mismatch("no-such-handle", "x")
    )["error_kind"] == "unknown_handle"
    assert json.loads(
        server.musubi_report_skill_mismatch(spawn["handle_id"], "   ")
    )["error_kind"] == "invalid_reason"

    from session import sub_sessions
    sub_sessions.complete(spawn["handle_id"], summary="done", turns=1)
    assert json.loads(
        server.musubi_report_skill_mismatch(spawn["handle_id"], "too late")
    )["error_kind"] == "not_running"


def test_every_worker_role_can_reach_the_mismatch_report() -> None:
    """Including a role whose capabilities map to no tools at all. A tool a
    worker cannot address is a tool it does not have."""
    from agent.boundary import evaluate_tool_call
    from agent.subagent import select_child_tools

    catalog = [
        {"name": "musubi_read_file"}, {"name": "musubi_write_file"},
        {"name": "musubi_report_skill_mismatch"},
    ]
    summarizer_surface = {t["name"] for t in select_child_tools(catalog, [])}
    assert summarizer_surface == {"musubi_report_skill_mismatch"}

    for role in ("coder", "planner", "summarizer", "explorer"):
        assert evaluate_tool_call(role, "musubi_report_skill_mismatch").allowed


def test_worker_prompt_names_the_handle_the_report_needs() -> None:
    """The escape hatch is unreachable without the worker's own handle_id, so
    the prompt that advertises it must also supply it."""
    from agent.subagent import build_subagent_system_prompt

    prompt = build_subagent_system_prompt(
        "---\nname: Coder\n---\nrole body", "skill body", "the brief",
        handle_id="abc123",
    )
    assert "musubi_report_skill_mismatch" in prompt
    assert "abc123" in prompt

    # No skill pushed → nothing to mismatch, so no instruction is added.
    assert "musubi_report_skill_mismatch" not in build_subagent_system_prompt(
        "---\nname: Coder\n---\nrole body", None, "the brief",
        handle_id="abc123",
    )
