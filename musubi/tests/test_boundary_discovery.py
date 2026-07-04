"""Governance contract for the read-only discovery tools.

musubi-tier: substrate test — pins the fail-closed boundary for
`musubi_glob` / `musubi_grep`. Read-only roles get discovery; a role
without the Grep/Glob capability is denied; discovery never implies shell.
"""

from __future__ import annotations

from agent import boundary


def test_discovery_tools_allowed_for_read_only_roles() -> None:
    for role in ("planner", "designer", "reviewer", "coder", "agent"):
        for tool in ("musubi_glob", "musubi_grep"):
            assert boundary.evaluate_tool_call(role, tool).allowed, (
                f"{role} should be allowed {tool}"
            )


def test_discovery_denied_for_role_without_capability() -> None:
    # summarizer is a text-only role: no Grep/Glob capability → fail-closed.
    assert not boundary.evaluate_tool_call("summarizer", "musubi_glob").allowed
    assert not boundary.evaluate_tool_call("summarizer", "musubi_grep").allowed


def test_discovery_is_not_shell() -> None:
    # A read-only role gets discovery but never Bash, so the grep/glob
    # addition can't be a backdoor to command execution.
    for role in ("planner", "designer", "reviewer"):
        assert boundary.evaluate_tool_call(role, "musubi_glob").allowed
        assert not boundary.evaluate_tool_call(role, "musubi_run_command").allowed
