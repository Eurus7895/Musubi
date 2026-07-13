"""Phase G.2 — startup-time policy-table validation tests.

`scripts/policy_engine.validate_policy_table()` walks
PIPELINE_POLICIES + SUBAGENT_POLICIES + MAIN_SUBAGENT_ALLOWLIST and
returns a list of misconfiguration errors. `validate_policies_or_raise`
turns that into a RuntimeError so the harness fails to boot when the
policy table is broken.

These tests pin: the current shipped policy table is clean; each
class of misconfiguration produces a clear error.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure scripts/ is on sys.path before importing policy_engine.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import policy_engine  # noqa: E402
from policy_engine import (  # noqa: E402
    MAIN_SUBAGENT_ALLOWLIST,
    SUBAGENT_POLICIES,
    validate_policies_or_raise,
    validate_policy_table,
)


def _restore_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Helper to ensure each test starts from the unaltered shipped
    tables. monkeypatch.setattr handles teardown automatically."""
    # No-op — defined for symmetry; monkeypatch's auto-undo is enough.
    pass


# ── The shipped policy table must validate clean ─────────────────────


def test_shipped_policy_table_validates_clean() -> None:
    errors = validate_policy_table()
    assert errors == [], (
        "The shipped PIPELINE_POLICIES + SUBAGENT_POLICIES + "
        "MAIN_SUBAGENT_ALLOWLIST must validate clean at boot. "
        f"Errors: {errors}"
    )


def test_validate_policies_or_raise_passes_on_shipped_table() -> None:
    # Should not raise.
    validate_policies_or_raise()


# ── PIPELINE_POLICIES misconfigurations ───────────────────────────────


def test_pipeline_policies_unknown_agent_name_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = {
        "feature-dev": {
            "planner": ["Read", "View"],
            "ghostagent": ["Read"],  # not in known agents
        },
    }
    monkeypatch.setattr(policy_engine, "PIPELINE_POLICIES", bad)
    errors = validate_policy_table()
    assert any("ghostagent" in e for e in errors), errors


def test_pipeline_policies_unknown_tool_name_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = {
        "feature-dev": {
            "planner": ["Read", "Hypnotise"],  # not a known tool
        },
    }
    monkeypatch.setattr(policy_engine, "PIPELINE_POLICIES", bad)
    errors = validate_policy_table()
    assert any("Hypnotise" in e for e in errors), errors


def test_pipeline_policies_non_list_tools_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = {"feature-dev": {"planner": "Read,View"}}  # str, not list
    monkeypatch.setattr(policy_engine, "PIPELINE_POLICIES", bad)
    errors = validate_policy_table()
    assert any("must be a list" in e for e in errors), errors


# ── SUBAGENT_POLICIES misconfigurations ───────────────────────────────


def test_subagent_policies_unknown_role_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = dict(SUBAGENT_POLICIES)
    bad["wizard"] = ["Read"]
    monkeypatch.setattr(policy_engine, "SUBAGENT_POLICIES", bad)
    errors = validate_policy_table()
    assert any("wizard" in e for e in errors), errors


def test_subagent_policies_unknown_tool_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = dict(SUBAGENT_POLICIES)
    bad["explorer"] = ["Read", "Telepathy"]
    monkeypatch.setattr(policy_engine, "SUBAGENT_POLICIES", bad)
    errors = validate_policy_table()
    assert any("Telepathy" in e for e in errors), errors


# ── Frontmatter spawn_allowlist: purpose-dir resolution ───────────────


def test_spawn_allowlist_resolves_from_purpose_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frontmatter authority follows the purpose-dir catalog: `root/`
    beats the flat legacy copy, and a pipeline-stage variant is found
    with no flat file present at all."""
    agents = tmp_path / ".github" / "agents"
    (agents / "root").mkdir(parents=True)
    (agents / "root" / "agent.agent.md").write_text(
        "---\nspawn_allowlist:\n  - explorer\n---\n", encoding="utf-8"
    )
    (agents / "agent.agent.md").write_text(
        "---\nspawn_allowlist:\n  - explorer\n  - coder\n---\n", encoding="utf-8"
    )
    stage = agents / "pipeline-stages" / "feature-dev"
    stage.mkdir(parents=True)
    (stage / "coder.agent.md").write_text(
        "---\nspawn_allowlist:\n  - investigator\n---\n", encoding="utf-8"
    )
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    policy_engine._reset_agent_spawns_cache()
    try:
        assert policy_engine.main_subagent_allowlist("agent") == ["explorer"]
        assert policy_engine.main_subagent_allowlist("coder") == ["investigator"]
    finally:
        policy_engine._reset_agent_spawns_cache()


def test_validate_catches_bad_spawn_role_in_purpose_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_policy_table walks the whole purpose-dir catalog — a
    spawn_allowlist referencing an unknown role fails validation even
    when the file lives under `workers/`, not flat."""
    agents = tmp_path / ".github" / "agents"
    (agents / "workers").mkdir(parents=True)
    (agents / "workers" / "coder.agent.md").write_text(
        "---\nspawn_allowlist:\n  - ghost-runner\n---\n", encoding="utf-8"
    )
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    policy_engine._reset_agent_spawns_cache()
    try:
        errors = validate_policy_table()
        assert any(
            "ghost-runner" in e and "coder.agent.md" in e for e in errors
        ), errors
    finally:
        policy_engine._reset_agent_spawns_cache()


