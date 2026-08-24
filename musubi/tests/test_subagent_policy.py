"""Tests for the sub-agent slice of scripts/policy_engine.py
(SUBAGENT_POLICIES + MAIN_SUBAGENT_ALLOWLIST + helpers)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ is not a package — same import-path trick as test_policy_engine.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import policy_engine
from policy_engine import (
    MAIN_SUBAGENT_ALLOWLIST,
    SUBAGENT_POLICIES,
    check_subagent_allowed,
    effective_subagent_tools,
    get_subagent_tools,
    list_subagent_roles,
    subagent_deny_reason,
)


# ── shape ────────────────────────────────────────────────────────────────────

def test_three_phase_a_roles_present() -> None:
    """Phase A roles are a subset of SUBAGENT_POLICIES; Phase B.1 added the
    pipeline roles as ad-hoc spawnable. Both must be present."""
    assert {"explorer", "investigator", "reviewer-aux"}.issubset(
        SUBAGENT_POLICIES.keys()
    )


def test_phase_b1_pipeline_roles_present_as_subagents() -> None:
    """Phase B.1 — agent can spawn pipeline roles ad-hoc. The role
    must therefore appear in SUBAGENT_POLICIES with its pipeline tool set."""
    assert {"planner", "coder", "reviewer"}.issubset(SUBAGENT_POLICIES.keys())


def test_agent_can_spawn_phase_a_roles() -> None:
    assert {"explorer", "investigator", "reviewer-aux"}.issubset(
        set(MAIN_SUBAGENT_ALLOWLIST["root"])
    )


def test_agent_can_spawn_direct_delivery_roles() -> None:
    assert {"designer", "coder", "reviewer"}.issubset(
        set(MAIN_SUBAGENT_ALLOWLIST["root"])
    )
    assert "planner" not in MAIN_SUBAGENT_ALLOWLIST["root"]


def test_pipeline_stages_have_phase_g16_allowlist() -> None:
    """Phase G.1.6 — feature-dev's coder + reviewer opt into read-only
    sub-agents. planner + designer remain spawn-locked (the
    dispatcher's stage routing returns [] for them anyway, but the
    policy table is the authoritative second line of defence).

    Earlier this test pinned EVERY stage to []; G.1.6 added two
    entries deliberately. If a future phase widens these, update
    this test deliberately too — don't relax.
    """
    assert MAIN_SUBAGENT_ALLOWLIST["planner"] == []
    assert MAIN_SUBAGENT_ALLOWLIST["designer"] == []
    assert MAIN_SUBAGENT_ALLOWLIST["coder"] == ["explorer", "investigator"]
    assert MAIN_SUBAGENT_ALLOWLIST["reviewer"] == ["reviewer-aux"]


def test_explorer_is_read_only() -> None:
    assert "Bash" not in SUBAGENT_POLICIES["explorer"]
    assert "Write" not in SUBAGENT_POLICIES["explorer"]
    assert "Edit" not in SUBAGENT_POLICIES["explorer"]


def test_investigator_can_run_bash() -> None:
    assert "Bash" in SUBAGENT_POLICIES["investigator"]


def test_reviewer_aux_only_reads() -> None:
    assert SUBAGENT_POLICIES["reviewer-aux"] == ["Read", "View"]


# ── check_subagent_allowed ──────────────────────────────────────────────────

def test_agent_can_spawn_explorer() -> None:
    assert check_subagent_allowed("agent", "explorer") is True


def test_check_subagent_allowed_is_case_insensitive_main() -> None:
    assert check_subagent_allowed("AGENT", "explorer") is True


def test_coder_can_spawn_explorer_in_phase_g16() -> None:
    """Phase G.1.6 — coder opts into explorer + investigator. Reviewer
    opts into reviewer-aux. Planner and designer stay spawn-locked."""
    assert check_subagent_allowed("coder", "explorer") is True
    assert check_subagent_allowed("coder", "investigator") is True
    # Other roles still denied for coder.
    assert check_subagent_allowed("coder", "reviewer-aux") is False
    assert check_subagent_allowed("coder", "summarizer") is False
    # Reviewer can spawn ONLY reviewer-aux.
    assert check_subagent_allowed("reviewer", "reviewer-aux") is True
    assert check_subagent_allowed("reviewer", "explorer") is False
    # Planner / designer stay spawn-locked.
    assert check_subagent_allowed("planner", "explorer") is False
    assert check_subagent_allowed("designer", "explorer") is False


def test_unknown_main_denies_all() -> None:
    assert check_subagent_allowed("villain", "explorer") is False


def test_unknown_role_denies_for_agent() -> None:
    assert check_subagent_allowed("agent", "saboteur") is False


# ── list_subagent_roles ─────────────────────────────────────────────────────

def test_list_subagent_roles_for_agent() -> None:
    roles = list_subagent_roles("agent")
    assert set(roles) >= {
        "explorer", "investigator", "reviewer-aux",
        "designer", "coder", "reviewer",
    }
    assert "planner" not in roles


def test_list_subagent_roles_for_pipeline_stages_phase_g16() -> None:
    """G.1.6 wiring — list_subagent_roles surfaces coder + reviewer
    opt-ins, planner + designer stay empty."""
    assert list_subagent_roles("coder") == ["explorer", "investigator"]
    assert list_subagent_roles("reviewer") == ["reviewer-aux"]
    assert list_subagent_roles("planner") == []
    assert list_subagent_roles("designer") == []


def test_list_subagent_roles_for_unknown_main_is_empty() -> None:
    assert list_subagent_roles("nobody") == []


def test_list_subagent_roles_returns_a_copy() -> None:
    """Mutating the returned list must not affect the global table."""
    roles = list_subagent_roles("agent")
    roles.append("hacker")
    assert "hacker" not in MAIN_SUBAGENT_ALLOWLIST["root"]


# ── get_subagent_tools ──────────────────────────────────────────────────────

def test_get_subagent_tools_known_role() -> None:
    assert "Read" in get_subagent_tools("explorer")
    assert "Bash" in get_subagent_tools("investigator")


def test_get_subagent_tools_unknown_role() -> None:
    assert get_subagent_tools("ghost") == []


def test_get_subagent_tools_returns_copy() -> None:
    tools = get_subagent_tools("explorer")
    tools.append("Bash")
    assert "Bash" not in SUBAGENT_POLICIES["explorer"]


# ── effective_subagent_tools (intersection) ─────────────────────────────────

def test_effective_tools_role_capped_by_main() -> None:
    """Even if main has Write, explorer is read-only."""
    main_tools = ["Read", "Grep", "Glob", "Write", "Edit", "Bash"]
    eff = effective_subagent_tools(
        "agent", main_tools, "explorer"
    )
    assert "Write" not in eff
    assert "Bash" not in eff
    assert "Read" in eff


def test_effective_tools_main_capped_by_role() -> None:
    """If main has fewer tools than the role allows, intersect down."""
    main_tools = ["Read"]
    eff = effective_subagent_tools(
        "agent", main_tools, "investigator"
    )
    assert eff == ["Read"]


def test_effective_tools_unknown_role_is_empty() -> None:
    assert effective_subagent_tools(
        "agent", ["Read", "Bash"], "ghost"
    ) == []


def test_effective_tools_with_caller_narrowing() -> None:
    """`requested_tools` further intersects below role∩main."""
    main_tools = ["Read", "Grep", "Glob", "Bash"]
    eff = effective_subagent_tools(
        "agent",
        main_tools,
        "investigator",
        requested_tools=["Read", "Glob"],
    )
    assert sorted(eff) == ["Glob", "Read"]


def test_effective_tools_disjoint_intersection_is_empty() -> None:
    eff = effective_subagent_tools(
        "agent", ["Write", "Edit"], "explorer"
    )
    assert eff == []


def test_effective_tools_empty_main_tools_is_empty() -> None:
    assert effective_subagent_tools(
        "agent", [], "explorer"
    ) == []


# ── subagent_deny_reason ────────────────────────────────────────────────────

def test_deny_reason_unknown_role_lists_valid_roles() -> None:
    msg = subagent_deny_reason("agent", "ghost")
    assert "ghost" in msg
    assert "explorer" in msg


def test_deny_reason_unknown_main_says_fail_closed() -> None:
    msg = subagent_deny_reason("villain", "explorer")
    assert "villain" in msg
    assert "fail-closed" in msg


def test_deny_reason_for_disallowed_pair_lists_allowed_roles() -> None:
    msg = subagent_deny_reason("coder", "explorer")
    assert "coder" in msg
    assert "explorer" in msg
    # Coder's allow-list is empty in Phase A.
    assert "Allowed roles" in msg


# ── helpers must not allow Phase B to silently widen policy ─────────────────

def test_explorer_tools_match_design_md_spec() -> None:
    """design.md Phase A.3 says Read + Grep + Glob (View is read-equivalent
    in our scheme; treat as a superset, but the read-only invariant must
    hold)."""
    tools = set(SUBAGENT_POLICIES["explorer"])
    assert {"Read", "Grep", "Glob"}.issubset(tools)
    assert tools.isdisjoint({"Write", "Edit", "Bash"})


def test_investigator_tools_match_design_md_spec() -> None:
    tools = set(SUBAGENT_POLICIES["investigator"])
    assert {"Read", "Grep", "Glob", "Bash"}.issubset(tools)
    assert tools.isdisjoint({"Write", "Edit"})


def test_reviewer_aux_tools_match_design_md_spec() -> None:
    tools = set(SUBAGENT_POLICIES["reviewer-aux"])
    assert tools == {"Read", "View"}


# ── the depth-0 driver's name: canonical `root`, legacy `agent` ─────────────


def test_the_root_role_has_one_canonical_name_and_one_legacy_alias() -> None:
    assert policy_engine.ROOT_ROLE == "root"
    assert policy_engine.ROOT_ROLE_ALIASES == frozenset({"root", "agent"})


def test_both_spellings_resolve_to_the_same_membership() -> None:
    """`policy_audit.role` and `subagent_audit.parent_agent_name` are
    append-only ledgers full of rows that say `agent`, and a stored role is
    fed straight back into these checks. The alias is what keeps a replayed
    row from silently losing its firewall."""
    canonical = policy_engine.list_subagent_roles("root")
    assert canonical == policy_engine.list_subagent_roles("agent")
    assert canonical == policy_engine.list_subagent_roles("  AGENT  ")
    assert policy_engine.check_subagent_allowed("agent", "coder")
    assert policy_engine.check_subagent_allowed("root", "coder")


def test_driver_is_not_an_alias_and_stays_denied() -> None:
    """`driver` is the console's word for the same actor, and it must never
    reach the policy engine as one. It has never carried the root's
    membership, so aliasing it would GRANT the whole spawn firewall to a name
    that never had it — a fail-open change (HI #5)."""
    assert policy_engine.normalize_role("driver") == "driver"
    assert policy_engine.list_subagent_roles("driver") == []
    assert not policy_engine.check_subagent_allowed("driver", "coder")


def test_an_unknown_role_still_misses_every_catalog() -> None:
    assert policy_engine.normalize_role("saboteur") == "saboteur"
    assert policy_engine.get_subagent_tools("saboteur") == []
    assert not policy_engine.check_subagent_allowed("saboteur", "coder")
    assert not policy_engine.check_subagent_allowed("agent", "saboteur")


def test_the_legacy_catalog_filename_still_declares_the_firewall(
    tmp_path, monkeypatch,
) -> None:
    """`spawn_allowlist:` frontmatter is AUTHORITATIVE when present, so a
    checkout still carrying `root/agent.agent.md` must keep declaring it.
    Missing the legacy filename would drop a user's own firewall back to the
    constant — an effective policy change caused by nothing but a rename."""
    agents = tmp_path / ".github" / "agents" / "root"
    agents.mkdir(parents=True)
    (agents / "agent.agent.md").write_text(
        "---\nspawn_allowlist:\n  - explorer\n---\n", encoding="utf-8"
    )
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    policy_engine._reset_agent_spawns_cache()
    try:
        assert policy_engine.main_subagent_allowlist("root") == ["explorer"]
        assert policy_engine.main_subagent_allowlist("agent") == ["explorer"]
    finally:
        policy_engine._reset_agent_spawns_cache()
