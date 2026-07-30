from __future__ import annotations

import json

import pytest

from agent.pipeline_runner import _plan_pipeline_resume


PLAN = [
    {"stage": "plan", "role": "planner"},
    {"stage": "design", "role": "designer"},
    {"stage": "code", "role": "coder"},
    {"stage": "review", "role": "reviewer"},
]


def row(stage: str, output: object | None, attempt: int = 1) -> dict:
    return {
        "stage": stage,
        "chunk_id": None,
        "attempt": attempt,
        "output": None if output is None else json.dumps(output),
    }


def test_approve_skips_durable_completed_stages() -> None:
    resume = _plan_pipeline_resume(
        PLAN,
        [row("plan", {"summary": "planned"}), row("design", None)],
        {"action": "approve", "user_hint": None, "extra_budget": 0},
    )

    assert resume.start_index == 1
    assert resume.completed_roles == ("planner",)
    assert resume.retry_stage is None


def test_retry_reopens_the_latest_durable_stage() -> None:
    resume = _plan_pipeline_resume(
        PLAN,
        [
            row("plan", {"summary": "planned"}),
            row("design", {"summary": "designed"}, attempt=2),
            row("code", None),
        ],
        {"action": "retry", "user_hint": "keep API stable", "extra_budget": 0},
    )

    assert resume.start_index == 1
    assert resume.retry_stage == "design"
    assert resume.retry_attempt == 3
    assert resume.user_hint == "keep API stable"


@pytest.mark.parametrize("action", ["grant", "force"])
def test_budget_actions_resume_first_incomplete_stage(action: str) -> None:
    resume = _plan_pipeline_resume(
        PLAN,
        [row("plan", {"summary": "planned"}), row("design", None)],
        {"action": action, "user_hint": None, "extra_budget": 3},
    )

    assert resume.start_index == 1
    assert resume.force_no_spawns is (action == "force")


def test_resume_rejects_missing_or_unknown_pending_action() -> None:
    with pytest.raises(RuntimeError, match="pending resume action"):
        _plan_pipeline_resume(PLAN, [], {"action": None})
    with pytest.raises(RuntimeError, match="Unknown pending"):
        _plan_pipeline_resume(PLAN, [], {"action": "invent"})
