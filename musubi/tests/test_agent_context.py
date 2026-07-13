"""Tests for the Agent slice of the harness (Phase B.1).

Covers:
  - validation/context_builder.py: dispatcher accepts "agent",
    returns memory_tier1 only, exposes no pipeline-stage state, denies
    every musubi_read_stage call via _STAGE_PERMISSIONS.
  - validation/context_builder.AGENT_SKILL_ALLOWLIST gates the
    agent-routing skill but nothing else.
  - scripts/policy_engine.py: agent's spawn_allowlist includes
    the Phase A roles + the pipeline roles, ad-hoc pipeline-role spawns
    intersect tools correctly, denied combinations remain denied.
  - .github/agents/agent.agent.md frontmatter declares the
    expected sees/spawn/budget contract.
  - .github/skills/agent-routing/SKILL.md exists and has the
    required frontmatter.

Phase B.2 wires the extension-side runner; this file does not exercise
that path.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from memory import memory_loader
from validation import context_builder
from validation.context_builder import AGENT_SKILL_ALLOWLIST

# scripts/ is not a package — match the import-path trick used by
# test_subagent_policy.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from policy_engine import (  # noqa: E402
    MAIN_SUBAGENT_ALLOWLIST,
    SUBAGENT_POLICIES,
    check_subagent_allowed,
    effective_subagent_tools,
    list_subagent_roles,
    subagent_deny_reason,
)

# ── _context_agent: shape + firewall ─────────────────────────────────

def test_agent_dispatcher_accepts_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_context must route 'agent' to the new builder."""
    monkeypatch.setattr(memory_loader, "get_memory_context", lambda: {})
    ctx = context_builder.build_context("ignored-session-id", "agent")
    assert isinstance(ctx, dict)


def test_agent_context_returns_only_memory_tier1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        memory_loader,
        "get_memory_context",
        lambda: {"tier1_index": "# Tier 1\n", "tier2_available": ["arch.md"]},
    )
    ctx = context_builder.build_context("ignored", "agent")
    assert set(ctx.keys()) == {"memory_tier1"}
    assert ctx["memory_tier1"]["tier1_index"].startswith("# Tier 1")


def test_agent_context_empty_when_no_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_memory_context returns {} when MEMORY.md is absent — the
    agent must still get a dict with the key, just empty."""
    monkeypatch.setattr(memory_loader, "get_memory_context", lambda: {})
    ctx = context_builder.build_context("ignored", "agent")
    assert ctx == {"memory_tier1": {}}


def test_agent_context_has_no_pipeline_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent must never see request, plan, design, code, review,
    fix_instructions, or fail_patterns — those belong to pipeline runs."""
    monkeypatch.setattr(memory_loader, "get_memory_context", lambda: {})
    ctx = context_builder.build_context("ignored", "agent")
    forbidden = {
        "request", "plan", "design", "code", "review",
        "fix_instructions", "fail_patterns",
    }
    assert forbidden.isdisjoint(ctx.keys())


def test_agent_context_has_no_runner_supplied_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """user_message + conversation_history are runner-supplied at LM-call
    time (Phase B.2 / Phase C). The harness must NOT synthesize them in
    Phase B.1 — fabricating empty values would mask a missing wire."""
    monkeypatch.setattr(memory_loader, "get_memory_context", lambda: {})
    ctx = context_builder.build_context("ignored", "agent")
    assert "user_message" not in ctx
    assert "conversation_history" not in ctx


