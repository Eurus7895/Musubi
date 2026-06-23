"""Phase G.1 firewall lock-in tests.

The G.1 runners (`explorer`, `investigator`, `reviewer-aux`) execute
sub-agent LM sessions on behalf of pipeline stages. They consume only
`musubi_get_subagent_context`'s output; if the firewall ever widens to
include parent-stage data (plan / design / code / review / memory), a
runner could surface that to an unprivileged role and Hard Invariant #3
would break.

These tests pin the contract the runners depend on:
  - For each of the three G.1 roles, the SubagentContext payload is
    *exactly* {brief, role, role_skill, allowed_tools} — nothing more.
  - The `allowed_tools` returned matches the policy table, byte-for-byte.
  - A brief that *contains* the literal text of parent-stage JSON does
    not cause the firewall to surface that data as a structured field —
    it stays opaque text.
  - `assert_no_session_leakage` flags every parent-stage key listed in
    the docstring, individually.

Distinct from `test_subagent_context.py` (which covers the firewall in
the abstract for every role) — this file is the G.1 runner's regression
guard. If a future refactor breaks one of these assertions, the runner
PR that did it gets blocked at CI.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

# Import validation first — its module-level _ensure_scripts_on_path
# call puts scripts/ on sys.path so policy_engine can resolve.
from validation.subagent_context import (  # isort: skip
    assert_no_session_leakage,
    build_subagent_context,
    context_keys,
)
from policy_engine import SUBAGENT_POLICIES  # noqa: E402  # isort: skip


G1_RUNNER_ROLES = ("explorer", "investigator", "reviewer-aux")


@pytest.mark.parametrize("role", G1_RUNNER_ROLES)
def test_g1_role_context_has_only_closed_set_keys(role: str) -> None:
    """Each runner role's SubagentContext must expose exactly the
    closed `context_keys()` set — no parent state, no sibling state,
    no session_id."""
    ctx = build_subagent_context("locate the foo bar", role)
    payload = asdict(ctx)
    assert set(payload.keys()) == context_keys(), (
        f"{role}: payload keys {sorted(payload.keys())} drifted from "
        f"context_keys() {sorted(context_keys())}"
    )


@pytest.mark.parametrize("role", G1_RUNNER_ROLES)
def test_g1_role_allowed_tools_match_policy_byte_for_byte(role: str) -> None:
    """The runner uses ctx.allowed_tools to drive the LM tool surface.
    Policy + context must agree exactly — anything else is an
    accidental privilege widening / narrowing."""
    ctx = build_subagent_context("brief", role)
    assert tuple(ctx.allowed_tools) == tuple(SUBAGENT_POLICIES[role]), (
        f"{role}: allowed_tools={ctx.allowed_tools} does not match "
        f"SUBAGENT_POLICIES[{role!r}]={SUBAGENT_POLICIES[role]}"
    )


@pytest.mark.parametrize("role", G1_RUNNER_ROLES)
def test_g1_role_only_sees_brief_role_role_skill_allowed_tools(role: str) -> None:
    """Type-level pin: `SubagentContext` must not grow new attributes
    that could surface parent state. If a refactor adds a field, this
    test must be updated *intentionally*."""
    ctx = build_subagent_context("brief", role)
    expected_attrs = {"brief", "role", "role_skill", "allowed_tools"}
    actual_attrs = {f for f in dir(ctx) if not f.startswith("_")}
    # asdict() includes only dataclass fields; dir() may include
    # frozen-dataclass machinery — restrict to dataclass fields.
    field_names = {f.name for f in ctx.__dataclass_fields__.values()}
    assert field_names == expected_attrs, (
        f"{role}: SubagentContext fields {field_names} drifted from "
        f"expected {expected_attrs}. Pin updated intentionally?"
    )
    # Sanity — dir() at minimum contains the expected attrs.
    assert expected_attrs.issubset(actual_attrs)


def test_brief_with_parent_state_payload_stays_opaque_text() -> None:
    """A runner caller might paste parent JSON into the brief by
    mistake. The firewall must keep that as the literal `brief` string
    — never deserialize it into structured fields the role can read."""
    parent_blob = json.dumps({
        "plan":   {"tasks": ["leak1"]},
        "design": {"modules": ["leak2"]},
        "code":   {"files_modified": ["leak3"]},
    })
    ctx = build_subagent_context(parent_blob, "explorer")
    assert ctx.brief == parent_blob
    payload = asdict(ctx)
    # The firewall should NOT have lifted plan/design/code into top-level
    # keys — they remain inert text inside the brief string.
    for forbidden in ("plan", "design", "code", "review", "memory"):
        assert forbidden not in payload, (
            f"firewall lifted {forbidden!r} out of brief into top-level"
        )


@pytest.mark.parametrize("role", G1_RUNNER_ROLES)
def test_g1_role_skill_is_either_string_or_none(role: str, tmp_path: Path) -> None:
    """role_skill is the only string field besides brief that comes
    from outside the brief. It must be a str or None — never a
    dict / list / structured payload that could carry parent state."""
    empty_skills = tmp_path / "skills"
    empty_skills.mkdir()
    ctx = build_subagent_context("x", role, skills_dir=empty_skills)
    assert ctx.role_skill is None or isinstance(ctx.role_skill, str)


@pytest.mark.parametrize("forbidden", [
    "plan", "design", "code", "review",
    "request", "memory", "fail_patterns", "fix_instructions",
    "session_id", "agent_versions",
])
def test_assert_no_session_leakage_catches_each_parent_key(forbidden: str) -> None:
    """Every parent-stage key that the runner could accidentally inject
    must trip the leakage check. Belt-and-braces guard — the type-level
    firewall is the primary defence; this is the runtime backstop."""
    payload = {forbidden: "would-leak-this"}
    with pytest.raises(AssertionError, match="firewall breach"):
        assert_no_session_leakage(payload)


def test_assert_no_session_leakage_passes_clean_runner_payload() -> None:
    """The exact shape `musubi_get_subagent_context` returns to the
    runner must NOT trip the leakage check."""
    clean = {
        "brief": "scan src/ for FooClass",
        "role": "explorer",
        "role_skill": "# Explorer\n\nrules",
        "allowed_tools": ["Read", "Grep"],
    }
    # Should not raise.
    assert_no_session_leakage(clean)


def test_subagent_context_to_dict_keys_equal_context_keys() -> None:
    """The runner deserializes the JSON envelope and compares keys
    against context_keys(). asdict(SubagentContext) must produce the
    exact set the runner expects, otherwise a JSON round-trip could
    drop or add fields silently."""
    ctx = build_subagent_context("b", "reviewer-aux")
    assert set(asdict(ctx).keys()) == context_keys()
