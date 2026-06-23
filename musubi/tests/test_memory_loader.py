"""Tests for memory_loader.py — Tier 1/2 memory injection."""

import pytest
from pathlib import Path

from memory import memory_loader


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


# ── Week 4 Day 4: cross-session query ────────────────────────────────────────


from session import state  # noqa: E402
from storage import db  # noqa: E402


@pytest.fixture()
def query_db(tmp_path: Path) -> Path:
    path = tmp_path / "query.db"
    db.init_db(path)
    return path


def _seed_session(db_path: Path, request: str, review: dict | None = None) -> str:
    sid = state.create_session(request, db_path=db_path)
    for stage in ["plan", "design", "code"]:
        state.write_stage(sid, stage, {"stub": True}, db_path=db_path)
    if review is not None:
        state.write_stage(sid, "review", review, db_path=db_path)
    return sid


def test_query_sessions_matches_request_substring(query_db: Path) -> None:
    sid = _seed_session(query_db, "add a login endpoint with OAuth")
    _seed_session(query_db, "rewrite the caching layer")
    results = memory_loader.query_sessions("oauth", db_path=query_db)
    assert len(results) == 1
    assert results[0]["session_id"] == sid
    assert results[0]["match_source"] in {"request", "both"}


def test_query_sessions_matches_review_substring(query_db: Path) -> None:
    review = {
        "status": "fail", "attempt": 1,
        "issues": [{"severity": "critical",
                    "description": "SQL injection risk in login endpoint",
                    "fix_instruction": "use params"}],
    }
    sid = _seed_session(query_db, "rewrite caching", review)
    results = memory_loader.query_sessions("sql injection", db_path=query_db)
    assert len(results) == 1
    assert results[0]["session_id"] == sid
    assert results[0]["match_source"] in {"review", "both"}
    assert "review_snippets" in results[0]
    assert any("SQL injection" in s for s in results[0]["review_snippets"])


def test_query_sessions_empty_query_returns_empty(query_db: Path) -> None:
    _seed_session(query_db, "anything")
    assert memory_loader.query_sessions("", db_path=query_db) == []
    assert memory_loader.query_sessions("   ", db_path=query_db) == []


def test_query_sessions_respects_limit(query_db: Path) -> None:
    for i in range(5):
        _seed_session(query_db, f"add widget {i}")
    results = memory_loader.query_sessions("widget", limit=3, db_path=query_db)
    assert len(results) == 3


def test_query_sessions_case_insensitive(query_db: Path) -> None:
    _seed_session(query_db, "Add a Login endpoint")
    upper = memory_loader.query_sessions("LOGIN", db_path=query_db)
    lower = memory_loader.query_sessions("login", db_path=query_db)
    mixed = memory_loader.query_sessions("LoGiN", db_path=query_db)
    assert len(upper) == len(lower) == len(mixed) == 1


def test_query_sessions_no_match_returns_empty(query_db: Path) -> None:
    _seed_session(query_db, "add login endpoint")
    assert memory_loader.query_sessions("kubernetes", db_path=query_db) == []


def test_query_sessions_truncates_request_excerpt(query_db: Path) -> None:
    long_req = "widget " * 200  # ~1400 chars
    _seed_session(query_db, long_req)
    results = memory_loader.query_sessions("widget", db_path=query_db)
    assert len(results) == 1
    # Excerpt must be capped — no full 1400-char transcript.
    assert len(results[0]["request"]) <= 400


# ── musubi_get_memory_context MCP tool ──────────────────────────────────────


def test_musubi_get_memory_context_returns_tier1_when_present(
    monkeypatch: pytest.MonkeyPatch, memory_root: Path
) -> None:
    """The MCP tool wraps memory_loader.get_memory_context — direct-mode uses it."""
    import json
    import server
    monkeypatch.setattr(
        memory_loader, "get_memory_context",
        lambda: {"tier1_index": "# Tier 1 Index\nKey decisions here.\n",
                 "tier2_available": ["architecture.md"]},
    )
    raw = server.musubi_get_memory_context()
    payload = json.loads(raw)
    assert payload["tier1_index"].startswith("# Tier 1 Index")
    assert "architecture.md" in payload["tier2_available"]


def test_musubi_get_memory_context_returns_empty_when_no_memory(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty repo → empty object, not an error."""
    import json
    import server
    monkeypatch.setattr(memory_loader, "get_memory_context", lambda: {})
    raw = server.musubi_get_memory_context()
    assert json.loads(raw) == {}