# ── PIPELINE_POLICIES ↔ SUBAGENT_POLICIES sync ────────────────────────


def test_subagent_role_lagging_pipeline_grant_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A role present in both tables with FEWER ad-hoc tools than its
    pipeline grant is drift (the manual-sync failure the old comment
    warned about) and must fail validation."""
    bad = dict(SUBAGENT_POLICIES)
    bad["coder"] = ["Read", "View", "Grep", "Glob", "Write", "Edit"]  # Bash dropped
    monkeypatch.setattr(policy_engine, "SUBAGENT_POLICIES", bad)
    errors = validate_policy_table()
    assert any("out of sync" in e and "'coder'" in e for e in errors), errors


def test_subagent_role_exceeding_pipeline_grant_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A role whose ad-hoc tool set EXCEEDS its pipeline grant would let
    an ad-hoc spawn do more than the same role inside a pipeline —
    the safety direction of the sync rule."""
    bad = dict(SUBAGENT_POLICIES)
    bad["reviewer"] = ["Read", "View", "Grep", "Glob", "Bash"]  # Bash added
    monkeypatch.setattr(policy_engine, "SUBAGENT_POLICIES", bad)
    errors = validate_policy_table()
    assert any("out of sync" in e and "'reviewer'" in e for e in errors), errors


def test_roles_missing_from_either_table_are_not_sync_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sync rule only binds roles present in BOTH tables: a pure
    sub-agent role (explorer) and a pipeline-only role are exempt —
    pinned here so the check can't silently widen. (The shipped
    code-review roles now live in both tables, so the pipeline-only
    case is simulated by dropping scoper from SUBAGENT_POLICIES.)"""
    from policy_engine import PIPELINE_POLICIES

    assert "explorer" in SUBAGENT_POLICIES
    assert all(
        "explorer" not in agents for agents in PIPELINE_POLICIES.values()
    )
    trimmed = {k: v for k, v in SUBAGENT_POLICIES.items() if k != "scoper"}
    monkeypatch.setattr(policy_engine, "SUBAGENT_POLICIES", trimmed)
    errors = validate_policy_table()
    assert not any("out of sync" in e for e in errors), errors


# ── MAIN_SUBAGENT_ALLOWLIST misconfigurations ─────────────────────────


def test_main_allowlist_undeclared_role_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = dict(MAIN_SUBAGENT_ALLOWLIST)
    bad["coder"] = ["explorer", "ghost-runner"]  # ghost-runner not in SUBAGENT_POLICIES
    monkeypatch.setattr(policy_engine, "MAIN_SUBAGENT_ALLOWLIST", bad)
    errors = validate_policy_table()
    assert any("ghost-runner" in e for e in errors), errors


def test_main_allowlist_non_list_value_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = dict(MAIN_SUBAGENT_ALLOWLIST)
    bad["coder"] = "explorer"  # str, not list
    monkeypatch.setattr(policy_engine, "MAIN_SUBAGENT_ALLOWLIST", bad)
    errors = validate_policy_table()
    assert any("must be a list" in e for e in errors), errors


# ── validate_policies_or_raise raises with a structured message ───────


def test_or_raise_includes_every_error_in_one_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_pipeline = {"feature-dev": {"ghost": ["Read"], "planner": ["Hypnotise"]}}
    monkeypatch.setattr(policy_engine, "PIPELINE_POLICIES", bad_pipeline)
    with pytest.raises(RuntimeError) as excinfo:
        validate_policies_or_raise()
    msg = str(excinfo.value)
    assert "ghost" in msg
    assert "Hypnotise" in msg
    assert "Phase G.2" in msg  # makes the trigger phase obvious in logs