def test_agent_session_id_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent does not have a pipeline session — passing any
    value (real, fake, or unused) must produce the same context."""
    monkeypatch.setattr(memory_loader, "get_memory_context", lambda: {"a": 1})
    ctx_a = context_builder.build_context("session-a", "agent")
    ctx_b = context_builder.build_context("session-b", "agent")
    ctx_c = context_builder.build_context("", "agent")
    assert ctx_a == ctx_b == ctx_c


def test_unknown_agent_message_now_lists_agent() -> None:
    """The dispatcher's error message must mention agent so a
    misspelled agent name surfaces it as a valid option."""
    with pytest.raises(ValueError, match="agent"):
        context_builder.build_context("sid", "orcestrator")


# ── _STAGE_PERMISSIONS: agent denies every stage read ────────────────

@pytest.mark.parametrize("stage", ["plan", "design", "code", "review"])
def test_agent_cannot_read_any_pipeline_stage(stage: str) -> None:
    """musubi_read_stage must return None for the agent on any
    pipeline stage — pipelines are user-invoked and run in their own
    session; the agent must not peek."""
    result = context_builder.read_stage_for_agent(
        "any-session", stage, "agent"
    )
    assert result is None


# ── AGENT_SKILL_ALLOWLIST: agent-routing only ────────────────────────

def test_agent_skill_allowlist_includes_routing() -> None:
    """The runner pushes agent-routing via inject_skills frontmatter
    and may also call musubi_get_skill('agent-routing') on demand;
    the policy gate must accept that call."""
    assert "agent-routing" in AGENT_SKILL_ALLOWLIST["agent"]


def test_agent_skill_allowlist_excludes_generator_skills() -> None:
    """The agent does not author code; loading python / api-design /
    code-review etc. into its context would blur the dispatcher boundary."""
    forbidden = {
        "python", "api-design", "testing", "database-patterns",
        "documentation", "code-review",
    }
    assert AGENT_SKILL_ALLOWLIST["agent"].isdisjoint(forbidden)


def test_agent_skill_permission_check() -> None:
    assert context_builder.check_skill_permission(
        "agent", "agent-routing"
    ) is True
    assert context_builder.check_skill_permission(
        "agent", "python"
    ) is False
    assert context_builder.check_skill_permission(
        "agent", "code-review"
    ) is False


# ── Spawn allowlist (locked decision #4) ────────────────────────────────────

def test_agent_spawn_allowlist_includes_phase_a_roles() -> None:
    roles = set(MAIN_SUBAGENT_ALLOWLIST["agent"])
    assert {"explorer", "investigator", "reviewer-aux"}.issubset(roles)


def test_agent_spawn_allowlist_includes_pipeline_roles() -> None:
    """Locked decision #4 — agent may spawn individual pipeline
    roles ad-hoc."""
    roles = set(MAIN_SUBAGENT_ALLOWLIST["agent"])
    assert {"planner", "coder", "reviewer"}.issubset(roles)


def test_agent_can_spawn_designer() -> None:
    """Designer became an ad-hoc-spawnable direct worker when the
    standalone catalog shipped `workers/designer.agent.md`. The old B.1
    rule ("ask the user for /feature-dev instead") was an embedded-host
    workaround; the CLI/GUI hosts delegate design work directly."""
    assert check_subagent_allowed("agent", "designer") is True


def test_agent_cannot_spawn_unknown_role() -> None:
    assert check_subagent_allowed("agent", "saboteur") is False


def test_agent_can_spawn_each_listed_role() -> None:
    for role in MAIN_SUBAGENT_ALLOWLIST["agent"]:
        assert check_subagent_allowed("agent", role) is True, role


def test_agent_list_includes_six_roles() -> None:
    roles = list_subagent_roles("agent")
    assert set(roles) >= {
        "explorer", "investigator", "reviewer-aux",
        "planner", "coder", "reviewer",
    }


def test_agent_deny_reason_for_unknown_role_lists_pipeline_roles() -> None:
    msg = subagent_deny_reason("agent", "ghost")
    assert "ghost" in msg
    # The message lists valid roles; pipeline roles must appear now.
    assert "coder" in msg or "planner" in msg


# ── Pipeline-role tool sets (intersection sanity) ───────────────────────────

def test_pipeline_roles_have_tool_policies() -> None:
    """Spawning planner/coder/reviewer requires entries in SUBAGENT_POLICIES
    so musubi_spawn_subagent doesn't fail-closed on an unknown role."""
    for role in ("planner", "coder", "reviewer"):
        assert role in SUBAGENT_POLICIES, role
        assert SUBAGENT_POLICIES[role], f"{role} has empty tool list"


def test_coder_subagent_can_write_when_main_grants_write() -> None:
    """An agent-spawned coder needs Write/Edit/Bash to be useful.
    The agent's own tool list does NOT include those, so in
    practice the intersection drops them — verify the math here."""
    main_tools = ["Read", "View", "Grep", "Glob"]  # agent's tools
    eff = effective_subagent_tools("agent", main_tools, "coder")
    assert "Write" not in eff
    assert "Edit" not in eff
    assert "Bash" not in eff
    assert "Read" in eff


