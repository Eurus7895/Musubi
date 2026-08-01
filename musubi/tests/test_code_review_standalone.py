"""Standalone code-review pipeline (worker-prompt unification).

The code-review roles (scoper / finder / synthesizer) are first-class
sub-agent roles with worker prompts, pushed skills, and pipeline-narrowed
spawn rights — so `agent "<diff>" --pipeline code-review` runs end-to-end
with the same governance as any other pipeline: fail-closed policy, skill
push (HI #2), evaluator firewall (HI #3), audited spawns (HI #8).
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any

from agent.run import run_agent
from agent.vendors.base import LMResponse, LMRouter
from validation.subagent_context import build_subagent_context

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from policy_engine import (  # noqa: E402
    MAIN_SUBAGENT_ALLOWLIST,
    PIPELINE_POLICIES,
    SUBAGENT_POLICIES,
    check_subagent_allowed,
)

_ROLES = ("scoper", "finder", "synthesizer")


def _musubi_dir() -> Path:
    return Path(__file__).resolve().parent.parent


# ── Policy: first-class roles, synced, but never ad-hoc spawnable ───────────


def test_code_review_roles_in_subagent_policies_and_synced() -> None:
    for role in _ROLES:
        assert role in SUBAGENT_POLICIES, role
        assert set(SUBAGENT_POLICIES[role]) == set(
            PIPELINE_POLICIES["code-review"][role]
        ), role


def test_code_review_roles_not_adhoc_spawnable_by_agent() -> None:
    """Pipeline-internal roles: reachable through `--pipeline code-review`
    only, never as a direct root spawn (locked decision #4)."""
    for role in _ROLES:
        assert role not in MAIN_SUBAGENT_ALLOWLIST["root"], role
        assert check_subagent_allowed("agent", role) is False, role


# ── HI #2: each stage role pushes its skill ─────────────────────────────────


def test_code_review_stage_roles_get_pushed_skills() -> None:
    expected = {
        "scoper": "pr-scope-detection",
        "finder": "per-file-review",
        "synthesizer": "code-review",
    }
    for role, skill_id in expected.items():
        ctx = build_subagent_context(
            brief="b", role=role, pushed_skill_id=skill_id,
        )
        assert ctx.role_skill, f"{role} skill did not load"
        assert ctx.role_skill_id == skill_id
        assert skill_id in ctx.role_skill or "name:" in ctx.role_skill


# ── Worker prompts exist and carry the worker contract ─────────────────────


def test_worker_prompts_exist_with_contract() -> None:
    workers = _REPO_ROOT / ".github" / "agents" / "workers"
    for role in _ROLES:
        p = workers / f"{role}.agent.md"
        assert p.is_file(), p
        body = p.read_text(encoding="utf-8")
        assert "musubi-tier: ephemeral" in body, role
        assert "expires-when:" in body and "cost-lever:" in body, role
        assert '["Read", "View", "Grep", "Glob"]' in body, role
        assert "## Output Contract" in body, role
        # Worker prompts must not resurrect the embedded ceremony or
        # shadow the constant firewall.
        assert "musubi_write_stage" not in body, role
        assert "musubi_read_stage" not in body, role
        assert "spawn_allowlist" not in body, role


# ── End-to-end: order + evaluator firewall (HI #3) ──────────────────────────


def _text(s: str) -> LMResponse:
    return LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": s}])


class ReviewRouter(LMRouter):
    """Canned responses per stage, in execution order; records briefs and
    tool surfaces."""

    name = "review"
    model = "review-1"

    def __init__(self) -> None:
        self._responses = [
            _text("scope: fileA.py high, fileB.py low"),
            _text("findings: F1 contract break in fileA.py\n"
                  "files for per-file review:\n- fileA.py | contract"),
            _text(json.dumps({
                "status": "pass", "summary": "review complete",
                "verdict": "one finding",
            })),
        ]
        self.briefs: list[str] = []
        self.tool_surfaces: list[set[str]] = []

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        system = str(messages[0].get("content") or "") if messages else ""
        if "STAGE PREFLIGHT" in system:
            payload = json.loads(str(messages[1]["content"]))
            selected = {
                "scoper": "pr-scope-detection",
                "finder": "per-file-review",
                "synthesizer": "code-review",
            }[payload["role"]]
            return _text(json.dumps({
                "skill_id": selected,
                "goal": f"Complete the {payload['role']} stage",
                "exit_when": [],
            }))
        self.tool_surfaces.append({t["name"] for t in tools})
        for m in messages:
            c = m.get("content")
            if isinstance(c, str) and "## Brief" in c:
                self.briefs.append(c.split("## Brief", 1)[1])
                break
        if not self._responses:
            raise AssertionError("ReviewRouter ran out of canned responses")
        return self._responses.pop(0)


def test_code_review_pipeline_runs_standalone_with_evaluator_firewall() -> None:
    router = ReviewRouter()
    diff_request = "review this diff:\n--- a/fileA.py\n+++ b/fileA.py\n+x = 1"
    answer = asyncio.run(run_agent(
        diff_request, router, _musubi_dir(), log=io.StringIO(),
        pipeline="code-review",
    ))

    # The final stage's summary is the answer.
    assert '"status": "pass"' in answer
    assert len(router.briefs) == 3

    scoper_brief, finder_brief, synth_brief = router.briefs
    # Stage 0 receives the raw request (the diff).
    assert "review this diff" in scoper_brief
    # The finder sees the request plus the scoper summary.
    assert "review this diff" in finder_brief
    assert "scope: fileA.py high" in finder_brief
    # HI #3: the evaluator sees ONLY the finder's output — not the
    # original request, not the scoper stage.
    assert "F1 contract break" in synth_brief
    assert "review this diff" not in synth_brief
    assert "scope: fileA.py high" not in synth_brief

    # Feature A: the synthesizer (spawns: [reviewer-aux]) carries the spawn
    # tool; scoper and finder declare nothing → leaves.
    assert "musubi_spawn_subagent" not in router.tool_surfaces[0]
    assert "musubi_spawn_subagent" not in router.tool_surfaces[1]
    assert "musubi_spawn_subagent" in router.tool_surfaces[2]
