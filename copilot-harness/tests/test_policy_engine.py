"""Tests for scripts/policy_engine.py (Week 3c)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Import the sibling scripts/ directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from policy_engine import PIPELINE_POLICIES, check_tool_allowed, deny_reason


# ── Allow decisions ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "agent,tool",
    [
        ("planner", "Read"),
        ("planner", "Glob"),
        ("designer", "View"),
        ("coder", "Write"),
        ("coder", "Edit"),
        ("coder", "Bash"),
        ("reviewer", "Read"),
    ],
)
def test_allowed_tools_for_known_agents(agent: str, tool: str) -> None:
    assert check_tool_allowed("feature-dev", agent, tool) is True


# ── Deny decisions ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "agent,tool",
    [
        ("planner", "Write"),
        ("planner", "Edit"),
        ("planner", "Bash"),
        ("designer", "Write"),
        ("designer", "Bash"),
        ("reviewer", "Write"),
        ("reviewer", "Edit"),
        ("reviewer", "Bash"),
    ],
)
def test_read_only_agents_cannot_mutate(agent: str, tool: str) -> None:
    assert check_tool_allowed("feature-dev", agent, tool) is False


# ── Fail-closed defaults ─────────────────────────────────────────────────────

def test_unknown_pipeline_denies() -> None:
    assert check_tool_allowed("made-up-pipeline", "coder", "Read") is False


def test_unknown_agent_denies() -> None:
    assert check_tool_allowed("feature-dev", "villain", "Read") is False


def test_case_insensitive_agent_name() -> None:
    assert check_tool_allowed("feature-dev", "PLANNER", "Read") is True


# ── deny_reason ──────────────────────────────────────────────────────────────

def test_deny_reason_unknown_pipeline() -> None:
    msg = deny_reason("x", "coder", "Read")
    assert "Unknown pipeline" in msg


def test_deny_reason_unknown_agent() -> None:
    msg = deny_reason("feature-dev", "villain", "Read")
    assert "villain" in msg and "feature-dev" in msg


def test_deny_reason_lists_allowed_tools() -> None:
    msg = deny_reason("feature-dev", "planner", "Write")
    assert "Write" in msg and "Read" in msg


# ── Policy shape ─────────────────────────────────────────────────────────────

def test_feature_dev_has_all_four_agents() -> None:
    expected = {"planner", "designer", "coder", "reviewer"}
    assert set(PIPELINE_POLICIES["feature-dev"].keys()) == expected


def test_coder_has_write_privileges() -> None:
    coder_tools = set(PIPELINE_POLICIES["feature-dev"]["coder"])
    assert {"Write", "Edit", "Bash"}.issubset(coder_tools)
