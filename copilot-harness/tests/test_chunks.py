"""Phase G.1.7 — chunked execution tests.

Covers `session/chunks.py::compute_chunks` plus the chunk-aware paths
through `state.py` / `db.py` (write_stage, read_stage,
increment_attempt, ensure_chunk_row, pause_session with chunk_id).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from session import state
from session.chunks import Chunk, compute_chunks, filter_design_for_chunk
from storage import db

# ── compute_chunks: chunking-rule matrix ──────────────────────────────────


def _plan(task_ids: list[str], descriptions: list[str] | None = None) -> dict:
    """Helper: build a minimal plan dict with the given task IDs."""
    descs = descriptions or [f"Description for {tid}" for tid in task_ids]
    return {
        "summary": "test plan",
        "tasks": [
            {
                "id": tid, "description": desc,
                "files_affected": [], "acceptance_criteria": [], "complexity": "low",
            }
            for tid, desc in zip(task_ids, descs, strict=False)
        ],
    }


def _module(file: str, **kwargs) -> dict:
    """Helper: build a module dict with optional task_id / purpose / etc."""
    base = {"file": file, "public_interface": []}
    base.update(kwargs)
    return base


def test_single_task_design_returns_empty_chunks() -> None:
    """≤ 1 task ⇒ no chunking — runner falls back to single-shot."""
    plan = _plan(["T1"])
    design = {"modules": [_module("a.py", task_id="T1"), _module("b.py", task_id="T1")]}
    assert compute_chunks(plan, design) == []


def test_single_module_design_returns_empty_chunks() -> None:
    """≤ 1 module ⇒ no chunking."""
    plan = _plan(["T1", "T2"])
    design = {"modules": [_module("a.py", task_id="T1")]}
    assert compute_chunks(plan, design) == []


def test_chunks_grouped_by_explicit_task_id() -> None:
    plan = _plan(["T1", "T2"], ["write tests", "wire pipeline"])
    design = {"modules": [
        _module("tests/a.py", task_id="T1"),
        _module("tests/b.py", task_id="T1"),
        _module("src/c.py",   task_id="T2"),
    ]}
    chunks = compute_chunks(plan, design)
    assert [c.chunk_id for c in chunks] == ["T1", "T2"]
    assert chunks[0].file_paths == ("tests/a.py", "tests/b.py")
    assert chunks[1].file_paths == ("src/c.py",)


def test_chunks_extract_task_id_from_purpose_when_field_missing() -> None:
    """Falls back to regex on the `purpose` text — matches the existing
    designer-output convention ('Implements T1 — …')."""
    plan = _plan(["T1", "T2"])
    design = {"modules": [
        _module("a.py", purpose="Implements T1 — db helpers"),
        _module("b.py", purpose="Implements T2 — runner"),
    ]}
    chunks = compute_chunks(plan, design)
    assert [c.chunk_id for c in chunks] == ["T1", "T2"]


def test_chunks_use_explicit_task_id_over_purpose_regex() -> None:
    """Explicit field wins. Avoid surprise when purpose mentions a
    different task name."""
    plan = _plan(["T1", "T2"])
    design = {"modules": [
        _module("a.py", task_id="T1", purpose="Helps T2 happen"),
    ]}
    chunks = compute_chunks(plan, _plan(["T1", "T2"]) | {"modules": []})
    # 1-module design => empty regardless. Re-test through the right call:
    design = {"modules": [
        _module("a.py", task_id="T1", purpose="Helps T2 happen"),
        _module("b.py", task_id="T2", purpose="No mention"),
    ]}
    chunks = compute_chunks(plan, design)
    assert chunks[0].chunk_id == "T1"
    assert chunks[0].file_paths == ("a.py",)


def test_unknown_task_modules_go_to_T_question_bucket_last() -> None:
    plan = _plan(["T1", "T2"])
    design = {"modules": [
        _module("a.py", task_id="T1"),
        _module("b.py", task_id="T2"),
        _module("orphan.py", purpose="random helpers"),  # no T-tag in purpose
    ]}
    chunks = compute_chunks(plan, design)
    ids = [c.chunk_id for c in chunks]
    assert ids == ["T1", "T2", "T?"]
    assert chunks[-1].file_paths == ("orphan.py",)


def test_only_unknown_chunk_fallback_to_no_chunking() -> None:
    """If every module is unassigned, fall back to single-shot."""
    plan = _plan(["T1", "T2"])
    design = {"modules": [
        _module("a.py", purpose="random helper"),
        _module("b.py", purpose="random helper too"),
    ]}
    assert compute_chunks(plan, design) == []


def test_tasks_addressed_array_field_resolves_to_first_match() -> None:
    plan = _plan(["T1", "T2"])
    design = {"modules": [
        _module("a.py", tasks_addressed=["T2", "T1"]),
        _module("b.py", tasks_addressed=["T1"]),
    ]}
    chunks = compute_chunks(plan, design)
    # First entry of tasks_addressed wins for `a.py` (T2)
    by_id = {c.chunk_id: c for c in chunks}
    assert "a.py" in by_id["T2"].file_paths
    assert "b.py" in by_id["T1"].file_paths


def test_chunks_preserve_plan_order() -> None:
    plan = _plan(["T2", "T1", "T3"])
    design = {"modules": [
        _module("c.py", task_id="T3"),
        _module("a.py", task_id="T2"),
        _module("b.py", task_id="T1"),
    ]}
    chunks = compute_chunks(plan, design)
    assert [c.chunk_id for c in chunks] == ["T2", "T1", "T3"]


def test_chunks_drop_tasks_with_zero_modules() -> None:
    plan = _plan(["T1", "T2", "T3"])
    design = {"modules": [
        _module("a.py", task_id="T1"),
        _module("b.py", task_id="T3"),
    ]}
    chunks = compute_chunks(plan, design)
    assert [c.chunk_id for c in chunks] == ["T1", "T3"]


def test_chunks_handle_malformed_input_gracefully() -> None:
    assert compute_chunks(None, None) == []
    assert compute_chunks({}, {}) == []
    assert compute_chunks({"tasks": []}, {"modules": []}) == []
    assert compute_chunks({"tasks": "not a list"}, {"modules": []}) == []


def test_chunk_is_frozen_dataclass() -> None:
    c = Chunk(chunk_id="T1", task_label="T1 — write tests", file_paths=("a.py",))
    with pytest.raises(Exception):
        c.chunk_id = "T2"  # type: ignore[misc]


# ── filter_design_for_chunk ────────────────────────────────────────────


def test_filter_design_keeps_only_chunk_modules() -> None:
    design = {"summary": "x", "modules": [
        _module("a.py"), _module("b.py"), _module("c.py"),
    ], "dependencies": ["lib"]}
    chunk = Chunk(chunk_id="T1", task_label="…", file_paths=("a.py", "c.py"))
    out = filter_design_for_chunk(design, chunk)
    paths = [m["file"] for m in out["modules"]]
    assert paths == ["a.py", "c.py"]
    # Top-level fields pass through.
    assert out["summary"] == "x"
    assert out["dependencies"] == ["lib"]
    # Chunk metadata appended for the agent's awareness.
    assert out["_chunk_id"] == "T1"


def test_filter_design_passes_through_when_modules_missing() -> None:
    design = {"summary": "x"}
    chunk = Chunk(chunk_id="T1", task_label="…", file_paths=("a.py",))
    out = filter_design_for_chunk(design, chunk)
    assert out == {"summary": "x"}


# ── Chunk-aware state round-trips ──────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    p = tmp_path / "harness.db"
    db.init_db(p)
    return p


def test_ensure_chunk_row_creates_first_attempt(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    attempt = state.ensure_chunk_row(sid, "code", "T1", fresh_db)
    assert attempt == 1
    # Idempotent — second call doesn't bump.
    assert state.ensure_chunk_row(sid, "code", "T1", fresh_db) == 1


def test_ensure_chunk_row_rejects_blank_chunk_id(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    with pytest.raises(ValueError, match="non-empty chunk_id"):
        state.ensure_chunk_row(sid, "code", "  ", fresh_db)


def test_chunked_write_isolated_from_global_row(fresh_db: Path) -> None:
    """Writing to chunk T1 does NOT touch the non-chunked code row."""
    sid = state.create_session("do x", fresh_db)
    state.ensure_chunk_row(sid, "code", "T1", fresh_db)
    state.write_stage(sid, "code", {"files_modified": ["a.py"]}, fresh_db, chunk_id="T1")
    # Non-chunked read returns nothing.
    assert state.read_stage(sid, "code", fresh_db) is None
    # Chunked read returns the written row.
    out = state.read_stage(sid, "code", fresh_db, chunk_id="T1")
    assert out == {"files_modified": ["a.py"]}


def test_chunked_attempt_increment_independent_per_chunk(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    state.ensure_chunk_row(sid, "code", "T1", fresh_db)
    state.ensure_chunk_row(sid, "code", "T2", fresh_db)
    assert state.increment_attempt(sid, "code", fresh_db, chunk_id="T1") == 2
    # T2's counter is untouched.
    assert state.get_attempt(sid, "code", fresh_db, chunk_id="T2") == 1


def test_chunked_user_hint_isolated_per_chunk(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    state.ensure_chunk_row(sid, "code", "T1", fresh_db)
    state.ensure_chunk_row(sid, "code", "T2", fresh_db)
    state.increment_attempt(sid, "code", fresh_db, chunk_id="T1", user_hint="fix T1")
    state.increment_attempt(sid, "code", fresh_db, chunk_id="T2", user_hint="fix T2")
    assert state.read_stage_user_hint(sid, "code", fresh_db, chunk_id="T1") == "fix T1"
    assert state.read_stage_user_hint(sid, "code", fresh_db, chunk_id="T2") == "fix T2"


def test_chunked_pause_records_chunk_id(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    state.pause_session(sid, "code", "stage_review", fresh_db, chunk_id="T2")
    pause = state.get_pause_state(sid, fresh_db)
    assert pause is not None
    assert pause["paused_at_stage"] == "code"
    assert pause["paused_at_chunk"] == "T2"
    assert pause["pause_reason"] == "stage_review"


def test_chunked_resume_clears_paused_at_chunk(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    state.pause_session(sid, "code", "stage_review", fresh_db, chunk_id="T2")
    state.resume_session(sid, "approve", fresh_db)
    pause = state.get_pause_state(sid, fresh_db)
    assert pause is not None
    assert pause["paused_at_stage"] is None
    assert pause["paused_at_chunk"] is None


def test_write_once_per_chunk_attempt(fresh_db: Path) -> None:
    """Write-once invariant survives the (session, stage, chunk_id, attempt)
    composite key extension."""
    sid = state.create_session("do x", fresh_db)
    state.ensure_chunk_row(sid, "code", "T1", fresh_db)
    state.write_stage(sid, "code", {"files_modified": ["a.py"]}, fresh_db, chunk_id="T1")
    with pytest.raises(ValueError, match="write-once"):
        state.write_stage(sid, "code", {"files_modified": ["b.py"]}, fresh_db, chunk_id="T1")


def test_increment_attempt_after_write_creates_new_chunk_row(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    state.ensure_chunk_row(sid, "code", "T1", fresh_db)
    state.write_stage(sid, "code", {"files_modified": ["a.py"]}, fresh_db, chunk_id="T1")
    new_attempt = state.increment_attempt(sid, "code", fresh_db, chunk_id="T1", user_hint="fix")
    assert new_attempt == 2
    # New attempt is empty + has the hint.
    row = db.get_stage_row(sid, "code", db_path=fresh_db, chunk_id="T1")
    assert row is not None
    assert row["attempt"] == 2
    assert row["output"] is None
    assert row["user_hint"] == "fix"


# ── No-regression: pre-G.1.7 behaviour preserved ────────────────────────


def test_non_chunked_write_still_works(fresh_db: Path) -> None:
    """Existing non-chunked feature-dev flow is unchanged when chunk_id
    is omitted."""
    sid = state.create_session("do x", fresh_db)
    state.write_stage(sid, "plan", {"summary": "ok", "tasks": []}, fresh_db)
    assert state.read_stage(sid, "plan", fresh_db) == {"summary": "ok", "tasks": []}
    assert state.read_stage(sid, "plan", fresh_db, chunk_id="T1") is None
