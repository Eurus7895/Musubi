"""Tests for the first non-coding skills — MVP item 9 / Track D.9.

musubi-tier: substrate test — pins that the new skills are discoverable
via the loader, parse cleanly, declare sensible applies-to, and reach
the agent (agent) through the allowlist.
"""

from __future__ import annotations

import json

import server
from skills import skill_loader
from validation.context_builder import AGENT_SKILL_ALLOWLIST, check_skill_permission


def test_docs_writing_skill_loads() -> None:
    content = skill_loader.get_skill("docs-writing")
    assert content is not None
    assert "## Procedure" in content


def test_research_skill_loads() -> None:
    content = skill_loader.get_skill("research")
    assert content is not None
    assert "## Procedure" in content


def test_docs_writing_declares_doc_tool_applies_to() -> None:
    metas = {m.skill_id: m for m in skill_loader.list_skills()}
    docs = metas["docs-writing"]
    assert docs.applies_to is not None
    assert "doc_tools" in docs.applies_to
    assert set(docs.applies_to["doc_tools"]) >= {"sphinx", "mkdocs"}


def test_research_is_universal() -> None:
    """Research applies anywhere — no `applies-to` declaration."""
    metas = {m.skill_id: m for m in skill_loader.list_skills()}
    assert metas["research"].applies_to is None


def test_agent_can_load_research() -> None:
    assert check_skill_permission("agent", "research") is True


def test_agent_can_load_docs_writing() -> None:
    assert check_skill_permission("agent", "docs-writing") is True


def test_designer_can_load_docs_writing() -> None:
    assert check_skill_permission("designer", "docs-writing") is True


def test_coder_cannot_load_research_or_docs_writing() -> None:
    """Coder allowlist stays code-focused — non-coding skills are
    agent/designer territory."""
    assert check_skill_permission("coder", "research") is False
    assert check_skill_permission("coder", "docs-writing") is False


def test_existing_allowlist_entries_unchanged() -> None:
    """Regression: adding two skills must not silently drop existing ones."""
    assert AGENT_SKILL_ALLOWLIST["agent"] >= {"agent-routing"}
    assert AGENT_SKILL_ALLOWLIST["designer"] >= {
        "api-design", "database-patterns", "documentation",
    }


def test_agent_catalog_surfaces_new_skills() -> None:
    """End-to-end: musubi_list_skills('agent') exposes both new
    skills to the agent. Router-level profile filtering is exercised
    in tests/test_skill_router.py; the next three tests cover its
    interaction with the new skills."""
    payload = json.loads(server.musubi_list_skills("agent"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert "research" in ids
    assert "docs-writing" in ids
    # The original entry stays — adding two skills must not displace the
    # routing skill the agent is pushed at startup.
    assert "agent-routing" in ids


def test_agent_catalog_no_profile_keeps_both_skills(monkeypatch) -> None:
    """Router degrades to no-op when no profile is loaded; both new
    skills must surface regardless. Guards the router from ever
    over-filtering when the workspace detector hasn't run."""
    monkeypatch.setattr(server, "_load_project_profile", lambda: None)
    payload = json.loads(server.musubi_list_skills("agent"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert payload["filtered_by_profile"] is False
    assert "research" in ids
    assert "docs-writing" in ids


def test_agent_catalog_rust_profile_drops_docs_writing(monkeypatch) -> None:
    """Headline router behaviour for non-coding skills:
    docs-writing is doc_tools-scoped (sphinx/mkdocs/mdbook), so a Rust
    workspace with no doc tool drops it. research stays — it has no
    applies-to and is universally applicable."""
    monkeypatch.setattr(server, "_load_project_profile", lambda: {
        "language": "rust",
        "secondary_languages": [],
        "test_framework": None,
        "doc_tool": None,
        "package_managers": ["cargo"],
        "file_types_present": [".rs"],
    })
    payload = json.loads(server.musubi_list_skills("agent"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert payload["filtered_by_profile"] is True
    assert "docs-writing" not in ids
    assert "research" in ids


def test_agent_catalog_sphinx_profile_keeps_docs_writing(monkeypatch) -> None:
    """Symmetric guard: a workspace WITH a matching doc tool keeps
    docs-writing. Pins that the router's positive case still passes
    through, not just the negative case above."""
    monkeypatch.setattr(server, "_load_project_profile", lambda: {
        "language": "python",
        "secondary_languages": [],
        "test_framework": "pytest",
        "doc_tool": "sphinx",
        "package_managers": ["pip"],
        "file_types_present": [".py"],
    })
    payload = json.loads(server.musubi_list_skills("agent"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert "docs-writing" in ids
    assert "research" in ids
