"""Standalone agent token accounting."""

from __future__ import annotations

import pytest

from agent import budget as budget_module
from agent.budget import (
    TokenBudgetEnforcer,
    TokenBudgetExhaustedError,
    estimate_tokens_from_chars,
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
