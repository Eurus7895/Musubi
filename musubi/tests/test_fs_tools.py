"""Tests for the filesystem + command MCP tools.

musubi-tier: substrate test — pins the path-safety boundary and the
core read/write/edit/run contracts. These tools are the lever any MCP
client uses to actually do work through the harness; their semantics
need a hard test floor.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import server
from tools import fs


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Anchor _workspace_root() to a tmp dir so the tests don't touch
    the real repo. Both the env-var path AND the cwd path are pinned."""
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ── Path resolution: traversal protection ──────────────────────────────────


def test_resolve_path_accepts_relative_inside_workspace(workspace: Path) -> None:
    resolved = fs.resolve_path("src/main.py")
    assert resolved == (workspace / "src" / "main.py").resolve()


def test_resolve_path_accepts_absolute_inside_workspace(workspace: Path) -> None:
    abs_path = str(workspace / "a" / "b.txt")
    assert fs.resolve_path(abs_path) == Path(abs_path).resolve()


def test_resolve_path_rejects_dotdot_traversal(workspace: Path) -> None:
    with pytest.raises(PermissionError, match="outside the workspace root"):
        fs.resolve_path("../../etc/passwd")


def test_resolve_path_rejects_absolute_outside_workspace(workspace: Path) -> None:
    with pytest.raises(PermissionError, match="outside the workspace root"):
        fs.resolve_path("/etc/passwd")


def test_resolve_path_rejects_empty(workspace: Path) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        fs.resolve_path("")


# ── read_file ──────────────────────────────────────────────────────────────


def test_read_file_returns_content(workspace: Path) -> None:
    (workspace / "hello.txt").write_text("hi there\n", encoding="utf-8")
    result = fs.read_file("hello.txt")
    assert result["status"] == "ok"
    assert result["content"] == "hi there\n"
    assert result["bytes"] == len(b"hi there\n")


def test_read_file_missing_returns_error(workspace: Path) -> None:
    result = fs.read_file("does-not-exist.txt")
    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_read_file_directory_returns_error(workspace: Path) -> None:
    (workspace / "d").mkdir()
    result = fs.read_file("d")
    assert result["status"] == "error"
    assert "not a regular file" in result["error"]


def test_read_file_traversal_blocked(workspace: Path) -> None:
    result = fs.read_file("../escape.txt")
    assert result["status"] == "error"
    assert "outside the workspace" in result["error"]


def test_read_file_binary_is_rejected_cleanly(workspace: Path) -> None:
    (workspace / "x.bin").write_bytes(b"\xff\xfe\x00\x01")
    result = fs.read_file("x.bin")
    assert result["status"] == "error"
    assert "non-utf-8" in result["error"]


# ── write_file ─────────────────────────────────────────────────────────────


def test_write_file_creates_new(workspace: Path) -> None:
    result = fs.write_file("note.md", "# Hello\n")
    assert result == {"status": "ok", "bytes_written": len(b"# Hello\n")}
    assert (workspace / "note.md").read_text(encoding="utf-8") == "# Hello\n"


def test_write_file_overwrites_existing(workspace: Path) -> None:
    (workspace / "f").write_text("old", encoding="utf-8")
    fs.write_file("f", "new")
    assert (workspace / "f").read_text(encoding="utf-8") == "new"


def test_write_file_creates_parents_by_default(workspace: Path) -> None:
    result = fs.write_file("a/b/c/file.txt", "x")
    assert result["status"] == "ok"
    assert (workspace / "a" / "b" / "c" / "file.txt").exists()


def test_write_file_respects_create_parents_false(workspace: Path) -> None:
    result = fs.write_file("a/b/file.txt", "x", create_parents=False)
    assert result["status"] == "error"
    assert "parent directory" in result["error"]


def test_write_file_refuses_directory_path(workspace: Path) -> None:
    (workspace / "d").mkdir()
    result = fs.write_file("d", "x")
    assert result["status"] == "error"
    assert "directory" in result["error"]


def test_write_file_traversal_blocked(workspace: Path) -> None:
    result = fs.write_file("../outside.txt", "x")
    assert result["status"] == "error"
    assert "outside the workspace" in result["error"]


# ── edit_file ──────────────────────────────────────────────────────────────


