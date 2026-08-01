"""Tests for the skill-catalog growth track.

musubi-tier: substrate test — pins that the new catalog entries
(debugging, refactoring, git-workflow, typescript, web-ui) load, parse,
declare sensible metadata (applies-to / description / tools), reach the
correct agents through the allowlist firewall (HI #3), and surface in the
catalog listing the model chooses from — without widening access.
"""

from __future__ import annotations

import json

import pytest

import server
from skills import skill_loader
from validation.context_builder import (
    AGENT_SKILL_ALLOWLIST,
    check_skill_permission,
)

NEW_SKILLS = ["debugging", "refactoring", "git-workflow", "typescript", "web-ui"]


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


def test_new_skills_carry_a_description() -> None:
    """The description IS the selection surface now.

    Nothing ranks the catalog any more — the model reads
    `musubi_list_skills(for_role=…)` and chooses. A skill with no
    description is therefore a skill the model cannot tell apart from any
    other, so this is load-bearing metadata rather than nice-to-have."""
    metas = {m.skill_id: m for m in skill_loader.list_skills()}
    for sid in NEW_SKILLS:
        assert metas[sid].description, f"{sid} declares no description"
        assert len(metas[sid].description) > 30, f"{sid} description too thin"


# ── applies-to declarations ────────────────────────────────────────────────


def test_typescript_scoped_to_ts_js() -> None:
    metas = {m.skill_id: m for m in skill_loader.list_skills()}
    assert metas["typescript"].applies_to == {
        "languages": ["typescript", "javascript"],
    }


def test_procedure_skills_are_universal() -> None:
    """debugging / refactoring / git-workflow / web-ui apply in any
    workspace — no applies-to constraint. web-ui is deliberately universal
    (not JS/TS-scoped) so an HTML artifact emitted from a Python repo still
    matches it."""
    metas = {m.skill_id: m for m in skill_loader.list_skills()}
    for sid in ["debugging", "refactoring", "git-workflow", "web-ui"]:
        assert metas[sid].applies_to is None, (
            f"{sid} unexpectedly declares applies-to: {metas[sid].applies_to}"
        )


# ── Allowlist wiring (HI #3) ───────────────────────────────────────────────


def test_coder_gains_generator_side_skills() -> None:
    coder = AGENT_SKILL_ALLOWLIST["coder"]
    assert {
        "typescript", "debugging", "refactoring", "git-workflow", "web-ui",
    } <= coder


def test_agent_gains_dispatcher_safe_skills_only() -> None:
    """The agent (dispatcher) may pull read-only, non-authoring skills.
    debugging + git-workflow are answerable with read tools; the
    authoring skills (refactoring/typescript) stay coder-only so the
    dispatcher boundary from test_agent_context stays intact."""
    agent = AGENT_SKILL_ALLOWLIST["root"]
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
    assert AGENT_SKILL_ALLOWLIST["root"] >= {
        "agent-routing", "docs-writing", "research",
    }


# ── End-to-end catalog surfacing ───────────────────────────────────────────


def test_coder_catalog_surfaces_new_skills(monkeypatch) -> None:
    """No profile → router is a no-op, so all allowlisted skills show,
    including the language-scoped typescript entry."""
    monkeypatch.setattr(server, "_load_project_profile", lambda: None)
    payload = json.loads(server.musubi_list_skills("coder"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert {
        "debugging", "refactoring", "git-workflow", "typescript", "web-ui",
    } <= ids


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
    # web-ui is universal, so it survives in a Python workspace — that is the
    # whole point: a dashboard emitted from a Python repo can still match it.
    assert {"debugging", "refactoring", "git-workflow", "web-ui"} <= ids


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


# ── The catalog listing carries what the model chooses on ─────────────────


def test_listing_respects_the_role_allowlist() -> None:
    """`musubi_list_skills` lists only skills the role may actually receive —
    the same firewall the ranker sat behind (HI #3)."""
    payload = json.loads(server.musubi_list_skills("root", for_role="coder"))
    ids = {item["skill_id"] for item in payload["skills"]}
    assert ids <= AGENT_SKILL_ALLOWLIST["coder"]
    assert {"refactoring", "debugging", "web-ui"} <= ids
    assert payload["for_role"] == "coder"


def test_listing_gives_the_model_a_description_to_choose_on() -> None:
    """Every listed entry carries the one line the choice is made from, and
    never a skill body — a catalog listing is not a skill dump."""
    payload = json.loads(server.musubi_list_skills("root", for_role="coder"))
    for item in payload["skills"]:
        assert item["description"], f"{item['skill_id']} listed with no description"
        assert "## Procedure" not in item["description"]


def test_the_ranker_is_gone() -> None:
    """It scored the request text to decide what a task was ABOUT, which is a
    judgement the substrate is not entitled to make — and it made it badly:
    request and conversation summary were scored as one bag of text, so a
    request to change the display language drew `web-ui` at 0.99 confidence
    off the back of an earlier dashboard. The model chooses now."""
    assert not hasattr(server, "musubi_recommend_skills")
    with pytest.raises(ModuleNotFoundError):
        __import__("skills.recommender")
