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
