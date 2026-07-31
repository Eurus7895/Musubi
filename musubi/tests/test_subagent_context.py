"""Tests for the sub-agent firewall (Phase A.2).

Covers `validation/subagent_context.py`. Sub-agents must never receive
parent session state — these tests assert that explicitly via the type
signature (no session_id / db_path) and via the closed `context_keys()`
set.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from skills import skill_loader
from validation import subagent_context
from validation.subagent_context import (
    SUBAGENT_ROLE_SKILLS,
    SubagentContext,
    assert_no_session_leakage,
    build_subagent_context,
    context_keys,
)


# ── signature firewall ──────────────────────────────────────────────────────

def test_signature_does_not_accept_session_id_or_db_path() -> None:
    """The firewall is enforced at the function signature level —
    `session_id` / `db_path` cannot be passed in. Any future refactor
    that adds them must update this test deliberately."""
    sig = inspect.signature(build_subagent_context)
    forbidden = {"session_id", "db_path"}
    assert forbidden.isdisjoint(set(sig.parameters.keys()))


def test_context_keys_is_closed_set() -> None:
    # `role_skill_id` names the catalog skill already carried as text in
    # `role_skill` — catalog metadata, never parent state. Added so a pushed
    # skill is nameable downstream: without it the audit ledger and the
    # console had no way to say which skill a worker received.
    assert context_keys() == {
        "brief", "role", "role_skill", "role_skill_id", "allowed_tools",
    }


def test_subagent_context_is_frozen() -> None:
    ctx = build_subagent_context("x", "explorer")
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        ctx.brief = "tampered"  # type: ignore[misc]


# ── happy path ──────────────────────────────────────────────────────────────

def test_build_returns_only_brief_role_skill_and_tools(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    (skills_dir / "explorer").mkdir(parents=True)
    (skills_dir / "explorer" / "SKILL.md").write_text(
        "# Explorer\n\nRead-only scan of the repo.\n", encoding="utf-8"
    )
    ctx = build_subagent_context(
        "scan src/ for FooClass", "explorer", skills_dir=skills_dir
    )
    assert isinstance(ctx, SubagentContext)
    assert ctx.brief == "scan src/ for FooClass"
    assert ctx.role == "explorer"
    assert ctx.role_skill is not None
    assert "Explorer" in ctx.role_skill
    assert ctx.allowed_tools == ("Read", "View", "Grep", "Glob")


def test_role_skill_is_none_when_skill_file_missing(tmp_path: Path) -> None:
    """Phase A.2 ships the table; Phase A.3 lands the SKILL.md files.
    Until then, role_skill is None — never a fallback skill."""
    empty_skills = tmp_path / "skills"
    empty_skills.mkdir()
    ctx = build_subagent_context(
        "x", "investigator", skills_dir=empty_skills
    )
    assert ctx.role_skill is None
    assert ctx.role == "investigator"


def test_brief_is_stripped_of_whitespace() -> None:
    ctx = build_subagent_context("  scan  ", "explorer")
    assert ctx.brief == "scan"


def test_each_role_has_a_registered_skill_id() -> None:
    """Every role in SUBAGENT_POLICIES must have an entry in
    SUBAGENT_ROLE_SKILLS — even if the SKILL.md file does not exist
    yet. This keeps Phase A.2 and Phase A.3 in lockstep."""
    from policy_engine import SUBAGENT_POLICIES
    for role in SUBAGENT_POLICIES:
        assert role in SUBAGENT_ROLE_SKILLS, (
            f"role {role!r} has a tool policy but no entry in "
            f"SUBAGENT_ROLE_SKILLS — add a skill_id (or `None`) before "
            f"shipping the role."
        )


# ── failure-mode rejections ─────────────────────────────────────────────────

def test_empty_brief_rejected() -> None:
    with pytest.raises(ValueError, match="brief"):
        build_subagent_context("", "explorer")


def test_whitespace_only_brief_rejected() -> None:
    with pytest.raises(ValueError, match="brief"):
        build_subagent_context("   ", "explorer")


def test_unknown_role_rejected() -> None:
    with pytest.raises(ValueError, match="role"):
        build_subagent_context("x", "ghost")


def test_non_string_brief_rejected() -> None:
    with pytest.raises(ValueError, match="brief"):
        build_subagent_context(None, "explorer")  # type: ignore[arg-type]


# ── assert_no_session_leakage ───────────────────────────────────────────────

@pytest.mark.parametrize("forbidden_key", [
    "plan", "design", "code", "review", "request", "memory",
    "fail_patterns", "fix_instructions", "session_id", "agent_versions",
])
def test_leakage_detection_flags_main_session_keys(forbidden_key: str) -> None:
    payload = {forbidden_key: "anything", "brief": "x"}
    with pytest.raises(AssertionError, match="firewall breach"):
        assert_no_session_leakage(payload)


def test_leakage_detection_passes_clean_payload() -> None:
    # The legitimate payload shape — no session keys present.
    assert_no_session_leakage({
        "brief": "scan", "role": "explorer", "role_skill": "x",
        "allowed_tools": ["Read"],
    })


def test_leakage_detection_ignores_non_dicts() -> None:
    # Strings / lists are not dicts and cannot leak via key match.
    assert_no_session_leakage("plain string")
    assert_no_session_leakage(["plan", "design"])
    assert_no_session_leakage(None)


# ── module never reads session state ────────────────────────────────────────

def test_module_does_not_import_session_state() -> None:
    """Static check: the firewall module must not import any module that
    can read parent session state. If a future refactor adds one, this
    test fails loudly."""
    src = Path(subagent_context.__file__).read_text(encoding="utf-8")
    forbidden_imports = [
        "from session import state",
        "from storage import db",
        "from memory import memory_loader",
    ]
    for imp in forbidden_imports:
        assert imp not in src, (
            f"subagent_context.py must not import {imp!r} — that would "
            f"give it a path to read parent session state."
        )
