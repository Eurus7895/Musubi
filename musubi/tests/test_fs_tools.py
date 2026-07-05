"""Tests for the filesystem + command MCP tools.

musubi-tier: substrate test — pins the path-safety boundary and the
core read/write/edit/run contracts. These tools are the lever any MCP
client uses to actually do work through the harness; their semantics
need a hard test floor.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import server
from tools import fs


def _py_cmd(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


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


# -- append_file ------------------------------------------------------------


def test_append_file_creates_new(workspace: Path) -> None:
    result = fs.append_file("note.md", "# Hello\n")
    assert result == {
        "status": "ok",
        "bytes_written": len(b"# Hello\n"),
        "total_bytes": len(b"# Hello\n"),
    }
    assert (workspace / "note.md").read_text(encoding="utf-8") == "# Hello\n"


def test_append_file_appends_existing(workspace: Path) -> None:
    (workspace / "f").write_text("old", encoding="utf-8")
    result = fs.append_file("f", "new", expected_offset=3)
    assert result == {
        "status": "ok",
        "bytes_written": len(b"new"),
        "total_bytes": len(b"oldnew"),
    }
    assert (workspace / "f").read_text(encoding="utf-8") == "oldnew"


def test_append_file_creates_parents_by_default(workspace: Path) -> None:
    result = fs.append_file("a/b/c/file.txt", "x")
    assert result["status"] == "ok"
    assert (workspace / "a" / "b" / "c" / "file.txt").exists()


def test_append_file_respects_create_parents_false(workspace: Path) -> None:
    result = fs.append_file("a/b/file.txt", "x", create_parents=False)
    assert result["status"] == "error"
    assert "parent directory" in result["error"]


def test_append_file_refuses_directory_path(workspace: Path) -> None:
    (workspace / "d").mkdir()
    result = fs.append_file("d", "x")
    assert result["status"] == "error"
    assert "directory" in result["error"]


def test_append_file_traversal_blocked(workspace: Path) -> None:
    result = fs.append_file("../outside.txt", "x")
    assert result["status"] == "error"
    assert "outside the workspace" in result["error"]


def test_append_file_expected_offset_mismatch(workspace: Path) -> None:
    (workspace / "f").write_text("abc", encoding="utf-8")
    result = fs.append_file("f", "d", expected_offset=2)
    assert result["status"] == "error"
    assert "expected offset 2" in result["error"]
    assert "current size 3" in result["error"]
    assert (workspace / "f").read_text(encoding="utf-8") == "abc"


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


def test_run_command_decodes_as_utf8_with_replacement(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="✓ ok", stderr="")

    monkeypatch.setattr(fs.subprocess, "run", fake_run)

    result = fs.run_command("type page.html")

    assert result["status"] == "ok"
    assert result["stdout"] == "✓ ok"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_run_command_runs_in_workspace_root(workspace: Path) -> None:
    (workspace / "marker.txt").write_text("here", encoding="utf-8")
    result = fs.run_command(
        _py_cmd("from pathlib import Path; print(Path('marker.txt').name if Path('marker.txt').exists() else '')")
    )
    assert result["status"] == "ok"
    assert "marker.txt" in result["stdout"]


def test_run_command_explicit_cwd_resolves_inside_workspace(workspace: Path) -> None:
    (workspace / "sub").mkdir()
    (workspace / "sub" / "x").write_text("hi", encoding="utf-8")
    result = fs.run_command(
        _py_cmd("from pathlib import Path; print(Path('x').name if Path('x').exists() else '')"),
        cwd="sub",
    )
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
    result = fs.run_command(_py_cmd("import time; time.sleep(5)"), timeout_seconds=1)
    assert result["status"] == "error"
    assert "timed out" in result["error"]


# ── Output truncation: a noisy command doesn't blow context ───────────────


def test_run_command_output_truncated_at_cap(workspace: Path) -> None:
    """A command that emits >1M chars gets head+tail-preserved output."""
    # Emit ~1.5M chars of 'A's via head/yes — cheap and bounded.
    result = fs.run_command(_py_cmd("import sys; sys.stdout.write('A' * 1500000)"))
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


def test_mcp_append_file_round_trip(workspace: Path) -> None:
    payload = json.loads(server.musubi_append_file("out.txt", "hello"))
    assert payload["status"] == "ok"
    payload = json.loads(server.musubi_append_file("out.txt", " world", expected_offset=5))
    assert payload == {"status": "ok", "bytes_written": 6, "total_bytes": 11}
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "hello world"


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


def test_run_command_does_not_hang_on_stdin_read(workspace: Path) -> None:
    # A command that reads stdin must get EOF immediately (stdin=DEVNULL),
    # not block until the timeout. The short timeout makes a regression fail
    # fast instead of stalling the suite.
    code = "import sys; print('eof' if sys.stdin.read() == '' else 'blocked')"
    payload = json.loads(server.musubi_run_command(_py_cmd(code), timeout_seconds=10))
    assert payload["status"] == "ok"
    assert payload["exit_code"] == 0
    assert payload["stdout"].strip() == "eof"


# ── Read-only discovery: glob ───────────────────────────────────────────────


def _seed_tree(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("import os\nprint('hi')\n", encoding="utf-8")
    (root / "src" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# title\nTODO: docs\n", encoding="utf-8")
    # A directory that must never be walked.
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("junk", encoding="utf-8")


def test_glob_lists_whole_tree_and_skips_heavy_dirs(workspace: Path) -> None:
    _seed_tree(workspace)
    payload = json.loads(server.musubi_glob())
    assert payload["status"] == "ok"
    assert payload["matches"] == ["README.md", "src/main.py", "src/util.py"]
    assert not any("node_modules" in m for m in payload["matches"])
    assert payload["truncated"] is False


def test_glob_pattern_and_path_scope(workspace: Path) -> None:
    _seed_tree(workspace)
    assert json.loads(server.musubi_glob(pattern="**/*.py"))["matches"] == [
        "src/main.py",
        "src/util.py",
    ]
    assert json.loads(server.musubi_glob(path="src", pattern="*.py"))["count"] == 2
    assert json.loads(server.musubi_glob(pattern="*.md"))["matches"] == ["README.md"]


def test_glob_rejects_empty_pattern_and_traversal(workspace: Path) -> None:
    assert json.loads(server.musubi_glob(pattern="  "))["status"] == "error"
    assert json.loads(server.musubi_glob(path="../.."))["status"] == "error"


# ── Read-only discovery: grep ───────────────────────────────────────────────


def test_grep_finds_matches_with_file_and_line(workspace: Path) -> None:
    _seed_tree(workspace)
    payload = json.loads(server.musubi_grep("TODO"))
    assert payload["status"] == "ok"
    assert payload["matches"] == [{"file": "README.md", "line": 2, "text": "TODO: docs"}]
    assert payload["files_scanned"] >= 1


def test_grep_file_glob_and_ignore_case(workspace: Path) -> None:
    _seed_tree(workspace)
    only_py = json.loads(server.musubi_grep("import", file_glob="*.py"))
    assert [h["file"] for h in only_py["matches"]] == ["src/main.py"]
    ci = json.loads(server.musubi_grep("todo", ignore_case=True))
    assert ci["count"] == 1
    assert json.loads(server.musubi_grep("todo"))["count"] == 0


def test_grep_rejects_bad_regex_and_empty_pattern(workspace: Path) -> None:
    assert json.loads(server.musubi_grep("("))["status"] == "error"
    assert json.loads(server.musubi_grep(""))["status"] == "error"


def test_grep_skips_non_utf8_files(workspace: Path) -> None:
    _seed_tree(workspace)
    (workspace / "blob.bin").write_bytes(b"\xff\xfe\x00binarypattern")
    # Must not raise; the binary file is skipped, text matches still found.
    payload = json.loads(server.musubi_grep("helper"))
    assert payload["status"] == "ok"
    assert [h["file"] for h in payload["matches"]] == ["src/util.py"]