def test_edit_file_unique_match_replaces(workspace: Path) -> None:
    (workspace / "f.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    result = fs.edit_file("f.py", "b = 2", "b = 99")
    assert result == {"status": "ok", "replacements": 1}
    assert (workspace / "f.py").read_text(encoding="utf-8") == "a = 1\nb = 99\n"


def test_edit_file_non_unique_match_errors_by_default(workspace: Path) -> None:
    (workspace / "f.py").write_text("x\nx\n", encoding="utf-8")
    result = fs.edit_file("f.py", "x", "y")
    assert result["status"] == "error"
    assert "occurs 2 times" in result["error"]
    # File unchanged.
    assert (workspace / "f.py").read_text(encoding="utf-8") == "x\nx\n"


def test_edit_file_replace_all_replaces_every_occurrence(workspace: Path) -> None:
    (workspace / "f.py").write_text("x\nx\nx\n", encoding="utf-8")
    result = fs.edit_file("f.py", "x", "y", replace_all=True)
    assert result == {"status": "ok", "replacements": 3}
    assert (workspace / "f.py").read_text(encoding="utf-8") == "y\ny\ny\n"


def test_edit_file_missing_old_string_errors(workspace: Path) -> None:
    (workspace / "f.py").write_text("a", encoding="utf-8")
    result = fs.edit_file("f.py", "missing", "x")
    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_edit_file_empty_old_string_errors(workspace: Path) -> None:
    result = fs.edit_file("f.py", "", "x")
    assert result["status"] == "error"
    assert "non-empty" in result["error"]


def test_edit_file_missing_file_errors(workspace: Path) -> None:
    result = fs.edit_file("never-existed.py", "a", "b")
    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_edit_file_traversal_blocked(workspace: Path) -> None:
    result = fs.edit_file("../outside.py", "a", "b")
    assert result["status"] == "error"
    assert "outside the workspace" in result["error"]


# ── run_command ────────────────────────────────────────────────────────────


def test_run_command_returns_stdout(workspace: Path) -> None:
    result = fs.run_command("echo hello")
    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "hello"


def test_run_command_returns_nonzero_exit(workspace: Path) -> None:
    result = fs.run_command("false")
    assert result["status"] == "ok"
    assert result["exit_code"] != 0


def test_run_command_captures_stderr(workspace: Path) -> None:
    result = fs.run_command("echo err 1>&2")
    assert result["status"] == "ok"
    assert "err" in result["stderr"]


def test_run_command_runs_in_workspace_root(workspace: Path) -> None:
    (workspace / "marker.txt").write_text("here", encoding="utf-8")
    result = fs.run_command("ls marker.txt")
    assert result["status"] == "ok"
    assert "marker.txt" in result["stdout"]


def test_run_command_explicit_cwd_resolves_inside_workspace(workspace: Path) -> None:
    (workspace / "sub").mkdir()
    (workspace / "sub" / "x").write_text("hi", encoding="utf-8")
    result = fs.run_command("ls x", cwd="sub")
    assert result["status"] == "ok"
    assert "x" in result["stdout"]


def test_run_command_explicit_cwd_rejected_outside_workspace(workspace: Path) -> None:
    result = fs.run_command("ls", cwd="..")
    assert result["status"] == "error"
    assert "outside the workspace" in result["error"]


def test_run_command_empty_errors(workspace: Path) -> None:
    result = fs.run_command("")
    assert result["status"] == "error"
    assert "non-empty" in result["error"]


def test_run_command_timeout(workspace: Path) -> None:
    result = fs.run_command("sleep 5", timeout_seconds=1)
    assert result["status"] == "error"
    assert "timed out" in result["error"]


# ── Output truncation: a noisy command doesn't blow context ───────────────


def test_run_command_output_truncated_at_cap(workspace: Path) -> None:
    """A command that emits >1M chars gets head+tail-preserved output."""
    # Emit ~1.5M chars of 'A's via head/yes — cheap and bounded.
    result = fs.run_command("yes A | head -c 1500000")
    assert result["status"] == "ok"
    assert "truncated by Musubi" in result["stdout"]


# ── MCP-tool layer: server.py wraps the impl correctly ────────────────────


def test_mcp_read_file_returns_json(workspace: Path) -> None:
    (workspace / "f").write_text("xyz", encoding="utf-8")
    payload = json.loads(server.musubi_read_file("f"))
    assert payload["status"] == "ok"
    assert payload["content"] == "xyz"


def test_mcp_write_file_round_trip(workspace: Path) -> None:
    payload = json.loads(server.musubi_write_file("out.txt", "hello"))
    assert payload["status"] == "ok"
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "hello"


def test_mcp_edit_file_round_trip(workspace: Path) -> None:
    (workspace / "f").write_text("abc def\n", encoding="utf-8")
    payload = json.loads(server.musubi_edit_file("f", "abc", "XYZ"))
    assert payload["status"] == "ok"
    assert payload["replacements"] == 1
    assert (workspace / "f").read_text(encoding="utf-8") == "XYZ def\n"


def test_mcp_run_command_round_trip(workspace: Path) -> None:
    payload = json.loads(server.musubi_run_command("echo ok"))
    assert payload["status"] == "ok"
    assert payload["stdout"].strip() == "ok"
