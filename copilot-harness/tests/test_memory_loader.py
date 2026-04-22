"""Tests for memory_loader.py — Tier 1/2 memory injection."""

import pytest
from pathlib import Path

import memory_loader


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def memory_root(tmp_path: Path) -> Path:
    """Repo root with a populated .github/memory/ directory."""
    mem_dir = tmp_path / ".github" / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text("# Tier 1 Index\nKey decisions here.\n")
    (mem_dir / "architecture.md").write_text("# Architecture\nSQLite chosen over Postgres.\n")
    (mem_dir / "failure-patterns.md").write_text("# Failure Patterns\n---\n")
    return tmp_path


@pytest.fixture()
def empty_root(tmp_path: Path) -> Path:
    """Repo root with no .github/memory/ directory at all."""
    return tmp_path


# ── get_tier1_index ───────────────────────────────────────────────────────────

def test_get_tier1_index_returns_content(memory_root: Path) -> None:
    content = memory_loader.get_tier1_index(memory_root)
    assert content is not None
    assert "Tier 1 Index" in content


def test_get_tier1_index_returns_none_when_missing(empty_root: Path) -> None:
    content = memory_loader.get_tier1_index(empty_root)
    assert content is None


def test_get_tier1_index_returns_none_when_dir_missing(tmp_path: Path) -> None:
    # No .github/memory directory at all
    content = memory_loader.get_tier1_index(tmp_path)
    assert content is None


# ── get_tier2_entry ───────────────────────────────────────────────────────────

def test_get_tier2_entry_returns_content(memory_root: Path) -> None:
    content = memory_loader.get_tier2_entry("architecture.md", memory_root)
    assert content is not None
    assert "SQLite" in content


def test_get_tier2_entry_missing_file(memory_root: Path) -> None:
    content = memory_loader.get_tier2_entry("nonexistent.md", memory_root)
    assert content is None


def test_get_tier2_entry_blocks_path_traversal(memory_root: Path) -> None:
    assert memory_loader.get_tier2_entry("../agents/planner.agent.md", memory_root) is None
    assert memory_loader.get_tier2_entry("..\\secret", memory_root) is None
    assert memory_loader.get_tier2_entry("subdir/../other.md", memory_root) is None


def test_get_tier2_entry_returns_none_for_empty_root(empty_root: Path) -> None:
    assert memory_loader.get_tier2_entry("architecture.md", empty_root) is None


# ── list_tier2_entries ────────────────────────────────────────────────────────

def test_list_tier2_entries_excludes_memory_md(memory_root: Path) -> None:
    entries = memory_loader.list_tier2_entries(memory_root)
    assert "MEMORY.md" not in entries


def test_list_tier2_entries_includes_md_files(memory_root: Path) -> None:
    entries = memory_loader.list_tier2_entries(memory_root)
    assert "architecture.md" in entries
    assert "failure-patterns.md" in entries


def test_list_tier2_entries_sorted(memory_root: Path) -> None:
    entries = memory_loader.list_tier2_entries(memory_root)
    assert entries == sorted(entries)


def test_list_tier2_entries_empty_when_no_dir(empty_root: Path) -> None:
    assert memory_loader.list_tier2_entries(empty_root) == []


def test_list_tier2_entries_excludes_non_md_files(memory_root: Path) -> None:
    (memory_root / ".github" / "memory" / "notes.txt").write_text("plain text")
    entries = memory_loader.list_tier2_entries(memory_root)
    assert "notes.txt" not in entries


# ── get_memory_context ────────────────────────────────────────────────────────

def test_get_memory_context_returns_tier1_and_tier2_list(memory_root: Path) -> None:
    ctx = memory_loader.get_memory_context(memory_root)
    assert "tier1_index" in ctx
    assert "tier2_available" in ctx
    assert isinstance(ctx["tier2_available"], list)
    assert "architecture.md" in ctx["tier2_available"]


def test_get_memory_context_empty_when_no_memory_md(empty_root: Path) -> None:
    ctx = memory_loader.get_memory_context(empty_root)
    assert ctx == {}


def test_get_memory_context_tier1_content_correct(memory_root: Path) -> None:
    ctx = memory_loader.get_memory_context(memory_root)
    assert "Tier 1 Index" in ctx["tier1_index"]


def test_get_memory_context_only_memory_md_present(tmp_path: Path) -> None:
    """MEMORY.md exists but no Tier 2 files → tier2_available is empty."""
    mem_dir = tmp_path / ".github" / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text("# Index\n")
    ctx = memory_loader.get_memory_context(tmp_path)
    assert ctx["tier2_available"] == []
