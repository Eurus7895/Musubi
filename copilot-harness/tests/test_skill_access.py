"""Tests for skill access control — allowlist + dynamic injection from plan.required_skills."""

import json

import pytest

import context_builder
import server
import skill_loader
import state
from context_builder import AGENT_SKILL_ALLOWLIST, check_skill_permission


# ── check_skill_permission ────────────────────────────────────────────────────

def test_coder_can_load_python() -> None:
    assert check_skill_permission("coder", "python") is True


def test_coder_can_load_testing() -> None:
    assert check_skill_permission("coder", "testing") is True


def test_coder_cannot_load_devops() -> None:
    assert check_skill_permission("coder", "devops") is False


def test_coder_cannot_load_code_review() -> None:
    assert check_skill_permission("coder", "code-review") is False


def test_reviewer_can_load_code_review() -> None:
    assert check_skill_permission("reviewer", "code-review") is True


def test_reviewer_cannot_load_python() -> None:
    assert check_skill_permission("reviewer", "python") is False


def test_designer_can_load_api_design() -> None:
    assert check_skill_permission("designer", "api-design") is True


def test_designer_cannot_load_testing() -> None:
    assert check_skill_permission("designer", "testing") is False


def test_planner_has_empty_allowlist() -> None:
    for skill in ["python", "api-design", "code-review", "testing"]:
        assert check_skill_permission("planner", skill) is False


def test_skill_builder_has_empty_allowlist() -> None:
    for skill in ["python", "api-design"]:
        assert check_skill_permission("skill-builder", skill) is False


def test_unknown_agent_denied() -> None:
    assert check_skill_permission("unknown-agent", "python") is False


def test_check_is_case_insensitive() -> None:
    assert check_skill_permission("CODER", "python") is True
    assert check_skill_permission("Reviewer", "code-review") is True


# ── harness_get_skill — allowlist enforcement ─────────────────────────────────

def test_unauthorized_agent_cannot_load_skill() -> None:
    result = json.loads(server.harness_get_skill("python", "planner"))
    assert "error" in result
    assert "not permitted" in result["error"].lower()


def test_unauthorized_skill_for_agent_blocked() -> None:
    result = json.loads(server.harness_get_skill("devops", "coder"))
    assert "error" in result
    assert "not permitted" in result["error"].lower()


def test_rejection_includes_allowed_skills_list() -> None:
    result = json.loads(server.harness_get_skill("devops", "coder"))
    assert "allowed_skills" in result
    assert "python" in result["allowed_skills"]


def test_authorized_skill_not_blocked_by_allowlist() -> None:
    # "python" is in coder's allowlist — error should be "not found", not "not permitted"
    result_str = server.harness_get_skill("python", "coder")
    if result_str.startswith("{"):
        result = json.loads(result_str)
        assert "not permitted" not in result.get("error", "").lower()


def test_reviewer_code_review_not_blocked() -> None:
    result_str = server.harness_get_skill("code-review", "reviewer")
    if result_str.startswith("{"):
        result = json.loads(result_str)
        assert "not permitted" not in result.get("error", "").lower()


# ── harness_get_reference — allowlist enforcement ─────────────────────────────

def test_unauthorized_reference_blocked() -> None:
    # code-review is not in coder's allowlist
    result = json.loads(server.harness_get_reference("code-review", "owasp.md", "coder"))
    assert "error" in result
    assert "not permitted" in result["error"].lower()


def test_authorized_reference_passes_allowlist() -> None:
    # python IS in coder's allowlist — if ref doesn't exist, error is about the ref, not permissions
    result = json.loads(server.harness_get_reference("python", "nonexistent.md", "coder"))
    assert "not permitted" not in result.get("error", "").lower()


# ── dynamic injection: plan.required_skills ───────────────────────────────────

def _fake_get_skill(skill_id: str) -> str | None:
    known = {"python", "testing", "api-design", "database-patterns", "code-review", "documentation"}
    return f"# {skill_id} skill content" if skill_id in known else None


def _make_read_stage(plan_extra: dict | None = None) -> object:
    """Return a fake state.read_stage that returns controlled plan data."""
    plan: dict = {"summary": "x", "tasks": []}
    if plan_extra:
        plan.update(plan_extra)

    def _read(session_id: str, stage: str, db_path: object = None) -> dict | None:
        if stage == "plan":
            return plan
        if stage == "design":
            return {"summary": "d", "tasks_addressed": [], "modules": []}
        return None

    return _read


def _patch_server(monkeypatch: pytest.MonkeyPatch, plan_extra: dict | None = None) -> None:
    monkeypatch.setattr(state, "read_stage", _make_read_stage(plan_extra))
    monkeypatch.setattr(state, "mark_in_progress", lambda *a, **k: None)
    monkeypatch.setattr(skill_loader, "get_skill", _fake_get_skill)
    monkeypatch.setattr(
        context_builder, "read_stage_for_agent",
        lambda *a, **k: {"summary": "design output"},
    )


def test_required_skill_in_allowlist_is_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    """plan.required_skills injects the skill when the agent is allowed to load it."""
    _patch_server(monkeypatch, {"required_skills": ["database-patterns"]})
    result = json.loads(server.harness_read_stage("sess1", "design", "coder"))
    assert "database-patterns" in result.get("injected_skills", {})


def test_required_skill_not_in_allowlist_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """plan.required_skills does NOT inject a skill the agent cannot load."""
    _patch_server(monkeypatch, {"required_skills": ["devops"]})
    result = json.loads(server.harness_read_stage("sess2", "design", "coder"))
    assert "devops" not in result.get("injected_skills", {})


def test_static_map_skill_injected_alongside_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Static map floor is kept; required_skills adds on top."""
    _patch_server(monkeypatch, {"required_skills": ["testing"]})
    result = json.loads(server.harness_read_stage("sess3", "design", "coder"))
    injected = result.get("injected_skills", {})
    assert "python" in injected    # static map for (design, coder)
    assert "testing" in injected   # added via required_skills


def test_no_required_skills_falls_back_to_static_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without required_skills only static map skills are injected."""
    _patch_server(monkeypatch)  # no required_skills
    result = json.loads(server.harness_read_stage("sess4", "design", "coder"))
    assert "python" in result.get("injected_skills", {})


# ── verifier: required_skills is optional and validated as list ───────────────

def test_planner_output_with_required_skills_passes_validation() -> None:
    import verifier
    output = {
        "summary": "build login",
        "tasks": [],
        "required_skills": ["python", "testing"],
    }
    result = verifier.validate(output, "planner")
    assert result.valid


def test_planner_output_required_skills_wrong_type_fails() -> None:
    import verifier
    output = {
        "summary": "build login",
        "tasks": [],
        "required_skills": "python",  # string, not list
    }
    result = verifier.validate(output, "planner")
    assert not result.valid
    assert any("required_skills" in e for e in result.errors)


def test_planner_output_without_required_skills_still_valid() -> None:
    import verifier
    output = {"summary": "build login", "tasks": []}
    result = verifier.validate(output, "planner")
    assert result.valid
