"""Tests for proposed_patch_applier.py — patch validation and application."""

from pathlib import Path

import pytest

from execution import proposed_patch_applier as pa
from validation.context_builder import validate_skill_builder_write


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Minimal repo with agents dir and a fake coder.agent.md."""
    agents_dir = tmp_path / ".github" / "agents"
    (agents_dir / "proposed").mkdir(parents=True)
    (agents_dir / "coder.agent.md").write_text(
        "---\nname: Coder\nversion: 1.0.0\n---\n\n"
        "## Behavior Rules\n\n"
        "- Rule 1\n"
        "- Rule 2\n"
    )
    return tmp_path


def _write_patch(proposed_dir: Path, agent: str, addition: str) -> Path:
    patch = proposed_dir / f"{agent}.patch.md"
    patch.write_text(
        f"# Proposed Patch: {agent}\n\n"
        "Generated: 2026-01-01T00:00:00\n"
        "Pattern threshold reached: 3 occurrences.\n\n"
        "## Recurring Failure\n\n"
        f"**Agent:** {agent}\n"
        "**Issue:** test issue\n\n"
        "## Proposed Behavior-Rules Addition\n\n"
        f"Add the following to the **Behavior Rules** section"
        f" of `.github/agents/{agent}.agent.md`:\n\n"
        "```\n"
        f"{addition}\n"
        "```\n\n"
        "## Review Instructions\n\nHuman review required.\n"
    )
    return patch


# ── validate_skill_builder_write (Day 5 path-guard requirement) ───────────────

def test_validate_skill_builder_blocks_direct_agent_write() -> None:
    """validate_skill_builder_write blocks writes directly to .github/agents/."""
    assert not validate_skill_builder_write(".github/agents/coder.agent.md")


def test_validate_skill_builder_blocks_root_write() -> None:
    assert not validate_skill_builder_write(".github/agents/planner.agent.md")


def test_validate_skill_builder_allows_proposed_write() -> None:
    assert validate_skill_builder_write(".github/agents/proposed/coder.patch.md")


def test_validate_skill_builder_blocks_traversal() -> None:
    """Path traversal tricks must not escape proposed/."""
    assert not validate_skill_builder_write(
        ".github/agents/proposed/../coder.agent.md"
    )


# ── validate_patch ────────────────────────────────────────────────────────────

def test_validate_missing_file() -> None:
    result = pa.validate_patch(Path("/nonexistent/proposed/coder.patch.md"))
    assert not result.valid
    assert any("not found" in e for e in result.errors)


def test_validate_not_in_proposed(tmp_path: Path) -> None:
    patch = tmp_path / "coder.patch.md"
    patch.write_text("# Proposed Patch: coder\n")
    result = pa.validate_patch(patch)
    assert not result.valid
    assert any("proposed" in e for e in result.errors)


def test_validate_valid_patch(repo: Path) -> None:
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- Always handle errors on external calls",
    )
    result = pa.validate_patch(patch)
    assert result.valid
    assert result.target_agent == "coder"
    assert result.addition is not None
    assert "Always handle errors" in result.addition


def test_validate_missing_agent_header(repo: Path) -> None:
    patch = repo / ".github" / "agents" / "proposed" / "coder.patch.md"
    patch.write_text(
        "No header here\n\n"
        "## Proposed Behavior-Rules Addition\n\n"
        "```\n- rule\n```\n"
    )
    result = pa.validate_patch(patch)
    assert not result.valid
    assert any("header" in e for e in result.errors)


def test_validate_missing_addition_section(repo: Path) -> None:
    patch = repo / ".github" / "agents" / "proposed" / "coder.patch.md"
    patch.write_text("# Proposed Patch: coder\n\nNo addition section.\n")
    result = pa.validate_patch(patch)
    assert not result.valid
    assert any("Behavior-Rules Addition" in e for e in result.errors)


def test_validate_blocks_tools_change(repo: Path) -> None:
    """Patch applier must block non-Behavior-Rules changes — tools."""
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- tools: [view, edit, bash, delete]",
    )
    result = pa.validate_patch(patch)
    assert not result.valid
    assert any("non-Behavior-Rules" in e for e in result.errors)


def test_validate_blocks_input_contract_change(repo: Path) -> None:
    """Patch applier must block non-Behavior-Rules changes — input contract."""
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- input contract: must call harness_get_active_session first",
    )
    result = pa.validate_patch(patch)
    assert not result.valid
    assert any("non-Behavior-Rules" in e for e in result.errors)


def test_validate_blocks_output_contract_change(repo: Path) -> None:
    """Patch applier must block non-Behavior-Rules changes — output contract."""
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- output contract: return plain text instead of JSON",
    )
    result = pa.validate_patch(patch)
    assert not result.valid
    assert any("non-Behavior-Rules" in e for e in result.errors)


def test_validate_extracts_agent_name(repo: Path) -> None:
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- Always validate inputs",
    )
    result = pa.validate_patch(patch)
    assert result.target_agent == "coder"


def test_validate_extracts_addition_text(repo: Path) -> None:
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- Always validate inputs before processing",
    )
    result = pa.validate_patch(patch)
    assert result.addition == "- Always validate inputs before processing"


# ── apply_patch ───────────────────────────────────────────────────────────────

def test_apply_invalid_patch_returns_errors(repo: Path) -> None:
    patch = repo / ".github" / "agents" / "proposed" / "coder.patch.md"
    patch.write_text("# Proposed Patch: coder\n\nno addition section\n")
    result = pa.apply_patch(patch, repo)
    assert not result.applied
    assert result.errors


def test_apply_valid_patch_appends_rule(repo: Path) -> None:
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- Always validate output schema before returning",
    )
    result = pa.apply_patch(patch, repo)
    assert result.applied
    agent_text = (repo / ".github" / "agents" / "coder.agent.md").read_text()
    assert "Always validate output schema before returning" in agent_text


def test_apply_creates_archive(repo: Path) -> None:
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- Always validate output schema before returning",
    )
    result = pa.apply_patch(patch, repo)
    assert result.applied
    assert result.archive_path is not None
    assert result.archive_path.exists()


def test_apply_archive_contains_original_content(repo: Path) -> None:
    original = (repo / ".github" / "agents" / "coder.agent.md").read_text()
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- New auto-suggested rule",
    )
    result = pa.apply_patch(patch, repo)
    assert result.applied
    assert result.archive_path is not None
    assert result.archive_path.read_text() == original


def test_apply_archive_dir_created(repo: Path) -> None:
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- Always ensure idempotency",
    )
    pa.apply_patch(patch, repo)
    archive_dir = repo / ".github" / "agents" / "archive"
    assert archive_dir.exists()
    assert any(archive_dir.iterdir())


def test_apply_agent_not_found(repo: Path) -> None:
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "nonexistent",
        "- Some rule",
    )
    result = pa.apply_patch(patch, repo)
    assert not result.applied
    assert any("not found" in e for e in result.errors)


def test_apply_agent_without_behavior_rules_section(repo: Path) -> None:
    (repo / ".github" / "agents" / "bare.agent.md").write_text(
        "---\nname: Bare\nversion: 1.0.0\n---\n\nNo sections here.\n"
    )
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "bare",
        "- Some rule",
    )
    result = pa.apply_patch(patch, repo)
    assert not result.applied
    assert any("Behavior Rules" in e for e in result.errors)


def test_apply_preserves_existing_rules(repo: Path) -> None:
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- New rule from patch",
    )
    result = pa.apply_patch(patch, repo)
    assert result.applied
    agent_text = (repo / ".github" / "agents" / "coder.agent.md").read_text()
    assert "Rule 1" in agent_text
    assert "Rule 2" in agent_text
    assert "New rule from patch" in agent_text


def test_apply_returns_agent_path(repo: Path) -> None:
    patch = _write_patch(
        repo / ".github" / "agents" / "proposed",
        "coder",
        "- Always handle errors",
    )
    result = pa.apply_patch(patch, repo)
    assert result.applied
    assert result.agent_path == repo / ".github" / "agents" / "coder.agent.md"
