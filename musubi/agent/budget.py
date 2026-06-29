"""Credit budgeting for the standalone agent host.

musubi-tier: substrate
expires-when: never - cost controls belong at the LM-call boundary, not
  in a provider-specific UI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

BudgetStatus = Literal["allow", "warn", "halt"]


@dataclass(frozen=True)
class ModelRate:
    """USD-per-million-token rates for one model family."""

    input: float
    cached_input: float
    output: float
    cache_write: float


RATES: dict[str, ModelRate] = {
    "claude-sonnet-4.6": ModelRate(3.00, 0.30, 15.00, 3.75),
    "claude-sonnet-4.5": ModelRate(3.00, 0.30, 15.00, 3.75),
    "claude-haiku-4.5": ModelRate(0.80, 0.08, 4.00, 1.00),
    "claude-opus-4.8": ModelRate(15.00, 1.50, 75.00, 18.75),
    "claude-opus-4.7": ModelRate(15.00, 1.50, 75.00, 18.75),
    "claude-opus-4.5": ModelRate(15.00, 1.50, 75.00, 18.75),
    "gpt-4o": ModelRate(2.50, 1.25, 10.00, 2.50),
    "gpt-4o-mini": ModelRate(0.15, 0.075, 0.60, 0.15),
    "gpt-4.1": ModelRate(2.00, 1.00, 8.00, 2.00),
    "gpt-4.1-mini": ModelRate(0.40, 0.20, 1.60, 0.40),
    "gpt-5-mini": ModelRate(0.25, 0.05, 2.00, 0.25),
    "gemini-2.5-flash": ModelRate(0.30, 0.075, 2.50, 0.30),
}

UNKNOWN_FAMILY_RATE = ModelRate(3.00, 0.30, 15.00, 3.75)


def rate_for(family: str) -> ModelRate:
    """Return a model-family rate, falling back conservatively."""
    return RATES.get(family, UNKNOWN_FAMILY_RATE)


def estimate_tokens_from_chars(chars: int) -> int:
    """Char-to-token approximation shared with the extension runner."""
    if chars <= 0:
        return 0
    return math.ceil(chars / 4)


def estimate_call_credits(
    family: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    """Estimate one LM call in credits, where one credit is USD 0.01."""
    rate = rate_for(family)
    cached = max(0, min(input_tokens, cached_input_tokens))
    fresh = max(0, input_tokens - cached)
    usd = (
        fresh * rate.input
        + cached * rate.cached_input
        + max(0, output_tokens) * rate.output
    ) / 1_000_000
    return usd / 0.01


class BudgetEnforcer:
    """Running credit accountant for one CLI turn."""

    def __init__(self, max_credits: float, warn_at_ratio: float = 0.8) -> None:
        if not math.isfinite(max_credits) or max_credits <= 0:
            raise ValueError(
                f"BudgetEnforcer: max_credits must be positive, got {max_credits}"
            )
        if warn_at_ratio <= 0 or warn_at_ratio > 1:
            raise ValueError(
                "BudgetEnforcer: warn_at_ratio must be in (0, 1], "
                f"got {warn_at_ratio}"
            )
        self.max_credits = float(max_credits)
        self.warn_at_ratio = float(warn_at_ratio)
        self._credits_used = 0.0
        self._warned = False

    @property
    def credits_used(self) -> float:
        return self._credits_used

    @property
    def remaining(self) -> float:
        return max(0.0, self.max_credits - self._credits_used)

    @property
    def warned(self) -> bool:
        return self._warned

    def preflight(self, estimated_credits: float) -> BudgetStatus:
        projected = self._credits_used + max(0.0, estimated_credits)
        if projected > self.max_credits:
            return "halt"
        if projected >= self.max_credits * self.warn_at_ratio and not self._warned:
            return "warn"
        return "allow"

    def charge(self, actual_credits: float) -> BudgetStatus:
        if not math.isfinite(actual_credits) or actual_credits < 0:
            raise ValueError(
                "BudgetEnforcer.charge: actual_credits must be non-negative, "
                f"got {actual_credits}"
            )
        self._credits_used += actual_credits
        if self._credits_used > self.max_credits:
            return "halt"
        if self._credits_used >= self.max_credits * self.warn_at_ratio and not self._warned:
            self._warned = True
            return "warn"
        return "allow"


class BudgetExhaustedError(RuntimeError):
    """Raised when a preflight or postflight budget check halts a turn."""

    def __init__(
        self,
        *,
        phase: Literal["preflight", "postflight"],
        credits_used: float,
        max_credits: float,
        family: str,
        this_call_credits: float,
    ) -> None:
        self.phase = phase
        self.credits_used = credits_used
        self.max_credits = max_credits
        self.family = family
        self.this_call_credits = this_call_credits
        super().__init__(
            f"agent budget exhausted at {phase}: {credits_used:.2f} of "
            f"{max_credits:.2f} credits used after a "
            f"{this_call_credits:.2f}-credit {family} call"
        )
