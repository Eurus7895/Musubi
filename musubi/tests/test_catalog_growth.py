"""Tests for the skill-catalog growth track.

musubi-tier: substrate test — pins that the new catalog entries
(debugging, refactoring, git-workflow, typescript) load, parse, declare
sensible metadata (applies-to / triggers / tools), reach the correct
agents through the allowlist firewall (HI #3), and surface via the
recommender without widening access.
"""

from __future__ import annotations

import json

import server
from skills import skill_loader
from skills.recommender import recommend_skills
from validation.context_builder import (
    AGENT_SKILL_ALLOWLIST,
    check_skill_permission,
)

NEW_SKILLS = ["debugging", "refactoring", "git-workflow", "typescript"]


# ── Every new skill loads and has the required section + tier tag ──────────


def test_new_skills_load_with_procedure() -> None:
    for sid in NEW_SKILLS:
        content = skill_loader.get_skill(sid)
        assert content is not None, f"{sid} missing from catalog"
        assert "## Procedure" in content, f"{sid} lacks a Procedure section"


def test_new_skills_declare_substrate_tier() -> None:
    """Every catalog entry carries a musubi-tier tag (HI #9)."""
    for sid in NEW_SKILLS:
        content = skill_loader.get_skill(sid) or ""
        assert "musubi-tier: substrate" in content, f"{sid} untagged"


def test_new_skills_carry_triggers() -> None:
    """Catalog growth entries ship recommender metadata so the skill
    router can rank them (roadmap: 'each new skill should carry useful
    metadata such as applies-to, triggers, and relevant tools')."""
    metas = {m.skill_id: m for m in skill_loader.list_skills()}
    for sid in NEW_SKILLS:
        assert metas[sid].triggers, f"{sid} declares no triggers"


# ── applies-to declarations ────────────────────────────────────────────────


def test_typescript_scoped_to_ts_js() -> None:
    metas = {m.skill_id: m for m in skill_loader.list_skills()}
    assert metas["typescript"].applies_to == {
        "languages": ["typescript", "javascript"],
    }


def test_procedure_skills_are_universal() -> None:
    """debugging / refactoring / git-workflow apply in any workspace —
    they are language-agnostic procedures, so no applies-to constraint."""
    metas = {m.skill_id: m for m in skill_loader.list_skills()}
    for sid in ["debugging", "refactoring", "git-workflow"]:
        assert metas[sid].applies_to is None, (
            f"{sid} unexpectedly declares applies-to: {metas[sid].applies_to}"
        )


# ── Allowlist wiring (HI #3) ───────────────────────────────────────────────


def test_coder_gains_generator_side_skills() -> None:
    coder = AGENT_SKILL_ALLOWLIST["coder"]
    assert {"typescript", "debugging", "refactoring", "git-workflow"} <= coder


def test_agent_gains_dispatcher_safe_skills_only() -> None:
    """The agent (dispatcher) may pull read-only, non-authoring skills.
    debugging + git-workflow are answerable with read tools; the
    authoring skills (refactoring/typescript) stay coder-only so the
    dispatcher boundary from test_agent_context stays intact."""
    agent = AGENT_SKILL_ALLOWLIST["agent"]
    assert {"debugging", "git-workflow"} <= agent
    assert "refactoring" not in agent
    assert "typescript" not in agent


def test_permission_checks_for_new_skills() -> None:
    assert check_skill_permission("coder", "typescript") is True
    assert check_skill_permission("coder", "debugging") is True
    assert check_skill_permission("agent", "debugging") is True
    assert check_skill_permission("agent", "git-workflow") is True
    # Firewall still denies out-of-role loads.
    assert check_skill_permission("agent", "typescript") is False
    assert check_skill_permission("reviewer", "refactoring") is False
    assert check_skill_permission("planner", "debugging") is False


def test_existing_allowlist_entries_preserved() -> None:
    """Regression: growth must not drop prior grants."""
    assert AGENT_SKILL_ALLOWLIST["coder"] >= {
        "python", "testing", "database-patterns", "api-design",
    }
    assert AGENT_SKILL_ALLOWLIST["agent"] >= {
        "agent-routing", "docs-writing", "research",
    }


# ── End-to-end catalog surfacing ───────────────────────────────────────────


def test_coder_catalog_surfaces_new_skills(monkeypatch) -> None:
    """No profile → router is a no-op, so all allowlisted skills show,
    including the language-scoped typescript entry."""
    monkeypatch.setattr(server, "_load_project_profile", lambda: None)
    payload = json.loads(server.musubi_list_skills("coder"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert {"debugging", "refactoring", "git-workflow", "typescript"} <= ids


def test_typescript_dropped_in_python_only_workspace(monkeypatch) -> None:
    """Router headline behaviour: a Python-only workspace with no JS/TS
    signal drops the typescript skill; the universal procedures stay."""
    monkeypatch.setattr(server, "_load_project_profile", lambda: {
        "language": "python",
        "secondary_languages": [],
        "test_framework": "pytest",
        "doc_tool": None,
        "package_managers": ["pip"],
        "file_types_present": [".py"],
    })
    payload = json.loads(server.musubi_list_skills("coder"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert "typescript" not in ids
    assert {"debugging", "refactoring", "git-workflow"} <= ids


def test_typescript_kept_in_ts_workspace(monkeypatch) -> None:
    monkeypatch.setattr(server, "_load_project_profile", lambda: {
        "language": "typescript",
        "secondary_languages": [],
        "test_framework": "jest",
        "doc_tool": None,
        "package_managers": ["npm"],
        "file_types_present": [".ts", ".tsx"],
    })
    payload = json.loads(server.musubi_list_skills("coder"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert "typescript" in ids


# ── Recommender ranks the new skills without widening access ───────────────


def test_recommender_surfaces_debugging_by_trigger() -> None:
    metas = [m for m in skill_loader.list_skills() if m.skill_id in NEW_SKILLS]
    out = recommend_skills(
        "There is a traceback and the test is flaky; find the root cause.",
        metas,
    )
    assert out, "expected at least one recommendation"
    assert out[0].skill_id == "debugging"


def test_recommend_skills_tool_respects_coder_allowlist() -> None:
    """musubi_recommend_skills ranks only skills the caller may load."""
    payload = json.loads(server.musubi_recommend_skills(
        "rename this function and extract the duplicated block",
        "coder",
    ))
    ids = {r["skill_id"] for r in payload["recommended"]}
    coder = AGENT_SKILL_ALLOWLIST["coder"]
    assert ids <= coder
    assert "refactoring" in ids