def test_coder_subagent_full_tools_when_main_grants_all() -> None:
    """If a future runner spawns coder with elevated main_tools (e.g.
    when the user has explicitly authorized writes for this turn), the
    intersection should yield the full coder tool set."""
    main_tools = ["Read", "View", "Grep", "Glob", "Write", "Edit", "Bash"]
    eff = effective_subagent_tools("agent", main_tools, "coder")
    assert {"Write", "Edit", "Bash"}.issubset(set(eff))


def test_planner_subagent_is_read_only() -> None:
    """Planner spawned ad-hoc must remain read-only — it scopes work,
    it does not write."""
    tools = set(SUBAGENT_POLICIES["planner"])
    assert tools.isdisjoint({"Write", "Edit", "Bash"})


def test_reviewer_subagent_is_read_only() -> None:
    tools = set(SUBAGENT_POLICIES["reviewer"])
    assert tools.isdisjoint({"Write", "Edit", "Bash"})


# ── Agent + skill files on disk ─────────────────────────────────────────────

_AGENT_FILE = _REPO_ROOT / ".github" / "agents" / "root" / "agent.agent.md"
_CODER_WORKER_FILE = (
    _REPO_ROOT / ".github" / "agents" / "workers" / "coder.agent.md"
)
_SKILL_FILE = (
    _REPO_ROOT / ".github" / "skills" / "agent-routing" / "SKILL.md"
)


def test_agent_agent_file_exists() -> None:
    assert _AGENT_FILE.is_file()


def test_catalog_has_no_flat_agent_files() -> None:
    """The catalog is fully purpose-organised (root/, workers/, meta/):
    a flat .agent.md would be dead weight no resolver prefers — the last
    host that read flat paths was the removed extension."""
    agents = _REPO_ROOT / ".github" / "agents"
    flat = sorted(p.name for p in agents.glob("*.agent.md"))
    assert flat == [], f"unexpected flat agent files: {flat}"


def test_agent_agent_frontmatter_declares_contract() -> None:
    """Spot-check the frontmatter so a refactor that drops a field fails
    here, not silently at runtime."""
    text = _AGENT_FILE.read_text(encoding="utf-8")
    assert text.startswith("---"), "must have YAML frontmatter"
    head, _, _ = text[3:].partition("---")
    for required in (
        "name:",
        "version:",
        "spawn_allowlist:",
        "max_spawns_per_role_per_turn:",
        "inject_skills:",
        "sees:",
    ):
        assert required in head, f"frontmatter missing {required!r}"


def test_agent_agent_declares_three_per_role_spawn_cap() -> None:
    text = _AGENT_FILE.read_text(encoding="utf-8")
    assert "max_spawns_per_role_per_turn: 3" in text


def test_agent_agent_lists_direct_worker_spawn_roles() -> None:
    text = _AGENT_FILE.read_text(encoding="utf-8")
    for role in (
        "explorer", "investigator", "reviewer-aux",
        "planner", "designer", "coder", "reviewer",
    ):
        assert f"- {role}" in text, f"spawn_allowlist missing {role!r}"


def test_agent_agent_disallows_writes() -> None:
    """The agent routes; it must not write to disk itself."""
    text = _AGENT_FILE.read_text(encoding="utf-8")
    assert 'disallowedTools: ["Write", "Edit", "Bash"]' in text


def test_agent_prompt_has_artifact_task_fast_path() -> None:
    text = _AGENT_FILE.read_text(encoding="utf-8")

    assert "Artifact creation requests are concrete targets" in text
    assert "create html dashboard" in text
    assert "Pull one relevant skill" in text
    assert "spawn coder once" in text
    assert "compact single-file HTML" in text


def test_coder_prompt_never_requests_empty_write() -> None:
    text = _CODER_WORKER_FILE.read_text(encoding="utf-8").lower()

    assert "write_file` with empty content" not in text
    assert "never reset a file with an empty write" in text


def test_coder_prompt_requires_complete_first_html() -> None:
    text = _CODER_WORKER_FILE.read_text(encoding="utf-8").lower()

    assert "complete valid html" in text
    assert "closing tags" in text
    assert "at most one verification round" in text


