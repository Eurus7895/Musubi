"""Standalone agent token and optional credit accounting.

Token counts are the standalone host's source of truth because public
model prices drift. Credit math remains as an optional estimate for logs.
"""

from __future__ import annotations

import pytest

from agent.budget import (
    BudgetEnforcer,
    UNKNOWN_FAMILY_RATE,
    BudgetExhaustedError,
    TokenBudgetEnforcer,
    TokenBudgetExhaustedError,
    estimate_call_credits,
    estimate_tokens_from_chars,
    rate_for,
)


def test_rate_for_returns_known_family() -> None:
    rate = rate_for("claude-sonnet-4.6")
    assert rate.input == 3.00
    assert rate.cached_input == 0.30
    assert rate.output == 15.00


def test_rate_for_uses_conservative_unknown_fallback() -> None:
    assert rate_for("not-a-real-family") == UNKNOWN_FAMILY_RATE


def test_estimate_tokens_from_chars_uses_four_char_ceil() -> None:
    assert estimate_tokens_from_chars(0) == 0
    assert estimate_tokens_from_chars(4) == 1
    assert estimate_tokens_from_chars(5) == 2


def test_estimate_call_credits_counts_cached_input_cheaper() -> None:
    credits = estimate_call_credits(
        "claude-sonnet-4.6",
        input_tokens=100_000,
        output_tokens=0,
        cached_input_tokens=80_000,
    )
    assert round(credits, 3) == 8.4


def test_budget_enforcer_warns_once_then_halts() -> None:
    budget = BudgetEnforcer(max_credits=50, warn_at_ratio=0.8)
    assert budget.preflight(39) == "allow"
    assert budget.preflight(40) == "warn"
    assert budget.charge(30) == "allow"
    assert budget.charge(15) == "warn"
    assert budget.charge(1) == "allow"
    assert budget.charge(5) == "halt"
    assert budget.credits_used == 51


def test_budget_exhausted_error_carries_context() -> None:
    err = BudgetExhaustedError(
        phase="preflight",
        credits_used=52.3,
        max_credits=50,
        family="claude-sonnet-4.6",
        this_call_credits=12.5,
    )
    assert err.phase == "preflight"
    assert err.credits_used == 52.3
    assert err.max_credits == 50
    assert err.family == "claude-sonnet-4.6"
    assert "claude-sonnet-4.6" in str(err)


def test_budget_enforcer_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        BudgetEnforcer(0)
    with pytest.raises(ValueError):
        BudgetEnforcer(10, warn_at_ratio=0)
    with pytest.raises(ValueError):
        BudgetEnforcer(10).charge(-1)


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
