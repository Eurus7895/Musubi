"""Tests for the first non-coding skills — MVP item 9 / Track D.9.

harness-tier: substrate test — pins that the new skills are discoverable
via the loader, parse cleanly, declare sensible applies-to, and reach
the orchestrator (butler) through the allowlist.
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


def test_orchestrator_can_load_research() -> None:
    assert check_skill_permission("orchestrator", "research") is True


def test_orchestrator_can_load_docs_writing() -> None:
    assert check_skill_permission("orchestrator", "docs-writing") is True


def test_designer_can_load_docs_writing() -> None:
    assert check_skill_permission("designer", "docs-writing") is True


def test_coder_cannot_load_research_or_docs_writing() -> None:
    """Coder allowlist stays code-focused — non-coding skills are
    orchestrator/designer territory."""
    assert check_skill_permission("coder", "research") is False
    assert check_skill_permission("coder", "docs-writing") is False


def test_existing_allowlist_entries_unchanged() -> None:
    """Regression: adding two skills must not silently drop existing ones."""
    assert AGENT_SKILL_ALLOWLIST["orchestrator"] >= {"orchestrator-routing"}
    assert AGENT_SKILL_ALLOWLIST["designer"] >= {
        "api-design", "database-patterns", "documentation",
    }


def test_orchestrator_catalog_surfaces_new_skills() -> None:
    """End-to-end: harness_list_skills('orchestrator') exposes both new
    skills to the butler. Router-level profile filtering is exercised
    separately in tests/test_skill_router.py (when that lands)."""
    payload = json.loads(server.harness_list_skills("orchestrator"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert "research" in ids
    assert "docs-writing" in ids
    # The original entry stays — adding two skills must not displace the
    # routing skill the butler is pushed at startup.
    assert "orchestrator-routing" in ids
