"""Standalone agent token accounting."""

from __future__ import annotations

import pytest

from agent import budget as budget_module
from agent.budget import (
    ChildTokenBudget,
    TokenBudgetEnforcer,
    TokenBudgetExhaustedError,
    estimate_tokens_from_chars,
    pipeline_stage_allowance,
)


def test_budget_module_exports_token_accounting_only() -> None:
    assert not hasattr(budget_module, "RATES")
    assert not hasattr(budget_module, "estimate_call_credits")
    assert not hasattr(budget_module, "BudgetEnforcer")
    assert not hasattr(budget_module, "BudgetExhaustedError")


def test_estimate_tokens_from_chars_uses_four_char_ceil() -> None:
    assert estimate_tokens_from_chars(0) == 0
    assert estimate_tokens_from_chars(4) == 1
    assert estimate_tokens_from_chars(5) == 2


def test_token_budget_enforcer_warns_once_then_halts() -> None:
    budget = TokenBudgetEnforcer(max_tokens=1000, warn_at_ratio=0.8)
    assert budget.preflight(799) == "allow"
    assert budget.preflight(800) == "warn"
    assert budget.charge(700) == "allow"
    assert budget.charge(100) == "warn"
    assert budget.charge(50) == "allow"
    assert budget.charge(151) == "halt"
    assert budget.tokens_used == 1001


def test_token_budget_exhausted_error_carries_context() -> None:
    err = TokenBudgetExhaustedError(
        phase="postflight",
        tokens_used=1201,
        max_tokens=1000,
        this_call_tokens=250,
    )
    assert err.phase == "postflight"
    assert err.tokens_used == 1201
    assert err.max_tokens == 1000
    assert err.this_call_tokens == 250
    assert "token budget" in str(err)


def test_token_budget_enforcer_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        TokenBudgetEnforcer(0)
    with pytest.raises(ValueError):
        TokenBudgetEnforcer(10, warn_at_ratio=0)
    with pytest.raises(ValueError):
        TokenBudgetEnforcer(10).charge(-1)


# ── ChildTokenBudget: per-stage allowance charged through to the parent ─────


def test_child_token_budget_charges_child_and_parent_once() -> None:
    parent = TokenBudgetEnforcer(200_000)
    child = ChildTokenBudget(parent, 50_000)
    assert child.max_tokens == 50_000
    assert child.preflight(30_000) == "allow"
    assert child.charge(30_000) == "allow"
    # The spend is reflected in BOTH the child and the shared parent.
    assert child.tokens_used == 30_000
    assert parent.tokens_used == 30_000
    # 30k + 25k = 55k > the 50k child allowance → halt, even though the parent
    # run still has plenty of room.
    assert child.preflight(25_000) == "halt"


def test_child_token_budget_is_stricter_of_parent() -> None:
    parent = TokenBudgetEnforcer(40_000)
    parent.charge(30_000)  # parent nearly exhausted
    # Child allowance is larger than the parent's remaining 10k.
    child = ChildTokenBudget(parent, 50_000)
    # The child alone would allow 20k, but the parent caps it → halt.
    assert child.preflight(20_000) == "halt"
    assert child.remaining == 10_000  # min(child 50k, parent 10k)


def test_pipeline_stage_allowance_reserves_later_stage_shares() -> None:
    parent = TokenBudgetEnforcer(200_000)
    # Four stages remaining: each gets an even quarter of the live remaining.
    a_plan = pipeline_stage_allowance(parent, 4)
    assert a_plan == 50_000
    plan_budget = ChildTokenBudget(parent, a_plan)
    plan_budget.charge(a_plan)  # planner burns its whole share

    a_design = pipeline_stage_allowance(parent, 3)
    assert a_design == 50_000  # remaining 150k // 3
    design_budget = ChildTokenBudget(parent, a_design)
    design_budget.charge(a_design)  # designer burns its whole share

    # Coder + reviewer still hold a positive, unspent reserve — the early
    # stages could NOT reach into it.
    assert parent.remaining == 100_000
    assert pipeline_stage_allowance(parent, 2) == 50_000


def test_pipeline_stage_allowance_rejects_nonpositive_remaining() -> None:
    parent = TokenBudgetEnforcer(1_000)
    with pytest.raises(ValueError):
        pipeline_stage_allowance(parent, 0)


def test_pipeline_stage_allowance_never_returns_zero_while_budget_remains() -> None:
    parent = TokenBudgetEnforcer(1_000)
    parent.charge(999)
    # 1 token left across 4 stages → floor of 1, never 0.
    assert pipeline_stage_allowance(parent, 4) == 1
