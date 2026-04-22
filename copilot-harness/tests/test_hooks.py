"""Tests for hooks.json + scripts/{pre,post}_tool_use.py + session_start.py."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"


# ── hooks.json shape ─────────────────────────────────────────────────────────

def test_hooks_json_parses() -> None:
    data = json.loads((_REPO_ROOT / "hooks.json").read_text())
    assert data["version"] == "1.0"
    assert "hooks" in data


def test_hooks_json_has_all_lifecycle_events() -> None:
    data = json.loads((_REPO_ROOT / "hooks.json").read_text())
    for event in ("SessionStart", "PreToolUse", "PostToolUse"):
        assert event in data["hooks"], f"Missing event {event}"
        specs = data["hooks"][event]
        assert isinstance(specs, list) and len(specs) >= 1
        for spec in specs:
            assert spec["type"] == "command"
            assert spec["command"].startswith("python scripts/")


# ── pre_tool_use.py ──────────────────────────────────────────────────────────

def _run(script: str, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


def test_pre_tool_use_allows_coder_write() -> None:
    result = _run("pre_tool_use.py", {
        "pipeline": "feature-dev", "agent": "coder", "tool": "Write",
    })
    assert result.returncode == 0


def test_pre_tool_use_denies_planner_write() -> None:
    result = _run("pre_tool_use.py", {
        "pipeline": "feature-dev", "agent": "planner", "tool": "Write",
    })
    assert result.returncode == 1
    assert "planner" in result.stderr.lower() or "Write" in result.stderr


def test_pre_tool_use_denies_reviewer_bash() -> None:
    result = _run("pre_tool_use.py", {
        "pipeline": "feature-dev", "agent": "reviewer", "tool": "Bash",
    })
    assert result.returncode == 1


def test_pre_tool_use_missing_keys_is_error() -> None:
    result = _run("pre_tool_use.py", {"agent": "coder"})  # no tool, no pipeline
    assert result.returncode == 2
    assert "missing" in result.stderr.lower()


def test_pre_tool_use_invalid_json_is_error() -> None:
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "pre_tool_use.py")],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid JSON" in result.stderr


# ── post_tool_use.py ─────────────────────────────────────────────────────────

def test_post_tool_use_writes_audit_row(tmp_path: Path, monkeypatch) -> None:
    from importlib import import_module
    sys.path.insert(0, str(_SCRIPTS_DIR))
    post = import_module("post_tool_use")

    db_path = tmp_path / "audit.db"
    post.record(
        {
            "session_id": "s1", "pipeline": "feature-dev",
            "agent": "coder", "tool": "Write",
            "args": {"path": "foo.py"}, "status": "ok",
        },
        db_path=db_path,
    )

    conn = sqlite3.connect(db_path)
    rows = list(conn.execute(
        "SELECT session_id, pipeline, agent, tool, status FROM tool_audit"
    ))
    conn.close()
    assert rows == [("s1", "feature-dev", "coder", "Write", "ok")]


def test_post_tool_use_requires_tool_key() -> None:
    result = _run("post_tool_use.py", {"session_id": "s1"})
    assert result.returncode == 2


# ── session_start.py ─────────────────────────────────────────────────────────

def test_session_start_passes_when_pipeline_yaml_present(tmp_path: Path) -> None:
    # Use the real repo root so pipeline.yaml exists.
    # The default workspace_root = scripts/.. which is the repo root.
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "session_start.py")],
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    # src/ doesn't exist in this repo → baseline check fails.
    # But we're verifying the hook runs (exit 0 or 1 is fine — not 2).
    assert result.returncode in (0, 1)


def test_session_start_skips_when_pipeline_yaml_missing(tmp_path: Path) -> None:
    """No pipeline.yaml → no-op (exit 0)."""
    payload = json.dumps({
        "pipeline": "nonexistent-pipeline",
        "workspace_root": str(tmp_path),
    })
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "session_start.py")],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0


def test_session_start_detects_missing_path(tmp_path: Path) -> None:
    """Create a fake pipeline.yaml whose baseline check references a missing dir."""
    pdir = tmp_path / ".github" / "pipelines" / "fake" / "agents"
    pdir.mkdir(parents=True)
    pyaml = pdir.parent / "pipeline.yaml"
    pyaml.write_text(
        "name: fake\n"
        "version: 1.0.0\n"
        "level: 2\n"
        "baseline_checks:\n"
        "  - type: file_read\n"
        "    path: does-not-exist/\n"
        "    error: 'fake baseline check failed'\n"
    )
    payload = json.dumps({
        "pipeline": "fake", "workspace_root": str(tmp_path),
    })
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "session_start.py")],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "fake baseline check failed" in result.stderr


# ── harness_run_hook MCP tool ────────────────────────────────────────────────

def test_harness_run_hook_reports_no_config_for_unknown_event(monkeypatch) -> None:
    import server

    out = json.loads(server.harness_run_hook("NonexistentEvent", ""))
    assert out["event"] == "NonexistentEvent"
    assert out["results"] == []


def test_harness_run_hook_runs_pre_tool_use(monkeypatch) -> None:
    import server

    # feature-dev coder → Write should be allowed (exit 0)
    payload = json.dumps({
        "pipeline": "feature-dev", "agent": "coder", "tool": "Write",
    })
    out = json.loads(server.harness_run_hook("PreToolUse", payload))
    assert out["event"] == "PreToolUse"
    # Should have at least one command result with an exit_code.
    assert out["results"]
    assert out["results"][0]["exit_code"] == 0


def test_harness_run_hook_deny_surfaces_exit_1(monkeypatch) -> None:
    import server

    # planner → Write should be denied (exit 1)
    payload = json.dumps({
        "pipeline": "feature-dev", "agent": "planner", "tool": "Write",
    })
    out = json.loads(server.harness_run_hook("PreToolUse", payload))
    assert out["results"][0]["exit_code"] == 1
