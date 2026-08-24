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


def test_docs_writing_is_universal_because_prose_needs_no_doc_generator() -> None:
    """`docs-writing` used to declare `doc_tools: [sphinx, mkdocs, mdbook]`.

    The gate asked about TOOLING for a skill whose deliverable is PROSE. A repo
    whose docs are plain Markdown — which includes this one — reports
    `doc_tool: null`, and an empty workspace value makes any skill declaring
    that dimension fail to match (`skills/router.py:44-47`). So "write a design
    doc" lost its skill in exactly the repos that write docs by hand. Whether a
    README needs writing does not depend on whether Sphinx is installed.
    """
    metas = {m.skill_id: m for m in skill_loader.list_skills()}
    assert metas["docs-writing"].applies_to is None


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
    assert AGENT_SKILL_ALLOWLIST["root"] >= {"agent-routing"}
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


def test_rust_profile_drops_language_scoped_skills_but_keeps_universal_ones(
    monkeypatch,
) -> None:
    """Headline router behaviour, demonstrated on a dimension that governs.

    This case used to be carried by `docs-writing`, gated on doc tooling —
    which dropped a prose skill for the wrong reason. The negative path is the
    same either way, so pin it where the constraint is real: a Rust workspace
    has no business being offered Python or TypeScript procedure, while a skill
    with no `applies-to` survives any workspace."""
    monkeypatch.setattr(server, "_load_project_profile", lambda: {
        "language": "rust",
        "secondary_languages": [],
        "test_framework": None,
        "doc_tool": None,
        "package_managers": ["cargo"],
        "file_types_present": [".rs"],
    })
    payload = json.loads(server.musubi_list_skills("agent", for_role="coder"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert payload["filtered_by_profile"] is True
    assert {"python", "typescript", "testing"}.isdisjoint(ids)
    assert {"debugging", "refactoring", "git-workflow"} <= ids


def test_rust_profile_keeps_docs_writing_and_research_for_the_agent(
    monkeypatch,
) -> None:
    """The agent's own non-coding catalog is language-independent: neither
    writing prose nor answering "how does X work?" is a Python-only act."""
    monkeypatch.setattr(server, "_load_project_profile", lambda: {
        "language": "rust",
        "secondary_languages": [],
        "test_framework": None,
        "doc_tool": None,
        "package_managers": ["cargo"],
        "file_types_present": [".rs"],
    })
    ids = {s["skill_id"] for s in json.loads(server.musubi_list_skills("agent"))["skills"]}
    assert {"docs-writing", "research"} <= ids


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