def test_agent_prompt_has_blocked_retry_guard() -> None:
    text = _AGENT_FILE.read_text(encoding="utf-8")

    assert "output_too_large_for_single_tool_call" in text
    assert "retry_same_strategy=false" in text
    assert "Do not spawn the same role with the same brief" in text
    assert "Do not summon a pipeline only to recover" in text


def test_coder_prompt_prefers_direct_html_over_generator() -> None:
    text = _CODER_WORKER_FILE.read_text(encoding="utf-8")

    assert "HTML/page/dashboard artifact" in text
    assert "write the requested HTML file as the primary artifact" in text
    assert "Do not substitute a generator script" in text
    assert "explicitly accepts that fallback" in text


def test_agent_routing_skill_file_exists() -> None:
    assert _SKILL_FILE.is_file()


def test_agent_routing_skill_has_name_frontmatter() -> None:
    text = _SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: agent-routing" in text


# ── SUBAGENT_ROLE_SKILLS lockstep with new pipeline roles ──────────────────

def test_agent_routing_skill_routes_mutating_work_to_workers() -> None:
    text = _SKILL_FILE.read_text(encoding="utf-8")

    assert "never spawn sub-agents while their runners are not wired" not in text
    assert "Create or edit files" in text
    assert "coder" in text
    assert "diagnostics" in text
    assert "investigator" in text


def test_pipeline_roles_register_no_role_skill() -> None:
    """B.1 ships pipeline roles in SUBAGENT_POLICIES with no role-procedure
    SKILL.md (the agent.md body is the procedure; the runner pushes that
    in B.2). The lockstep test in test_subagent_context.py requires every
    role have an entry — make sure ours is `None`, not a stray skill_id."""
    from validation.subagent_context import SUBAGENT_ROLE_SKILLS
    for role in ("planner", "coder", "reviewer"):
        assert role in SUBAGENT_ROLE_SKILLS, role
        assert SUBAGENT_ROLE_SKILLS[role] is None, role


# ── No regressions: non-agent paths unchanged ────────────────────────

# ── Phase C.2: summarizer role wiring ────────────────────────────────────────


def test_agent_can_spawn_summarizer() -> None:
    """C.2 — the agent must be able to spawn the summarizer for
    the 90% reactive-compaction branch."""
    assert check_subagent_allowed("agent", "summarizer") is True


def test_summarizer_role_has_no_tools() -> None:
    """C.2 — summarizer is text-only. No tools, no spawns, no Read/Write."""
    from policy_engine import SUBAGENT_POLICIES
    assert "summarizer" in SUBAGENT_POLICIES
    assert SUBAGENT_POLICIES["summarizer"] == []


def test_summarizer_role_has_skill() -> None:
    """C.2 — summarizer's procedure is pushed via a SKILL.md."""
    from validation.subagent_context import SUBAGENT_ROLE_SKILLS
    assert SUBAGENT_ROLE_SKILLS.get("summarizer") == "summarizer"


def test_summarizer_agent_file_exists() -> None:
    p = Path(_REPO_ROOT) / ".github" / "agents" / "workers" / "summarizer.agent.md"
    assert p.exists()
    body = p.read_text(encoding="utf-8")
    assert "name: Summarizer" in body
    # Hard guarantees from the agent file.
    assert "tools: []" in body
    assert "maxTurns: 1" in body


def test_summarizer_skill_file_exists() -> None:
    p = Path(_REPO_ROOT) / ".github" / "skills" / "summarizer" / "SKILL.md"
    assert p.exists()
    body = p.read_text(encoding="utf-8")
    assert "name: summarizer" in body


def test_planner_context_unchanged_by_agent_addition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Adding the agent branch must not bleed memory or other keys
    into the existing planner path."""
    from session import state
    from storage import db as _db

    # Build an isolated DB rather than importing the conftest fixture chain.
    db_path = tmp_path / "orch_regress.db"
    _db.init_db(db_path)
    sid = state.create_session("smoke request", db_path=db_path)
    ctx: dict[str, Any] = context_builder.build_context(
        sid, "planner", db_path=db_path
    )
    assert set(ctx.keys()) == {"request"}
    assert ctx["request"] == "smoke request"
